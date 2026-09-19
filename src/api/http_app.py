import contextlib
import html
import json
import logging

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
if (btnLogout) btnLogout.onclick = () => {
  sessionStorage.removeItem('opmAccessToken');
  authStatus.className = 'status-badge pending';
  authStatus.textContent = 'Signed out';
  load();
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
        client_agent = headers.get(b"user-agent", "unknown-mcp-client")

        if not auth_header or not auth_header.lower().startswith("bearer "):
            res = JSONResponse(
                {"error": "unauthorized", "message": "Missing Bearer token."},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="ProcessMemory"'},
            )
            await res(scope, receive, send)
            return

        token = auth_header[7:].strip()
        if not token:
            res = JSONResponse(
                {"error": "unauthorized", "message": "Bearer token cannot be empty."},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
            await res(scope, receive, send)
            return

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
