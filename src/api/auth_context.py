import contextvars
from contextlib import contextmanager

from src.api.auth import AuthenticationError, AuthorizationError
from src.config import HOSTED_MODE
from src.models.enums import CompanyStatus, MembershipStatus, RoleType
from src.models.schemas import RequestContext
from src.storage.base_repository import BaseRepository

# ContextVar storing the current authenticated request context for thread/async safety
_current_request_context: contextvars.ContextVar[RequestContext | None] = (
    contextvars.ContextVar("current_request_context", default=None)
)


def set_current_context(ctx: RequestContext | None) -> None:
    _current_request_context.set(ctx)


@contextmanager
def request_context(ctx: RequestContext | None):
    """Context manager for scoping execution to a specific authenticated RequestContext."""
    token = _current_request_context.set(ctx)
    try:
        yield ctx
    finally:
        _current_request_context.reset(token)


def get_current_context() -> RequestContext:
    ctx = _current_request_context.get()
    if not ctx:
        if HOSTED_MODE:
            raise AuthenticationError(
                "Unauthenticated request: no active request context established."
            )
        # Default fallback for local offline stdio dev/test environment
        return RequestContext(
            company_id="demo_company",
            company_slug="demo_company",
            user_id="demo_owner",
            email="owner@example.com",
            role=RoleType.OWNER,
            client_agent="local_stdio",
        )
    return ctx


class AuthContextResolver:
    """
    Validates company membership and authorization rules.
    Public MCP tools never accept caller-controlled client_id, reviewer, role, or principal.
    Implicit auto-provisioning is strictly disabled.
    """

    def __init__(self, repo: BaseRepository):
        self.repo = repo

    def resolve_context(
        self,
        company_slug: str,
        user_id: str,
        email: str | None = None,
        client_agent: str | None = None,
    ) -> RequestContext:
        # 1. Resolve Company
        company = self.repo.get_company_by_slug(company_slug)
        if not company:
            raise AuthorizationError(f"Company '{company_slug}' not found.")

        if company.status != CompanyStatus.ACTIVE:
            raise AuthorizationError(f"Company '{company_slug}' is not active.")

        # 2. Resolve User
        user = self.repo.get_user(user_id)
        if not user:
            raise AuthorizationError(f"User account '{user_id}' is not provisioned.")

        if user.status != "active":
            raise AuthorizationError(
                f"User account '{user_id}' is disabled or suspended."
            )

        # 3. Resolve Membership
        membership = self.repo.get_membership(company.company_id, user.user_id)
        if not membership:
            raise AuthorizationError(
                f"User '{user_id}' is not a member of company '{company_slug}'."
            )

        if membership.status != MembershipStatus.ACTIVE:
            raise AuthorizationError(
                f"Membership for user '{user_id}' in company '{company_slug}' is not active."
            )

        ctx = RequestContext(
            company_id=company.company_id,
            company_slug=company.company_slug,
            user_id=user.user_id,
            email=user.email,
            role=membership.role,
            client_agent=client_agent,
        )
        set_current_context(ctx)
        return ctx

    def require_role(self, ctx: RequestContext, allowed_roles: list[RoleType]) -> None:
        if ctx.role not in allowed_roles:
            raise AuthorizationError(
                f"Action requires one of roles {[r.value for r in allowed_roles]}, but current role is '{ctx.role.value}'."
            )
