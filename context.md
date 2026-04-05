# Session Context — Docker Debug (2026-04-04)

## Môi trường
- MacBook Intel, macOS
- Project: LangGraph RAG chatbot (`autoResearchAgent`)
- Stack: FastAPI + Streamlit + ChromaDB + PostgreSQL + Slack bot
- Chạy bằng Docker Compose

---

## Lỗi 1: `exec /entrypoint.sh: exec format error` (exit code 255)

### Triệu chứng
```
app-1 | exec /entrypoint.sh: exec format error
app-1 exited with code 255 (restarting)
```

### Nguyên nhân
`CMD ["/entrypoint.sh"]` yêu cầu kernel tự exec script qua shebang — dễ fail nếu có bất kỳ vấn đề format nào.

### Fix — `Dockerfile`
```dockerfile
# Trước
CMD ["/entrypoint.sh"]

# Sau
CMD ["bash", "/entrypoint.sh"]
```

---

## Lỗi 2: `NameError: name 'Loader' is not defined` trong PyYAML

### Triệu chứng
```
File "/usr/local/lib/python3.11/site-packages/yaml/__init__.py", line 29, in <module>
    def scan(stream, Loader=Loader):
                            ^^^^^^
NameError: name 'Loader' is not defined. Did you mean: 'loader'?
```
Container seeding fail ở bước `[1/3] Seeding data...`

### Nguyên nhân (đã verify bằng `docker run --rm`)
Multi-stage Dockerfile dùng `pip install --prefix=/install` ở builder stage.  
Khi `COPY --from=builder /install /usr/local`, **tất cả `.py` files của PyYAML thành 0 bytes** — chỉ còn `__init__.py` (12KB) và C extension `_yaml.so` (2.6MB).

```
-rw-r--r-- 1 root root       0  loader.py      ← empty
-rw-r--r-- 1 root root       0  dumper.py      ← empty
-rw-r--r-- 1 root root       0  cyaml.py       ← empty
...
```

`from .loader import *` của empty file → `Loader` không được define → `def scan(stream, Loader=Loader)` fail.

### Cách phát hiện
```bash
# Không exec được vào container đang restart → dùng image trực tiếp
docker run --rm autoresearchagent-app bash -c "ls -la /usr/local/lib/python3.11/site-packages/yaml/"
```

### Verify fix trước khi sửa Dockerfile
```bash
docker run --rm autoresearchagent-app bash -c "
pip install --no-cache-dir --force-reinstall PyYAML
python -c 'import chromadb; print(\"chromadb OK\")'
"
# → chromadb OK
```

### Fix — `Dockerfile` (runtime stage)
```dockerfile
# Thêm sau COPY --from=builder
RUN pip install --no-cache-dir --force-reinstall PyYAML
```

---

## Quy trình debug hiệu quả (rút ra từ session)

| Loại lỗi | Cách test nhanh | Rebuild? |
|-----------|----------------|----------|
| Python/import error | `docker run --rm <image> bash -c "..."` | Không (test trước) |
| Code Python (`src/`) | Sửa local → `docker-compose restart app` | Không (volume mount) |
| Dockerfile/entrypoint | Test local trước, rebuild sau | Bắt buộc |

**Nguyên tắc**: Luôn verify fix trong container/image trước → chỉ sửa file và rebuild khi đã chắc chắn.

---

## Trạng thái cuối session
- `Dockerfile`: đã fix cả 2 lỗi
- `requirements.txt`: không thay đổi (revert dòng PyYAML thừa)
- Chờ chạy `docker-compose down && docker-compose up --build` để verify hoàn chỉnh
