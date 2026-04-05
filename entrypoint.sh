#!/bin/bash
# Container entrypoint — seeds data then starts all services.
set -e

echo "────────────────────────────────────────"
echo " Internal KB Chatbot — starting up"
echo "────────────────────────────────────────"

# 0. Wait for PostgreSQL to be ready
echo "[0/3] Waiting for PostgreSQL..."
until python -c "import psycopg2; psycopg2.connect('${DATABASE_URL}').close()" 2>/dev/null; do
    echo "      PostgreSQL not ready, retrying..."
    sleep 2
done
echo "      PostgreSQL is ready."

# 1. Seed database and index policy documents (idempotent)
echo "[1/3] Seeding data..."
python -m scripts.seed_data

# 2. Start FastAPI in background
echo "[2/3] Starting FastAPI on :8000..."
uvicorn src.api:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --log-level info &

# Wait for API to be ready before starting UI
echo "      Waiting for API to be ready..."
until curl -sf http://localhost:8000/health > /dev/null; do
    sleep 1
done
echo "      API is up."

# 3. Start Streamlit in background
echo "[3/3] Starting Streamlit on :8501..."
streamlit run src/ui.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableCORS false \
    --browser.gatherUsageStats false &

# 4. Start Slack bot if tokens are configured
if [ -n "${SLACK_BOT_TOKEN}" ] && [ "${SLACK_BOT_TOKEN}" != "xoxb-your-bot-token" ] && \
   [ -n "${SLACK_APP_TOKEN}" ] && [ "${SLACK_APP_TOKEN}" != "xapp-your-app-token" ]; then
    echo "[4/4] Starting Slack bot..."
    python -m main --slack &
else
    echo "[!] Slack tokens not configured — Slack bot disabled"
fi

echo "────────────────────────────────────────"
echo " Services running:"
echo "   API       → http://localhost:8000"
echo "   API docs  → http://localhost:8000/docs"
echo "   Web UI    → http://localhost:8501"
echo "────────────────────────────────────────"

# Keep container alive — exit if any background job dies
wait -n
