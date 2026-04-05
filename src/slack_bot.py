"""
Slack bot — runs alongside FastAPI, calls the same /chat endpoint.

Supports:
  • Direct messages (DMs) to the bot
  • @mentions in any channel

Each Slack user gets their own conversation context (session_id = slack_{user_id}).
Mentions in a channel share context per user-per-channel pair.

Setup:
  1. Create a Slack App at https://api.slack.com/apps
  2. Enable Socket Mode (Settings → Socket Mode → Enable)
  3. Subscribe to events: message.im, app_mention
     (Event Subscriptions → Subscribe to bot events)
  4. Add Bot Token Scopes: chat:write, im:history, app_mentions:read
  5. Install the app to your workspace
  6. Copy SLACK_BOT_TOKEN and SLACK_APP_TOKEN to .env
"""
import re

import httpx
from loguru import logger
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .config import get_settings

settings = get_settings()

_TOKEN: str = ""


def _login() -> str:
    """Obtain a JWT for the Slack service account."""
    r = httpx.post(
        f"{settings.api_base_url}/auth/login",
        json={"username": settings.slack_service_username,
              "password": settings.slack_service_password},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["token"]


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


# ── Helpers ───────────────────────────────────────────────────

def _call_chat_api(text: str, session_id: str) -> dict:
    global _TOKEN
    r = httpx.post(
        f"{settings.api_base_url}/chat",
        json={"message": text, "session_id": session_id},
        headers=_auth_headers(),
        timeout=45,
    )
    if r.status_code == 401:
        # Token expired — refresh and retry once
        _TOKEN = _login()
        r = httpx.post(
            f"{settings.api_base_url}/chat",
            json={"message": text, "session_id": session_id},
            headers=_auth_headers(),
            timeout=45,
        )
    r.raise_for_status()
    return r.json()


def _build_blocks(result: dict) -> list[dict]:
    """Format the API response as Slack Block Kit blocks."""
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": result["answer"]},
        }
    ]
    if result.get("citations"):
        sources = "  ·  ".join(f"`{c}`" for c in result["citations"])
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"📎 *Sources:* {sources}"},
            ],
        })
    return blocks


# ── Entry point ───────────────────────────────────────────────

def start() -> None:
    """Start the Slack bot using Socket Mode (no public URL needed)."""
    global _TOKEN
    if not settings.slack_bot_token or not settings.slack_app_token:
        logger.warning("Slack tokens not set — Slack bot disabled")
        return

    # Obtain JWT for Slack service account
    try:
        _TOKEN = _login()
        logger.info("Slack service account authenticated")
    except Exception as exc:
        logger.error(f"Slack service account login failed: {exc} — bot may not function")

    # Initialize App inside start() to avoid crash on import
    # when tokens are placeholder values
    app = App(token=settings.slack_bot_token)

    # ── Event handlers ────────────────────────────────────────

    @app.event("message")
    def handle_dm(event, say):
        """Handle direct messages sent to the bot."""
        # Ignore messages from bots, edited messages, and thread replies
        if event.get("bot_id") or event.get("subtype") or event.get("thread_ts"):
            return

        user_id = event.get("user", "unknown")
        text = event.get("text", "").strip()
        if not text:
            return

        session_id = f"slack_dm_{user_id}"
        logger.info(f"DM from {user_id}: {text[:80]}")

        try:
            result = _call_chat_api(text, session_id)
            say(blocks=_build_blocks(result), text=result["answer"])
        except Exception as exc:
            logger.error(f"DM handler error: {exc}")
            say(text="Sorry, I ran into an error. Please try again in a moment.")

    @app.event("app_mention")
    def handle_mention(event, say):
        """Handle @bot mentions in channels."""
        user_id = event.get("user", "unknown")
        channel_id = event.get("channel", "unknown")

        # Strip the @mention tag from the text
        raw_text = event.get("text", "")
        clean_text = re.sub(r"<@[A-Z0-9]+>", "", raw_text).strip()

        if not clean_text:
            say(text=(
                "Hi! I can help with:\n"
                "• Company policies and HR procedures\n"
                "• Employee information and statistics\n\n"
                "Just ask me anything!"
            ))
            return

        # Channel mentions share context per user per channel
        session_id = f"slack_ch_{user_id}_{channel_id}"
        logger.info(f"Mention from {user_id} in {channel_id}: {clean_text[:80]}")

        try:
            result = _call_chat_api(clean_text, session_id)
            say(blocks=_build_blocks(result), text=result["answer"])
        except Exception as exc:
            logger.error(f"Mention handler error: {exc}")
            say(text="Sorry, I ran into an error. Please try again in a moment.")

    logger.info("Starting Slack bot (Socket Mode)…")
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()


if __name__ == "__main__":
    start()
