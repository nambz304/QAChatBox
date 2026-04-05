"""
main.py — single entry point for local development.

Usage:
  python main.py            # start all services (API + UI + Slack bot)
  python main.py --api      # API only
  python main.py --ui       # Streamlit UI only
  python main.py --slack    # Slack bot only
  python main.py --seed     # seed DB and documents, then exit
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path


def seed():
    """Initialise DB and index policy documents."""
    from scripts.seed_data import seed as _seed
    _seed()


def start_api():
    from src.config import get_settings
    s = get_settings()
    return subprocess.Popen([
        sys.executable, "-m", "uvicorn",
        "src.api:app",
        "--host", s.api_host,
        "--port", str(s.api_port),
        "--reload",
        "--log-level", "info",
    ])


def start_ui():
    return subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", "src/ui.py",
        "--server.port", "8501",
        "--server.address", "0.0.0.0",
        "--server.headless", "false",
    ])


def start_slack():
    from src.config import get_settings
    s = get_settings()
    if not s.slack_bot_token or not s.slack_app_token:
        print("[!] Slack tokens not configured — skipping Slack bot")
        return None
    return subprocess.Popen([sys.executable, "-m", "src.slack_bot"])


def wait_for_api(retries: int = 20) -> bool:
    """Poll /health until the API is up."""
    import httpx
    for _ in range(retries):
        try:
            r = httpx.get("http://localhost:8000/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main():
    parser = argparse.ArgumentParser(description="Internal KB Chatbot")
    parser.add_argument("--api",   action="store_true", help="Start API only")
    parser.add_argument("--ui",    action="store_true", help="Start Streamlit UI only")
    parser.add_argument("--slack", action="store_true", help="Start Slack bot only")
    parser.add_argument("--seed",  action="store_true", help="Seed data and exit")
    args = parser.parse_args()

    # Always seed on startup (idempotent)
    print("Seeding database and documents...")
    seed()

    if args.seed:
        print("Seed complete.")
        return

    procs = []

    if args.api:
        procs.append(start_api())
    elif args.ui:
        procs.append(start_ui())
    elif args.slack:
        p = start_slack()
        if p:
            procs.append(p)
    else:
        # Default: start everything
        print("\nStarting all services...\n")

        api_proc = start_api()
        procs.append(api_proc)

        print("Waiting for API to be ready...")
        if wait_for_api():
            print("  API is up → http://localhost:8000")
            print("  Docs     → http://localhost:8000/docs\n")
        else:
            print("  [!] API did not respond in time — check for errors above")

        procs.append(start_ui())
        print("  Streamlit → http://localhost:8501\n")

        slack_proc = start_slack()
        if slack_proc:
            procs.append(slack_proc)

    print("Press Ctrl+C to stop all services.\n")

    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    # Ensure we run from project root so relative paths work
    project_root = Path(__file__).parent
    import os
    os.chdir(project_root)

    main()
