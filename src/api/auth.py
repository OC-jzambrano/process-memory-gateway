import time
import json
import urllib.request
import logging
from typing import Optional, Dict, Any
import jwt
from jwt.exceptions import PyJWTError, ExpiredSignatureError, InvalidTokenError

from src.config import (
    COGNITO_USER_POOL_ID,
    COGNITO_REGION,
    COGNITO_APP_CLIENT_ID,
    COGNITO_RESOURCE_SERVER_IDENTIFIER,
    COGNITO_REQUIRED_SCOPE
)
from src.models.schemas import RequestContext
from src.models.enums import CompanyStatus, MembershipStatus
from src.storage.base_repository import BaseRepository

logger = logging.getLogger(__name__)

class AuthenticationError(Exception):
    """Raised when authentication credentials (JWT token) are missing, invalid, or expired."""
    pass

class AuthorizationError(Exception):
    """Raised when the authenticated user is not authorized for the requested company or action."""
    pass

class CognitoTokenVerifier:
    """
    Validates Cognito OAuth 2.0 / OIDC access tokens against User Pool JWKS.
    - Validates signature using JWKS public keys.
    - Validates issuer: https://cognito-idp.{region}.amazonaws.com/{user_pool_id}.
    - Validates token_use == 'access'.
    - Validates expiration (exp).
    - Validates client ID (client_id).
    - Validates required scope (e.g., mcp:tools or resource-bound scope).
    """

    def __init__(
        self,
        user_pool_id: Optional[str] = None,
        region: Optional[str] = None,
        app_client_id: Optional[str] = None,
        resource_identifier: Optional[str] = None,
        required_scope: Optional[str] = None,
        jwks_override: Optional[Dict[str, Any]] = None
    ):
        self.user_pool_id = user_pool_id or COGNITO_USER_POOL_ID
        self.region = region or COGNITO_REGION
        self.app_client_id = app_client_id or COGNITO_APP_CLIENT_ID
        self.resource_identifier = resource_identifier or COGNITO_RESOURCE_SERVER_IDENTIFIER
        self.required_scope = required_scope or COGNITO_REQUIRED_SCOPE
        self.expected_issuer = f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}" if self.user_pool_id else ""
        
        self._jwks_override = jwks_override
        self._jwks_cache: Optional[Dict[str, Any]] = None
        self._jwks_cache_time: float = 0.0

    def get_jwks(self) -> Dict[str, Any]:
        if self._jwks_override is not None:
            return self._jwks_override

        now = time.time()
        # Cache for 1 hour
        if self._jwks_cache and (now - self._jwks_cache_time) < 3600:
            return self._jwks_cache

        if not self.expected_issuer:
            raise AuthenticationError("Cognito User Pool ID is not configured.")

        jwks_url = f"{self.expected_issuer}/.well-known/jwks.json"
        try:
            req = urllib.request.Request(jwks_url, headers={"User-Agent": "Process-Memory-Gateway"})
            with urllib.request.urlopen(req, timeout=5) as response:
                self._jwks_cache = json.loads(response.read().decode("utf-8"))
                self._jwks_cache_time = now
                return self._jwks_cache
        except Exception as e:
            logger.error("Failed to fetch Cognito JWKS from %s: %s", jwks_url, str(e))
            raise AuthenticationError(f"Failed to fetch public signing keys: {str(e)}") from e

    def verify_token(self, token: str) -> Dict[str, Any]:
        """
        Validates JWT token and returns verified claims payload.
        Fails closed on any signature, issuer, expiration, scope, or client mismatch.
        """
        if not token or not token.strip():
            raise AuthenticationError("Missing Bearer token.")

        # Strip Bearer prefix if passed
        raw_token = token.strip()
        if raw_token.lower().startswith("bearer "):
            raw_token = raw_token[7:].strip()

        try:
            unverified_headers = jwt.get_unverified_header(raw_token)
        except PyJWTError as e:
            raise AuthenticationError(f"Malformed JWT header: {str(e)}") from e

        kid = unverified_headers.get("kid")
        if not kid:
            raise AuthenticationError("JWT header missing 'kid'.")

        jwks = self.get_jwks()
        key_dict = None
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                key_dict = key
                break

        if not key_dict:
            raise AuthenticationError(f"Signing key with kid '{kid}' not found in JWKS.")

        try:
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_dict))
            claims = jwt.decode(
                raw_token,
                key=public_key,
                algorithms=["RS256"],
                options={"verify_exp": True, "verify_aud": False}
            )
        except ExpiredSignatureError as e:
            raise AuthenticationError("Token has expired.") from e
        except InvalidTokenError as e:
            raise AuthenticationError(f"Invalid token signature or payload: {str(e)}") from e

        # 1. Validate Issuer
        if self.expected_issuer and claims.get("iss") != self.expected_issuer:
            raise AuthenticationError(
                f"Token issuer mismatch: expected '{self.expected_issuer}', got '{claims.get('iss')}'."
            )

        # 2. Validate token_use == 'access'
        if claims.get("token_use") != "access":
            raise AuthenticationError(
                f"Invalid token_use: expected 'access', got '{claims.get('token_use')}'."
            )

        # 3. Validate Client ID
        token_client_id = claims.get("client_id")
        if self.app_client_id and token_client_id != self.app_client_id:
            raise AuthenticationError(
                f"Token client_id mismatch: expected '{self.app_client_id}', got '{token_client_id}'."
            )

        # 4. Validate Scope
        if self.required_scope:
            scopes = claims.get("scope", "").split()
            # Handle full resource server scope e.g. "https://mcp.example.com/mcp:tools" or simple "mcp:tools"
            expected_full = f"{self.resource_identifier}/{self.required_scope}".rstrip("/")
            if self.required_scope not in scopes and expected_full not in scopes:
                raise AuthenticationError(
                    f"Token missing required scope '{self.required_scope}'."
                )

        return claims

def resolve_authenticated_context(
    claims: Dict[str, Any],
    company_slug: str,
    repo: BaseRepository,
    client_agent: Optional[str] = None
) -> RequestContext:
    """
    Resolves verified token claims against database entities:
    - Company must exist and be 'active'.
    - User must exist (by cognito_sub or username/email) and be 'active'.
    - Membership must exist for (company, user) and be 'active'.
    - Zero auto-provisioning!
    """
    # 1. Resolve Company
    company = repo.get_company_by_slug(company_slug)
    if not company:
        raise AuthorizationError(f"Company '{company_slug}' not found.")
    if company.status != CompanyStatus.ACTIVE:
        raise AuthorizationError(f"Company '{company_slug}' is not active.")

    # 2. Resolve User by cognito_sub or username
    sub = claims.get("sub")
    username = claims.get("username")
    user = None

    if sub:
        user = repo.get_user_by_cognito_sub(sub)
    if not user and username:
        user = repo.get_user(username)
    if not user and sub:
        user = repo.get_user(sub)

    if not user:
        raise AuthorizationError("Authenticated user account is not provisioned in Process Memory.")
    if user.status != "active":
        raise AuthorizationError(f"User account '{user.user_id}' is disabled or suspended.")

    # 3. Resolve Membership
    membership = repo.get_membership(company.company_id, user.user_id)
    if not membership:
        raise AuthorizationError(f"User '{user.user_id}' is not a member of company '{company_slug}'.")
    if membership.status != MembershipStatus.ACTIVE:
        raise AuthorizationError(f"Membership for user '{user.user_id}' in company '{company_slug}' is not active.")

    return RequestContext(
        company_id=company.company_id,
        company_slug=company.company_slug,
        user_id=user.user_id,
        email=user.email,
        role=membership.role,
        client_agent=client_agent
    )
