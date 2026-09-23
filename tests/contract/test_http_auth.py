import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from src.api.http_app import app, token_verifier
from src.models.enums import CompanyStatus, MembershipStatus, RoleType
from src.models.schemas import Company, Membership, User
from src.storage.repository import MemoryRepository


@pytest.fixture(scope="module")
def rsa_keypair():
    """Generates an in-memory RSA keypair for JWT signing and JWKS mocking."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    # Export public numbers for JWK
    pub_numbers = public_key.public_numbers()

    def to_b64(val: int, length: int = 256) -> str:
        import base64

        return (
            base64.urlsafe_b64encode(val.to_bytes(length, byteorder="big"))
            .decode("utf-8")
            .rstrip("=")
        )

    jwk = {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": "test-key-1",
        "n": to_b64(pub_numbers.n, 256),
        "e": to_b64(pub_numbers.e, 3),
    }

    return private_key, jwk


@pytest.fixture(scope="module")
def auth_setup(rsa_keypair, tmp_path_factory):
    private_key, jwk = rsa_keypair

    # Configure token verifier to use test JWKS
    token_verifier._jwks_override = {"keys": [jwk]}
    token_verifier.expected_issuer = (
        "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_TestPool"
    )
    token_verifier.app_client_id = "test-client-id"
    token_verifier.required_scope = "mcp:tools"

    # Setup database with test tenants and users
    db_dir = tmp_path_factory.mktemp("auth_test")
    db_path = db_dir / "auth_test.db"
    test_repo = MemoryRepository(db_path=db_path)

    # Company A (Active)
    test_repo.upsert_company(
        Company(
            company_id="company_a",
            company_slug="company_a",
            name="Company A",
            status=CompanyStatus.ACTIVE,
        )
    )

    # Company B (Active)
    test_repo.upsert_company(
        Company(
            company_id="company_b",
            company_slug="company_b",
            name="Company B",
            status=CompanyStatus.ACTIVE,
        )
    )

    # User 1 (Active member of Company A only)
    test_repo.upsert_user(
        User(
            user_id="user_alice",
            email="alice@company-a.com",
            name="Alice",
            cognito_sub="sub-alice-12345",
            status="active",
        )
    )
    test_repo.upsert_membership(
        Membership(
            membership_id="mem_alice_a",
            company_id="company_a",
            user_id="user_alice",
            role=RoleType.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )

    # User 2 (Suspended account)
    test_repo.upsert_user(
        User(
            user_id="user_bob",
            email="bob@company-a.com",
            name="Bob",
            cognito_sub="sub-bob-67890",
            status="suspended",
        )
    )
    test_repo.upsert_membership(
        Membership(
            membership_id="mem_bob_a",
            company_id="company_a",
            user_id="user_bob",
            role=RoleType.MEMBER,
            status=MembershipStatus.ACTIVE,
        )
    )

    # Patch global repo for http_app
    import src.api.http_app as http_module

    old_repo = http_module.repo
    http_module.repo = test_repo

    with TestClient(app) as client:
        yield {
            "client": client,
            "private_key": private_key,
            "jwk": jwk,
            "repo": test_repo,
        }

    http_module.repo = old_repo
    token_verifier._jwks_override = None


def make_token(
    private_key,
    sub="sub-alice-12345",
    username="alice",
    iss="https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_TestPool",
    client_id="test-client-id",
    scope="openid email mcp:tools",
    exp_offset=3600,
    kid="test-key-1",
    aud=None,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "username": username,
        "iss": iss,
        "client_id": client_id,
        "token_use": "access",
        "scope": scope,
        "iat": now,
        "exp": now + exp_offset,
    }
    if aud is not None:
        payload["aud"] = aud
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


@pytest.mark.parametrize(
    "audience,client_id,accepted",
    [
        ("https://public.example/companies/company_a/mcp", "test-client-id", True),
        ("https://other.example/mcp", "test-client-id", False),
        ("https://public.example/companies/company_b/mcp", "test-client-id", False),
        ("https://public.example/companies/company_a/mcp", "other-client", False),
    ],
)
def test_resource_bound_signed_token(auth_setup, monkeypatch, audience, client_id, accepted):
    from src.api.auth import AuthenticationError

    monkeypatch.setattr(
        token_verifier,
        "protected_resource_url",
        "https://public.example/companies/company_a/mcp",
    )
    token = make_token(auth_setup["private_key"], aud=audience, client_id=client_id)
    if accepted:
        assert token_verifier.verify_token(token)["aud"] == audience
    else:
        with pytest.raises(AuthenticationError):
            token_verifier.verify_token(token)


def test_health_endpoints(auth_setup):
    client = auth_setup["client"]
    res_live = client.get("/health/live")
    assert res_live.status_code == 200
    assert res_live.json() == {"status": "alive"}

    res_ready = client.get("/health/ready")
    assert res_ready.status_code == 200
    assert res_ready.json()["status"] == "ready"


def test_oauth_discovery_metadata(auth_setup):
    client = auth_setup["client"]
    res = client.get("/.well-known/oauth-authorization-server")
    assert res.status_code == 200
    data = res.json()
    assert "authorization_endpoint" in data
    assert "token_endpoint" in data
    assert "jwks_uri" in data
    assert "mcp:tools" in data["scopes_supported"]


def test_oauth_protected_resource_metadata(auth_setup):
    client = auth_setup["client"]
    res = client.get("/.well-known/oauth-protected-resource")
    assert res.status_code == 200
    data = res.json()
    assert data["resource"].startswith("https://")
    assert data["authorization_servers"]
    assert "mcp:tools" in data["scopes_supported"]

    forwarded = client.get(
        "/.well-known/oauth-protected-resource",
        headers={"x-forwarded-host": "public.example.com"},
    )
    assert forwarded.json()["resource"] == "https://public.example.com"


def test_request_missing_token_returns_401(auth_setup):
    client = auth_setup["client"]
    res = client.post("/companies/company_a/mcp", json={})
    assert res.status_code == 401
    assert res.json()["error"] == "unauthorized"
    # An empty Bearer token must also be rejected.
    res_auth = client.post(
        "/companies/company_a/mcp", headers={"Authorization": "Bearer "}, json={}
    )
    assert res_auth.status_code == 401
    assert res_auth.json()["error"] == "unauthorized"


def test_forged_token_signature_returns_401(auth_setup):
    client = auth_setup["client"]
    # Sign with a different rogue key
    rogue_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = make_token(rogue_key)

    res = client.post(
        "/companies/company_a/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert res.status_code == 401
    assert "Invalid token signature" in res.json()["message"]


def test_expired_token_returns_401(auth_setup):
    client = auth_setup["client"]
    token = make_token(auth_setup["private_key"], exp_offset=-60)  # Expired 60s ago

    res = client.post(
        "/companies/company_a/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert res.status_code == 401
    assert "Token has expired" in res.json()["message"]


def test_wrong_client_id_returns_401(auth_setup):
    client = auth_setup["client"]
    token = make_token(auth_setup["private_key"], client_id="wrong-client")

    res = client.post(
        "/companies/company_a/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert res.status_code == 401
    assert "client_id mismatch" in res.json()["message"]


def test_missing_required_scope_returns_401(auth_setup):
    client = auth_setup["client"]
    token = make_token(
        auth_setup["private_key"], scope="openid email"
    )  # Missing mcp:tools

    res = client.post(
        "/companies/company_a/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert res.status_code == 401
    assert "missing required scope" in res.json()["message"].lower()


def test_unprovisioned_user_returns_403(auth_setup):
    client = auth_setup["client"]
    token = make_token(auth_setup["private_key"], sub="sub-unregistered-999")

    res = client.post(
        "/companies/company_a/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert res.status_code == 403
    assert "not provisioned" in res.json()["message"]


def test_disabled_account_returns_403(auth_setup):
    client = auth_setup["client"]
    token = make_token(auth_setup["private_key"], sub="sub-bob-67890")

    res = client.post(
        "/companies/company_a/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert res.status_code == 403
    assert "disabled or suspended" in res.json()["message"]


def test_tenant_mismatch_returns_403(auth_setup):
    client = auth_setup["client"]
    # Alice is member of Company A, attempting to access Company B
    token = make_token(auth_setup["private_key"], sub="sub-alice-12345")

    res = client.post(
        "/companies/company_b/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert res.status_code == 403
    assert "not a member of company 'company_b'" in res.json()["message"]


def test_valid_token_and_tenant_succeeds(auth_setup):
    client = auth_setup["client"]
    token = make_token(auth_setup["private_key"], sub="sub-alice-12345")

    # FastMCP Streamable HTTP expects client to accept application/json and text/event-stream
    res = client.post(
        "/companies/company_a/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")


def test_incompatible_accept_header_returns_406(auth_setup):
    """Preserved Accept headers: client requesting incompatible media type must receive 406."""
    client = auth_setup["client"]
    token = make_token(auth_setup["private_key"], sub="sub-alice-12345")

    res = client.post(
        "/companies/company_a/mcp",
        headers={"Authorization": f"Bearer {token}", "Accept": "text/plain"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert res.status_code == 406


def test_health_ready_probe_503_does_not_leak_paths(auth_setup, monkeypatch):
    """503 readiness failure must never leak internal filesystem paths or raw exceptions."""
    import src.api.http_app as http_mod

    client = auth_setup["client"]

    # Point to nonexistent DB path to trigger error
    class BrokenRepo:
        db_path = "/nonexistent/private/path/db.sqlite"

    monkeypatch.setattr(http_mod, "repo", BrokenRepo())
    res = client.get("/health/ready")
    assert res.status_code == 503
    data = res.json()
    assert data["status"] == "not_ready"
    # Ensure no path fragments leaked
    res_text = res.text.lower()
    assert "nonexistent" not in res_text
    assert "private" not in res_text
    assert "sqlite" not in res_text


def test_install_page_loads(auth_setup):
    """Verify /install serves HTML with config for Antigravity, Claude, and Codex."""
    client = auth_setup["client"]
    res = client.get("/install")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    text = res.text
    assert "Process Memory Gateway" in text
    assert "Antigravity Desktop" in text
    assert "Antigravity CLI" in text
    assert "Claude Desktop" in text
    assert "Codex" in text
    assert "mcp_config.json" in text
    assert ".mcp.json" in text
    assert "claude_desktop_config.json" in text


def test_install_auth_config(auth_setup):
    """Verify /install/auth-config returns valid OAuth/PKCE discovery parameters."""
    client = auth_setup["client"]
    res = client.get("/install/auth-config")
    assert res.status_code == 200
    data = res.json()
    assert "client_id" in data
    assert "redirect_uri" in data
    assert data["redirect_uri"].endswith("/install")
    assert "authorization_endpoint" in data
    assert "token_endpoint" in data


def test_install_api_key_lifecycle(auth_setup):
    """Test generating, listing, authenticating with, and revoking API keys."""
    client = auth_setup["client"]
    token = make_token(auth_setup["private_key"], sub="sub-alice-12345")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Unauthenticated request should fail
    resp = client.post("/install/api-key?company=company_a", json={"label": "test-key"})
    assert resp.status_code == 401

    # 2. Authenticated request generates an API key
    resp = client.post(
        "/install/api-key?company=company_a",
        json={"label": "test-key"},
        headers=headers,
    )
    assert resp.status_code == 200
    key_data = resp.json()
    assert "api_key" in key_data
    api_key = key_data["api_key"]
    assert api_key.startswith("opm_")
    key_id = key_data["key_id"]
    assert key_data["label"] == "test-key"

    # 3. List keys for the user
    resp = client.get("/install/api-keys?company=company_a", headers=headers)
    assert resp.status_code == 200
    keys = resp.json()
    assert len(keys) >= 1
    found = [k for k in keys if k["key_id"] == key_id]
    assert len(found) == 1
    assert found[0]["status"] == "active"
    assert found[0]["label"] == "test-key"
    assert "api_key" not in found[0]

    # 4. Use the API key against the MCP endpoint
    mcp_headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json, text/event-stream",
    }
    mcp_resp = client.post(
        "/companies/company_a/mcp",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert mcp_resp.status_code == 200

    # 5. Using key against wrong company should be 403 Forbidden
    mcp_resp_wrong = client.post(
        "/companies/company_b/mcp",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert mcp_resp_wrong.status_code == 403

    # 6. Revoke the key
    revoke_resp = client.post(
        "/install/api-key/revoke?company=company_a",
        json={"key_id": key_id},
        headers=headers,
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["status"] == "revoked"

    # 7. Use revoked key against MCP endpoint -> 401
    mcp_resp_revoked = client.post(
        "/companies/company_a/mcp",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert mcp_resp_revoked.status_code == 401


def test_invalid_api_key(auth_setup):
    """Verify invalid opm_ key returns 401."""
    client = auth_setup["client"]
    headers = {"Authorization": "Bearer opm_invalidkey1234567890"}
    resp = client.post(
        "/companies/company_a/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert resp.status_code == 401

