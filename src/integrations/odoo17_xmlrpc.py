import html
import http.client
import logging
import xmlrpc.client
from html.parser import HTMLParser
from typing import Any

from src.config import (
    ODOO_API_KEY,
    ODOO_DB,
    ODOO_DEFAULT_PROJECT_ID,
    ODOO_LOGIN,
    ODOO_PASSWORD,
    ODOO_URL,
)
from src.integrations.base_executor import TaskExecutor
from src.models.enums import ExecutionPhase
from src.models.schemas import CreateTaskOutcome, TaskRecord

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


class TolerantHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_chunks: list[str] = []
        self.li_items: list[str] = []
        self._current_li: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "li":
            self._current_li = []

    def handle_endtag(self, tag):
        if tag.lower() == "li" and self._current_li is not None:
            self.li_items.append("".join(self._current_li).strip())
            self._current_li = None

    def handle_data(self, data):
        self.text_chunks.append(data)
        if self._current_li is not None:
            self._current_li.append(data)


def normalize_html_text(raw_html: str) -> tuple[str, list[str]]:
    parser = TolerantHTMLParser()
    parser.feed(raw_html or "")
    plain_text = " ".join("".join(parser.text_chunks).split())
    cleaned_lis = [
        html.unescape(" ".join(item.split()))
        for item in parser.li_items
        if item.strip()
    ]
    return html.unescape(plain_text), cleaned_lis


def verify_task_html(
    returned_html: str, expected_description: str, expected_dod: list[str] | None
) -> tuple[bool, str | None]:
    plain_text, extracted_lis = normalize_html_text(returned_html)
    norm_expected_desc = " ".join((expected_description or "").split())

    if norm_expected_desc and norm_expected_desc not in plain_text:
        return (
            False,
            f"Expected description text '{norm_expected_desc}' not found in task description.",
        )

    if expected_dod:
        cleaned_expected_dod = [
            html.unescape(" ".join(item.strip().split()))
            for item in expected_dod
            if isinstance(item, str) and item.strip()
        ]
        if len(extracted_lis) < len(cleaned_expected_dod):
            return (
                False,
                f"Expected {len(cleaned_expected_dod)} DoD items, found {len(extracted_lis)} in task HTML.",
            )

        curr = 0
        for exp in cleaned_expected_dod:
            found = False
            while curr < len(extracted_lis):
                if exp in extracted_lis[curr]:
                    found = True
                    curr += 1
                    break
                curr += 1
            if not found:
                return (
                    False,
                    f"Definition of Done item '{exp}' not found in expected order in task HTML.",
                )

    return True, None


class Odoo17XmlRpcExecutor(TaskExecutor):
    """
    Direct Odoo 17 XML-RPC Task Execution Adapter.
    - Credentials MUST be provided explicitly via runtime secret injection or environment.
    - Strictly escapes agent plain text into safe HTML.
    - Performs immediate read-back verification.
    - Returns typed outcomes across execution phases.
    - Redacts all secret values and raw credentials from logs and exception messages.
    """

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        password: str,
        default_project_id: int | None = None,
        timeout: float = 15.0,
    ):
        if not url or not url.strip():
            raise ValueError("Odoo URL is required and cannot be empty.")
        if not db or not db.strip():
            raise ValueError("Odoo Database name is required and cannot be empty.")
        if not username or not username.strip():
            raise ValueError("Odoo username/login is required and cannot be empty.")
        if not password or not password.strip():
            raise ValueError(
                "Odoo password or API key is required and cannot be empty."
            )

        self.url = url.rstrip("/")
        self.db = db.strip()
        self.username = username.strip()
        self.password = password.strip()
        self.default_project_id = (
            int(default_project_id) if default_project_id is not None else None
        )
        self.timeout = float(timeout)
        self._uid: int | None = None

    @classmethod
    def from_env(cls) -> "Odoo17XmlRpcExecutor":
        """Constructs executor from environment variables, failing closed if any are missing."""
        pwd = ODOO_API_KEY or ODOO_PASSWORD
        if not ODOO_URL or not ODOO_DB or not ODOO_LOGIN or not pwd:
            raise ValueError(
                "Incomplete Odoo configuration in environment. "
                "ODOO_URL, ODOO_DB, ODOO_LOGIN, and ODOO_API_KEY/ODOO_PASSWORD must be configured."
            )
        return cls(
            url=ODOO_URL,
            db=ODOO_DB,
            username=ODOO_LOGIN,
            password=pwd,
            default_project_id=ODOO_DEFAULT_PROJECT_ID,
        )

    @classmethod
    def from_secret_dict(
        cls, secret_data: dict[str, Any], default_project_id: int | None = None
    ) -> "Odoo17XmlRpcExecutor":
        """Constructs executor from AWS Secrets Manager secret dictionary payload."""
        url = secret_data.get("url") or secret_data.get("ODOO_URL", "")
        db = secret_data.get("database") or secret_data.get("ODOO_DB", "")
        username = secret_data.get("username") or secret_data.get("ODOO_LOGIN", "")
        pwd = (
            secret_data.get("api_key")
            or secret_data.get("password")
            or secret_data.get("ODOO_PASSWORD", "")
        )
        proj_id = (
            default_project_id
            or secret_data.get("default_project_id")
            or secret_data.get("DEFAULT_PROJECT_ID")
        )
        if proj_id is None:
            raise ValueError("default_project_id is required and cannot be empty.")

        return cls(
            url=url,
            db=db,
            username=username,
            password=pwd,
            default_project_id=int(proj_id),
        )

    def _get_proxy(self, service: str) -> xmlrpc.client.ServerProxy:
        endpoint = f"{self.url}/xmlrpc/2/{service}"
        if self.url.lower().startswith("https"):
            transport = SafeTimeoutTransport(timeout=self.timeout)
        else:
            transport = TimeoutTransport(timeout=self.timeout)
        return xmlrpc.client.ServerProxy(endpoint, transport=transport, allow_none=True)

    def _get_common_proxy(self) -> xmlrpc.client.ServerProxy:
        return self._get_proxy("common")

    def _get_object_proxy(self) -> xmlrpc.client.ServerProxy:
        return self._get_proxy("object")

    def _authenticate(self) -> int:
        if self._uid:
            return self._uid
        try:
            common = self._get_common_proxy()
            uid = common.authenticate(self.db, self.username, self.password, {})
            if not uid:
                raise OdooAccessDeniedError(
                    "Odoo authentication failed: Invalid credentials."
                )
            self._uid = uid
            return uid
        except xmlrpc.client.Fault:
            logger.error("Odoo XML-RPC authentication fault occurred.")
            raise OdooExecutionError("Odoo authentication service error.") from None
        except OdooAccessDeniedError:
            raise
        except Exception as e:  # noqa: BLE001 - Boundary converts arbitrary provider failures to controlled outcomes.
            logger.error("Odoo connection error: %s", str(e))
            raise OdooExecutionError(
                f"Could not connect to Odoo server at {self.url}."
            ) from None

    def healthcheck(self) -> bool:
        try:
            uid = self._authenticate()
            models = self._get_object_proxy()
            count = models.execute_kw(
                self.db,
                uid,
                self.password,
                "project.task",
                "search_count",
                [[["project_id", "=", self.default_project_id]]],
            )
            return count is not None and count >= 0
        except Exception as e:  # noqa: BLE001 - Boundary converts arbitrary provider failures to controlled outcomes.
            logger.warning("Odoo healthcheck failed: %s", str(e))
            return False

    def _render_safe_html(
        self, description: str, definition_of_done: list[str] | None
    ) -> str:
        escaped_desc = html.escape(description).replace("\n", "<br/>")
        html_parts = [f"<div class='opm-task-description'><p>{escaped_desc}</p></div>"]

        if definition_of_done:
            html_parts.append(
                "<div class='opm-dod-section' style='margin-top: 15px; padding: 10px; background-color: #f8f9fa; border-left: 4px solid #00a09d;'>"
            )
            html_parts.append(
                "<h4 style='margin-top: 0; color: #212529;'>Definition of Done</h4>"
            )
            html_parts.append("<ul style='margin-bottom: 0; padding-left: 20px;'>")
            for item in definition_of_done:
                if isinstance(item, str) and item.strip():
                    html_parts.append(f"<li>{html.escape(item.strip())}</li>")
            html_parts.append("</ul></div>")

        return "".join(html_parts)

    def create_project_task_phase_aware(
        self,
        title: str,
        description: str,
        definition_of_done: list[str] | None = None,
        project_id: int | None = None,
    ) -> CreateTaskOutcome:
        try:
            uid = self._authenticate()
        except OdooAccessDeniedError as e:
            return CreateTaskOutcome(
                phase=ExecutionPhase.BEFORE_CREATE,
                error_code="access_denied",
                error_detail=str(e),
            )
        except Exception as e:  # noqa: BLE001 - Boundary converts arbitrary provider failures to controlled outcomes.
            return CreateTaskOutcome(
                phase=ExecutionPhase.BEFORE_CREATE,
                error_code="auth_failed",
                error_detail=str(e),
            )

        target_project_id = (
            project_id if project_id is not None else self.default_project_id
        )
        if target_project_id is None:
            return CreateTaskOutcome(
                phase=ExecutionPhase.BEFORE_CREATE,
                error_code="missing_project_id",
                error_detail="Target project_id is required.",
            )

        safe_html = self._render_safe_html(description, definition_of_done)
        task_payload = {
            "name": title.strip(),
            "description": safe_html,
            "project_id": target_project_id,
        }

        models = self._get_object_proxy()
        try:
            task_id = models.execute_kw(
                self.db, uid, self.password, "project.task", "create", [task_payload]
            )
        except xmlrpc.client.Fault as f:
            fault_str = f.faultString
            err_code = (
                "access_denied"
                if ("AccessError" in fault_str or "Access Denied" in fault_str)
                else "create_rejected"
            )
            return CreateTaskOutcome(
                phase=ExecutionPhase.CREATE_REJECTED,
                error_code=err_code,
                error_detail=fault_str,
            )
        except (
            TimeoutError,
            ConnectionResetError,
            ConnectionError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
        ) as e:
            return CreateTaskOutcome(
                phase=ExecutionPhase.UNCERTAIN_CREATE,
                error_code="network_timeout_uncertain",
                error_detail=str(e),
            )
        except Exception as e:  # noqa: BLE001 - Boundary converts arbitrary provider failures to controlled outcomes.
            return CreateTaskOutcome(
                phase=ExecutionPhase.UNCERTAIN_CREATE,
                error_code="create_exception_uncertain",
                error_detail=str(e),
            )

        if not task_id or not isinstance(task_id, int) or task_id <= 0:
            return CreateTaskOutcome(
                phase=ExecutionPhase.CREATE_REJECTED,
                error_code="invalid_task_id",
                error_detail=f"Odoo returned invalid task ID: {task_id}",
            )

        try:
            records = models.execute_kw(
                self.db,
                uid,
                self.password,
                "project.task",
                "read",
                [[task_id], ["id", "name", "description", "project_id"]],
            )
        except Exception as e:  # noqa: BLE001 - Boundary converts arbitrary provider failures to controlled outcomes.
            logger.error(
                "Read-back verification failed for task #%d: %s", task_id, str(e)
            )
            return CreateTaskOutcome(
                phase=ExecutionPhase.VERIFICATION_FAILED,
                task_id=task_id,
                error_code="readback_exception",
                error_detail=f"Failed to read back created task #{task_id}: {e!s}",
            )

        if not records or len(records) == 0:
            return CreateTaskOutcome(
                phase=ExecutionPhase.VERIFICATION_FAILED,
                task_id=task_id,
                error_code="task_not_found",
                error_detail=f"Task #{task_id} not found during read-back verification.",
            )

        r = records[0]
        proj = r.get("project_id")
        proj_id = proj[0] if isinstance(proj, (list, tuple)) and len(proj) > 0 else proj
        proj_name = (
            proj[1] if isinstance(proj, (list, tuple)) and len(proj) > 1 else None
        )

        if r.get("id") != task_id:
            return CreateTaskOutcome(
                phase=ExecutionPhase.VERIFICATION_FAILED,
                task_id=task_id,
                error_code="id_mismatch",
                error_detail=f"Read-back ID mismatch: expected {task_id}, found {r.get('id')}",
            )

        returned_name = (r.get("name") or "").strip()
        if returned_name != title.strip():
            return CreateTaskOutcome(
                phase=ExecutionPhase.VERIFICATION_FAILED,
                task_id=task_id,
                error_code="title_mismatch",
                error_detail=f"Title mismatch: expected '{title.strip()}', found '{returned_name}'",
            )

        if proj_id is None or proj_id != target_project_id:
            return CreateTaskOutcome(
                phase=ExecutionPhase.VERIFICATION_FAILED,
                task_id=task_id,
                error_code="project_mismatch",
                error_detail=f"Project mismatch: expected {target_project_id}, found {proj_id}",
            )

        desc_html = r.get("description") or ""
        valid_content, err_msg = verify_task_html(
            desc_html, description, definition_of_done
        )
        if not valid_content:
            return CreateTaskOutcome(
                phase=ExecutionPhase.VERIFICATION_FAILED,
                task_id=task_id,
                error_code="content_mismatch",
                error_detail=err_msg,
            )

        task_rec = TaskRecord(
            id=task_id,
            name=returned_name,
            description=desc_html,
            project_id=proj_id,
            project_name=proj_name,
        )

        return CreateTaskOutcome(
            phase=ExecutionPhase.SUCCESS,
            task_id=task_id,
            task_record=task_rec,
            is_success=True,
        )

    def create_project_task(
        self,
        title: str,
        description: str,
        definition_of_done: list[str] | None = None,
        project_id: int | None = None,
    ) -> TaskRecord:
        outcome = self.create_project_task_phase_aware(
            title=title,
            description=description,
            definition_of_done=definition_of_done,
            project_id=project_id,
        )
        if outcome.is_success and outcome.task_record:
            return outcome.task_record

        if outcome.error_code == "access_denied":
            raise OdooAccessDeniedError(
                outcome.error_detail or "Access denied to project.task."
            )
        raise OdooExecutionError(
            outcome.error_detail or f"Task creation failed at phase {outcome.phase}"
        )

    def get_project_task(self, task_id: int) -> TaskRecord | None:
        uid = self._authenticate()
        try:
            models = self._get_object_proxy()
            records = models.execute_kw(
                self.db,
                uid,
                self.password,
                "project.task",
                "read",
                [[task_id], ["id", "name", "description", "project_id"]],
            )
            if not records:
                return None
            r = records[0]
            proj = r.get("project_id")
            proj_id = (
                proj[0] if isinstance(proj, (list, tuple)) and len(proj) > 0 else proj
            )
            proj_name = (
                proj[1] if isinstance(proj, (list, tuple)) and len(proj) > 1 else None
            )

            return TaskRecord(
                id=r["id"],
                name=r["name"],
                description=r.get("description") or "",
                project_id=proj_id if proj_id is not None else -1,
                project_name=proj_name,
            )
        except Exception as e:  # noqa: BLE001 - Boundary converts arbitrary provider failures to controlled outcomes.
            logger.error("Error reading task #%d from Odoo: %s", task_id, str(e))
            return None
