import html
import xmlrpc.client
import logging
from typing import Optional, List, Dict, Any
from src.integrations.base_executor import TaskExecutor
from src.models.schemas import TaskRecord
from src.config import ODOO_URL, ODOO_DB, ODOO_LOGIN, ODOO_PASSWORD, ODOO_API_KEY, ODOO_DEFAULT_PROJECT_ID

logger = logging.getLogger(__name__)

class OdooExecutionError(Exception):
    pass

class OdooAccessDeniedError(OdooExecutionError):
    pass

class Odoo17XmlRpcExecutor(TaskExecutor):
    """
    Direct Odoo 17 XML-RPC Task Execution Adapter.
    - Credentials MUST be provided explicitly via runtime secret injection or environment.
    - No default usernames, passwords, URLs, or API keys.
    - Strictly escapes agent plain text into safe HTML.
    - Performs immediate read-back verification.
    - Redacts all secret values and raw credentials from logs and exception messages.
    """

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        password: str,
        default_project_id: int = 142
    ):
        if not url or not url.strip():
            raise ValueError("Odoo URL is required and cannot be empty.")
        if not db or not db.strip():
            raise ValueError("Odoo Database name is required and cannot be empty.")
        if not username or not username.strip():
            raise ValueError("Odoo username/login is required and cannot be empty.")
        if not password or not password.strip():
            raise ValueError("Odoo password or API key is required and cannot be empty.")

        self.url = url.rstrip("/")
        self.db = db.strip()
        self.username = username.strip()
        self.password = password.strip()
        self.default_project_id = int(default_project_id)
        self._uid: Optional[int] = None

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
            default_project_id=ODOO_DEFAULT_PROJECT_ID
        )

    @classmethod
    def from_secret_dict(cls, secret_data: Dict[str, Any]) -> "Odoo17XmlRpcExecutor":
        """Constructs executor from AWS Secrets Manager secret dictionary payload."""
        url = secret_data.get("url") or secret_data.get("ODOO_URL", "")
        db = secret_data.get("database") or secret_data.get("ODOO_DB", "")
        username = secret_data.get("username") or secret_data.get("ODOO_LOGIN", "")
        pwd = secret_data.get("api_key") or secret_data.get("password") or secret_data.get("ODOO_PASSWORD", "")
        proj_id = secret_data.get("default_project_id") or secret_data.get("DEFAULT_PROJECT_ID", 142)

        return cls(
            url=url,
            db=db,
            username=username,
            password=pwd,
            default_project_id=int(proj_id)
        )

    def _get_common_proxy(self) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)

    def _get_object_proxy(self) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

    def _authenticate(self) -> int:
        if self._uid:
            return self._uid
        try:
            common = self._get_common_proxy()
            uid = common.authenticate(self.db, self.username, self.password, {})
            if not uid:
                raise OdooAccessDeniedError("Odoo authentication failed: Invalid credentials.")
            self._uid = uid
            return uid
        except xmlrpc.client.Fault as f:
            logger.error("Odoo XML-RPC authentication fault occurred.")
            raise OdooExecutionError("Odoo authentication service error.") from None
        except OdooAccessDeniedError:
            raise
        except Exception as e:
            logger.error("Odoo connection error: %s", str(e))
            raise OdooExecutionError(f"Could not connect to Odoo server at {self.url}.") from None

    def healthcheck(self) -> bool:
        try:
            uid = self._authenticate()
            models = self._get_object_proxy()
            # Verify read access to project 142 or project.task without writing
            count = models.execute_kw(
                self.db, uid, self.password,
                'project.task', 'search_count',
                [[['project_id', '=', self.default_project_id]]]
            )
            return count is not None and count >= 0
        except Exception as e:
            logger.warning("Odoo healthcheck failed: %s", str(e))
            return False

    def _render_safe_html(self, description: str, definition_of_done: Optional[List[str]]) -> str:
        """
        Safely formats plain text into sanitised Odoo HTML, escaping all user text
        and constructing a clear Definition of Done checklist.
        """
        escaped_desc = html.escape(description).replace("\n", "<br/>")
        html_parts = [f"<div class='opm-task-description'><p>{escaped_desc}</p></div>"]

        if definition_of_done:
            html_parts.append("<div class='opm-dod-section' style='margin-top: 15px; padding: 10px; background-color: #f8f9fa; border-left: 4px solid #00a09d;'>")
            html_parts.append("<h4 style='margin-top: 0; color: #212529;'>Definition of Done</h4>")
            html_parts.append("<ul style='margin-bottom: 0; padding-left: 20px;'>")
            for item in definition_of_done:
                if isinstance(item, str) and item.strip():
                    html_parts.append(f"<li>{html.escape(item.strip())}</li>")
            html_parts.append("</ul></div>")

        return "".join(html_parts)

    def create_project_task(
        self,
        title: str,
        description: str,
        definition_of_done: Optional[List[str]] = None,
        project_id: Optional[int] = None
    ) -> TaskRecord:
        uid = self._authenticate()
        target_project_id = project_id or self.default_project_id
        safe_html = self._render_safe_html(description, definition_of_done)

        task_payload = {
            "name": title.strip(),
            "description": safe_html,
            "project_id": target_project_id
        }

        try:
            models = self._get_object_proxy()
            task_id = models.execute_kw(
                self.db, uid, self.password,
                'project.task', 'create',
                [task_payload]
            )
            if not task_id or not isinstance(task_id, int):
                raise OdooExecutionError("Odoo task creation returned invalid task ID.")

            # Immediate read-back verification
            read_back = self.get_project_task(task_id)
            if not read_back:
                raise OdooExecutionError(f"Task #{task_id} was created but read-back verification failed.")

            if read_back.name != title.strip():
                raise OdooExecutionError(
                    f"Task #{task_id} read-back mismatch: expected title '{title}', found '{read_back.name}'."
                )

            return read_back

        except xmlrpc.client.Fault as f:
            fault_str = f.faultString
            if "AccessError" in fault_str or "Access Denied" in fault_str:
                raise OdooAccessDeniedError("Odoo rejected task creation: Insufficient permissions on project.task.")
            raise OdooExecutionError("Odoo task creation failed due to server fault.")
        except OdooExecutionError:
            raise
        except Exception as e:
            raise OdooExecutionError(f"Odoo execution error: {str(e)}")

    def get_project_task(self, task_id: int) -> Optional[TaskRecord]:
        uid = self._authenticate()
        try:
            models = self._get_object_proxy()
            records = models.execute_kw(
                self.db, uid, self.password,
                'project.task', 'read',
                [[task_id], ['id', 'name', 'description', 'project_id']]
            )
            if not records:
                return None
            r = records[0]
            proj = r.get("project_id")
            proj_id = proj[0] if isinstance(proj, (list, tuple)) else proj
            proj_name = proj[1] if isinstance(proj, (list, tuple)) and len(proj) > 1 else None

            return TaskRecord(
                id=r["id"],
                name=r["name"],
                description=r.get("description") or "",
                project_id=proj_id or self.default_project_id,
                project_name=proj_name
            )
        except Exception as e:
            logger.error("Error reading task #%d from Odoo: %s", task_id, str(e))
            return None
