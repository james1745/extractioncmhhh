import os, re, json, asyncio, threading
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template_string, request, redirect, session

# ── Cloud-friendly paths (use /data volume on Railway, local otherwise) ─────────
DATA_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '.')
os.makedirs(DATA_DIR, exist_ok=True)

# ── Background event loop for Telethon ─────────────────────────────────────────
_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()

def run_async(coro, timeout=120):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout=timeout)

# ── Config ──────────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
_config = {}

def load_config():
    global _config
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            _config = json.load(f)

def save_config():
    with open(CONFIG_FILE, 'w') as f:
        json.dump(_config, f, indent=2)

load_config()

# ── Telegram ────────────────────────────────────────────────────────────────────
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

_client       = None
_code_hash    = None
_domain_data  = []
_last_fetch   = None
_tg_username  = None

async def get_client():
    global _client
    if _client is None:
        aid  = _config.get('api_id')
        ahsh = _config.get('api_hash')
        if not aid or not ahsh:
            return None
        session_path = os.path.join(DATA_DIR, 'telegram_session')
        _client = TelegramClient(session_path, int(aid), ahsh)
        await _client.connect()
    return _client

async def is_authed():
    c = await get_client()
    return c is not None and await c.is_user_authorized()

async def send_code(phone):
    global _code_hash
    c = await get_client()
    result = await c.send_code_request(phone)
    _code_hash = result.phone_code_hash
    _config['phone'] = phone
    save_config()

async def sign_in_code(phone, code):
    c = await get_client()
    await c.sign_in(phone, code, phone_code_hash=_code_hash)

async def sign_in_2fa(password):
    c = await get_client()
    await c.sign_in(password=password)

async def get_me_name():
    global _tg_username
    c = await get_client()
    me = await c.get_me()
    _tg_username = f"@{me.username}" if me.username else me.first_name
    return _tg_username

async def get_groups():
    c = await get_client()
    dialogs = await c.get_dialogs()
    return [
        {'id': str(d.id), 'name': d.name or d.title or 'Unknown'}
        for d in dialogs if d.is_group or d.is_channel
    ]

async def fetch_group_messages(group_id, limit=300):
    global _domain_data, _last_fetch
    c = await get_client()
    entity   = await c.get_entity(int(group_id))
    messages = await c.get_messages(entity, limit=int(limit))
    results  = []
    for msg in messages:
        if msg.text:
            parsed = parse_message(msg.text, msg.date)
            if parsed:
                results.append(parsed)
    _domain_data = results
    _last_fetch  = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    return results

# ── Message parser ──────────────────────────────────────────────────────────────
DOMAIN_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}$')

def parse_message(text, date=None):
    lines   = [l.strip() for l in text.strip().split('\n') if l.strip()]
    in_val  = out_val = domain = None
    for line in lines:
        m_in  = re.search(r'(\d+)\s*\(IN\)',  line)
        m_out = re.search(r'(\d+)\s*\(OUT\)', line)
        if m_in:
            in_val  = int(m_in.group(1))
        if m_out:
            out_val = int(m_out.group(1))
        if DOMAIN_RE.match(line):
            domain = line.lower()
    if domain and (in_val is not None or out_val is not None):
        return {
            'domain': domain,
            'in':     in_val,
            'out':    out_val,
            'date':   date.strftime('%Y-%m-%d %H:%M') if date else '—',
        }
    return None

# ── Flask ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.urandom(32)

# ── HTML Templates ──────────────────────────────────────────────────────────────
BASE_STYLE = """
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#07070f;--s1:#0d0d1c;--s2:#111120;--border:#1c1c30;--text:#d8d8f0;--muted:#6060
90;--accent:#64ffda;--pink:#ff6b9d;--in:#64ffda;--out:#ff6b9d;--warn:#ffd166;}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'Syne',sans-serif;min-height:100vh;}
input,select{background:var(--s2);border:1px solid var(--border);color:var(--text);
  font-family:'Syne',sans-serif;font-size:.9rem;padding:.6rem .9rem;border-radius:6px;outline:none;
  transition:border .2s;}
input:focus,select:focus{border-color:var(--accent);}
button{cursor:pointer;font-family:'Syne',sans-serif;font-weight:600;border:none;
  border-radius:6px;padding:.65rem 1.4rem;transition:all .2s;}
.btn-primary{background:var(--accent);color:#07070f;}
.btn-primary:hover{opacity:.85;}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text);}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent);}
a{color:var(--accent);text-decoration:none;}
a:hover{opacity:.8;}
.card{background:var(--s1);border:1px solid var(--border);border-radius:10px;padding:1.4rem;}
.mono{font-family:'JetBrains Mono',monospace;}
</style>
"""

AUTH_SHELL = BASE_STYLE + """
<style>
.auth-wrap{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:1rem;}
.auth-box{width:100%;max-width:420px;}
.auth-logo{font-size:1.1rem;font-weight:800;color:var(--accent);margin-bottom:2rem;
  letter-spacing:.05em;}
.auth-box h1{font-size:1.8rem;font-weight:800;margin-bottom:.4rem;}
.auth-box p{color:var(--muted);margin-bottom:1.8rem;font-size:.92rem;line-height:1.6;}
.form-group{margin-bottom:1rem;}
.form-group label{display:block;font-size:.8rem;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:.4rem;}
.form-group input{width:100%;}
.form-hint{font-size:.8rem;color:var(--muted);margin-top:.4rem;}
.auth-box button[type=submit]{width:100%;margin-top:.5rem;padding:.8rem;}
.error{color:var(--pink);font-size:.85rem;margin-bottom:.8rem;padding:.6rem .8rem;
  background:rgba(255,107,157,.08);border-radius:6px;border:1px solid rgba(255,107,157,.2);}
</style>
<div class="auth-wrap"><div class="auth-box card">
<div class="auth-logo">◈ DOMAIN TRACKER</div>
"""

DASHBOARD_HTML = BASE_STYLE + """
<style>
header{display:flex;align-items:center;justify-content:space-between;padding:1rem 2rem;
  border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--bg);z-index:10;}
.logo{font-size:1rem;font-weight:800;color:var(--accent);letter-spacing:.06em;}
.hdr-right{display:flex;align-items:center;gap:1rem;font-size:.85rem;color:var(--muted);}
.dot{width:7px;height:7px;border-radius:50%;background:var(--accent);display:inline-block;
  margin-right:.4rem;box-shadow:0 0 8px var(--accent);}
main{padding:1.5rem 2rem;max-width:1400px;margin:0 auto;}

/* Controls */
.controls{display:flex;align-items:center;gap:.8rem;flex-wrap:wrap;margin-bottom:1.5rem;}
.controls select{flex:1;min-width:220px;max-width:340px;}
.controls input[type=number]{width:90px;}
.search-wrap{flex:1;min-width:180px;}
.search-wrap input{width:100%;}
#fetchBtn{min-width:110px;}
#fetchBtn.loading{opacity:.6;pointer-events:none;}

/* Stats */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
  gap:1rem;margin-bottom:1.5rem;}
.stat{background:var(--s1);border:1px solid var(--border);border-radius:8px;
  padding:1rem 1.2rem;}
.stat-label{font-size:.75rem;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);margin-bottom:.3rem;}
.stat-val{font-size:1.5rem;font-weight:700;font-family:'JetBrains Mono',monospace;}
.c-accent{color:var(--accent);}
.c-pink{color:var(--pink);}
.c-warn{color:var(--warn);}
.c-text{color:var(--text);}

/* Table */
.table-wrap{background:var(--s1);border:1px solid var(--border);border-radius:10px;
  overflow:hidden;}
.table-header{display:flex;align-items:center;justify-content:space-between;
  padding:.9rem 1.2rem;border-bottom:1px solid var(--border);}
.table-title{font-size:.85rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);}
.result-count{font-family:'JetBrains Mono',monospace;font-size:.8rem;color:var(--muted);}
table{width:100%;border-collapse:collapse;}
th{padding:.75rem 1rem;text-align:left;font-size:.75rem;font-weight:700;
  text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  border-bottom:1px solid var(--border);cursor:pointer;user-select:none;
  white-space:nowrap;}
th:hover{color:var(--text);}
th .sort-arrow{margin-left:.3rem;opacity:.3;}
th.sorted .sort-arrow{opacity:1;color:var(--accent);}
td{padding:.7rem 1rem;font-size:.88rem;border-bottom:1px solid rgba(255,255,255,.04);}
tr:last-child td{border-bottom:none;}
tr:hover td{background:var(--s2);}
.domain-cell{font-family:'JetBrains Mono',monospace;font-weight:600;font-size:.85rem;}
.val-in{color:var(--in);font-family:'JetBrains Mono',monospace;font-weight:600;}
.val-out{color:var(--out);font-family:'JetBrains Mono',monospace;font-weight:600;}
.val-null{color:var(--muted);}
.date-cell{color:var(--muted);font-size:.8rem;font-family:'JetBrains Mono',monospace;}
.empty{padding:3rem;text-align:center;color:var(--muted);}
.empty-icon{font-size:2rem;margin-bottom:.5rem;}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid rgba(100,255,218,.2);
  border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite;}
@keyframes spin{to{transform:rotate(360deg)}}
.flash{animation:flash-row .4s ease;}
@keyframes flash-row{0%,100%{background:transparent}50%{background:rgba(100,255,218,.06)}}
</style>

<header>
  <div class="logo">◈ DOMAIN TRACKER</div>
  <div class="hdr-right">
    <span><span class="dot"></span>Connected as <span id="tgUser" style="color:var(--text)">—</span></span>
    <span id="lastFetch" style="font-size:.8rem">—</span>
  </div>
</header>

<main>
  <div class="controls">
    <select id="groupSel"><option value="">⏳ Loading groups…</option></select>
    <input type="number" id="limitInp" value="300" min="10" max="3000" title="Message limit">
    <div class="search-wrap">
      <input type="text" id="searchInp" placeholder="🔍 Filter domains…">
    </div>
    <button class="btn-primary" id="fetchBtn" onclick="fetchData()">Fetch</button>
    <button class="btn-ghost" onclick="exportCSV()">Export CSV</button>
  </div>

  <div class="stats" id="stats" style="display:none">
    <div class="stat"><div class="stat-label">Domains Found</div>
      <div class="stat-val c-accent" id="s-total">0</div></div>
    <div class="stat"><div class="stat-label">Avg IN</div>
      <div class="stat-val c-text" id="s-avgin">—</div></div>
    <div class="stat"><div class="stat-label">Avg OUT</div>
      <div class="stat-val c-pink" id="s-avgout">—</div></div>
    <div class="stat"><div class="stat-label">Top OUT</div>
      <div class="stat-val c-warn" id="s-topout">—</div></div>
  </div>

  <div class="table-wrap">
    <div class="table-header">
      <span class="table-title">Results</span>
      <span class="result-count" id="rowCount">Select a group and click Fetch</span>
    </div>
    <div id="tableBody">
      <div class="empty"><div class="empty-icon">📡</div>No data yet — select a group and fetch messages</div>
    </div>
  </div>
</main>

<script>
let allData = [], sortCol = 'out', sortAsc = false;

async function loadGroups() {
  const r = await fetch('/api/groups');
  const groups = await r.json();
  const sel = document.getElementById('groupSel');
  sel.innerHTML = '<option value="">— Select a group —</option>' +
    groups.map(g => `<option value="${g.id}">${g.name}</option>`).join('');
}

async function loadMe() {
  const r = await fetch('/api/me');
  const d = await r.json();
  document.getElementById('tgUser').textContent = d.name || '—';
}

async function fetchData() {
  const gid = document.getElementById('groupSel').value;
  if (!gid) { alert('Please select a group first.'); return; }
  const limit = document.getElementById('limitInp').value || 300;
  const btn = document.getElementById('fetchBtn');
  btn.classList.add('loading');
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    const r = await fetch('/api/fetch', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({group_id: gid, limit})
    });
    const d = await r.json();
    allData = d.data;
    document.getElementById('lastFetch').textContent = 'Fetched: ' + d.last_fetch;
    renderTable();
    updateStats();
  } catch(e) {
    alert('Error fetching: ' + e.message);
  } finally {
    btn.classList.remove('loading');
    btn.innerHTML = 'Fetch';
  }
}

function updateStats() {
  const stats = document.getElementById('stats');
  if (!allData.length) { stats.style.display='none'; return; }
  stats.style.display='grid';
  const ins  = allData.map(d=>d.in).filter(v=>v!=null);
  const outs = allData.map(d=>d.out).filter(v=>v!=null);
  const avg  = arr => arr.length ? Math.round(arr.reduce((a,b)=>a+b,0)/arr.length).toLocaleString() : '—';
  const max  = arr => arr.length ? Math.max(...arr).toLocaleString() : '—';
  document.getElementById('s-total').textContent  = allData.length.toLocaleString();
  document.getElementById('s-avgin').textContent  = avg(ins);
  document.getElementById('s-avgout').textContent = avg(outs);
  document.getElementById('s-topout').textContent = max(outs);
}

function getFiltered() {
  const q = document.getElementById('searchInp').value.toLowerCase();
  return allData.filter(d => !q || d.domain.includes(q));
}

function sortData(data) {
  return [...data].sort((a,b) => {
    let av = a[sortCol] ?? -1, bv = b[sortCol] ?? -1;
    if (typeof av === 'string') av = av.toLowerCase(), bv = bv.toLowerCase();
    return sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
  });
}

function renderTable() {
  const filtered = sortData(getFiltered());
  document.getElementById('rowCount').textContent = `${filtered.length} / ${allData.length} domains`;
  if (!filtered.length) {
    document.getElementById('tableBody').innerHTML =
      '<div class="empty"><div class="empty-icon">🔍</div>No matching domains</div>';
    return;
  }
  const cols = [
    {key:'domain', label:'Domain'},
    {key:'in',     label:'IN  ▲'},
    {key:'out',    label:'OUT ▼'},
    {key:'date',   label:'Date'},
  ];
  const thead = `<thead><tr>${cols.map(c=>`
    <th class="${c.key===sortCol?'sorted':''}" onclick="setSort('${c.key}')">
      ${c.label}<span class="sort-arrow">${c.key===sortCol?(sortAsc?'↑':'↓'):'↕'}</span>
    </th>`).join('')}</tr></thead>`;
  const tbody = filtered.map(d => `<tr class="flash">
    <td class="domain-cell">${d.domain}</td>
    <td class="${d.in!=null?'val-in':'val-null'}">${d.in!=null?d.in.toLocaleString():'—'}</td>
    <td class="${d.out!=null?'val-out':'val-null'}">${d.out!=null?d.out.toLocaleString():'—'}</td>
    <td class="date-cell">${d.date||'—'}</td>
  </tr>`).join('');
  document.getElementById('tableBody').innerHTML =
    `<table>${thead}<tbody>${tbody}</tbody></table>`;
}

function setSort(col) {
  if (sortCol === col) sortAsc = !sortAsc;
  else { sortCol = col; sortAsc = false; }
  renderTable();
}

function exportCSV() {
  if (!allData.length) return;
  const rows = [['Domain','IN','OUT','Date'],
    ...allData.map(d=>[d.domain,d.in??'',d.out??'',d.date??''])];
  const csv = rows.map(r=>r.join(',')).join('\\n');
  const a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
  a.download = 'domains_' + new Date().toISOString().slice(0,10) + '.csv';
  a.click();
}

document.getElementById('searchInp').addEventListener('input', renderTable);
loadGroups();
loadMe();
</script>
"""

SETUP_HTML = AUTH_SHELL + """
<h1>Setup API</h1>
<p>You need Telegram API credentials. Get them free at
<a href="https://my.telegram.org" target="_blank">my.telegram.org</a> →
App Configuration.</p>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post">
  <div class="form-group">
    <label>API ID</label>
    <input name="api_id" type="text" required placeholder="12345678" value="{{ api_id or '' }}">
  </div>
  <div class="form-group">
    <label>API Hash</label>
    <input name="api_hash" type="text" required placeholder="0123456789abcdef…">
  </div>
  <button type="submit" class="btn-primary">Continue →</button>
</form>
</div></div>
"""

LOGIN_HTML = AUTH_SHELL + """
<h1>Sign In</h1>
<p>Enter your Telegram phone number. A verification code will be sent to your Telegram app.</p>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post">
  <div class="form-group">
    <label>Phone Number</label>
    <input name="phone" type="tel" required placeholder="+1 234 567 8900" autofocus>
    <div class="form-hint">Include country code (e.g. +212 for Morocco)</div>
  </div>
  <button type="submit" class="btn-primary">Send Code →</button>
</form>
</div></div>
"""

VERIFY_HTML = AUTH_SHELL + """
<h1>Enter Code</h1>
<p>A code was sent to your Telegram app{% if phone %} for {{ phone }}{% endif %}.</p>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post">
  <div class="form-group">
    <label>Verification Code</label>
    <input name="code" type="text" required placeholder="12345" autofocus
      class="mono" style="letter-spacing:.3em;font-size:1.4rem;text-align:center;">
  </div>
  <button type="submit" class="btn-primary">Verify →</button>
</form>
<p style="margin-top:1rem;font-size:.85rem;color:var(--muted)">
  Wrong number? <a href="/login">Go back</a></p>
</div></div>
"""

TWO_FA_HTML = AUTH_SHELL + """
<h1>Two-Factor Auth</h1>
<p>Your account has 2FA enabled. Enter your cloud password to continue.</p>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post">
  <div class="form-group">
    <label>Cloud Password</label>
    <input name="password" type="password" required autofocus>
  </div>
  <button type="submit" class="btn-primary">Unlock →</button>
</form>
</div></div>
"""

# ── Routes ──────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if not _config.get('api_id'):
        return redirect('/setup')
    try:
        authed = run_async(is_authed(), timeout=10)
    except Exception:
        authed = False
    if not authed:
        return redirect('/login')
    return render_template_string(DASHBOARD_HTML)

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    error = None
    if request.method == 'POST':
        _config['api_id']   = request.form['api_id'].strip()
        _config['api_hash'] = request.form['api_hash'].strip()
        save_config()
        global _client
        _client = None  # reset so new credentials are used
        return redirect('/login')
    return render_template_string(SETUP_HTML, error=error, api_id=_config.get('api_id',''))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        phone = request.form['phone'].strip()
        try:
            run_async(send_code(phone))
            session['phone'] = phone
            return redirect('/verify')
        except Exception as e:
            error = str(e)
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    phone = session.get('phone', _config.get('phone', ''))
    error = None
    if request.method == 'POST':
        code = request.form['code'].strip()
        try:
            run_async(sign_in_code(phone, code))
            return redirect('/')
        except SessionPasswordNeededError:
            return redirect('/2fa')
        except Exception as e:
            error = str(e)
    return render_template_string(VERIFY_HTML, error=error, phone=phone)

@app.route('/2fa', methods=['GET', 'POST'])
def two_fa():
    error = None
    if request.method == 'POST':
        pw = request.form['password']
        try:
            run_async(sign_in_2fa(pw))
            return redirect('/')
        except Exception as e:
            error = str(e)
    return render_template_string(TWO_FA_HTML, error=error)

# ── API Endpoints ───────────────────────────────────────────────────────────────
@app.route('/api/me')
def api_me():
    try:
        name = run_async(get_me_name(), timeout=10)
    except Exception:
        name = _tg_username or '—'
    return jsonify({'name': name})

@app.route('/api/groups')
def api_groups():
    try:
        groups = run_async(get_groups())
        return jsonify(groups)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/fetch', methods=['POST'])
def api_fetch():
    data    = request.get_json()
    gid     = data.get('group_id')
    limit   = data.get('limit', 300)
    try:
        results = run_async(fetch_group_messages(gid, limit))
        return jsonify({'count': len(results), 'data': results, 'last_fetch': _last_fetch})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/data')
def api_data():
    return jsonify({'data': _domain_data, 'last_fetch': _last_fetch})

# ── Run ─────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n  ◈  Domain Tracker  →  http://localhost:{port}\n")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
