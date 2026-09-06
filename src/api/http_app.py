import os
import json
import logging
from typing import Optional, Dict, Any
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from src.config import (
    DATA_DIR,
    HOSTED_MODE,
    COGNITO_USER_POOL_ID,
    COGNITO_REGION,
    COGNITO_APP_CLIENT_ID,
    COGNITO_RESOURCE_SERVER_IDENTIFIER,
    COGNITO_REQUIRED_SCOPE
)
from src.storage.db import get_connection
from src.storage.repository import MemoryRepository
from src.api.auth import CognitoTokenVerifier, resolve_authenticated_context, AuthenticationError, AuthorizationError
from src.api.auth_context import set_current_context, RequestContext
from src.models.enums import RoleType
from server import mcp

logger = logging.getLogger(__name__)

# Singletons
repo = MemoryRepository()
token_verifier = CognitoTokenVerifier()
fastmcp_http_app = mcp.streamable_http_app()

async def health_live(request: Request) -> JSONResponse:
    """Liveness probe: verifies process is running."""
    return JSONResponse({"status": "alive"})

async def health_ready(request: Request) -> JSONResponse:
    """Readiness probe: verifies persistent storage mount and database connectivity."""
    errors = []
    
    # 1. Storage mount check
    if not DATA_DIR.exists() or not DATA_DIR.is_dir():
        errors.append(f"Storage mount directory '{DATA_DIR}' missing or not a directory.")

    # 2. Database connectivity check
    try:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1;").fetchone()
        finally:
            conn.close()
    except Exception as e:
        errors.append(f"Database connection error: {str(e)}")

    if errors:
        return JSONResponse(
            {"status": "not_ready", "errors": errors},
            status_code=503
        )

    return JSONResponse({
        "status": "ready",
        "database": "connected",
        "storage": "mounted"
    })

async def oauth_discovery(request: Request) -> JSONResponse:
    """RFC 8414 OAuth 2.0 Authorization Server Metadata."""
    domain_name = request.headers.get("host", "mcp.example.com")
    issuer = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}" if COGNITO_USER_POOL_ID else f"https://{domain_name}"
    
    metadata = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth2/authorize",
        "token_endpoint": f"{issuer}/oauth2/token",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
        "scopes_supported": ["openid", "email", COGNITO_REQUIRED_SCOPE],
        "code_challenge_methods_supported": ["S256"]
    }
    return JSONResponse(metadata)

async def handle_company_mcp(request: Request) -> Response:
    """
    Stateless Streamable HTTP endpoint for /companies/{company_slug}/mcp.
    Enforces Cognito OAuth 2.0 Bearer token validation and tenant isolation.
    """
    company_slug = request.path_params.get("company_slug")
    if not company_slug:
        return JSONResponse({"error": "missing_company_slug"}, status_code=400)

    auth_header = request.headers.get("Authorization", "")
    client_agent = request.headers.get("User-Agent", "unknown-mcp-client")

    req_ctx: Optional[RequestContext] = None

    if not auth_header or not auth_header.lower().startswith("bearer "):
        return JSONResponse(
            {"error": "unauthorized", "message": "Missing Bearer token."},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="ProcessMemory"'}
        )

    token = auth_header[7:].strip()
    if not token:
        return JSONResponse(
            {"error": "unauthorized", "message": "Bearer token cannot be empty."},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}
        )

    try:
        claims = token_verifier.verify_token(token)
        req_ctx = resolve_authenticated_context(
            claims=claims,
            company_slug=company_slug,
            repo=repo,
            client_agent=client_agent
        )
    except AuthenticationError as e:
        return JSONResponse(
            {"error": "unauthorized", "message": str(e)},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}
        )
    except AuthorizationError as e:
        return JSONResponse(
            {"error": "forbidden", "message": str(e)},
            status_code=403
        )

    set_current_context(req_ctx)
    try:
        # Rewrite ASGI scope to route into FastMCP's /mcp handler
        scope = dict(request.scope)
        scope["path"] = "/mcp"
        scope["raw_path"] = b"/mcp"

        # Ensure Accept header accommodates Streamable HTTP if omitted or generic
        raw_headers = list(scope.get("headers", []))
        has_accept = False
        new_headers = []
        for k, v in raw_headers:
            if k.lower() == b"accept":
                has_accept = True
                val = v.decode("latin1", errors="replace")
                if "text/event-stream" not in val or "application/json" not in val:
                    new_headers.append((k, b"application/json, text/event-stream"))
                else:
                    new_headers.append((k, v))
            else:
                new_headers.append((k, v))
        if not has_accept:
            new_headers.append((b"accept", b"application/json, text/event-stream"))
        scope["headers"] = new_headers
        
        async def receive():
            return await request.receive()

        response_started = False
        response_status = 200
        response_headers = []
        response_body = []

        async def send(message):
            nonlocal response_started, response_status, response_headers, response_body
            if message["type"] == "http.response.start":
                response_started = True
                response_status = message["status"]
                response_headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                response_body.append(message.get("body", b""))

        await fastmcp_http_app(scope, receive, send)

        headers_dict = {k.decode("latin1"): v.decode("latin1") for k, v in response_headers}
        return Response(content=b"".join(response_body), status_code=response_status, headers=headers_dict)
    finally:
        set_current_context(None)

def create_app() -> Starlette:
    """Creates the ASGI application instance."""
    app = Starlette(
        debug=False,
        lifespan=fastmcp_http_app.router.lifespan_context,
        routes=[
            Route("/health/live", health_live, methods=["GET"]),
            Route("/health/ready", health_ready, methods=["GET"]),
            Route("/.well-known/oauth-authorization-server", oauth_discovery, methods=["GET"]),
            Route("/.well-known/openid-configuration", oauth_discovery, methods=["GET"]),
            Route("/companies/{company_slug}/mcp", handle_company_mcp, methods=["GET", "POST", "HEAD", "OPTIONS"]),
        ]
    )
    return app

app = create_app()
