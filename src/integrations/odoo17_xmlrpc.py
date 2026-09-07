import http.client
import logging
import re
import xmlrpc.client
from typing import Any
from urllib.parse import urlparse

from src.utils.privacy import sanitize_evidence

logger = logging.getLogger(__name__)


class OdooExecutionError(Exception):
    pass


class OdooAccessDeniedError(OdooExecutionError):
    pass


class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float = 15.0, use_datetime: bool = False):
        super().__init__(use_datetime=use_datetime)
        self.timeout = timeout

    def make_connection(self, host):
        return http.client.HTTPConnection(host, timeout=self.timeout)


class SafeTimeoutTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: float = 15.0, use_datetime: bool = False, context=None):
        super().__init__(use_datetime=use_datetime, context=context)
        self.timeout = timeout

    def make_connection(self, host):
        return http.client.HTTPSConnection(
            host, timeout=self.timeout, context=self.context
        )


def _sanitize_error_message(message: str) -> str:
    """Sanitizes error text to prevent leaking credentials or database names."""
    if not message:
        return "Unknown Odoo error"
    clean = re.sub(r"://([^:@]+):([^@]+)@", r"://\1:[REDACTED]@", message)
    clean = re.sub(r"password['\"]?\s*[:=]\s*['\"][^'\"]+['\"]", "password='[REDACTED]'", clean, flags=re.IGNORECASE)
    clean = re.sub(r"api_key['\"]?\s*[:=]\s*['\"][^'\"]+['\"]", "api_key='[REDACTED]'", clean, flags=re.IGNORECASE)
    return sanitize_evidence(clean)


class Odoo17Connector:
    """
    Thin Odoo 17 XML-RPC connector.
    Authenticates, creates the requested Odoo record, and returns immediate XML-RPC result.
    Zero business validation, zero DoD checking, zero read-back verification.
    """

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        password: str,
        timeout: float = 15.0,
        **kwargs: Any,
    ):
        if not url:
            raise ValueError("Odoo URL is required")
        if not db:
            raise ValueError("Odoo Database name is required")

        eff_username = username or kwargs.get("login")
        if not eff_username:
            raise ValueError("Odoo username/login is required")

        eff_password = password or kwargs.get("api_key")
        if not eff_password:
            raise ValueError("Odoo password or API key is required")

        self.url = url.rstrip("/")
        self.db = db
        self.username = eff_username
        self.login = eff_username
        self.password = eff_password
        self.timeout = timeout
        self._uid: int | None = None

        parsed = urlparse(self.url)
        self._is_ssl = parsed.scheme == "https"

    def _get_transport(self) -> xmlrpc.client.Transport:
        if self._is_ssl:
            return SafeTimeoutTransport(timeout=self.timeout)
        return TimeoutTransport(timeout=self.timeout)

    def _get_proxy(self, service: str) -> xmlrpc.client.ServerProxy:
        endpoint = f"{self.url}/xmlrpc/2/{service}"
        return xmlrpc.client.ServerProxy(
            endpoint, transport=self._get_transport(), allow_none=True
        )

    def authenticate(self) -> int:
        """Authenticates against Odoo /xmlrpc/2/common and returns the user ID (uid)."""
        if self._uid is not None:
            return self._uid

        if not self.url or not self.db or not self.login or not self.password:
            raise OdooExecutionError("Odoo connection requires url, db, login, and password/api_key.")

        try:
            common = self._get_proxy("common")
            uid = common.authenticate(self.db, self.login, self.password, {})
            if not uid or not isinstance(uid, int):
                raise OdooAccessDeniedError("Odoo authentication failed: invalid credentials or database.")
            self._uid = uid
            return uid
        except (OdooExecutionError, OdooAccessDeniedError):
            raise
        except Exception as e:  # noqa: BLE001 - Catch XML-RPC exceptions to re-raise sanitized errors
            clean_msg = _sanitize_error_message(str(e))
            raise OdooExecutionError(f"Odoo authentication error: {clean_msg}") from None

    _authenticate = authenticate

    def healthcheck(self) -> bool:
        """Verifies connectivity and credentials without modifying data."""
        try:
            self.authenticate()
            return True
        except Exception as e:  # noqa: BLE001 - Non-blocking connectivity healthcheck
            logger.warning("Odoo healthcheck failed: %s", _sanitize_error_message(str(e)))
            return False

    def create_record(self, model: str, values: dict[str, Any]) -> dict[str, Any]:
        """
        Creates a record in Odoo and returns immediate XML-RPC result.
        No business validation or read-back verification performed.
        """
        uid = self.authenticate()
        try:
            models = self._get_proxy("object")
            record_id = models.execute_kw(
                self.db,
                uid,
                self.password,
                model,
                "create",
                [values],
            )
            if not record_id or not isinstance(record_id, int):
                raise OdooExecutionError(f"Odoo create returned invalid record ID: {record_id}")

            return {
                "id": record_id,
                "model": model,
                "values": sanitize_evidence(values),
            }
        except OdooExecutionError:
            raise
        except Exception as e:  # noqa: BLE001 - Catch XML-RPC errors to re-raise sanitized errors
            clean_msg = _sanitize_error_message(str(e))
            raise OdooExecutionError(f"Odoo record creation failed for model '{model}': {clean_msg}") from None

    def execute_kw(
        self, model: str, method: str, args: list | None = None, kwargs: dict | None = None
    ) -> Any:
        """Executes an arbitrary method on an Odoo model via XML-RPC."""
        uid = self.authenticate()
        args = args or []
        kwargs = kwargs or {}
        try:
            models = self._get_proxy("object")
            return models.execute_kw(
                self.db,
                uid,
                self.password,
                model,
                method,
                args,
                kwargs,
            )
        except Exception as e:  # noqa: BLE001 - Catch XML-RPC errors to re-raise sanitized errors
            clean_msg = _sanitize_error_message(str(e))
            raise OdooExecutionError(f"Odoo execute_kw '{method}' failed on '{model}': {clean_msg}") from None


# Backward compatibility alias
Odoo17XmlRpcExecutor = Odoo17Connector
