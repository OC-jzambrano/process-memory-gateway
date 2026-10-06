import hashlib
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

import src.api.http_app as http_module
from src.api.http_app import app, token_verifier
from src.models.enums import (
    CompanyStatus,
    EnforcementMode,
    MembershipStatus,
    RoleType,
    RuleStatus,
    RuleType,
    Severity,
)
from src.models.schemas import CanonicalRule, Company, Membership, User
from src.storage.repository import MemoryRepository


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
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
        "kid": "rules-test-key",
        "n": to_b64(pub_numbers.n, 256),
        "e": to_b64(pub_numbers.e, 3),
    }
    return private_key, jwk


@pytest.fixture(scope="module")
def rules_api_setup(rsa_keypair, tmp_path_factory):
    private_key, jwk = rsa_keypair
    token_verifier._jwks_override = {"keys": [jwk]}
    token_verifier.expected_issuer = "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_TestPool"
    token_verifier.app_client_id = "test-client-id"
    token_verifier.required_scope = "mcp:tools"

    db_dir = tmp_path_factory.mktemp("rules_test")
    db_path = db_dir / "rules_api_test.db"
    test_repo = MemoryRepository(db_path=db_path)

    # 1. Company A
    test_repo.upsert_company(
        Company(
            company_id="company_a",
            company_slug="comp_a",
            name="Company Alpha",
            status=CompanyStatus.ACTIVE,
        )
    )

    # 2. Company B
    test_repo.upsert_company(
        Company(
            company_id="company_b",
            company_slug="comp_b",
            name="Company Beta",
            status=CompanyStatus.ACTIVE,
        )
    )

    # 3. Owner user in Company A
    test_repo.upsert_user(
        User(
            user_id="user_owner",
            email="owner@comp-a.com",
            name="Alice Owner",
            cognito_sub="sub-owner-123",
            status="active",
        )
    )
    test_repo.upsert_membership(
        Membership(
            membership_id="mem_owner",
            company_id="company_a",
            user_id="user_owner",
            role=RoleType.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )

    # 4. Member user in Company A (without delete privileges)
    test_repo.upsert_user(
        User(
            user_id="user_member",
            email="member@comp-a.com",
            name="Bob Member",
            cognito_sub="sub-member-456",
            status="active",
        )
    )
    test_repo.upsert_membership(
        Membership(
            membership_id="mem_member",
            company_id="company_a",
            user_id="user_member",
            role=RoleType.MEMBER,
            status=MembershipStatus.ACTIVE,
        )
    )

    # 5. Generate API Key for Owner
    raw_key = "opm_" + "a" * 32
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    test_repo.create_api_key(
        key_id="key_owner_test",
        key_hash=key_hash,
        key_prefix=raw_key[:12],
        company_id="company_a",
        user_id="user_owner",
        label="Owner Test Key",
    )

    # 6. Generate API Key for Member
    raw_member_key = "opm_" + "b" * 32
    member_key_hash = hashlib.sha256(raw_member_key.encode()).hexdigest()
    test_repo.create_api_key(
        key_id="key_member_test",
        key_hash=member_key_hash,
        key_prefix=raw_member_key[:12],
        company_id="company_a",
        user_id="user_member",
        label="Member Test Key",
    )

    old_repo = http_module.repo
    http_module.repo = test_repo

    with TestClient(app) as client:
        yield {
            "client": client,
            "private_key": private_key,
            "repo": test_repo,
            "owner_key": raw_key,
            "member_key": raw_member_key,
        }

    http_module.repo = old_repo
    token_verifier._jwks_override = None


def make_jwt(private_key, sub="sub-owner-123", username="owner", scope="openid email mcp:tools"):
    now = int(time.time())
    payload = {
        "sub": sub,
        "username": username,
        "iss": "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_TestPool",
        "client_id": "test-client-id",
        "token_use": "access",
        "scope": scope,
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "rules-test-key"})


def create_test_rule(repo, rule_id, client_id="company_a", rule_text="Task names must start with [DEV]"):
    rule = CanonicalRule(
        rule_id=rule_id,
        client_id=client_id,
        rule_text=rule_text,
        rule_type=RuleType.NAMING_CONVENTION,
        severity=Severity.INFO,
        enforcement_mode=EnforcementMode.ADVISORY,
        version=1,
        status=RuleStatus.APPROVED,
        approved_by="owner",
    )
    repo.create_canonical_rule(rule)
    return rule


def test_delete_canonical_rule_success_via_api_key(rules_api_setup):
    client = rules_api_setup["client"]
    repo = rules_api_setup["repo"]
    owner_key = rules_api_setup["owner_key"]

    create_test_rule(repo, "rule_to_delete_1", client_id="company_a")
    assert repo.get_rule("rule_to_delete_1", "company_a") is not None

    headers = {"Authorization": f"Bearer {owner_key}"}
    resp = client.delete("/companies/comp_a/rules/rule_to_delete_1?notes=Testing+purge", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deleted"
    assert data["rule_id"] == "rule_to_delete_1"
    assert data["company_slug"] == "comp_a"

    # Confirm rule is removed from repository
    assert repo.get_rule("rule_to_delete_1", "company_a") is None

    # Confirm audit event was recorded
    events = repo.get_review_events(client_id="company_a")
    delete_events = [e for e in events if e.rule_id == "rule_to_delete_1"]
    assert len(delete_events) == 1
    assert delete_events[0].decision.value == "archive"
    assert "Testing purge" in (delete_events[0].notes or "")


def test_delete_canonical_rule_via_jwt_token(rules_api_setup):
    client = rules_api_setup["client"]
    repo = rules_api_setup["repo"]
    private_key = rules_api_setup["private_key"]

    create_test_rule(repo, "rule_jwt_1", client_id="company_a")
    token = make_jwt(private_key, sub="sub-owner-123")
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.delete("/companies/comp_a/rules/rule_jwt_1", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    assert repo.get_rule("rule_jwt_1", "company_a") is None


def test_delete_canonical_rule_via_custom_header(rules_api_setup):
    client = rules_api_setup["client"]
    repo = rules_api_setup["repo"]
    owner_key = rules_api_setup["owner_key"]

    create_test_rule(repo, "rule_custom_header", client_id="company_a")
    headers = {"x-api-key": owner_key}

    resp = client.delete("/companies/comp_a/rules/rule_custom_header", headers=headers)
    assert resp.status_code == 200
    assert repo.get_rule("rule_custom_header", "company_a") is None


def test_delete_canonical_rule_unauthenticated(rules_api_setup):
    client = rules_api_setup["client"]
    resp = client.delete("/companies/comp_a/rules/some_rule")
    assert resp.status_code == 401


def test_delete_canonical_rule_forbidden_for_member_role(rules_api_setup):
    client = rules_api_setup["client"]
    repo = rules_api_setup["repo"]
    member_key = rules_api_setup["member_key"]

    create_test_rule(repo, "rule_forbidden_1", client_id="company_a")
    headers = {"Authorization": f"Bearer {member_key}"}

    resp = client.delete("/companies/comp_a/rules/rule_forbidden_1", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"
    # Ensure rule was NOT deleted
    assert repo.get_rule("rule_forbidden_1", "company_a") is not None


def test_delete_canonical_rule_not_found(rules_api_setup):
    client = rules_api_setup["client"]
    owner_key = rules_api_setup["owner_key"]
    headers = {"Authorization": f"Bearer {owner_key}"}

    resp = client.delete("/companies/comp_a/rules/rule_nonexistent_xyz", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


def test_delete_canonical_rule_tenant_isolation(rules_api_setup):
    client = rules_api_setup["client"]
    repo = rules_api_setup["repo"]
    owner_key = rules_api_setup["owner_key"]

    # Rule belongs to company B
    create_test_rule(repo, "rule_company_b", client_id="company_b")

    # Company A owner tries to delete Company B's rule via company_a endpoint
    headers = {"Authorization": f"Bearer {owner_key}"}
    resp = client.delete("/companies/comp_a/rules/rule_company_b", headers=headers)
    assert resp.status_code == 404

    # Rule still exists in Company B
    assert repo.get_rule("rule_company_b", "company_b") is not None


def test_batch_delete_canonical_rules_success(rules_api_setup):
    client = rules_api_setup["client"]
    repo = rules_api_setup["repo"]
    owner_key = rules_api_setup["owner_key"]

    create_test_rule(repo, "batch_rule_1", client_id="company_a")
    create_test_rule(repo, "batch_rule_2", client_id="company_a")
    create_test_rule(repo, "batch_rule_3", client_id="company_a")

    headers = {"Authorization": f"Bearer {owner_key}"}
    payload = {
        "rule_ids": ["batch_rule_1", "batch_rule_2"],
        "notes": "Bulk deprecation cleanup",
    }
    resp = client.post("/companies/comp_a/rules/batch-delete", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "completed"
    assert data["deleted_count"] == 2
    assert "batch_rule_1" in data["deleted"]
    assert "batch_rule_2" in data["deleted"]
    assert data["failed_count"] == 0

    # batch_rule_1 and batch_rule_2 deleted; batch_rule_3 still exists
    assert repo.get_rule("batch_rule_1", "company_a") is None
    assert repo.get_rule("batch_rule_2", "company_a") is None
    assert repo.get_rule("batch_rule_3", "company_a") is not None


def test_batch_delete_canonical_rules_partial(rules_api_setup):
    client = rules_api_setup["client"]
    repo = rules_api_setup["repo"]
    owner_key = rules_api_setup["owner_key"]

    create_test_rule(repo, "batch_partial_exist", client_id="company_a")

    headers = {"Authorization": f"Bearer {owner_key}"}
    payload = {
        "rule_ids": ["batch_partial_exist", "batch_nonexistent_99"],
    }
    resp = client.post("/companies/comp_a/rules/batch-delete", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted_count"] == 1
    assert "batch_partial_exist" in data["deleted"]
    assert data["failed_count"] == 1
    assert data["failed"][0]["rule_id"] == "batch_nonexistent_99"


def test_batch_delete_canonical_rules_bad_request(rules_api_setup):
    client = rules_api_setup["client"]
    owner_key = rules_api_setup["owner_key"]
    headers = {"Authorization": f"Bearer {owner_key}"}

    # Empty list
    resp = client.post("/companies/comp_a/rules/batch-delete", json={"rule_ids": []}, headers=headers)
    assert resp.status_code == 400

    # Non-list
    resp = client.post("/companies/comp_a/rules/batch-delete", json={"rule_ids": "not-a-list"}, headers=headers)
    assert resp.status_code == 400
