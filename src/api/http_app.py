import contextlib
import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from server import mcp
from src.api.auth import (
    AuthenticationError,
    AuthorizationError,
    CognitoTokenVerifier,
    resolve_authenticated_context,
)
from src.api.auth_context import set_current_context
from src.config import (
    COGNITO_DOMAIN,
    COGNITO_REGION,
    COGNITO_REQUIRED_SCOPE,
    COGNITO_USER_POOL_ID,
    DATA_DIR,
    DOMAIN_NAME,
)
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
        "x-forwarded-host",
        request.headers.get("host", DOMAIN_NAME or "localhost"),
    )
    resource = f"https://{host_domain}"
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
