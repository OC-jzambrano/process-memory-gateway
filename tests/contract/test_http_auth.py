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
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


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
