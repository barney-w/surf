"""Dev-only admin dashboard for browsing conversation history.

This module is conditionally imported in main.py only when
``settings.environment == "dev"``.  It provides a self-contained HTML
dashboard and supporting JSON API endpoints for inspecting conversations,
messages, and feedback stored in PostgreSQL.
"""

import contextlib
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _get_pool(request: Request):  # noqa: ANN202
    """Retrieve the asyncpg pool from the conversation service."""
    svc = request.app.state.conversation_service
    return svc._get_pool()  # noqa: SLF001


def _serialise_row(row: dict) -> dict:  # noqa: ANN001
    """Convert asyncpg Record values to JSON-safe types."""
    out: dict = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif hasattr(v, "__str__") and type(v).__name__ == "UUID":
            out[k] = str(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# JSON API endpoints
# ---------------------------------------------------------------------------


@router.get("/api/stats")
async def admin_stats(request: Request) -> JSONResponse:
    """Aggregate statistics across all conversations."""
    pool = _get_pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM conversations) AS total_conversations,
                (SELECT COUNT(*) FROM conversations
                 WHERE created_at > now() - interval '24 hours') AS conversations_today,
                (SELECT COUNT(*) FROM messages) AS total_messages,
                (SELECT COUNT(*) FROM feedback) AS total_feedback
            """
        )
        avg = await conn.fetchval(
            """
            SELECT COALESCE(AVG(cnt), 0)
            FROM (
                SELECT COUNT(*) AS cnt
                FROM messages
                GROUP BY conversation_id
            ) sub
            """
        )

    return JSONResponse(
        {
            "total_conversations": row["total_conversations"],
            "conversations_today": row["conversations_today"],
            "total_messages": row["total_messages"],
            "total_feedback": row["total_feedback"],
            "avg_messages_per_conversation": round(float(avg), 1),
        }
    )


@router.get("/api/conversations")
async def list_conversations(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    user_id: str | None = None,
    agent: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> JSONResponse:
    """Paginated conversation list with optional filters."""
    pool = _get_pool(request)
    conditions: list[str] = []
    params: list[object] = []
    idx = 1

    if user_id:
        conditions.append(f"c.user_id = ${idx}")
        params.append(user_id)
        idx += 1
    if agent:
        conditions.append(f"c.last_active_agent = ${idx}")
        params.append(agent)
        idx += 1
    if date_from:
        try:
            params.append(datetime.fromisoformat(date_from).replace(tzinfo=UTC))
        except ValueError:
            raise HTTPException(400, "Invalid date_from format")
        conditions.append(f"c.created_at >= ${idx}")
        idx += 1
    if date_to:
        try:
            params.append(datetime.fromisoformat(date_to).replace(tzinfo=UTC))
        except ValueError:
            raise HTTPException(400, "Invalid date_to format")
        conditions.append(f"c.created_at <= ${idx}")
        idx += 1

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    limit = per_page
    offset = (page - 1) * per_page

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM conversations c {where}",  # noqa: S608  # nosec B608
            *params,
        )
        rows = await conn.fetch(
            f"""
            SELECT c.id, c.user_id, c.created_at, c.updated_at,
                   c.last_active_agent,
                   COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            {where}
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,  # noqa: S608  # nosec B608
            *params,
            limit,
            offset,
        )

    return JSONResponse(
        {
            "conversations": [_serialise_row(dict(r)) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    )


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(request: Request, conversation_id: str) -> JSONResponse:
    """Full conversation detail including messages and feedback."""
    pool = _get_pool(request)
    async with pool.acquire() as conn:
        conv = await conn.fetchrow(
            "SELECT id, user_id, created_at, updated_at, last_active_agent "
            "FROM conversations WHERE id = $1",
            conversation_id,
        )
        if conv is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        messages = await conn.fetch(
            "SELECT id, role, content, agent, response, attachments, "
            "timestamp, ordinal "
            "FROM messages WHERE conversation_id = $1 ORDER BY ordinal",
            conversation_id,
        )
        feedback = await conn.fetch(
            "SELECT message_id, rating, comment FROM feedback WHERE conversation_id = $1",
            conversation_id,
        )

    msg_list = []
    for m in messages:
        row = _serialise_row(dict(m))
        # Parse JSON columns
        if row.get("response"):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                row["response"] = (
                    json.loads(row["response"])
                    if isinstance(row["response"], str)
                    else row["response"]
                )
        if row.get("attachments"):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                row["attachments"] = (
                    json.loads(row["attachments"])
                    if isinstance(row["attachments"], str)
                    else row["attachments"]
                )
        msg_list.append(row)

    feedback_map: dict[str, list[dict]] = {}
    for f in feedback:
        mid = str(f["message_id"])
        feedback_map.setdefault(mid, []).append({"rating": f["rating"], "comment": f["comment"]})

    return JSONResponse(
        {
            "conversation": _serialise_row(dict(conv)),
            "messages": msg_list,
            "feedback": feedback_map,
        }
    )


# ---------------------------------------------------------------------------
# HTML admin dashboard
# ---------------------------------------------------------------------------

_ADMIN_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Surf Admin &mdash; Conversations</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
:root {
  --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
  --border: #475569; --text: #e2e8f0; --muted: #94a3b8;
  --accent: #38bdf8; --accent2: #818cf8; --positive: #4ade80;
  --negative: #f87171; --user-badge: #7c3aed; --assistant-badge: #0ea5e9;
}
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg); color: var(--text); height: 100vh; overflow: hidden; }
a { color: var(--accent); text-decoration: none; }

/* Layout */
.app { display: flex; height: 100vh; }
.sidebar { width: 380px; min-width: 380px; background: var(--surface);
  border-right: 1px solid var(--border); display: flex; flex-direction: column; }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

/* Header */
.header { padding: 16px 20px; border-bottom: 1px solid var(--border); }
.header h1 { font-size: 18px; font-weight: 600; color: var(--accent); }
.header p { font-size: 12px; color: var(--muted); margin-top: 2px; }

/* Stats */
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;
  padding: 12px 16px; border-bottom: 1px solid var(--border); }
.stat { background: var(--surface2); border-radius: 6px; padding: 8px 10px; text-align: center; }
.stat .val { font-size: 20px; font-weight: 700; color: var(--accent); }
.stat .lbl { font-size: 10px; color: var(--muted);
  text-transform: uppercase; letter-spacing: .5px; }

/* Filters */
.filters { padding: 10px 16px; border-bottom: 1px solid var(--border);
  display: flex; gap: 6px; flex-wrap: wrap; }
.filters input, .filters select { background: var(--surface2); border: 1px solid var(--border);
  color: var(--text); border-radius: 4px; padding: 5px 8px; font-size: 12px; }
.filters input { flex: 1; min-width: 100px; }
.filters select { min-width: 80px; }
.filters input[type="date"] { min-width: 120px; flex: unset; }
.filters button { background: var(--accent); color: var(--bg); border: none;
  border-radius: 4px; padding: 5px 12px; font-size: 12px; cursor: pointer; font-weight: 600; }
.filters button:hover { opacity: .85; }

/* Conversation list */
.conv-list { flex: 1; overflow-y: auto; }
.conv-item { padding: 10px 16px; border-bottom: 1px solid var(--border);
  cursor: pointer; transition: background .15s; }
.conv-item:hover { background: var(--surface2); }
.conv-item.active { background: var(--surface2); border-left: 3px solid var(--accent); }
.conv-item .top { display: flex; justify-content: space-between; align-items: center; }
.conv-item .uid { font-size: 12px; font-family: monospace; color: var(--accent); }
.conv-item .time { font-size: 11px; color: var(--muted); }
.conv-item .meta { display: flex; gap: 8px; margin-top: 4px; font-size: 11px; color: var(--muted); }
.conv-item .agent-tag { background: var(--accent2); color: #fff; padding: 1px 6px;
  border-radius: 3px; font-size: 10px; }

/* Pagination */
.pagination { padding: 10px 16px; border-top: 1px solid var(--border);
  display: flex; justify-content: space-between;
  align-items: center; font-size: 12px; color: var(--muted); }
.pagination button { background: var(--surface2); color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 12px; }
.pagination button:disabled { opacity: .4; cursor: not-allowed; }

/* Detail panel */
.detail { flex: 1; overflow-y: auto; padding: 20px 24px; }
.detail-empty { display: flex; align-items: center; justify-content: center;
  height: 100%; color: var(--muted); font-size: 14px; }
.detail-header { margin-bottom: 16px; padding-bottom: 12px;
  border-bottom: 1px solid var(--border); }
.detail-header h2 { font-size: 14px; color: var(--accent); font-family: monospace; }
.detail-header .info { font-size: 12px; color: var(--muted); margin-top: 4px; }

/* Messages */
.msg { margin-bottom: 12px; padding: 10px 14px; border-radius: 8px;
  background: var(--surface); border: 1px solid var(--border); }
.msg.user { border-left: 3px solid var(--user-badge); }
.msg.assistant { border-left: 3px solid var(--assistant-badge); }
.msg-header { display: flex; justify-content: space-between;
  align-items: center; margin-bottom: 6px; }
.role-badge { font-size: 10px; font-weight: 700; text-transform: uppercase; padding: 2px 8px;
  border-radius: 3px; }
.role-badge.user { background: var(--user-badge); color: #fff; }
.role-badge.assistant { background: var(--assistant-badge); color: #fff; }
.msg-agent { font-size: 11px; color: var(--accent2); margin-left: 6px; }
.msg-time { font-size: 11px; color: var(--muted); }
.msg-content { font-size: 13px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.msg-feedback { margin-top: 6px; display: flex; gap: 6px; }
.fb-badge { font-size: 10px; padding: 2px 8px; border-radius: 3px; }
.fb-badge.positive { background: var(--positive); color: var(--bg); }
.fb-badge.negative { background: var(--negative); color: #fff; }
.msg-attachments { margin-top: 6px; font-size: 11px; color: var(--muted); }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>
<div class="app">
  <div class="sidebar">
    <div class="header">
      <h1>Surf Admin</h1>
      <p>Conversation browser &mdash; dev only</p>
    </div>
    <div class="stats" id="stats">
      <div class="stat"><div class="val" id="s-total">-</div>
        <div class="lbl">Conversations</div></div>
      <div class="stat"><div class="val" id="s-today">-</div><div class="lbl">Today</div></div>
      <div class="stat"><div class="val" id="s-msgs">-</div><div class="lbl">Messages</div></div>
      <div class="stat"><div class="val" id="s-fb">-</div><div class="lbl">Feedback</div></div>
      <div class="stat"><div class="val" id="s-avg">-</div><div class="lbl">Avg msgs</div></div>
      <div class="stat"><div class="val" id="s-agents">-</div><div class="lbl">Agents</div></div>
    </div>
    <div class="filters">
      <input type="text" id="f-user" placeholder="User ID">
      <select id="f-agent"><option value="">All agents</option></select>
      <input type="date" id="f-from">
      <input type="date" id="f-to">
      <button onclick="applyFilters()">Filter</button>
    </div>
    <div class="conv-list" id="conv-list"></div>
    <div class="pagination" id="pagination"></div>
  </div>
  <div class="main">
    <div class="detail" id="detail">
      <div class="detail-empty">Select a conversation to view messages</div>
    </div>
  </div>
</div>
<script>
const BASE = '/api/v1/admin/api';
let currentPage = 1, perPage = 20, totalCount = 0;
let activeId = null;

function relTime(iso) {
  const d = new Date(iso), now = new Date(), s = Math.floor((now - d) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}

async function loadStats() {
  try {
    const r = await fetch(BASE + '/stats');
    const d = await r.json();
    document.getElementById('s-total').textContent = d.total_conversations;
    document.getElementById('s-today').textContent = d.conversations_today;
    document.getElementById('s-msgs').textContent = d.total_messages;
    document.getElementById('s-fb').textContent = d.total_feedback;
    document.getElementById('s-avg').textContent = d.avg_messages_per_conversation;
    document.getElementById('s-agents').textContent = '-';
  } catch(e) { console.error('Stats error:', e); }
}

async function loadConversations() {
  const params = new URLSearchParams({page: currentPage, per_page: perPage});
  const uid = document.getElementById('f-user').value;
  const agent = document.getElementById('f-agent').value;
  const from = document.getElementById('f-from').value;
  const to = document.getElementById('f-to').value;
  if (uid) params.set('user_id', uid);
  if (agent) params.set('agent', agent);
  if (from) params.set('date_from', from + 'T00:00:00Z');
  if (to) params.set('date_to', to + 'T23:59:59Z');

  try {
    const r = await fetch(BASE + '/conversations?' + params);
    const d = await r.json();
    totalCount = d.total;
    const list = document.getElementById('conv-list');

    // Collect unique agents for the filter dropdown
    const agentSel = document.getElementById('f-agent');
    const agents = new Set();
    d.conversations.forEach(c => { if (c.last_active_agent) agents.add(c.last_active_agent); });
    const cur = agentSel.value;
    while (agentSel.options.length > 1) agentSel.remove(1);
    agents.forEach(a => { const o = document.createElement('option');
      o.value = a; o.textContent = a; agentSel.add(o); });
    agentSel.value = cur;

    if (!d.conversations.length) {
      list.innerHTML = '<div style="padding:20px;text-align:center;' +
        'color:var(--muted)">No conversations found</div>';
      renderPagination();
      return;
    }

    list.innerHTML = d.conversations.map(c => {
      const uid = c.user_id ? c.user_id.substring(0, 12) : 'unknown';
      const agentTag = c.last_active_agent
        ? '<span class="agent-tag">' + esc(c.last_active_agent) + '</span>' : '';
      return '<div class="conv-item' + (c.id === activeId ? ' active' : '') +
        '" data-id="' + c.id + '" onclick="selectConv(\\'' + c.id + '\\')">' +
        '<div class="top"><span class="uid">' + esc(uid) + '&hellip;</span>' +
        '<span class="time">' + relTime(c.updated_at) + '</span></div>' +
        '<div class="meta">' + agentTag +
        '<span>' + c.message_count + ' msgs</span></div></div>';
    }).join('');

    renderPagination();
  } catch(e) { console.error('List error:', e); }
}

function renderPagination() {
  const pages = Math.ceil(totalCount / perPage) || 1;
  document.getElementById('pagination').innerHTML =
    '<button ' + (currentPage <= 1 ? 'disabled' : '') +
    ' onclick="currentPage--;loadConversations()">Prev</button>' +
    '<span>Page ' + currentPage + ' of ' + pages + ' (' + totalCount + ' total)</span>' +
    '<button ' + (currentPage >= pages ? 'disabled' : '') +
    ' onclick="currentPage++;loadConversations()">Next</button>';
}

async function selectConv(id) {
  activeId = id;
  document.querySelectorAll('.conv-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === id);
  });

  try {
    const r = await fetch(BASE + '/conversations/' + id);
    const d = await r.json();
    const c = d.conversation;
    const detail = document.getElementById('detail');

    let html = '<div class="detail-header"><h2>' + esc(c.id) + '</h2>' +
      '<div class="info">User: ' + esc(c.user_id) +
      ' &bull; Created: ' + new Date(c.created_at).toLocaleString() +
      ' &bull; Agent: ' + esc(c.last_active_agent || 'none') + '</div></div>';

    if (!d.messages.length) {
      html += '<div style="color:var(--muted);padding:20px;text-align:center">No messages</div>';
    }

    d.messages.forEach(m => {
      const role = m.role || 'assistant';
      const fb = d.feedback[m.id] || [];
      let fbHtml = '';
      if (fb.length) {
        fbHtml = '<div class="msg-feedback">' + fb.map(f =>
          '<span class="fb-badge ' + f.rating + '">' + f.rating +
          (f.comment ? ': ' + esc(f.comment) : '') + '</span>'
        ).join('') + '</div>';
      }
      let attachHtml = '';
      const att = m.attachments;
      if (att && Array.isArray(att) && att.length) {
        attachHtml = '<div class="msg-attachments">Attachments: ' +
          att.map(a => esc(a.filename || a)).join(', ') + '</div>';
      }
      html += '<div class="msg ' + role + '">' +
        '<div class="msg-header"><div>' +
        '<span class="role-badge ' + role + '">' + role + '</span>' +
        (m.agent ? '<span class="msg-agent">' + esc(m.agent) + '</span>' : '') +
        '</div><span class="msg-time">' +
        (m.timestamp ? new Date(m.timestamp).toLocaleString() : '') + '</span></div>' +
        '<div class="msg-content">' + esc(m.content || '') + '</div>' +
        fbHtml + attachHtml + '</div>';
    });

    detail.innerHTML = html;
  } catch(e) { console.error('Detail error:', e); }
}

function applyFilters() { currentPage = 1; loadConversations(); }

// Initial load
loadStats();
loadConversations();
</script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    """Serve the self-contained admin dashboard HTML page."""
    return HTMLResponse(content=_ADMIN_HTML)
