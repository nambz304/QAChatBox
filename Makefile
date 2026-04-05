VENV     := .venv
PYTHON   := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
UVICORN  := $(VENV)/bin/uvicorn
STREAMLIT:= $(VENV)/bin/streamlit

# Python 3.11 để khớp với Dockerfile (psycopg2-binary chưa có wheel cho 3.14)
PYTHON3  := $(shell python3.11 -c "import sys; print(sys.executable)" 2>/dev/null || echo "python3.11_NOT_FOUND")

.DEFAULT_GOAL := help

# Guard: báo lỗi rõ ràng nếu chưa chạy make install
_check-venv:
	@test -f $(VENV)/bin/activate || \
	    (echo "Lỗi: venv chưa có. Chạy 'make install' trước." && exit 1)

# ── Setup ─────────────────────────────────────────────────────────────────────

.PHONY: install
install: ## Tạo venv (Python 3.11) và cài toàn bộ dependencies
	@if [ "$(PYTHON3)" = "python3.11_NOT_FOUND" ]; then \
	    echo "Lỗi: python3.11 không tìm thấy. Cài bằng: brew install python@3.11"; \
	    exit 1; \
	fi
	$(PYTHON3) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "Done. Run 'make db' rồi 'make seed' để khởi tạo data."

.PHONY: seed
seed: ## Seed SQLite + index ChromaDB (idempotent)
	$(PYTHON) -m scripts.seed_data

# ── Database (chỉ postgres container, không rebuild app) ──────────────────────

.PHONY: db
db: ## Khởi động postgres container (nhẹ, không rebuild)
	docker-compose up -d db
	@echo "Đợi PostgreSQL sẵn sàng..."
	@until docker-compose exec db pg_isready -U kb_user -d kb_db > /dev/null 2>&1; do sleep 1; done
	@echo "PostgreSQL ready."

.PHONY: db-stop
db-stop: ## Dừng postgres container
	docker-compose stop db

# ── Dev loop (local, không Docker) ────────────────────────────────────────────

.PHONY: dev
dev: _check-venv ## Chạy API + UI cùng lúc (local). Cần 'make db' trước nếu dùng Postgres
	@echo "Khởi động API :8000 và UI :8501 ..."
	@trap 'kill %1 %2 2>/dev/null; exit 0' INT; \
	$(UVICORN) src.api:app --reload --port 8000 & \
	sleep 2 && \
	$(STREAMLIT) run src/ui.py --server.port 8501 --server.headless true \
	    --browser.gatherUsageStats false & \
	wait

.PHONY: api
api: _check-venv ## Chỉ FastAPI :8000 với auto-reload
	$(UVICORN) src.api:app --reload --host 0.0.0.0 --port 8000

.PHONY: ui
ui: _check-venv ## Chỉ Streamlit :8501
	$(STREAMLIT) run src/ui.py --server.port 8501 --browser.gatherUsageStats false

.PHONY: slack
slack: _check-venv ## Chỉ Slack bot
	$(PYTHON) main.py --slack

# ── Package management ────────────────────────────────────────────────────────

.PHONY: add
add: ## Thêm package: make add pkg=<tên>   ví dụ: make add pkg=ragas
ifndef pkg
	$(error Thiếu tên package. Dùng: make add pkg=<tên>)
endif
	$(PIP) install $(pkg)
	@echo "$(pkg)" >> requirements.txt
	@echo "Đã thêm '$(pkg)' vào requirements.txt"

.PHONY: freeze
freeze: ## Sync requirements.txt từ venv hiện tại (pip freeze)
	$(PIP) freeze > requirements.txt
	@echo "requirements.txt đã được cập nhật."

# ── Inspect ───────────────────────────────────────────────────────────────────

.PHONY: chroma-inspect
chroma-inspect: _check-venv ## Xem toàn bộ chunks trong ChromaDB (filename, số chunks, preview)
	@$(PYTHON) scripts/chroma_inspect.py 2>&1 | grep -v "telemetry\|capture()"

.PHONY: sync
sync: _check-venv ## Detect desync giữa PostgreSQL và ChromaDB (dry-run, không sửa)
	@$(PYTHON) -m scripts.sync_docs 2>&1 | grep -v "telemetry\|capture()"

.PHONY: sync-fix
sync-fix: _check-venv ## Auto-fix desync giữa PostgreSQL và ChromaDB (không cần file gốc)
	@$(PYTHON) -m scripts.sync_docs --fix 2>&1 | grep -v "telemetry\|capture()"

.PHONY: sync-reindex
sync-reindex: _check-venv ## Xóa và index lại policy files từ disk
	@$(PYTHON) -m scripts.sync_docs --reindex 2>&1 | grep -v "telemetry\|capture()"

# ── Tests ─────────────────────────────────────────────────────────────────────

.PHONY: test
test: ## Chạy test offline (không cần API key)
	$(VENV)/bin/pytest tests/test_tools.py -v

.PHONY: test-all
test-all: ## Chạy toàn bộ test (cần ANTHROPIC_API_KEY)
	$(VENV)/bin/pytest tests/ -v

# ── Docker (integration / deploy) ─────────────────────────────────────────────

.PHONY: up
up: ## docker-compose up (không rebuild)
	docker-compose up

.PHONY: build
build: ## docker-compose up --build (rebuild image)
	docker-compose up --build

.PHONY: down
down: ## Dừng và xóa toàn bộ containers
	docker-compose down

.PHONY: logs
logs: ## Xem logs app container realtime
	docker-compose logs -f app

# ── Cleanup ───────────────────────────────────────────────────────────────────

.PHONY: clean
clean: ## Xóa venv và cache
	rm -rf $(VENV) .pytest_cache __pycache__ src/__pycache__ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned."

# ── Help ──────────────────────────────────────────────────────────────────────

.PHONY: help
help: ## Liệt kê tất cả lệnh
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
	    | awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
