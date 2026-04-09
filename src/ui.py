"""
Streamlit web UI — four pages:
  login     : username/password form
  chat      : main chat + conversation history sidebar
  admin     : document upload & management (admin role only)
  dashboard : monitoring dashboard for admins
"""
import json
import os
import uuid
from datetime import date, timedelta

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

    /* ── Sidebar buttons ── */
    [data-testid="stSidebar"] .stButton > button {
        background: #ffffff;
        border: 1.5px solid #d0d3da;
        border-radius: 10px;
        color: #1a1a2e;
        font-weight: 600;
        font-size: 0.95rem;
        padding: 10px 16px;
        transition: all 0.18s ease;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background: #f0f2ff;
        border-color: #7c83fd;
        color: #4a50e0;
        box-shadow: 0 3px 8px rgba(124,131,253,0.2);
        transform: translateY(-1px);
    }
    /* Sign Out — subtle red tint */
    [data-testid="stSidebar"] .stButton:last-of-type > button {
        border-color: #ffc2c2;
        color: #c0392b;
    }
    [data-testid="stSidebar"] .stButton:last-of-type > button:hover {
        background: #fff0f0;
        border-color: #e74c3c;
        color: #c0392b;
        box-shadow: 0 3px 8px rgba(231,76,60,0.18);
    }
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


def _api(method: str, path: str, timeout: int = 45, **kwargs):
    url = f"{_API_BASE}{path}"
    try:
        request_kwargs = {"verify": False, "timeout": timeout}
        request_kwargs.update(kwargs)

        r = httpx.request(method, url, headers=_headers(), **request_kwargs)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = f"HTTP {exc.response.status_code}: {exc.response.text[:200] or str(exc)}"
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
            if st.button("📊 Dashboard", use_container_width=True):
                st.session_state.page = "dashboard"
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
                msg_id = msg.get("id", idx)
                _render_feedback(f"hist_{idx}", msg_id)

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
                    _render_feedback(f"live_{message_id}", message_id)

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


# ── Feedback helper ───────────────────────────────────────────

def _render_feedback(key_prefix: str, message_id: int) -> None:
    """
    Render 👍/👎 buttons. Rating is saved immediately on click.
    An optional comment input appears after rating is given (same rerun — 1 click).
    """
    rated_key  = f"fb_rated_{key_prefix}"
    rating_key = f"fb_rating_{key_prefix}"

    # Show buttons only if not yet rated
    if not st.session_state.get(rated_key):
        fb_col1, fb_col2, _ = st.columns([1, 1, 10])
        clicked_up   = fb_col1.button("👍", key=f"up_{key_prefix}",   help="Helpful")
        clicked_down = fb_col2.button("👎", key=f"down_{key_prefix}", help="Not helpful")

        if clicked_up or clicked_down:
            rating = 1 if clicked_up else -1
            try:
                _api("POST", "/feedback", json={
                    "session_id": st.session_state.session_id,
                    "message_id": message_id,
                    "rating":     rating,
                })
                st.session_state[rated_key]  = True
                st.session_state[rating_key] = rating
                st.rerun()
            except RuntimeError:
                pass

    # Separate if (not else) — runs immediately after rating is set in the same rerun
    if st.session_state.get(rated_key):
        icon = "👍" if st.session_state.get(rating_key) == 1 else "👎"
        st.caption(f"{icon} Cảm ơn phản hồi!")
        submitted_key = f"fb_comment_sent_{key_prefix}"
        if not st.session_state.get(submitted_key):
            with st.form(key=f"comment_form_{key_prefix}"):
                comment = st.text_input(
                    "Thêm ghi chú (tùy chọn)",
                    placeholder="Nhập ghi chú...",
                    label_visibility="collapsed",
                )
                col_sub, col_skip = st.columns([1, 1])
                submitted = col_sub.form_submit_button("Gửi")
                skipped   = col_skip.form_submit_button("Bỏ qua")
            if submitted and comment.strip():
                try:
                    _api("POST", "/feedback", json={
                        "session_id": st.session_state.session_id,
                        "message_id": message_id,
                        "rating":     st.session_state.get(rating_key, 1),
                        "comment":    comment.strip(),
                    })
                except RuntimeError:
                    pass
                st.session_state[submitted_key] = True
                st.rerun()
            if skipped:
                st.session_state[submitted_key] = True
                st.rerun()


# ── Page: dashboard ───────────────────────────────────────────

def _time_filter_ui(prefix: str = "dash") -> int:
    """Render time range selector; return number of days selected."""
    options = {"1 ngày": 1, "3 ngày": 3, "7 ngày": 7, "30 ngày": 30, "Tuỳ chọn 📅": -1}
    col_btns = st.columns(len(options))
    sel_key = f"{prefix}_days_label"
    if sel_key not in st.session_state:
        st.session_state[sel_key] = "7 ngày"

    for i, (label, val) in enumerate(options.items()):
        if col_btns[i].button(
            label,
            key=f"{prefix}_tf_{label}",
            type="primary" if st.session_state[sel_key] == label else "secondary",
            use_container_width=True,
        ):
            st.session_state[sel_key] = label
            st.rerun()

    selected = st.session_state[sel_key]
    if selected == "Tuỳ chọn 📅":
        col_from, col_to = st.columns(2)
        from_date = col_from.date_input("Từ ngày", value=date.today() - timedelta(days=7),
                                         key=f"{prefix}_from")
        to_date   = col_to.date_input("Đến ngày", value=date.today(), key=f"{prefix}_to")
        return max(1, (to_date - from_date).days + 1)

    return options.get(selected, 7)


_DASHBOARD_TAB_CSS = """
<style>
/* ── Tab bar container ── */
[data-baseweb="tab-list"] {
    background: #f7f8fc !important;
    border-radius: 12px !important;
    padding: 4px 6px !important;
    gap: 4px !important;
    border: 1px solid #e4e6ef !important;
}

/* ── Individual tab ── */
button[data-baseweb="tab"] {
    font-size: 1rem !important;
    font-weight: 600 !important;
    padding: 10px 22px !important;
    border-radius: 9px !important;
    color: #555c7a !important;
    background: transparent !important;
    border: none !important;
    transition: all 0.18s ease !important;
}
button[data-baseweb="tab"]:hover {
    background: #eceeff !important;
    color: #4a50e0 !important;
}

/* ── Active tab ── */
button[data-baseweb="tab"][aria-selected="true"] {
    background: #ffffff !important;
    color: #ff4b4b !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.10) !important;
    border-bottom: none !important;
}

/* Hide the default sliding underline */
[data-baseweb="tab-highlight"] {
    display: none !important;
}
[data-baseweb="tab-border"] {
    display: none !important;
}
</style>
"""


def _page_dashboard() -> None:
    st.markdown(_HIDE_TOOLBAR_CSS, unsafe_allow_html=True)
    st.markdown(_DASHBOARD_TAB_CSS, unsafe_allow_html=True)

    with st.sidebar:
        if st.button("← Quay lại Chat", use_container_width=True):
            st.session_state.page = "chat"
            st.rerun()
        if st.button("📁 Admin Panel", use_container_width=True):
            st.session_state.page = "admin"
            st.rerun()
        st.divider()
        if st.button("Sign Out", use_container_width=True):
            _logout()

    st.title("📊 Monitoring Dashboard")

    # ── Time filter ───────────────────────────────────────────
    days = _time_filter_ui("dash")
    st.divider()

    # ── Fetch dashboard data ──────────────────────────────────
    try:
        data = _api("GET", "/monitoring/dashboard", params={"days": days})
    except RuntimeError as exc:
        st.error(f"Không thể tải dữ liệu dashboard: {exc}")
        return

    kpis           = data.get("kpis", {})
    latency_trend  = data.get("latency_trend", [])
    tool_breakdown = data.get("tool_breakdown", {})
    judge          = data.get("judge_summary", {})
    fb_by_tool     = data.get("feedback_by_tool", {})
    ragas          = data.get("ragas_cache") or {}

    # ── 4 Tabs ────────────────────────────────────────────────
    tab_perf, tab_quality, tab_feedback, tab_logs = st.tabs(
        ["⚡ Performance", "🎯 Quality", "👍 Feedback", "📋 Logs"]
    )

    # ════════════════════════════════════════════════════════
    # Tab 1 — Performance
    # ════════════════════════════════════════════════════════
    with tab_perf:
        c1, c2, c3 = st.columns(3)
        with c1:
            avg_lat = kpis.get("avg_latency_ms", 0)
            st.metric("Avg Latency", f"{avg_lat:,} ms",
                      help="Thời gian phản hồi trung bình từ lúc gửi câu hỏi đến khi nhận câu trả lời")
            st.caption(f"Tổng: **{kpis.get('total_requests', 0):,}** requests")
        with c2:
            avg_in  = kpis.get("avg_input_tokens", 0)
            avg_out = kpis.get("avg_output_tokens", 0)
            st.metric("Avg Tokens / request", f"{avg_in} in / {avg_out} out",
                      help="Số token trung bình mỗi request (Input = ngữ cảnh + câu hỏi; Output = câu trả lời được sinh ra)")
        with c3:
            avg_usd = kpis.get("avg_cost_usd", 0)
            avg_vnd = kpis.get("avg_cost_vnd", 0)
            total_usd = kpis.get("total_cost_usd", 0)
            total_vnd = kpis.get("total_cost_vnd", 0)
            st.metric(
                "Avg Cost / request ⓘ",
                f"${avg_usd:.5f}",
                help="Chi phí ước tính mỗi request, tính từ tokens × giá model Haiku/Sonnet. "
                     "Bao gồm: synthesis (Sonnet) + routing + judge (Haiku).",
            )
            st.caption(f"≈ {avg_vnd:,.0f} VND  |  Tổng: ${total_usd:.4f} ≈ {total_vnd:,.0f} VND")

        st.divider()

        if latency_trend:
            import pandas as pd
            df_lat = pd.DataFrame(latency_trend).set_index("date")
            st.subheader("Latency theo ngày (ms)")
            st.line_chart(df_lat[["avg_ms"]], height=220)
        else:
            st.info("Chưa có đủ dữ liệu latency — gửi một vài tin nhắn trước.")

        st.divider()
        rag_cnt = tool_breakdown.get("rag", 0)
        sql_cnt = tool_breakdown.get("sql", 0)
        total_tool = rag_cnt + sql_cnt or 1
        st.subheader("Tool Usage")
        col_r, col_s = st.columns(2)
        col_r.metric("RAG", f"{rag_cnt:,}", f"{rag_cnt/total_tool*100:.0f}%")
        col_s.metric("SQL", f"{sql_cnt:,}", f"{sql_cnt/total_tool*100:.0f}%")

    # ════════════════════════════════════════════════════════
    # Tab 2 — Quality
    # ════════════════════════════════════════════════════════
    with tab_quality:
        q1, q2, q3 = st.columns(3)
        avg_help = judge.get("avg_helpfulness", 0.0)
        avg_fact = judge.get("avg_factual", 0.0)
        hall_pct = judge.get("hallucination_pct", 0.0)
        q1.metric("Helpfulness", f"{avg_help:.1f} / 5",
                  help="Điểm mức độ hữu ích (1-5) do LLM judge chấm sau mỗi câu trả lời")
        q2.metric("Factual Score", f"{avg_fact:.1f} / 5",
                  help="Điểm độ chính xác thực tế (1-5) do LLM judge chấm")
        q3.metric("Hallucination", f"{hall_pct:.1f}% / 100%",
                  delta=None if hall_pct == 0 else f"{judge.get('flagged_count',0)} flagged",
                  delta_color="inverse",
                  help="Tỷ lệ câu trả lời bị phát hiện có sự ảo giác thông tin (bịa đặt)")

        st.divider()
        st.subheader("RAGAS Evaluation")

        if ragas:
            cached_at = ragas.get("_cached_at", "")[:16].replace("T", " ")
            col_info, col_btn = st.columns([3, 1])
            col_info.caption(f"Last run: {cached_at} (tự động mỗi 6 tiếng)")
            if col_btn.button("▶ Run Evaluation", type="primary"):
                with st.spinner("Đang chạy RAGAS evaluation..."):
                    try:
                        result = _api("GET", "/evaluate", timeout=120, params={"limit": 20})
                        st.success("Evaluation hoàn thành!")
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(str(exc))

            faith = ragas.get("faithfulness", 0)
            relev = ragas.get("answer_relevancy", 0)

            for label, score in [
                ("Faithfulness", faith),
                ("Answer Relevancy", relev),
            ]:
                col_lbl, col_score, col_bar = st.columns([2, 1, 4])
                col_lbl.write(label)
                col_score.write(f"**{score:.2f} / 1**")
                col_bar.progress(float(score))
        else:
            st.info("Chưa có kết quả RAGAS. Bấm nút bên dưới để chạy lần đầu.")
            if st.button("▶ Run Evaluation Now", type="primary"):
                with st.spinner("Đang chạy RAGAS evaluation..."):
                    try:
                        _api("GET", "/evaluate", timeout=120, params={"limit": 20})
                        st.success("Hoàn thành! Dashboard sẽ cập nhật tự động.")
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(str(exc))

        flagged = judge.get("flagged_responses", [])
        if flagged:
            st.divider()
            st.warning(f"⚠ {judge.get('flagged_count', 0)} responses bị flag hallucination")
            rows = [{"Msg ID":   r["message_id"],
                     "Câu hỏi":  (r.get("question") or "—")[:80],
                     "Lý do":    (r.get("judge_rationale") or "—")[:120]}
                    for r in flagged[:10]]
            import pandas as pd
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ════════════════════════════════════════════════════════
    # Tab 3 — Feedback
    # ════════════════════════════════════════════════════════
    with tab_feedback:
        pos_pct = kpis.get("positive_feedback_pct", 0.0)
        total_fb = kpis.get("total_feedback", 0)
        pos_count = round(pos_pct / 100 * total_fb)
        neg_count = total_fb - pos_count

        fb1, fb2, fb3 = st.columns(3)
        fb1.metric("Positive Rate", f"{pos_pct:.1f}%")
        fb2.metric("👍 Like", f"{pos_count:,}")
        fb3.metric("👎 Dislike", f"{neg_count:,}")
        st.progress(pos_pct / 100)

        st.divider()
        st.subheader("Breakdown by Tool")
        for tool_name in ("rag", "sql"):
            info = fb_by_tool.get(tool_name, {})
            pct  = info.get("positive_pct", 0.0)
            tot  = info.get("total", 0)
            col_t, col_p, col_bar = st.columns([1, 1, 4])
            col_t.write(tool_name.upper())
            col_p.write(f"**{pct:.0f}%** ({tot})")
            col_bar.progress(pct / 100)

        st.divider()
        st.subheader("Recent Feedback")

        # Separate time filter for feedback table
        fb_days = _time_filter_ui("fb")

        try:
            feedback_data = _api("GET", "/monitoring/feedback_comments",
                                 params={"days": fb_days})
        except RuntimeError:
            feedback_data = []

        if feedback_data:
            import pandas as pd
            rows_display = []
            for r in feedback_data:
                rows_display.append({
                    "Time": str(r.get("created_at", ""))[:16].replace("T", " "),
                    "Câu hỏi": (r.get("question") or "")[:60],
                    "Rating": "👍" if r.get("rating") == 1 else "👎",
                    "Comment": r.get("comment") or "—",
                })
            st.dataframe(pd.DataFrame(rows_display), use_container_width=True, hide_index=True)
        else:
            st.info("Chưa có feedback trong khoảng thời gian này.")

    # ════════════════════════════════════════════════════════
    # Tab 4 — Logs
    # ════════════════════════════════════════════════════════
    with tab_logs:
        log_col1, log_col2 = st.columns(2)
        tool_filter   = log_col1.selectbox("Tool", ["Tất cả", "RAG", "SQL"],
                                            key="log_tool_filter")
        rating_filter = log_col2.selectbox("Rating", ["Tất cả", "👍 Like", "👎 Dislike"],
                                            key="log_rating_filter")

        log_days = _time_filter_ui("logs")

        tool_param   = None if tool_filter == "Tất cả" else tool_filter.lower()
        rating_param = None
        if rating_filter == "👍 Like":
            rating_param = 1
        elif rating_filter == "👎 Dislike":
            rating_param = -1

        try:
            params: dict = {"days": log_days}
            if tool_param:
                params["tool"] = tool_param
            if rating_param is not None:
                params["rating"] = rating_param
            logs = _api("GET", "/monitoring/logs", params=params)
        except RuntimeError as exc:
            st.error(str(exc))
            logs = []

        if logs:
            import pandas as pd
            rows_log = []
            for r in logs:
                lat = r.get("response_time_ms")
                lat_str = f"{lat:,} ms" if lat else "—"
                help_score = r.get("helpfulness")
                fact_score = r.get("factual_score")
                judge_str = f"{help_score}/5" if help_score else "—"
                rat = r.get("rating")
                fb_str = "👍" if rat == 1 else ("👎" if rat == -1 else "—")
                rows_log.append({
                    "Time":    str(r.get("created_at", ""))[:16].replace("T", " "),
                    "Tool":    (r.get("tool_used") or "").upper(),
                    "Câu hỏi": (r.get("question") or "")[:60],
                    "Latency": lat_str,
                    "Judge":   judge_str,
                    "Feedback": fb_str,
                })
            st.dataframe(pd.DataFrame(rows_log), use_container_width=True, hide_index=True)
        else:
            st.info("Không có dữ liệu trong khoảng thời gian này.")


# ── Page: admin ───────────────────────────────────────────────

def _page_admin() -> None:
    st.markdown(_HIDE_TOOLBAR_CSS, unsafe_allow_html=True)

    with st.sidebar:
        if st.button("← Quay lại Chat", use_container_width=True):
            st.session_state.page = "chat"
            st.rerun()
        if st.button("📊 Dashboard", use_container_width=True):
            st.session_state.page = "dashboard"
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
    elif page == "dashboard" and st.session_state.role == "admin":
        _page_dashboard()
    else:
        _page_chat()


if __name__ == "__main__":
    main()
