"""
FundzAiBot — Flask keep-alive + health/status endpoints.
Runs in a background thread. Railway hits /health for healthchecks.

Endpoints:
  GET /          — basic identity card
  GET /health    — Railway healthcheck (always 200 while process is alive)
  GET /ready     — readiness probe (200 only after bot finishes startup)
  GET /ping      — lightweight liveness probe
  GET /status    — detailed runtime stats (users, queue, usage)
"""

import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string, request

from config.settings import (
    FLASK_HOST, FLASK_PORT, BOT_NAME, BOT_VERSION, IS_RAILWAY,
    TELEGRAM_CHANNEL_URL, TELEGRAM_GROUP_URL,
)
from utils.logger import get_logger

log = get_logger(__name__)

app = Flask(__name__)
_start_time = datetime.now(timezone.utc)

# Set to True once the bot has finished its startup sequence.
# /ready will return 503 until this is set, preventing Railway from routing
# traffic to a replica that hasn't fully initialised yet.
_bot_ready: bool = False


def mark_ready() -> None:
    """Call this after the bot event loop and all background services are up."""
    global _bot_ready
    _bot_ready = True
    log.info("Bot marked as ready — /ready will now return 200.")


def _uptime() -> int:
    return int((datetime.now(timezone.utc) - _start_time).total_seconds())


@app.route("/")
def index():
    return jsonify({
        "bot":            BOT_NAME,
        "version":        BOT_VERSION,
        "status":         "running",
        "ready":          _bot_ready,
        "uptime_seconds": _uptime(),
        "endpoints": {
            "health": "/health",
            "ready":  "/ready",
            "status": "/status",
            "ping":   "/ping",
        },
    })


@app.route("/health")
def health():
    """
    Railway liveness probe — responds immediately so the container is never
    killed mid-request.  Always returns 200 while the Python process is alive.
    """
    return jsonify({"status": "ok", "uptime_seconds": _uptime()}), 200


@app.route("/ready")
def ready():
    """
    Readiness probe — returns 200 only after the bot has fully initialised.
    Railway uses this to hold traffic until the replica is ready.
    """
    if _bot_ready:
        return jsonify({"status": "ready", "uptime_seconds": _uptime()}), 200
    return jsonify({"status": "starting", "uptime_seconds": _uptime()}), 503


@app.route("/ping")
def ping():
    return "pong", 200


@app.route("/status")
def status():
    """Detailed runtime stats. Failures are swallowed so /health is unaffected."""
    user_counts: dict = {}
    totals:      dict = {}
    q:           dict = {}
    ai_health:   dict = {}

    try:
        from services.database import count_users, get_total_stats
        user_counts = count_users()
        totals      = get_total_stats()
    except Exception as exc:
        log.debug("Status: DB stats unavailable — %s", exc)

    try:
        from services.queue_manager import queue_manager
        q = queue_manager.stats()
    except Exception as exc:
        log.debug("Status: queue stats unavailable — %s", exc)

    try:
        from services.ai_service import check_provider_health
        ai_health = check_provider_health()
    except Exception as exc:
        log.debug("Status: AI health check unavailable — %s", exc)

    return jsonify({
        "bot":            BOT_NAME,
        "version":        BOT_VERSION,
        "ready":          _bot_ready,
        "uptime_seconds": _uptime(),
        "environment":    "railway" if IS_RAILWAY else "development",
        "users":          user_counts,
        "usage":          totals,
        "queue":          q,
        "ai_providers":   ai_health,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    })


# ── Premium Announcement Mini-App (Telegram Web App) ─────────────────────────

_ANNOUNCEMENT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0">
<title>FundzAiBot — Announcements</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:var(--tg-theme-bg-color,#17212b);
  color:var(--tg-theme-text-color,#e8e8e8);
  min-height:100vh;display:flex;flex-direction:column;
}

/* ── Sticky header ─────────────────────────────────────────────── */
.sticky-header{
  position:sticky;top:0;z-index:100;
  background:var(--tg-theme-secondary-bg-color,#232e3c);
  border-bottom:2px solid #5288c1;
  padding:10px 14px;
  display:flex;align-items:center;gap:8px;
  box-shadow:0 2px 8px rgba(0,0,0,0.3);
}
.sticky-header .pin{font-size:17px}
.sticky-header .title{font-size:14px;font-weight:700;color:#5288c1;letter-spacing:.3px}
.sticky-header .badge{
  margin-left:auto;background:#5288c1;color:#fff;
  border-radius:12px;padding:2px 8px;font-size:11px;font-weight:600;
}

/* ── Card ──────────────────────────────────────────────────────── */
.card{
  margin:14px;
  background:var(--tg-theme-secondary-bg-color,#232e3c);
  border-radius:14px;
  border-left:4px solid #5288c1;
  overflow:hidden;
  animation:slideIn .25s ease;
  box-shadow:0 4px 12px rgba(0,0,0,0.2);
  flex:1;
}
@keyframes slideIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}
.card-photo img{width:100%;max-height:200px;object-fit:cover;display:block}
.card-body{padding:16px}
.card-body .msg{
  font-size:14px;line-height:1.65;
  color:var(--tg-theme-text-color,#e8e8e8);
  white-space:pre-wrap;word-break:break-word;
}
.card-meta{
  display:flex;align-items:center;gap:8px;
  margin-top:10px;font-size:11px;color:rgba(255,255,255,.4);
}
.active-dot{width:7px;height:7px;border-radius:50%;background:#4CAF50;flex-shrink:0}

/* ── Nav bar ───────────────────────────────────────────────────── */
.nav-bar{
  display:flex;align-items:center;justify-content:center;
  gap:10px;padding:10px 14px;
}
.nav-btn{
  background:var(--tg-theme-button-color,#5288c1);
  color:var(--tg-theme-button-text-color,#fff);
  border:none;border-radius:8px;padding:8px 18px;
  font-size:13px;font-weight:600;cursor:pointer;
  transition:opacity .15s;min-width:72px;
}
.nav-btn:disabled{opacity:.35;cursor:default}
.nav-counter{
  font-size:13px;color:rgba(255,255,255,.55);
  min-width:50px;text-align:center;
}

/* ── Action buttons ────────────────────────────────────────────── */
.actions{display:flex;gap:8px;padding:0 14px 16px}
.action{
  flex:1;background:rgba(82,136,193,.13);
  border:1px solid rgba(82,136,193,.3);
  color:#5288c1;border-radius:10px;
  padding:10px 4px;font-size:12px;font-weight:500;
  cursor:pointer;text-align:center;text-decoration:none;
  display:flex;flex-direction:column;align-items:center;gap:3px;
  transition:background .15s;
}
.action:hover{background:rgba(82,136,193,.25)}
.action .ico{font-size:18px}

/* ── Empty / loading ───────────────────────────────────────────── */
.empty{
  flex:1;display:flex;align-items:center;justify-content:center;
  flex-direction:column;gap:8px;
  color:rgba(255,255,255,.35);padding:40px;text-align:center;font-size:14px;
}
.empty .ico{font-size:40px}
</style>
</head>
<body>
<div class="sticky-header">
  <span class="pin">📌</span>
  <span class="title">Pinned Announcements</span>
  <span class="badge" id="badge">…</span>
</div>

<div id="root" style="flex:1;display:flex;flex-direction:column">
  <div class="empty"><span class="ico">⏳</span><span>Loading…</span></div>
</div>

<div class="nav-bar">
  <button class="nav-btn" id="prevBtn" onclick="nav(-1)" disabled>◀ Prev</button>
  <span class="nav-counter" id="navC">—</span>
  <button class="nav-btn" id="nextBtn" onclick="nav(1)" disabled>Next ▶</button>
</div>

<div class="actions">
  <a class="action" href="https://t.me/Biodunfund" target="_blank">
    <span class="ico">🔧</span>Support
  </a>
  <a class="action" id="chBtn" href="#" target="_blank">
    <span class="ico">📢</span>Channel
  </a>
  <a class="action" id="grBtn" href="#" target="_blank">
    <span class="ico">👥</span>Community
  </a>
</div>

<script>
const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();

let anns=[],idx=0;

function esc(t){
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\n/g,'<br>');
}
function ago(d){
  if(!d)return'';
  const s=Math.floor((Date.now()-new Date(d))/1000);
  if(s<60)return'just now';
  if(s<3600)return Math.floor(s/60)+'m ago';
  if(s<86400)return Math.floor(s/3600)+'h ago';
  return Math.floor(s/86400)+'d ago';
}
function render(){
  const root=document.getElementById('root');
  if(!anns.length){
    root.innerHTML='<div class="empty"><span class="ico">📭</span><span>No announcements yet.</span></div>';
    document.getElementById('badge').textContent='0';
    return;
  }
  const a=anns[idx];
  const photo=a.photo_url?`<div class="card-photo"><img src="${a.photo_url}" alt="" onerror="this.parentElement.remove()"></div>`:'';
  const dot=a.is_active?'<span class="active-dot"></span>':'';
  root.innerHTML=`<div class="card">
    ${photo}
    <div class="card-body">
      <div class="msg">${esc(a.message||'')}</div>
      <div class="card-meta">${dot}<span>${ago(a.created_at)}${a.is_active?' · Active':''}</span></div>
    </div>
  </div>`;
  document.getElementById('prevBtn').disabled=idx<=0;
  document.getElementById('nextBtn').disabled=idx>=anns.length-1;
  document.getElementById('navC').textContent=(idx+1)+' / '+anns.length;
  document.getElementById('badge').textContent=anns.length;
}
function nav(d){
  const ni=idx+d;
  if(ni<0||ni>=anns.length)return;
  idx=ni;render();
}
async function load(){
  try{
    const r=await fetch('/api/announcements');
    const data=await r.json();
    anns=data.announcements||[];
    const ch=data.channel_url||'#';
    const gr=data.group_url||'#';
    document.getElementById('chBtn').href=ch;
    document.getElementById('grBtn').href=gr;
    render();
  }catch(e){
    document.getElementById('root').innerHTML=
      '<div class="empty"><span class="ico">⚠️</span><span>Could not load announcements.</span></div>';
  }
}
load();
</script>
</body>
</html>"""


@app.route("/announcement")
def announcement_webapp():
    """Telegram Web App — premium sticky announcement overlay."""
    return render_template_string(_ANNOUNCEMENT_HTML)


@app.route("/api/announcement")
def api_announcement():
    """JSON — current active announcement (used by mini-app)."""
    try:
        from services.database import get_active_announcement
        ann = get_active_announcement()
        return jsonify({"announcement": ann or {}})
    except Exception as exc:
        log.debug("API /api/announcement error: %s", exc)
        return jsonify({"announcement": {}})


@app.route("/api/announcements")
def api_announcements():
    """JSON — recent announcements list for cycling in the mini-app."""
    try:
        from services.database import get_announcement_history
        limit = min(int(request.args.get("limit", 10)), 20)
        anns  = get_announcement_history(limit=limit)
        return jsonify({
            "announcements": anns or [],
            "channel_url":   TELEGRAM_CHANNEL_URL or "https://t.me/FundzAiChannel",
            "group_url":     TELEGRAM_GROUP_URL   or "https://t.me/FundzAiGroup",
        })
    except Exception as exc:
        log.debug("API /api/announcements error: %s", exc)
        return jsonify({"announcements": [], "channel_url": "", "group_url": ""})


def run_flask() -> None:
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.ERROR)
    log.info("Flask keep-alive on %s:%s", FLASK_HOST, FLASK_PORT)
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False, use_reloader=False)


def start_keepalive() -> threading.Thread:
    t = threading.Thread(target=run_flask, name="keepalive", daemon=True)
    t.start()
    return t
