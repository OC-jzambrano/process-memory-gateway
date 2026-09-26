import asyncio
import contextlib
import hashlib
import html
import json
import logging
import secrets as stdlib_secrets
import uuid

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from server import get_default_service, mcp
from src.api.auth import (
    AuthenticationError,
    AuthorizationError,
    CognitoTokenVerifier,
    resolve_authenticated_context,
)
from src.api.auth_context import set_current_context
from src.config import (
    AWS_REGION,
    COGNITO_APP_CLIENT_ID,
    COGNITO_DOMAIN,
    COGNITO_REGION,
    COGNITO_REQUIRED_SCOPE,
    COGNITO_RESOURCE_SERVER_IDENTIFIER,
    COGNITO_USER_POOL_ID,
    DATA_DIR,
    DOMAIN_NAME,
    MCP_RESOURCE_URL,
)
from src.models.enums import CompanyStatus, MembershipStatus, RoleType
from src.models.schemas import RequestContext
from src.orchestration.dispatcher import DownstreamDispatcher
from src.storage.db import get_connection
from src.storage.repository import MemoryRepository
from src.utils.privacy import sanitize_evidence

logger = logging.getLogger(__name__)

# Singletons
repo = MemoryRepository()
token_verifier = CognitoTokenVerifier()
fastmcp_http_app = mcp.streamable_http_app()


async def health_live(request: Request) -> JSONResponse:
    """Liveness probe: verifies process is running."""
    return JSONResponse({"status": "alive"})


async def health_ready(request: Request) -> JSONResponse:
    """
    Readiness probe: verifies persistent storage mount, database connectivity,
    and schema migrations table without leaking internal filesystem paths on 503.
    """
    storage_ok = True
    db_ok = True
    schema_version: int | None = None

    # 1. Storage mount check and writability probe
    if not DATA_DIR.exists() or not DATA_DIR.is_dir():
        storage_ok = False
        logger.error(
            "Readiness check failed: persistent storage mount is not available."
        )
    else:
        try:
            probe = DATA_DIR / ".readiness_probe"
            probe.write_text("probe")
            probe.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001 - Catch filesystem errors during probe check
            storage_ok = False
            logger.error(
                "Readiness check failed: persistent storage is not writable: %s",
                sanitize_evidence(str(e)),
            )

    # 2. Database connectivity & schema migrations check
    try:
        active_db_path = getattr(repo, "db_path", None)
        conn = get_connection(active_db_path) if active_db_path else get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1;").fetchone()
            row = cursor.execute(
                "SELECT MAX(version) FROM schema_migrations;"
            ).fetchone()
            if row is not None and row[0] is not None:
                schema_version = int(row[0])
            else:
                schema_version = 0
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - Boundary converts arbitrary provider failures to controlled outcomes.
        db_ok = False
        logger.error(
            "Readiness check failed: database connectivity or schema verification error: %s",
            sanitize_evidence(str(e)),
        )

    if not storage_ok or not db_ok:
        return JSONResponse(
            {
                "status": "not_ready",
                "checks": {
                    "storage": "ok" if storage_ok else "failed",
                    "database": "ok" if db_ok else "failed",
                },
            },
            status_code=503,
        )

    return JSONResponse(
        {
            "status": "ready",
            "database": "connected",
            "storage": "mounted",
            "schema_version": schema_version,
        }
    )


DOWNSTREAM_UI = """<!doctype html><html><head><meta charset='utf-8'><title>OPM Downstreams</title>
<style>
body{font:15px system-ui;max-width:900px;margin:40px auto;padding:0 20px;color:#18212b}
input,select,button{padding:10px;margin:5px 0;width:100%;box-sizing:border-box}
button{cursor:pointer;background:#1769aa;color:white;border:0;border-radius:4px;font-weight:500}
button:disabled{opacity:0.6;cursor:not-allowed}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.server{border:1px solid #ccd5df;padding:14px;margin:12px 0;border-radius:6px}
.ready{color:#087f3f;font-weight:600}
.bad{color:#b42318;font-weight:600}
.auth-panel{background:#f8fafc;padding:18px;border-radius:8px;margin-bottom:24px;border:1px solid #cbd5e1}
.auth-panel h2{margin-top:0;font-size:16px;color:#0f172a}
.status-badge{display:inline-block;padding:6px 12px;border-radius:4px;font-size:13px;font-weight:600}
.status-badge.ok{background:#dcfce7;color:#15803d}
.status-badge.err{background:#fee2e2;color:#b91c1c}
.status-badge.pending{background:#fef3c7;color:#b45309}
.btn-secondary{background:#64748b}
.token-container{display:flex;gap:8px}
</style></head>
<body>
<h1>OPM Downstream Management</h1>

<div class='auth-panel'>
  <h2>1. Sign in &amp; Company Context</h2>
  <div class='row'>
    <label>Company Slug
      <input id='auth_company' placeholder='odooconcept' value='odooconcept'>
    </label>
    <div>
      <button type='button' id='btn_login' style='width:auto;padding:8px 18px'>Sign in with Cognito</button>
      <button type='button' id='btn_logout' style='width:auto;padding:8px 18px' class='btn-secondary'>Sign out</button>
    </div>
  </div>
  <div style='display:flex;gap:10px;align-items:center;margin-top:10px'>
    <span id='auth_status' class='status-badge pending'>Checking auth...</span>
  </div>
</div>

<h2>2. Register Downstream Connection</h2>
<p>For Odoo, enter connection details. OPM stores them in Secrets Manager and keeps only the secret reference.</p>
<form id='form'>
  <div class='row'>
    <label>Server ID<input name='server_id' required placeholder='odoo-main'></label>
    <label>Transport
      <select name='transport' id='transport'>
        <option value='odoo_xmlrpc'>Odoo XML-RPC</option>
        <option value='streamable_http'>Streamable HTTP</option>
        <option value='stdio'>stdio</option>
      </select>
    </label>
  </div>
  <label>Endpoint<input name='endpoint' required placeholder='https://community.odooconcept.com'></label>
  <label>Odoo user<input name='username' placeholder='process-memory-pilot'></label>
  <label>Odoo password or API key<input type='password' name='password'></label>
  <label>Database (optional)<input name='database' placeholder='community'></label>
  <label>Existing Secrets Manager ARN (generic MCP only)<input name='secret_ref' placeholder='arn:aws:secretsmanager:...'></label>
  <button type='submit' id='btn_submit'>Register and connect</button>
</form>

<h2>3. Registered Downstreams</h2>
<section id='servers'></section>

<script>
const f = document.querySelector('#form'),
      out = document.querySelector('#servers'),
      companyInput = document.querySelector('#auth_company'),
      authStatus = document.querySelector('#auth_status'),
      btnLogin = document.querySelector('#btn_login'),
      btnLogout = document.querySelector('#btn_logout'),
      btnSubmit = document.querySelector('#btn_submit');

function getCompany() {
  const urlParams = new URLSearchParams(window.location.search);
  return (companyInput.value.trim() || urlParams.get('company') || localStorage.getItem('opmCompany') || 'odooconcept');
}

function getBearer() {
  const token = sessionStorage.getItem('opmAccessToken') || '';
  return token ? 'Bearer ' + token : '';
}

function syncInputs() {
  const urlParams = new URLSearchParams(window.location.search);
  const comp = urlParams.get('company') || localStorage.getItem('opmCompany') || 'odooconcept';
  companyInput.value = comp;
  const callbackError = urlParams.get('error_description') || urlParams.get('error');
  if (callbackError) {
    authStatus.className = 'status-badge err';
    authStatus.textContent = 'Login failed';
    out.innerHTML = '<p class="bad">' + callbackError.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) + '</p>';
  }
}

function saveCompany() {
  const comp = getCompany();
  companyInput.value = comp;
  localStorage.setItem('opmCompany', comp);
  const url = new URL(window.location);
  url.searchParams.set('company', comp);
  window.history.replaceState({}, '', url);
  load();
}

async function login() {
  const cfg = await fetch('/admin/downstreams/auth-config').then(r => r.json());
  const base64url = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = base64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
  const state = base64url(crypto.getRandomValues(new Uint8Array(24)));
  sessionStorage.setItem('opmPkceVerifier', verifier);
  sessionStorage.setItem('opmOAuthState', state);
  saveCompany();
  const params = new URLSearchParams({
    response_type: 'code', client_id: cfg.client_id, redirect_uri: cfg.redirect_uri,
    scope: cfg.scope, state, code_challenge: challenge, code_challenge_method: 'S256'
  });
  window.location.assign(cfg.authorization_endpoint + '?' + params.toString());
}

async function completeLogin() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  if (!code) return;
  const state = params.get('state');
  const expectedState = sessionStorage.getItem('opmOAuthState');
  const verifier = sessionStorage.getItem('opmPkceVerifier');
  sessionStorage.removeItem('opmOAuthState');
  sessionStorage.removeItem('opmPkceVerifier');
  if (!state || !expectedState || state !== expectedState || !verifier) {
    throw new Error('Invalid OAuth callback state');
  }
  const cfg = await fetch('/admin/downstreams/auth-config').then(r => r.json());
  const response = await fetch(cfg.token_endpoint, {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({grant_type: 'authorization_code', client_id: cfg.client_id,
      code, redirect_uri: cfg.redirect_uri, code_verifier: verifier})
  });
  const payload = await response.json();
  if (!response.ok || !payload.access_token) throw new Error(payload.error_description || 'Token exchange failed');
  sessionStorage.setItem('opmAccessToken', payload.access_token);
  const clean = new URL(window.location);
  clean.searchParams.delete('code'); clean.searchParams.delete('state');
  clean.searchParams.delete('error'); clean.searchParams.delete('error_description');
  window.history.replaceState({}, '', clean);
}

if (btnLogin) btnLogin.onclick = () => login().catch(err => {
  authStatus.className = 'status-badge err'; authStatus.textContent = 'Login error';
  out.innerHTML = '<p class="bad">' + err.message.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) + '</p>';
});
if (btnLogout) btnLogout.onclick = async () => {
  sessionStorage.removeItem('opmAccessToken');
  sessionStorage.removeItem('opmOAuthState');
  sessionStorage.removeItem('opmPkceVerifier');
  try {
    const cfg = await fetch('/admin/downstreams/auth-config').then(r => r.json());
    const params = new URLSearchParams({client_id: cfg.client_id, logout_uri: cfg.logout_uri});
    window.location.assign(cfg.logout_endpoint + '?' + params.toString());
  } catch {
    authStatus.className = 'status-badge pending';
    authStatus.textContent = 'Signed out';
    load();
  }
};

async function load() {
  const bearer = getBearer();
  const comp = getCompany();
  if (!bearer) {
    authStatus.className = 'status-badge err';
    authStatus.textContent = 'Sign-in required';
    out.innerHTML = '<p class="bad">Sign in with Cognito before checking downstreams.</p>';
    return;
  }
  authStatus.className = 'status-badge pending';
  authStatus.textContent = 'Verifying auth...';
  try {
    const res = await fetch('/admin/downstreams/status?company=' + encodeURIComponent(comp), {
      headers: { Authorization: bearer }
    });
    const txt = await res.text();
    if (res.ok) {
      authStatus.className = 'status-badge ok';
      authStatus.textContent = 'Authenticated (' + comp + ')';
      out.innerHTML = txt;
    } else {
      authStatus.className = 'status-badge err';
      authStatus.textContent = 'Auth error (' + res.status + ')';
      out.innerHTML = txt || '<p class="bad">Authentication failed with status ' + res.status + '</p>';
    }
  } catch (err) {
    authStatus.className = 'status-badge err';
    authStatus.textContent = 'Network error';
    out.innerHTML = '<p class="bad">Network error: ' + err.message + '</p>';
  }
}

f.onsubmit = async (e) => {
  e.preventDefault();
  const bearer = getBearer();
  const comp = getCompany();
  if (!bearer) {
    alert('Sign in with Cognito before registering a downstream.');
    return;
  }
  saveCompany();
  const origText = btnSubmit.textContent;
  btnSubmit.disabled = true;
  btnSubmit.textContent = 'Connecting...';
  try {
    const x = Object.fromEntries(new FormData(f));
    const r = await fetch('/admin/downstreams/register?company=' + encodeURIComponent(comp), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: bearer
      },
      body: JSON.stringify(x)
    });
    const result = await r.text();
    try {
      const parsed = JSON.parse(result);
      if (!r.ok) {
        alert('Error: ' + (parsed.message || parsed.error || result));
      } else {
        alert('Success: Downstream registered (' + (parsed.server_id || 'OK') + ')');
      }
    } catch {
      alert(result);
    }
    load();
  } catch (err) {
    alert('Request failed: ' + err.message);
  } finally {
    btnSubmit.disabled = false;
    btnSubmit.textContent = origText;
  }
};

syncInputs();
completeLogin().then(load).catch(err => {
  authStatus.className = 'status-badge err'; authStatus.textContent = 'Login error';
  out.innerHTML = '<p class="bad">' + err.message.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) + '</p>';
});
</script>
</body></html>"""


async def downstream_ui(request: Request) -> HTMLResponse:
    return HTMLResponse(DOWNSTREAM_UI)


# ─── INSTALL PAGE: ONE-CLICK MCP CONFIG FOR ALL AI CLIENTS ───────────

INSTALL_UI = """<!doctype html><html><head><meta charset='utf-8'>
<title>Install Process Memory Gateway</title>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<style>
:root{--bg:#0f172a;--surface:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;
--accent:#3b82f6;--accent-hover:#2563eb;--success:#22c55e;--danger:#ef4444;
--card-bg:#1e293b;--code-bg:#0f172a;--radius:10px}
*{box-sizing:border-box;margin:0;padding:0}
body{font:15px/1.6 system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
min-height:100vh}
.container{max-width:860px;margin:0 auto;padding:32px 20px}
h1{font-size:28px;font-weight:700;background:linear-gradient(135deg,#60a5fa,#a78bfa);
-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:4px}
.subtitle{color:var(--muted);font-size:14px;margin-bottom:32px}
.step{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
padding:24px;margin-bottom:20px}
.step h2{font-size:17px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:10px}
.step-num{background:var(--accent);color:white;width:28px;height:28px;border-radius:50%;
display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px;align-items:end}
input{padding:10px 12px;background:var(--code-bg);border:1px solid var(--border);
border-radius:6px;color:var(--text);font-size:14px;width:100%}
input:focus{outline:none;border-color:var(--accent)}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:4px}
button{padding:10px 20px;border:none;border-radius:6px;font-size:14px;font-weight:600;
cursor:pointer;transition:all .15s}
.btn-primary{background:var(--accent);color:white}
.btn-primary:hover{background:var(--accent-hover)}
.btn-secondary{background:#475569;color:white}
.btn-danger{background:var(--danger);color:white;font-size:12px;padding:6px 12px}
.btn-sm{font-size:12px;padding:6px 14px}
.status-badge{display:inline-block;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600}
.status-badge.ok{background:rgba(34,197,94,.15);color:#4ade80}
.status-badge.err{background:rgba(239,68,68,.15);color:#f87171}
.status-badge.pending{background:rgba(234,179,8,.15);color:#facc15}
.key-display{background:var(--code-bg);border:1px solid var(--success);border-radius:6px;
padding:16px;margin:12px 0;font-family:monospace;font-size:14px;
word-break:break-all;color:var(--success);position:relative}
.key-display .warn{color:var(--danger);font-size:12px;display:block;margin-top:8px;font-family:system-ui}
.client-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
overflow:hidden;margin-bottom:12px}
.client-header{padding:14px 20px;display:flex;align-items:center;gap:12px;
border-bottom:1px solid var(--border);cursor:pointer;user-select:none}
.client-header h3{font-size:15px;font-weight:600;flex:1}
.client-badge{font-size:11px;padding:3px 8px;border-radius:4px;background:rgba(59,130,246,.15);color:#60a5fa}
.client-body{padding:20px;display:none}
.client-card.open .client-body{display:block}
.client-card.open .client-header{background:rgba(59,130,246,.05)}
.config-block{position:relative;background:var(--code-bg);border:1px solid var(--border);
border-radius:6px;padding:14px;margin:8px 0 12px;overflow-x:auto}
.config-block pre{font-family:monospace;font-size:12px;line-height:1.5;
white-space:pre;color:#93c5fd;margin:0}
.config-block .copy-btn{position:absolute;top:8px;right:8px;background:var(--accent);
color:white;border:none;border-radius:4px;padding:4px 10px;font-size:11px;cursor:pointer}
.file-path{font-size:12px;color:var(--muted);font-family:monospace;margin-bottom:4px}
.instructions{font-size:13px;color:var(--muted);margin-top:8px;line-height:1.6}
.instructions ol{padding-left:20px}
.instructions li{margin-bottom:4px}
.existing-keys table{width:100%;border-collapse:collapse;font-size:13px;margin-top:12px}
.existing-keys th{text-align:left;padding:8px;color:var(--muted);border-bottom:1px solid var(--border)}
.existing-keys td{padding:8px;border-bottom:1px solid var(--border)}
.panel-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.service-card{border:1px solid var(--border);border-radius:8px;padding:14px;margin-top:10px;background:rgba(15,23,42,.42)}
.service-card header{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.service-card h3{font-size:14px;flex:1}
.service-card small{color:var(--muted);word-break:break-all}
.service-actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.help-line{font-size:12px;color:var(--muted);margin-top:8px}
select,textarea{padding:10px 12px;background:var(--code-bg);border:1px solid var(--border);
border-radius:6px;color:var(--text);font-size:14px;width:100%}
textarea{min-height:68px;resize:vertical}
@media(max-width:720px){.row,.panel-grid{grid-template-columns:1fr}.container{padding:20px 14px}}
.hidden{display:none!important}
.flex-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.chevron{transition:transform .2s;font-size:12px;color:var(--muted)}
.client-card.open .chevron{transform:rotate(90deg)}
</style></head>
<body>
<div class='container'>
<h1>Process Memory Gateway</h1>
<p class='subtitle'>One-click MCP server installation for your AI coding assistant</p>

<div class='step'>
  <h2><span class='step-num'>1</span> Sign in with your company account</h2>
  <div class='row'>
    <div><label>Company Slug</label>
    <input id='auth_company' placeholder='odooconcept' value='odooconcept'></div>
    <div class='flex-row' style='padding-bottom:2px'>
      <button id='btn_login' class='btn-primary'>Sign in with Cognito</button>
      <button id='btn_logout' class='btn-secondary btn-sm'>Sign out</button>
    </div>
  </div>
  <div style='margin-top:10px'>
    <span id='auth_status' class='status-badge pending'>Checking...</span>
  </div>
</div>

<div class='step' id='step_key'>
  <h2><span class='step-num'>2</span> Generate a persistent API key</h2>
  <p style='font-size:13px;color:var(--muted);margin-bottom:12px'>
    This key never expires and replaces short-lived Cognito tokens in your AI client config.</p>
  <div class='flex-row'>
    <select id='key_label' style='max-width:220px; padding: 10px 12px; background: var(--code-bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text);'>
      <option value='Antigravity Desktop'>Antigravity Desktop</option>
      <option value='Antigravity CLI'>Antigravity CLI</option>
      <option value='Claude Desktop'>Claude Desktop</option>
      <option value='Codex'>Codex (OpenAI)</option>
      <option value='Other'>Other</option>
    </select>
    <button id='btn_gen_key' class='btn-primary'>Generate API Key</button>
  </div>
  <div id='new_key_display' class='hidden'>
    <div class='key-display'>
      <span id='new_key_value'></span>
      <span class='warn'>&#9888; Copy this key now &mdash; it will not be shown again.</span>
    </div>
    <button class='btn-secondary btn-sm' onclick="navigator.clipboard?navigator.clipboard.writeText(document.getElementById('new_key_value').textContent).then(()=>{this.textContent='Copied!';setTimeout(()=>this.textContent='Copy Key',1500)}):alert('Please copy the key manually (clipboard requires HTTPS).')">Copy Key</button>
  </div>
  <div id='existing_keys' class='existing-keys'></div>
</div>

<div class='step' id='step_install'>
  <h2><span class='step-num'>3</span> Add to your AI client</h2>
  <p style='font-size:13px;color:var(--muted);margin-bottom:16px'>
    Pick a client, copy or download the config, then open that client's MCP settings.</p>
  <div id='client_cards'></div>
</div>

<div class='step' id='step_services'>
  <h2><span class='step-num'>4</span> Connected services</h2>
  <div class='panel-grid'>
    <div>
      <label>Service ID</label>
      <input id='svc_id' placeholder='odoo-main'>
    </div>
    <div>
      <label>Transport</label>
      <select id='svc_transport'>
        <option value='odoo_xmlrpc'>Odoo XML-RPC</option>
        <option value='streamable_http'>Streamable HTTP MCP</option>
        <option value='stdio'>stdio MCP</option>
      </select>
    </div>
    <div>
      <label>Endpoint</label>
      <input id='svc_endpoint' placeholder='https://community.odooconcept.com'>
    </div>
    <div>
      <label>Secrets Manager ARN</label>
      <input id='svc_secret' placeholder='arn:aws:secretsmanager:...'>
    </div>
    <div>
      <label>Odoo user</label>
      <input id='svc_user' placeholder='process-memory-pilot'>
    </div>
    <div>
      <label>Odoo password or API key</label>
      <input id='svc_pass' type='password'>
    </div>
  </div>
  <div class='flex-row' style='margin-top:12px'>
    <button id='btn_save_service' class='btn-primary'>Save service</button>
    <button id='btn_refresh_services' class='btn-secondary btn-sm'>Refresh</button>
  </div>
  <p class='help-line'>For Odoo, enter user/password once. The server stores them in AWS Secrets Manager and only keeps the secret reference.</p>
  <div id='services_list'></div>
</div>
</div>

<script>
const AC=document.getElementById('auth_company'),AS=document.getElementById('auth_status'),
BL=document.getElementById('btn_login'),BO=document.getElementById('btn_logout'),
BG=document.getElementById('btn_gen_key'),KL=document.getElementById('key_label'),
NKD=document.getElementById('new_key_display'),NKV=document.getElementById('new_key_value'),
EK=document.getElementById('existing_keys'),CC=document.getElementById('client_cards'),
SI=document.getElementById('svc_id'),ST=document.getElementById('svc_transport'),
SE=document.getElementById('svc_endpoint'),SS=document.getElementById('svc_secret'),
SU=document.getElementById('svc_user'),SP=document.getElementById('svc_pass'),
BS=document.getElementById('btn_save_service'),BR=document.getElementById('btn_refresh_services'),
SL=document.getElementById('services_list');
let curKey=null;
function safeGet(t,k){try{return window[t].getItem(k)||''}catch(e){return ''}}
function safeSet(t,k,v){try{window[t].setItem(k,v)}catch(e){console.warn('Storage blocked:',e)}}
function safeRem(t,k){try{window[t].removeItem(k)}catch(e){}}
function gc(){return AC.value.trim()||new URLSearchParams(location.search).get('company')||safeGet('localStorage','opmCompany')||'odooconcept'}
function gb(){const t=safeGet('sessionStorage','opmAccessToken');return t?'Bearer '+t:''}
function sc(){const c=gc();AC.value=c;safeSet('localStorage','opmCompany',c);const u=new URL(location);u.searchParams.set('company',c);history.replaceState({},'',u)}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function copyText(t){return navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject(new Error('Clipboard requires HTTPS'))}
function downloadText(name,text){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type:'application/json'}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
async function login(){
  try {
    AS.className='status-badge pending';AS.textContent='Redirecting...';
    const cfg=await fetch('/install/auth-config').then(r=>r.json());
    const b64u=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
    const v=b64u(crypto.getRandomValues(new Uint8Array(32)));
    let ch=v, m='plain';
    if(crypto.subtle){
      ch=b64u(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(v)));
      m='S256';
    }
    const st=b64u(crypto.getRandomValues(new Uint8Array(24)));
    safeSet('sessionStorage','opmPkceVerifier',v);safeSet('sessionStorage','opmOAuthState',st);sc();
    const p=new URLSearchParams({response_type:'code',client_id:cfg.client_id,redirect_uri:cfg.redirect_uri,scope:cfg.scope,state:st,code_challenge:ch,code_challenge_method:m});
    location.assign(cfg.authorization_endpoint+'?'+p)
  } catch(e) {
    AS.className='status-badge err';AS.textContent='Login error: '+e.message;
  }
}
async function completeLogin(){
  const p=new URLSearchParams(location.search),code=p.get('code');if(!code)return;
  const st=p.get('state'),ex=safeGet('sessionStorage','opmOAuthState'),v=safeGet('sessionStorage','opmPkceVerifier');
  safeRem('sessionStorage','opmOAuthState');safeRem('sessionStorage','opmPkceVerifier');
  if(!st||!ex||st!==ex||!v)throw new Error('Invalid OAuth state');
  const cfg=await fetch('/install/auth-config').then(r=>r.json());
  const res=await fetch(cfg.token_endpoint,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({grant_type:'authorization_code',client_id:cfg.client_id,code,redirect_uri:cfg.redirect_uri,code_verifier:v})});
  const pl=await res.json();if(!res.ok||!pl.access_token)throw new Error(pl.error_description||'Token exchange failed');
  safeSet('sessionStorage','opmAccessToken',pl.access_token);
  const cl=new URL(location);['code','state','error','error_description'].forEach(k=>cl.searchParams.delete(k));
  history.replaceState({},'',cl)
}
BL.onclick=()=>login().catch(()=>{AS.className='status-badge err';AS.textContent='Login error'});
BO.onclick=()=>{safeRem('sessionStorage','opmAccessToken');AS.className='status-badge err';AS.textContent='Signed out';EK.innerHTML='';CC.innerHTML='';NKD.classList.add('hidden');curKey=null};
BG.onclick=async()=>{
  const b=gb(),c=gc();if(!b){alert('Sign in first.');return}
  BG.disabled=true;BG.textContent='Generating...';
  try{const r=await fetch('/install/api-key?company='+encodeURIComponent(c),{method:'POST',headers:{'Content-Type':'application/json',Authorization:b},body:JSON.stringify({label:KL.value.trim()||'default'})});
    const d=await r.json();if(!r.ok){alert(d.error||d.message||'Failed');return}
    curKey=d.api_key;NKV.textContent=d.api_key;NKD.classList.remove('hidden');renderClients();loadKeys()
  }catch(e){alert('Error: '+e.message)}
  finally{BG.disabled=false;BG.textContent='Generate API Key'}
};
async function loadKeys(){
  const b=gb(),c=gc();if(!b)return;
  try{const r=await fetch('/install/api-keys?company='+encodeURIComponent(c),{headers:{Authorization:b}});
    if(!r.ok)return;const keys=await r.json();
    if(!keys.length){EK.innerHTML='<p style="font-size:13px;color:var(--muted)">No keys yet.</p>';return}
    let h='<table><tr><th>Prefix</th><th>Label</th><th>Created</th><th>Last Used</th><th></th></tr>';
    keys.forEach(k=>{const kid=esc(k.key_id);h+='<tr><td><code>'+esc(k.key_prefix)+'...</code></td><td>'+esc(k.label)+'</td><td>'+(k.created_at||'').slice(0,10)+'</td><td>'+(k.last_used_at||'never')+'</td><td>'+(k.status==='active'?'<button class="btn-danger" onclick="revokeKey(&quot;'+kid+'&quot;)">Revoke</button>':'<em>revoked</em>')+'</td></tr>'});
    EK.innerHTML=h+'</table>';if(!curKey&&keys.some(k=>k.status==='active'))renderClients()
  }catch(e){console.error(e)}
}
async function revokeKey(id){if(!confirm('Revoke this key?'))return;const b=gb(),c=gc();
  await fetch('/install/api-key/revoke?company='+encodeURIComponent(c),{method:'POST',headers:{'Content-Type':'application/json',Authorization:b},body:JSON.stringify({key_id:id})});loadKeys()
}
function mcpUrl(){const h=location.host;const p=location.protocol;return p+'//'+h+'/companies/'+encodeURIComponent(gc())+'/mcp'}
function renderClients(){
  const url=mcpUrl(),kp=curKey||'opm_YOUR_API_KEY_HERE',bv='Bearer '+kp;
  const clients=[
    {name:'Antigravity Desktop',badge:'Recommended',
     path:'Settings > Customizations > Open MCP Config',
     open:'antigravity://settings/mcp',
     config:JSON.stringify({mcpServers:{"process-memory":{serverUrl:url,headers:{Authorization:bv}}}},null,2),
     file:'antigravity-mcp.json',
     steps:['Open Antigravity Desktop','Go to <b>Settings &rarr; Customizations</b>','Click <b>Open MCP Config</b>','Replace contents with the config below','Click <b>Refresh</b> in Installed MCP Servers']},
    {name:'Antigravity CLI (agy)',badge:'Developers',
     path:'.mcp.json (project root) or global mcp_config.json',
     open:null,
     config:JSON.stringify({mcpServers:{"process-memory":{serverUrl:url,headers:{Authorization:bv}}}},null,2),
     file:'.mcp.json',
     steps:['Create or edit <b>.mcp.json</b> in your project root','Paste the config below','Run <b>agy</b> &mdash; the server appears automatically']},
    {name:'Claude Desktop',badge:'Anthropic',
     path:navigator.platform.includes('Win')?'%APPDATA%\\Claude\\claude_desktop_config.json':navigator.platform.includes('Mac')?'~/Library/Application Support/Claude/claude_desktop_config.json':'~/.config/claude/claude_desktop_config.json',
     open:'claude://settings/developer',
     config:JSON.stringify({mcpServers:{"process-memory":{command:"npx",args:["-y","@anthropic/mcp-remote",url,"--header","Authorization: "+bv]}}},null,2),
     file:'claude_desktop_config.json',
     steps:['Open Claude Desktop &rarr; <b>Settings &rarr; Developer &rarr; Edit Config</b>','Paste the config below','Restart Claude Desktop','The process-memory tools will appear in chat']},
    {name:'Codex (OpenAI)',badge:'OpenAI',
     path:'.codex/mcp.json',
     open:null,
     config:JSON.stringify({mcpServers:{"process-memory":{type:"url",url:url,headers:{Authorization:bv}}}},null,2),
     file:'codex-mcp.json',
     steps:['Create <b>.codex/mcp.json</b> in your project root','Paste the config below','Run <b>codex</b> &mdash; it discovers the MCP server']}
  ];
  let h='';
  clients.forEach((c,i)=>{
    h+='<div class="client-card'+(i===0?' open':'')+'">'
      +'<div class="client-header" onclick="this.parentElement.classList.toggle(&quot;open&quot;)"><span class="chevron">&#9654;</span><h3>'+esc(c.name)+'</h3><span class="client-badge">'+esc(c.badge)+'</span></div>'
      +'<div class="client-body"><div class="file-path">'+esc(c.path)+'</div>'
      +'<div class="flex-row" style="margin-bottom:10px">'
      +(c.open?'<button class="btn-secondary btn-sm" onclick="location.href=&quot;'+esc(c.open)+'&quot;">Open client settings</button>':'')
      +'<button class="btn-secondary btn-sm" onclick="downloadText(&quot;'+esc(c.file)+'&quot;,'+JSON.stringify(c.config).replace(/"/g,'&quot;')+')">Download config</button>'
      +'</div>'
      +'<div class="config-block"><button class="copy-btn" onclick="event.stopPropagation();const p=this.parentElement.querySelector(&quot;pre&quot;);copyText(p.textContent).then(()=>{this.textContent=&quot;Copied!&quot;;setTimeout(()=>this.textContent=&quot;Copy&quot;,1500)}).catch(()=>alert(&quot;Please copy manually.&quot;))">Copy</button>'
      +'<pre>'+esc(c.config)+'</pre></div>'
      +'<div class="instructions"><ol>';
    c.steps.forEach(s=>{h+='<li>'+s+'</li>'});
    h+='</ol></div></div></div>'
  });
  CC.innerHTML=h
}
async function loadServices(){
  const b=gb(),c=gc();if(!b){SL.innerHTML='<p class="help-line">Sign in to manage services.</p>';return}
  SL.innerHTML='<p class="help-line">Loading services...</p>';
  try{const r=await fetch('/install/downstreams?company='+encodeURIComponent(c),{headers:{Authorization:b}});
    const d=await r.json();if(!r.ok){SL.innerHTML='<p class="help-line">'+esc(d.message||d.error||'Could not load services')+'</p>';return}
    if(!d.servers.length){SL.innerHTML='<p class="help-line">No services connected yet.</p>';return}
    let h='';d.servers.forEach(s=>{const sid=esc(s.server_id),tr=esc(s.transport),ep=esc(s.endpoint);h+='<div class="service-card"><header><h3>'+sid+'</h3><span class="status-badge '+(s.ready?'ok':'err')+'">'+(s.ready?'Ready':'Needs attention')+'</span></header>'
      +'<small>'+esc(s.transport)+' · '+esc(s.endpoint)+'</small><p class="help-line">'+esc(s.state)+'</p>'
      +'<div class="service-actions"><button class="btn-secondary btn-sm" onclick="editService(&quot;'+sid+'&quot;,&quot;'+tr+'&quot;,&quot;'+ep+'&quot;)">Edit</button>'
      +'<button class="btn-danger" onclick="deleteService(&quot;'+sid+'&quot;)">Delete</button></div></div>'});
    SL.innerHTML=h
  }catch(e){SL.innerHTML='<p class="help-line">Network error: '+esc(e.message)+'</p>'}
}
function editService(id,t,e){SI.value=id;ST.value=t;SE.value=e;SS.value='';SU.value='';SP.value='';SI.focus()}
BS.onclick=async()=>{
  const b=gb(),c=gc();if(!b){alert('Sign in first.');return}
  const payload={server_id:SI.value.trim(),transport:ST.value,endpoint:SE.value.trim(),secret_ref:SS.value.trim(),username:SU.value.trim(),password:SP.value};
  if(!payload.server_id||!payload.endpoint){alert('Service ID and endpoint are required.');return}
  BS.disabled=true;BS.textContent='Saving...';
  try{const r=await fetch('/admin/downstreams/register?company='+encodeURIComponent(c),{method:'POST',headers:{'Content-Type':'application/json',Authorization:b},body:JSON.stringify(payload)});
    const d=await r.json();if(!r.ok){alert(d.error||d.message||'Save failed');return}
    SS.value='';SP.value='';await loadServices()
  }catch(e){alert('Error: '+e.message)}
  finally{BS.disabled=false;BS.textContent='Save service'}
};
BR.onclick=()=>loadServices();
async function deleteService(id){if(!confirm('Delete '+id+'?'))return;const b=gb(),c=gc();
  const r=await fetch('/install/downstreams/'+encodeURIComponent(id)+'?company='+encodeURIComponent(c),{method:'DELETE',headers:{Authorization:b}});
  if(!r.ok){const d=await r.json().catch(()=>({error:'Delete failed'}));alert(d.error||d.message||'Delete failed');return}
  loadServices()
}
// Init
(function(){
  try {
    const p=new URLSearchParams(location.search);
    AC.value=p.get('company')||safeGet('localStorage','opmCompany')||'odooconcept';
    if(p.get('error'))AS.textContent='Login failed';
    completeLogin().then(async()=>{
      const b=gb();if(!b){AS.className='status-badge err';AS.textContent='Sign-in required';return}
      try{const c=gc(),r=await fetch('/install/api-keys?company='+encodeURIComponent(c),{headers:{Authorization:b}});
        if(r.ok){AS.className='status-badge ok';AS.textContent='Authenticated';loadKeys();loadServices()}
        else if(r.status===401){safeRem('sessionStorage','opmAccessToken');AS.className='status-badge err';AS.textContent='Session expired'}
        else{AS.className='status-badge err';AS.textContent='Error ('+r.status+')'}
      }catch(e){AS.className='status-badge err';AS.textContent='Network error'}
    }).catch(e=>{AS.className='status-badge err';AS.textContent='Login error: '+e.message});
  } catch(err) {
    AS.className='status-badge err';AS.textContent='Init error: '+err.message;
  }
})();
</script></body></html>"""


async def install_page(request: Request) -> HTMLResponse:
    """Serves the one-click MCP install page."""
    return HTMLResponse(INSTALL_UI)


async def install_auth_config(request: Request) -> JSONResponse:
    """Return Cognito OAuth metadata for the install page PKCE flow."""
    host = request.headers.get(
        "x-opm-public-host",
        request.headers.get("x-forwarded-host", request.headers.get("host", DOMAIN_NAME)),
    )
    proto = "http" if host in {"localhost", "127.0.0.1"} else "https"
    origin = f"{proto}://{host}"
    auth_base = COGNITO_DOMAIN or (
        f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
    )
    if not auth_base.startswith("http"):
        auth_base = f"https://{auth_base}"
    return JSONResponse({
        "client_id": COGNITO_APP_CLIENT_ID,
        "redirect_uri": f"{origin}/install",
        "authorization_endpoint": f"{auth_base}/oauth2/authorize",
        "token_endpoint": f"{auth_base}/oauth2/token",
        "logout_endpoint": f"{auth_base}/logout",
        "logout_uri": f"{origin}/install",
        "scope": (
            f"openid email {COGNITO_RESOURCE_SERVER_IDENTIFIER}/"
            f"{COGNITO_REQUIRED_SCOPE}"
        ),
    })


async def install_generate_key(request: Request) -> JSONResponse:
    """Generate a persistent API key for MCP client authentication."""
    try:
        ctx = await _resolve_ui_context(request)
    except AuthenticationError as e:
        return JSONResponse({"error": "unauthorized", "message": sanitize_evidence(str(e))}, status_code=401)
    except AuthorizationError as e:
        return JSONResponse({"error": "forbidden", "message": sanitize_evidence(str(e))}, status_code=403)

    try:
        data = await request.json()
        label = data.get("label", "default").strip() or "default"

        # Generate key: opm_ + 32 hex chars (128-bit entropy)
        raw_key = "opm_" + stdlib_secrets.token_hex(16)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_prefix = raw_key[:12]
        key_id = f"key_{uuid.uuid4().hex[:16]}"

        repo.create_api_key(
            key_id=key_id,
            key_hash=key_hash,
            key_prefix=key_prefix,
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            label=label,
        )

        return JSONResponse({
            "api_key": raw_key,
            "key_id": key_id,
            "key_prefix": key_prefix,
            "label": label,
            "message": "API key generated. Store it securely — it cannot be retrieved again.",
        })
    except Exception as exc:  # noqa: BLE001
        logger.error("API key generation failed: %s", sanitize_evidence(str(exc)))
        return JSONResponse({"error": sanitize_evidence(str(exc))}, status_code=400)


async def install_list_keys(request: Request) -> JSONResponse:
    """List API keys for the authenticated user (prefix only, never full key)."""
    try:
        ctx = await _resolve_ui_context(request)
    except AuthenticationError as e:
        return JSONResponse({"error": "unauthorized", "message": sanitize_evidence(str(e))}, status_code=401)
    except AuthorizationError as e:
        return JSONResponse({"error": "forbidden", "message": sanitize_evidence(str(e))}, status_code=403)

    keys = repo.list_api_keys(company_id=ctx.company_id, user_id=ctx.user_id)
    return JSONResponse(keys)


async def install_revoke_key(request: Request) -> JSONResponse:
    """Revoke an API key so it can no longer authenticate MCP requests."""
    try:
        ctx = await _resolve_ui_context(request)
    except AuthenticationError as e:
        return JSONResponse({"error": "unauthorized", "message": sanitize_evidence(str(e))}, status_code=401)
    except AuthorizationError as e:
        return JSONResponse({"error": "forbidden", "message": sanitize_evidence(str(e))}, status_code=403)

    try:
        data = await request.json()
        key_id = data.get("key_id", "").strip()
        if not key_id:
            return JSONResponse({"error": "key_id is required"}, status_code=400)
        revoked = repo.revoke_api_key(key_id=key_id, company_id=ctx.company_id)
        if not revoked:
            return JSONResponse({"error": "Key not found or already revoked"}, status_code=404)
        return JSONResponse({"status": "revoked", "key_id": key_id})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": sanitize_evidence(str(exc))}, status_code=400)


async def install_list_downstreams(request: Request) -> JSONResponse:
    """List registered downstream services with a lightweight readiness probe."""
    try:
        ctx = await _resolve_ui_context(request)
    except AuthenticationError as e:
        return JSONResponse({"error": "unauthorized", "message": sanitize_evidence(str(e))}, status_code=401)
    except AuthorizationError as e:
        return JSONResponse({"error": "forbidden", "message": sanitize_evidence(str(e))}, status_code=403)

    set_current_context(ctx)
    try:
        servers = get_default_service().list_downstream_mcps()
        payload = []
        for server in servers:
            ready = False
            state = "registered, awaiting connection check"
            tool_count = len(server.available_tools)
            try:
                probe = DownstreamDispatcher(repo).probe(server)
                tool_count = len(probe.get("tools", []))
                state = f"connected and ready: {tool_count} tool(s) discovered"
                ready = True
            except Exception as exc:  # noqa: BLE001 - status endpoint reports probe failures
                state = f"not ready: {sanitize_evidence(str(exc))}"
            payload.append(
                {
                    "server_id": server.server_id,
                    "endpoint": server.endpoint,
                    "transport": server.transport.value,
                    "tool_count": tool_count,
                    "ready": ready,
                    "state": state,
                    "created_at": server.created_at,
                    "updated_at": server.updated_at,
                }
            )
        return JSONResponse({"servers": payload})
    finally:
        set_current_context(None)


async def install_delete_downstream(request: Request) -> JSONResponse:
    """Delete a registered downstream service for the authenticated company."""
    try:
        ctx = await _resolve_ui_context(request)
    except AuthenticationError as e:
        return JSONResponse({"error": "unauthorized", "message": sanitize_evidence(str(e))}, status_code=401)
    except AuthorizationError as e:
        return JSONResponse({"error": "forbidden", "message": sanitize_evidence(str(e))}, status_code=403)

    server_id = request.path_params.get("server_id", "").strip()
    if not server_id:
        return JSONResponse({"error": "server_id is required"}, status_code=400)
    try:
        get_default_service().auth_resolver.require_role(
            ctx,
            [RoleType.OWNER, RoleType.REVIEWER],
        )
    except AuthorizationError as e:
        return JSONResponse({"error": "forbidden", "message": sanitize_evidence(str(e))}, status_code=403)
    deleted = repo.delete_downstream_mcp(
        company_id=ctx.company_id,
        server_id=server_id,
    )
    if not deleted:
        return JSONResponse({"error": "Service not found"}, status_code=404)
    return JSONResponse({"status": "deleted", "server_id": server_id})


def _resolve_api_key_context(raw_token: str) -> RequestContext | None:
    """Resolve a RequestContext from an OPM API key (opm_...) instead of a Cognito JWT."""
    key_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    key_record = repo.get_api_key_by_hash(key_hash)
    if not key_record:
        return None

    company = repo.get_company(key_record["company_id"])
    if not company or company.status != CompanyStatus.ACTIVE:
        return None

    user = repo.get_user(key_record["user_id"])
    if not user or user.status != "active":
        return None

    membership = repo.get_membership(company.company_id, user.user_id)
    if not membership or membership.status != MembershipStatus.ACTIVE:
        return None

    # Update last_used_at asynchronously to avoid blocking the ASGI event loop
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, repo.touch_api_key_usage, key_record["key_id"])
    except RuntimeError:
        try:
            repo.touch_api_key_usage(key_record["key_id"])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to touch API key usage: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to dispatch touch API key usage: %s", exc)

    return RequestContext(
        company_id=company.company_id,
        company_slug=company.company_slug,
        user_id=user.user_id,
        email=user.email,
        role=membership.role,
        client_agent="api-key",
    )



async def downstream_auth_config(request: Request) -> JSONResponse:
    """Return public Cognito OAuth metadata needed by the browser PKCE client."""
    host = request.headers.get(
        "x-opm-public-host",
        request.headers.get("x-forwarded-host", request.headers.get("host", DOMAIN_NAME)),
    )
    proto = "http" if host in {"localhost", "127.0.0.1"} else "https"
    origin = f"{proto}://{host}"
    auth_base = COGNITO_DOMAIN or (
        f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
    )
    if not auth_base.startswith("http"):
        auth_base = f"https://{auth_base}"
    return JSONResponse({
        "client_id": COGNITO_APP_CLIENT_ID,
        "redirect_uri": f"{origin}/admin/downstreams",
        "authorization_endpoint": f"{auth_base}/oauth2/authorize",
        "token_endpoint": f"{auth_base}/oauth2/token",
        "logout_endpoint": f"{auth_base}/logout",
        "logout_uri": f"{origin}/admin/downstreams",
        "scope": (
            f"openid email {COGNITO_RESOURCE_SERVER_IDENTIFIER}/"
            f"{COGNITO_REQUIRED_SCOPE}"
        ),
    })


async def downstream_register(request: Request) -> JSONResponse:
    try:
        ctx = await _resolve_ui_context(request)
    except AuthenticationError as e:
        return JSONResponse({"error": "unauthorized", "message": sanitize_evidence(str(e))}, status_code=401)
    except AuthorizationError as e:
        return JSONResponse({"error": "forbidden", "message": sanitize_evidence(str(e))}, status_code=403)

    set_current_context(ctx)
    try:
        data = await request.json()
        secret_ref = data.get("secret_ref")
        if data.get("transport") == "odoo_xmlrpc" and data.get("username") and data.get("password"):
            import boto3

            secret_name = f"opm/downstream/{ctx.company_slug}/{data['server_id'].strip()}"
            secret_value = {
                "database": data.get("database", "").strip(),
                "username": data["username"].strip(),
                "password": data["password"],
            }
            secrets = boto3.client("secretsmanager", region_name=AWS_REGION)
            try:
                secret_ref = secrets.create_secret(
                    Name=secret_name,
                    SecretString=json.dumps(secret_value),
                    Description="OPM downstream credentials",
                )["ARN"]
            except secrets.exceptions.ResourceExistsException:
                secret_ref = secrets.update_secret(
                    SecretId=secret_name,
                    SecretString=json.dumps(secret_value),
                )["ARN"]
        if not secret_ref:
            return JSONResponse({"error": "Provide Odoo credentials or an existing secret_ref."}, status_code=400)
        result = get_default_service().register_downstream_mcp(
            server_id=data["server_id"], endpoint=data["endpoint"],
            transport=data.get("transport", "streamable_http"),
            secret_ref=secret_ref, available_tools=data.get("available_tools"),
        )
        return JSONResponse(result.model_dump())
    except Exception as exc:  # noqa: BLE001
        logger.error("Downstream registration failed: %s", sanitize_evidence(str(exc)))
        return JSONResponse({"error": sanitize_evidence(str(exc))}, status_code=400)
    finally:
        set_current_context(None)


async def downstream_status(request: Request) -> HTMLResponse:
    try:
        ctx = await _resolve_ui_context(request)
    except AuthenticationError as e:
        return HTMLResponse(f"<p class='bad'>Authentication required: {sanitize_evidence(str(e))}</p>", status_code=401)
    except AuthorizationError as e:
        return HTMLResponse(f"<p class='bad'>Authorization error: {sanitize_evidence(str(e))}</p>", status_code=403)

    set_current_context(ctx)
    try:
        servers = get_default_service().list_downstream_mcps()
        cards = []
        for server in servers:
            state = "registered, awaiting connection check"
            cls = ""
            try:
                probe = DownstreamDispatcher(repo).probe(server)
                state = f"connected and ready: {len(probe.get('tools', []))} tool(s) discovered"
                cls = "ready"
            except Exception as exc:  # noqa: BLE001 - status endpoint must report probe failures
                state = f"not ready: {sanitize_evidence(str(exc))}"
                cls = "bad"
            cards.append(
                f"<div class='server'><b>{html.escape(server.server_id)}</b> "
                f"<span class='{html.escape(cls)}'>{html.escape(state)}</span><br>"
                f"<small>{html.escape(server.transport.value)} · {html.escape(server.endpoint)}</small></div>"
            )
        return HTMLResponse("".join(cards) or "<p>No downstreams registered.</p>")
    finally:
        set_current_context(None)


async def _resolve_ui_context(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise AuthenticationError("Missing Bearer token")
    token = auth[7:].strip()
    if not token:
        raise AuthenticationError("Bearer token cannot be empty")
    claims = token_verifier.verify_token(token)
    company_slug = request.query_params.get("company", "").strip()
    if not company_slug:
        raise AuthorizationError("Missing required query parameter '?company='")
    return resolve_authenticated_context(claims=claims, company_slug=company_slug, repo=repo, client_agent="opm-ui")


async def oauth_discovery(request: Request) -> JSONResponse:
    """RFC 8414 OAuth 2.0 Authorization Server Metadata."""
    host_domain = request.headers.get("host", DOMAIN_NAME or "localhost")
    issuer = (
        f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
        if COGNITO_USER_POOL_ID
        else f"https://{host_domain}"
    )

    # Determine real OAuth endpoints (Cognito Managed Login domain if configured)
    if COGNITO_DOMAIN:
        auth_base = (
            COGNITO_DOMAIN
            if COGNITO_DOMAIN.startswith("http")
            else f"https://{COGNITO_DOMAIN}"
        )
    elif COGNITO_USER_POOL_ID:
        auth_base = issuer
    else:
        auth_base = f"https://{host_domain}"

    metadata = {
        "issuer": issuer,
        "authorization_endpoint": f"{auth_base}/oauth2/authorize",
        "token_endpoint": f"{auth_base}/oauth2/token",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
        "scopes_supported": ["openid", "email", COGNITO_REQUIRED_SCOPE],
        "code_challenge_methods_supported": ["S256"],
    }
    return JSONResponse(metadata)


async def oauth_protected_resource(request: Request) -> JSONResponse:
    """RFC 9728 metadata describing the protected MCP resource."""
    host_domain = request.headers.get(
        "x-opm-public-host",
        request.headers.get(
            "x-forwarded-host",
        request.headers.get("host", DOMAIN_NAME or "localhost"),
        ),
    )
    resource = MCP_RESOURCE_URL or f"https://{host_domain}"
    if COGNITO_USER_POOL_ID:
        authorization_server = (
            f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
            f"{COGNITO_USER_POOL_ID}"
        )
    else:
        authorization_server = resource
    return JSONResponse(
        {
            "resource": resource,
            "authorization_servers": [authorization_server],
            "scopes_supported": ["openid", "email", COGNITO_REQUIRED_SCOPE],
        }
    )


class CompanyMCPHandler:
    """
    Direct ASGI delegation endpoint for /companies/{company_slug}/mcp.
    Enforces Cognito OAuth 2.0 Bearer token validation and tenant isolation,
    then forwards directly to FastMCP's Streamable HTTP app without response buffering.
    Preserves streaming, disconnects, status codes, and client Accept headers.
    """

    def __init__(self, target_app):
        self.target_app = target_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return

        path_params = scope.get("path_params", {})
        company_slug = path_params.get("company_slug")
        if not company_slug:
            res = JSONResponse({"error": "missing_company_slug"}, status_code=400)
            await res(scope, receive, send)
            return

        # Extract headers from ASGI scope
        raw_headers = scope.get("headers", [])
        headers = {k.lower(): v.decode("latin1") for k, v in raw_headers}
        auth_header = headers.get(b"authorization", "")
        api_key_header = (
            headers.get(b"x-api-key", "")
            or headers.get(b"x-consumer-api-key", "")
        )
        client_agent = headers.get(b"user-agent", "unknown-mcp-client")

        # Determine token and whether it is an API key
        token = ""
        is_api_key = False
        if api_key_header:
            token = api_key_header.strip()
            is_api_key = True
        elif auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            if token.startswith("opm_"):
                is_api_key = True

        if not token:
            res = JSONResponse(
                {"error": "unauthorized", "message": "Missing Bearer token or API key."},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="ProcessMemory"'},
            )
            await res(scope, receive, send)
            return

        # API key authentication (persistent keys starting with opm_ or sent via X-API-Key)
        if is_api_key:
            req_ctx = _resolve_api_key_context(token)
            if not req_ctx:
                res = JSONResponse(
                    {"error": "unauthorized", "message": "Invalid or revoked API key."},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                )
                await res(scope, receive, send)
                return
            if req_ctx.company_slug != company_slug:
                res = JSONResponse(
                    {"error": "forbidden", "message": "API key is not authorized for this company."},
                    status_code=403,
                )
                await res(scope, receive, send)
                return
        else:
            try:
                claims = token_verifier.verify_token(token)
                req_ctx = resolve_authenticated_context(
                    claims=claims,
                    company_slug=company_slug,
                    repo=repo,
                    client_agent=client_agent,
                )
            except AuthenticationError as e:
                res = JSONResponse(
                    {"error": "unauthorized", "message": sanitize_evidence(str(e))},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                )
                await res(scope, receive, send)
                return
            except AuthorizationError as e:
                res = JSONResponse(
                    {"error": "forbidden", "message": sanitize_evidence(str(e))},
                    status_code=403,
                )
                await res(scope, receive, send)
                return

        # Forward directly into FastMCP Streamable HTTP app
        target_scope = dict(scope)
        target_scope["path"] = "/mcp"
        target_scope["raw_path"] = b"/mcp"

        set_current_context(req_ctx)
        try:
            await self.target_app(target_scope, receive, send)
        finally:
            set_current_context(None)


@contextlib.asynccontextmanager
async def app_lifespan(app: Starlette):
    async with fastmcp_http_app.router.lifespan_context(app):
        yield


def create_app() -> Starlette:
    """Creates the ASGI application instance."""
    app = Starlette(
        debug=False,
        lifespan=app_lifespan,
        routes=[
            Route("/health/live", health_live, methods=["GET"]),
            Route("/health/ready", health_ready, methods=["GET"]),
            Route("/admin/downstreams", downstream_ui, methods=["GET"]),
            Route("/admin/downstreams/auth-config", downstream_auth_config, methods=["GET"]),
            Route("/admin/downstreams/register", downstream_register, methods=["POST"]),
            Route("/admin/downstreams/status", downstream_status, methods=["GET"]),
            Route("/install", install_page, methods=["GET"]),
            Route("/install/auth-config", install_auth_config, methods=["GET"]),
            Route("/install/api-key", install_generate_key, methods=["POST"]),
            Route("/install/api-keys", install_list_keys, methods=["GET"]),
            Route("/install/api-key/revoke", install_revoke_key, methods=["POST"]),
            Route("/install/downstreams", install_list_downstreams, methods=["GET"]),
            Route(
                "/install/downstreams/{server_id}",
                install_delete_downstream,
                methods=["DELETE"],
            ),
            Route(
                "/.well-known/oauth-authorization-server",
                oauth_discovery,
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-protected-resource",
                oauth_protected_resource,
                methods=["GET"],
            ),
            Route(
                "/.well-known/openid-configuration", oauth_discovery, methods=["GET"]
            ),
            Route(
                "/companies/{company_slug}/mcp",
                CompanyMCPHandler(fastmcp_http_app),
                methods=["GET", "POST", "HEAD", "OPTIONS"],
            ),
        ],
    )
    return app


app = create_app()
