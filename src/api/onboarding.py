"""Administrator controlled Cognito and Process Memory user provisioning."""

from __future__ import annotations

import uuid

from botocore.exceptions import ClientError

from src.models.enums import MembershipStatus, RoleType
from src.models.schemas import Membership, User
from src.storage.base_repository import BaseRepository


class InviteError(Exception):
    """An invitation could not safely be completed."""


def invite_company_user(
    *,
    cognito,
    user_pool_id: str,
    company_id: str,
    email: str,
    role: RoleType,
    repo: BaseRepository,
) -> dict[str, bool | str]:
    """Create an invite-only Cognito account and an OPM company membership."""
    if role is RoleType.OWNER:
        raise ValueError(
            "Owner access must be granted through the owner bootstrap process"
        )

    created_in_cognito = False
    try:
        cognito_user = cognito.admin_get_user(
            UserPoolId=user_pool_id,
            Username=email,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "UserNotFoundException":
            raise InviteError("Could not check the Cognito account") from exc
        try:
            cognito_user = cognito.admin_create_user(
                UserPoolId=user_pool_id,
                Username=email,
                UserAttributes=[{"Name": "email", "Value": email}],
                MessageAction="SUPPRESS",
            )
            created_in_cognito = True
        except (
            Exception
        ) as create_error:  # Cognito transport errors can leave creation uncertain
            raise InviteError(
                "Cognito could not confirm account creation. Check the user pool before retrying."
            ) from create_error

    attributes = {
        item.get("Name"): item.get("Value")
        for item in cognito_user.get("User", {}).get("Attributes", [])
    }
    cognito_sub = attributes.get("sub")
    if not cognito_sub:
        if created_in_cognito:
            _delete_cognito_user(cognito, user_pool_id, email)
        raise InviteError("Cognito did not return the invited user's identity")
    cognito_email = attributes.get("email", "").strip().lower()
    if cognito_email != email:
        if created_in_cognito:
            _delete_cognito_user(cognito, user_pool_id, email)
        raise InviteError("The Cognito account email does not match the invitation")

    existing_user = repo.get_user_by_cognito_sub(cognito_sub) or repo.get_user_by_email(
        email
    )
    if existing_user and existing_user.status != "active":
        if created_in_cognito:
            _delete_cognito_user(cognito, user_pool_id, email)
        raise ValueError("This Process Memory account is disabled")
    user_id = existing_user.user_id if existing_user else cognito_sub
    user = User(
        user_id=user_id,
        email=email,
        name=existing_user.name if existing_user else email.split("@", 1)[0],
        cognito_sub=cognito_sub,
        status=existing_user.status if existing_user else "active",
    )
    membership = Membership(
        membership_id=f"mem_{company_id}_{uuid.uuid4().hex}",
        company_id=company_id,
        user_id=user_id,
        role=role,
        status=MembershipStatus.ACTIVE,
    )

    try:
        repo.provision_user_membership(user, membership)
    except Exception as exc:  # compensate external Cognito state on failure
        if created_in_cognito:
            _delete_cognito_user(cognito, user_pool_id, email)
        if isinstance(exc, ValueError):
            raise
        raise InviteError("Could not save the company membership") from exc

    if not created_in_cognito:
        return {"created": False, "invitation_sent": False}

    try:
        cognito.admin_create_user(
            UserPoolId=user_pool_id,
            Username=email,
            MessageAction="RESEND",
            DesiredDeliveryMediums=["EMAIL"],
        )
        return {"created": True, "invitation_sent": True}
    except (
        Exception
    ) as exc:  # account/membership are committed; email status may be uncertain
        raise InviteError(
            "User access was provisioned, but Cognito could not confirm invitation delivery. "
            "Check the Cognito user pool and resend the invitation if needed."
        ) from exc


def _delete_cognito_user(cognito, user_pool_id: str, email: str) -> None:
    try:
        cognito.admin_delete_user(UserPoolId=user_pool_id, Username=email)
    except Exception as exc:
        raise InviteError(
            "Provisioning stopped, but the unused Cognito account could not be removed. "
            "An administrator must remove it from the Cognito user pool."
        ) from exc
