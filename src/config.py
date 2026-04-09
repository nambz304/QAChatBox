from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Anthropic ──────────────────────────────────────────────
    anthropic_api_key: str = "sk-ant-placeholder"
    # Fast + cheap: routing decisions
    claude_model: str = "claude-haiku-4-5-20251001"
    # Smart: final answer synthesis
    claude_model_smart: str = "claude-sonnet-4-6"

    # ── Storage paths ──────────────────────────────────────────
    database_url: str = "postgresql://kb_user:kb_pass@localhost:5432/kb_db"
    chroma_path: str = "data/chroma_db"

    # ── Embeddings (sentence-transformers, runs locally) ───────
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # ── API ────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ── Admin credentials ──────────────────────────────────────
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # ── Employee demo credentials ──────────────────────────────
    employee_username: str = "employee"
    employee_password: str = "employee123"

    # ── Slack service account ──────────────────────────────────
    slack_service_username: str = "slack_service"
    slack_service_password: str = "slack-internal"

    # ── JWT ────────────────────────────────────────────────────
    jwt_secret: str = "change-me-in-production"

    # ── Slack (optional) ───────────────────────────────────────
    slack_bot_token: str = ""
    slack_app_token: str = ""
    api_base_url: str = "http://localhost:8000"

    # ── Pricing ($/1M tokens) — configurable via .env ──────────
    haiku_input_cost_per_1m:  float = 0.80
    haiku_output_cost_per_1m: float = 4.00
    sonnet_input_cost_per_1m: float = 3.00
    sonnet_output_cost_per_1m: float = 15.00
    usd_to_vnd_rate:           float = 25400.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
