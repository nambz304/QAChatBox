"""
Streamlit web UI — three pages:
  login  : username/password form
  chat   : main chat + conversation history sidebar
  admin  : document upload & management (admin role only)
"""
import json
import os
import uuid

import httpx
import streamlit as st

# ── Hide Streamlit default toolbar (Print, Record, Clear cache…) ──
_HIDE_TOOLBAR_CSS = """
<style>
    #MainMenu          { visibility: hidden; }
    header             { visibility: hidden; }
    footer             { visibility: hidden; }
    .stDeployButton    { display: none; }
    [data-testid="stToolbar"]          { display: none; }
    [data-testid="stDecoration"]       { display: none; }
    [data-testid="stStatusWidget"]     { display: none; }
</style>
"""

# ── API client ────────────────────────────────────────────────

_API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")


def _headers() -> dict:
    """Return headers with auth token only if it exists."""
    token = st.session_state.get('token', '').strip()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api(method: str, path: str, **kwargs):
    url = f"{_API_BASE}{path}"
    try:
        # Always disable SSL verification for localhost development
        # Merge user kwargs with defaults to allow override
        request_kwargs = {"verify": False, "timeout": 45}
        request_kwargs.update(kwargs)
        
        r = httpx.request(method, url, headers=_headers(), **request_kwargs)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.json().get("detail", str(exc))
        raise RuntimeError(detail) from exc
    except httpx.RequestError as exc:
        # Provide more detailed error info for debugging
        error_msg = f"Cannot reach API at {_API_BASE}"
        if "ssl" in str(exc).lower():
            error_msg += " (SSL error - try verify=False)"
        elif "connect" in str(exc).lower():
            error_msg += " (Connection refused - is server running?)"
        error_msg += f"\n\nDetails: {exc}"
        raise RuntimeError(error_msg) from exc


# ── Session helpers ───────────────────────────────────────────

def _init_session() -> None:
    defaults = {
        "logged_in":   False,
        "username":    "",
        "role":        "employee",
        "token":       "",
        "session_id":  "",   # set after login
        "messages":    [],   # display cache for current session
        "page":        "chat",
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def _default_session_id(username: str) -> str:
    """Stable session ID for the user's latest/default conversation."""
    return f"web_{username}"


def _new_session_id(username: str) -> str:
    """Unique session ID for a brand-new conversation."""
    return f"web_{username}_{uuid.uuid4().hex[:8]}"


def _logout() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()


def _load_session_messages(session_id: str) -> list[dict]:
    """Fetch conversation history from API and return as display-ready list."""
    try:
        rows = _api("GET", f"/history/{session_id}")
        return [
            {
                "role":      r["role"],
                "content":   r["content"],
                "citations": r.get("citations", []),
                "id":        r.get("id", 0),
            }
            for r in rows
        ]
    except RuntimeError:
        return []


def _list_conversations() -> list[dict]:
    try:
        return _api("GET", "/sessions",
                    params={"username": st.session_state.username})
    except RuntimeError:
        return []


def _session_title(conv: dict) -> str:
    """Short title derived from the first user message."""
    msg = conv.get("first_message") or ""
    return (msg[:45] + "…") if len(msg) > 45 else (msg or "New conversation")


# ── Page: login ───────────────────────────────────────────────

def _page_login() -> None:
    st.markdown(_HIDE_TOOLBAR_CSS, unsafe_allow_html=True)
    st.title("🏢 Internal Knowledge Base")
    st.caption("Powered by Claude · LangGraph · ChromaDB")
    st.divider()

    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.form("login"):
            st.subheader("Sign In")
            username = st.text_input("Username", placeholder="admin")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button(
                "Sign In", use_container_width=True, type="primary"
            )

        if submitted:
            try:
                data = _api("POST", "/auth/login",
                            json={"username": username, "password": password})
                st.session_state.logged_in  = True
                st.session_state.username   = data["username"]
                st.session_state.role       = data["role"]
                st.session_state.token      = data["token"]

                # Load the default (most recent) session for this user
                default_sid = _default_session_id(data["username"])
                st.session_state.session_id = default_sid
                st.session_state.messages   = _load_session_messages(default_sid)
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))

    st.divider()
    with st.expander("Demo credentials", expanded=True):
        col1, col2 = st.columns(2)
        col1.markdown("**Admin**\n\n`admin` / `admin123`")
        col2.markdown("**Employee**\n\n`employee` / `employee123`")


# ── Page: chat ────────────────────────────────────────────────

def _page_chat() -> None:
    st.markdown(_HIDE_TOOLBAR_CSS, unsafe_allow_html=True)

    # ── Sidebar ───────────────────────────────────────────────
    with st.sidebar:
        st.markdown(f"👤 **{st.session_state.username}**")
        st.caption(f"Role: {st.session_state.role}")
        st.divider()

        # New conversation button
        if st.button("✏️ New conversation", use_container_width=True, type="primary"):
            new_sid = _new_session_id(st.session_state.username)
            st.session_state.session_id = new_sid
            st.session_state.messages   = []
            st.rerun()

        # Past conversations list
        st.markdown("**Past conversations**")
        convs = _list_conversations()

        if not convs:
            st.caption("No conversations yet.")
        else:
            for conv in convs:
                sid   = conv["session_id"]
                title = _session_title(conv)
                count = conv.get("message_count", 0)
                date  = (conv.get("last_active") or "")[:10]

                is_active = sid == st.session_state.session_id
                label = f"{'▶ ' if is_active else ''}{title}"

                if st.button(label, key=f"conv_{sid}",
                             use_container_width=True,
                             help=f"{count} messages · {date}"):
                    st.session_state.session_id = sid
                    st.session_state.messages   = _load_session_messages(sid)
                    st.rerun()

                # Delete button for each conversation
                col_gap, col_del = st.columns([4, 1])
                with col_del:
                    if st.button("🗑", key=f"del_{sid}", help="Delete this conversation"):
                        try:
                            _api("DELETE", f"/history/{sid}")
                            # If deleting active session, reset to default
                            if sid == st.session_state.session_id:
                                st.session_state.session_id = _default_session_id(
                                    st.session_state.username
                                )
                                st.session_state.messages = []
                            st.rerun()
                        except RuntimeError as exc:
                            st.error(str(exc))

        st.divider()
        if st.session_state.role == "admin":
            if st.button("📁 Admin Panel", use_container_width=True):
                st.session_state.page = "admin"
                st.rerun()

        if st.button("Sign Out", use_container_width=True):
            _logout()

        st.divider()
        st.caption("**What you can ask:**")
        st.caption("📄 Company policies & leave rules")
        st.caption("👥 Employee data & statistics")

    # ── Chat area ─────────────────────────────────────────────
    st.title("💬 Company Assistant")

    # Render existing messages
    for idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("citations"):
                with st.expander("📎 Sources", expanded=False):
                    for src in msg["citations"]:
                        st.caption(f"• {src}")
            # Feedback buttons for assistant messages
            if msg["role"] == "assistant" and not msg.get("needs_clarification"):
                fb_col1, fb_col2, _ = st.columns([1, 1, 10])
                msg_id = msg.get("id", idx)
                if fb_col1.button("👍", key=f"up_{idx}", help="Helpful"):
                    try:
                        _api("POST", "/feedback", json={
                            "session_id": st.session_state.session_id,
                            "message_id": msg_id,
                            "rating":     1,
                        })
                    except RuntimeError:
                        pass
                if fb_col2.button("👎", key=f"down_{idx}", help="Not helpful"):
                    try:
                        _api("POST", "/feedback", json={
                            "session_id": st.session_state.session_id,
                            "message_id": msg_id,
                            "rating":     -1,
                        })
                    except RuntimeError:
                        pass

    # Chat input
    if user_input := st.chat_input("Ask about policies, employees, or anything…"):
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            # Capture metadata yielded at end of stream via a mutable dict.
            # st.write_stream() only handles the generator — we piggyback
            # metadata collection inside the same iteration.
            meta = {
                "citations":           [],
                "tool_used":           "",
                "needs_clarification": False,
                "message_id":          0,
                "error":               None,
            }

            def _token_stream():
                """
                Generator consumed by st.write_stream().
                Parses SSE events from /chat/stream:
                  {"token": "..."}        → yield the text
                  {"done": true, ...}     → capture metadata, stop
                  {"error": "..."}        → capture error, stop
                """
                try:
                    with httpx.stream(
                        "POST",
                        f"{_API_BASE}/chat/stream",
                        json={
                            "message":    user_input,
                            "session_id": st.session_state.session_id,
                        },
                        headers=_headers(),
                        timeout=60,
                    ) as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = json.loads(line[6:])

                            if "error" in payload:
                                meta["error"] = payload["error"]
                                return

                            if payload.get("done"):
                                meta["citations"]           = payload.get("citations", [])
                                meta["tool_used"]           = payload.get("tool_used", "")
                                meta["needs_clarification"] = payload.get("needs_clarification", False)
                                meta["message_id"]          = payload.get("message_id", 0)
                                return

                            if "token" in payload:
                                yield payload["token"]

                except httpx.HTTPStatusError as exc:
                    try:
                        detail = exc.response.json().get("detail", str(exc))
                    except Exception:
                        detail = str(exc)
                    meta["error"] = detail
                except httpx.RequestError as exc:
                    meta["error"] = f"Cannot reach API at {_API_BASE}. Is it running ?"

            try:
                # st.write_stream renders tokens live and returns full text
                answer = st.write_stream(_token_stream())

                if meta["error"]:
                    raise RuntimeError(meta["error"])

                citations           = meta["citations"]
                tool                = meta["tool_used"]
                needs_clarification = meta["needs_clarification"]
                message_id          = meta["message_id"]

                if needs_clarification:
                    st.info("Please clarify your question and I'll get you the right answer.")

                if citations:
                    with st.expander("📎 Sources", expanded=False):
                        for src in citations:
                            st.caption(f"• {src}")
                if tool and tool not in ("clarification", ""):
                    st.caption(f"_via {tool.upper()} search_")

                # Feedback buttons for the freshly received message
                if not needs_clarification and message_id:
                    fb_col1, fb_col2, _ = st.columns([1, 1, 10])
                    if fb_col1.button("👍", key=f"up_live_{message_id}", help="Helpful"):
                        try:
                            _api("POST", "/feedback", json={
                                "session_id": st.session_state.session_id,
                                "message_id": message_id,
                                "rating":     1,
                            })
                        except RuntimeError:
                            pass
                    if fb_col2.button("👎", key=f"down_live_{message_id}", help="Not helpful"):
                        try:
                            _api("POST", "/feedback", json={
                                "session_id": st.session_state.session_id,
                                "message_id": message_id,
                                "rating":     -1,
                            })
                        except RuntimeError:
                            pass

                st.session_state.messages.append({
                    "role":                "assistant",
                    "content":             answer,
                    "citations":           citations,
                    "needs_clarification": needs_clarification,
                    "id":                  message_id,
                })

            except RuntimeError as exc:
                err = str(exc)
                st.error(err)
                st.session_state.messages.append({
                    "role":      "assistant",
                    "content":   f"⚠️ {err}",
                    "citations": [],
                })


# ── Page: admin ───────────────────────────────────────────────

def _page_admin() -> None:
    st.markdown(_HIDE_TOOLBAR_CSS, unsafe_allow_html=True)

    with st.sidebar:
        if st.button("← Back to Chat", use_container_width=True):
            st.session_state.page = "chat"
            st.rerun()
        st.divider()
        if st.button("Sign Out", use_container_width=True):
            _logout()

    st.title("📁 Admin Panel")
    st.caption(f"Logged in as **{st.session_state.username}** (admin)")
    st.divider()

    # ── Upload ────────────────────────────────────────────────
    st.subheader("Upload Documents")
    st.caption("Supported: PDF, DOCX, TXT · Max 10 MB per file")

    uploaded = st.file_uploader(
        "Choose files",
        type=["pdf", "docx", "txt"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if uploaded:
        if st.button("📤 Index selected files", type="primary", use_container_width=True):
            for f in uploaded:
                with st.spinner(f"Processing {f.name}…"):
                    try:
                        r = httpx.post(
                            f"{_API_BASE}/ingest",
                            files={"file": (f.name, f.read(),
                                            f.type or "application/octet-stream")},
                            params={"uploaded_by": st.session_state.username},
                            headers=_headers(),
                            timeout=120,
                        )
                        r.raise_for_status()
                        result = r.json()
                        st.success(
                            f"✅ **{f.name}** — {result['chunks_indexed']} chunks indexed"
                        )
                    except Exception as exc:
                        st.error(f"❌ **{f.name}** — {exc}")

    # ── Document list ─────────────────────────────────────────
    st.divider()
    st.subheader("Indexed Documents")

    try:
        docs = _api("GET", "/documents")
        if not docs:
            st.info("No documents uploaded yet.")
        else:
            for doc in docs:
                c1, c2, c3, c4 = st.columns([4, 1, 3, 1])
                c1.write(f"📄 **{doc['filename']}**")
                c2.write(f"{doc['chunk_count']} chunks")
                c3.caption(f"by {doc['uploaded_by']}  ·  {doc['uploaded_at'][:10]}")
                if c4.button("🗑", key=f"del_doc_{doc['id']}",
                             help="Delete this document"):
                    try:
                        _api("DELETE", f"/documents/{doc['id']}")
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(str(exc))
    except RuntimeError as exc:
        st.error(f"Could not load documents: {exc}")


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="Company Knowledge Base",
        page_icon="🏢",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _init_session()

    if not st.session_state.logged_in:
        _page_login()
        return

    page = st.session_state.get("page", "chat")
    if page == "admin" and st.session_state.role == "admin":
        _page_admin()
    else:
        _page_chat()


if __name__ == "__main__":
    main()
