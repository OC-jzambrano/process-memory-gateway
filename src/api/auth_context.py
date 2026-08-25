import contextvars
from typing import Optional
from src.models.schemas import RequestContext, Company, User, Membership
from src.models.enums import RoleType, MembershipStatus, CompanyStatus
from src.storage.base_repository import BaseRepository

# ContextVar storing the current authenticated request context for thread/async safety
_current_request_context: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar(
    "current_request_context",
    default=None
)

class AuthenticationError(Exception):
    pass

class AuthorizationError(Exception):
    pass

def set_current_context(ctx: RequestContext) -> None:
    _current_request_context.set(ctx)

def get_current_context() -> RequestContext:
    ctx = _current_request_context.get()
    if not ctx:
        # Default fallback for local stdio dev environment
        return RequestContext(
            company_id="demo_company",
            company_slug="demo_company",
            user_id="demo_owner",
            email="owner@example.com",
            role=RoleType.OWNER,
            client_agent="local_stdio"
        )
    return ctx

class AuthContextResolver:
    """
    Validates company membership and authorization rules.
    Public MCP tools never accept caller-controlled client_id, reviewer, role, or principal.
    """

    def __init__(self, repo: BaseRepository):
        self.repo = repo

    def resolve_context(
        self,
        company_slug: str,
        user_id: str = "demo_owner",
        email: str = "owner@example.com",
        client_agent: Optional[str] = None
    ) -> RequestContext:
        # 1. Resolve Company
        company = self.repo.get_company_by_slug(company_slug)
        if not company:
            # Auto-provision on first local access if missing
            company = self.repo.upsert_company(
                Company(
                    company_id=company_slug,
                    company_slug=company_slug,
                    name=company_slug,
                    status=CompanyStatus.ACTIVE
                )
            )

        if company.status != CompanyStatus.ACTIVE:
            raise AuthorizationError(f"Company '{company_slug}' is suspended.")

        # 2. Resolve User & Membership
        user = self.repo.get_user(user_id)
        if not user:
            user = self.repo.upsert_user(
                User(
                    user_id=user_id,
                    email=email,
                    name=user_id,
                    status="active"
                )
            )

        membership = self.repo.get_membership(company.company_id, user.user_id)
        if not membership:
            # Auto-provision owner membership for pilot
            membership = self.repo.upsert_membership(
                Membership(
                    membership_id=f"mem_{company.company_id}_{user.user_id}",
                    company_id=company.company_id,
                    user_id=user.user_id,
                    role=RoleType.OWNER,
                    status=MembershipStatus.ACTIVE
                )
            )

        if membership.status != MembershipStatus.ACTIVE:
            raise AuthorizationError(f"User '{user_id}' does not have an active membership in company '{company_slug}'.")

        ctx = RequestContext(
            company_id=company.company_id,
            company_slug=company.company_slug,
            user_id=user.user_id,
            email=user.email,
            role=membership.role,
            client_agent=client_agent
        )
        set_current_context(ctx)
        return ctx

    def require_role(self, ctx: RequestContext, allowed_roles: list[RoleType]) -> None:
        if ctx.role not in allowed_roles:
            raise AuthorizationError(
                f"Action requires one of roles {[r.value for r in allowed_roles]}, but current role is '{ctx.role.value}'."
            )
