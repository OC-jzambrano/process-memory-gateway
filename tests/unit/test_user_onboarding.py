import asyncio
import json

import pytest
from botocore.exceptions import ClientError
from starlette.requests import Request

from src.api.onboarding import InviteError, invite_company_user
from src.models.enums import RoleType
from src.models.schemas import Company, Membership, RequestContext, User
from src.storage.repository import MemoryRepository


class FakeCognito:
    def __init__(self, *, existing_user=None, fail_resend=False):
        self.existing_user = existing_user
        self.fail_resend = fail_resend
        self.created = []
        self.deleted = []

    def admin_get_user(self, **kwargs):
        if self.existing_user is not None:
            return self.existing_user
        raise ClientError(
            {"Error": {"Code": "UserNotFoundException", "Message": "not found"}},
            "AdminGetUser",
        )

    def admin_create_user(self, **kwargs):
        self.created.append(kwargs)
        if kwargs.get("MessageAction") == "RESEND" and self.fail_resend:
            raise ClientError(
                {"Error": {"Code": "LimitExceededException", "Message": "quota"}},
                "AdminCreateUser",
            )
        if kwargs.get("MessageAction") == "SUPPRESS":
            return {
                "User": {
                    "Attributes": [
                        {"Name": "sub", "Value": "cognito-sub-1"},
                        {"Name": "email", "Value": kwargs["Username"]},
                    ]
                }
            }
        return {}

    def admin_delete_user(self, **kwargs):
        self.deleted.append(kwargs)


def _repo(tmp_path):
    repo = MemoryRepository(tmp_path / "onboarding.db")
    repo.upsert_company(
        Company(
            company_id="company-a",
            company_slug="company-a",
            name="Company A",
        )
    )
    return repo


def test_invite_creates_cognito_identity_and_member_membership(tmp_path):
    repo = _repo(tmp_path)
    cognito = FakeCognito()

    result = invite_company_user(
        cognito=cognito,
        user_pool_id="pool-a",
        company_id="company-a",
        email="new.user@example.com",
        role=RoleType.MEMBER,
        repo=repo,
    )

    user = repo.get_user_by_cognito_sub("cognito-sub-1")
    assert result == {"created": True, "invitation_sent": True}
    assert user is not None
    membership = repo.get_membership("company-a", user.user_id)
    assert membership is not None
    assert membership.role is RoleType.MEMBER
    assert len(cognito.created) == 2
    assert cognito.created[0]["MessageAction"] == "SUPPRESS"
    assert cognito.created[1]["MessageAction"] == "RESEND"


def test_existing_cognito_user_is_added_without_resetting_credentials(tmp_path):
    repo = _repo(tmp_path)
    cognito = FakeCognito(
        existing_user={
            "User": {
                "Attributes": [
                    {"Name": "sub", "Value": "existing-sub"},
                    {"Name": "email", "Value": "existing@example.com"},
                ]
            }
        }
    )

    result = invite_company_user(
        cognito=cognito,
        user_pool_id="pool-a",
        company_id="company-a",
        email="existing@example.com",
        role=RoleType.OPERATOR,
        repo=repo,
    )

    user = repo.get_user_by_cognito_sub("existing-sub")
    assert result == {"created": False, "invitation_sent": False}
    assert user is not None
    assert repo.get_membership("company-a", user.user_id).role is RoleType.OPERATOR
    assert cognito.created == []


def test_duplicate_membership_is_not_invited_again(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(
        User(
            user_id="existing-sub",
            email="existing@example.com",
            name="Existing",
            cognito_sub="existing-sub",
        )
    )
    repo.upsert_membership(
        Membership(
            membership_id="existing-membership",
            company_id="company-a",
            user_id="existing-sub",
            role=RoleType.MEMBER,
        )
    )
    cognito = FakeCognito(
        existing_user={
            "User": {
                "Attributes": [
                    {"Name": "sub", "Value": "existing-sub"},
                    {"Name": "email", "Value": "existing@example.com"},
                ]
            }
        }
    )

    with pytest.raises(ValueError, match="already has a membership"):
        invite_company_user(
            cognito=cognito,
            user_pool_id="pool-a",
            company_id="company-a",
            email="existing@example.com",
            role=RoleType.MEMBER,
            repo=repo,
        )
    assert cognito.created == []


def test_invite_does_not_reactivate_a_disabled_process_memory_user(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(
        User(
            user_id="disabled-user",
            email="new.user@example.com",
            name="Disabled",
            status="suspended",
        )
    )
    cognito = FakeCognito()

    with pytest.raises(ValueError, match="account is disabled"):
        invite_company_user(
            cognito=cognito,
            user_pool_id="pool-a",
            company_id="company-a",
            email="new.user@example.com",
            role=RoleType.MEMBER,
            repo=repo,
        )
    assert cognito.deleted == [
        {"UserPoolId": "pool-a", "Username": "new.user@example.com"}
    ]


def test_failed_database_provisioning_removes_suppressed_cognito_account(tmp_path):
    repo = _repo(tmp_path)
    cognito = FakeCognito()

    def fail_provision(_user, _membership):
        raise RuntimeError("database unavailable")

    repo.provision_user_membership = fail_provision
    with pytest.raises(InviteError, match="Could not save the company membership"):
        invite_company_user(
            cognito=cognito,
            user_pool_id="pool-a",
            company_id="company-a",
            email="new.user@example.com",
            role=RoleType.MEMBER,
            repo=repo,
        )
    assert cognito.deleted == [
        {"UserPoolId": "pool-a", "Username": "new.user@example.com"}
    ]


def test_failed_invitation_delivery_keeps_membership_for_manual_resend(tmp_path):
    repo = _repo(tmp_path)
    cognito = FakeCognito(fail_resend=True)

    with pytest.raises(InviteError, match="User access was provisioned"):
        invite_company_user(
            cognito=cognito,
            user_pool_id="pool-a",
            company_id="company-a",
            email="new.user@example.com",
            role=RoleType.MEMBER,
            repo=repo,
        )
    user = repo.get_user_by_cognito_sub("cognito-sub-1")
    assert user is not None
    assert repo.get_membership("company-a", user.user_id) is not None
    assert cognito.deleted == []


def test_invitation_cannot_grant_owner_role(tmp_path):
    repo = _repo(tmp_path)
    cognito = FakeCognito()

    try:
        invite_company_user(
            cognito=cognito,
            user_pool_id="pool-a",
            company_id="company-a",
            email="owner@example.com",
            role=RoleType.OWNER,
            repo=repo,
        )
    except ValueError as exc:
        assert "bootstrap" in str(exc)
    else:
        raise AssertionError("Owner role was granted through the invitation flow")
    assert cognito.created == []


def test_invite_endpoint_rejects_non_owner(monkeypatch):
    from src.api import http_app

    async def member_context(_request):
        return RequestContext(
            company_id="company-a",
            company_slug="company-a",
            user_id="member-a",
            email="member@example.com",
            role=RoleType.MEMBER,
        )

    monkeypatch.setattr(http_app, "_resolve_ui_context", member_context)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/install/invite",
            "query_string": b"company=company-a",
            "headers": [],
        }
    )
    response = asyncio.run(http_app.install_invite_user(request))

    assert response.status_code == 403
    assert (
        response.body
        == b'{"error":"forbidden","message":"Only a company Owner can invite users."}'
    )


def test_owner_invite_endpoint_persists_invited_member(monkeypatch, tmp_path):
    from src.api import http_app

    async def owner_context(_request):
        return RequestContext(
            company_id="company-a",
            company_slug="company-a",
            user_id="owner-a",
            email="owner@example.com",
            role=RoleType.OWNER,
        )

    repo = _repo(tmp_path)
    cognito = FakeCognito()
    monkeypatch.setattr(http_app, "_resolve_ui_context", owner_context)
    monkeypatch.setattr(http_app, "repo", repo)
    monkeypatch.setattr(http_app, "COGNITO_USER_POOL_ID", "pool-a")
    monkeypatch.setattr(http_app.boto3, "client", lambda *_args, **_kwargs: cognito)

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(
                {"email": "new.user@example.com", "role": "member"}
            ).encode(),
            "more_body": False,
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/install/invite",
            "query_string": b"company=company-a",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )
    response = asyncio.run(http_app.install_invite_user(request))

    assert response.status_code == 201
    assert json.loads(response.body) == {"created": True, "invitation_sent": True}
    user = repo.get_user_by_cognito_sub("cognito-sub-1")
    assert user is not None
    assert repo.get_membership("company-a", user.user_id).role is RoleType.MEMBER


def test_api_key_cannot_be_revoked_by_another_company_user(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(User(user_id="alice", email="alice@example.com", name="Alice"))
    repo.create_api_key(
        key_id="key_for_alice",
        key_hash="hash-alice",
        key_prefix="opm_alice",
        company_id="company-a",
        user_id="alice",
        label="Antigravity CLI",
    )

    assert not repo.revoke_api_key("key_for_alice", "company-a", "bob")
    assert repo.get_api_key_by_hash("hash-alice")["status"] == "active"
    assert repo.revoke_api_key("key_for_alice", "company-a", "alice")
