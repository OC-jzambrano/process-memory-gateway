import html
from typing import Optional, List, Dict
from src.integrations.base_executor import TaskExecutor
from src.integrations.odoo17_xmlrpc import OdooAccessDeniedError, OdooExecutionError
from src.models.schemas import TaskRecord

class MockTaskExecutor(TaskExecutor):
    """
    In-memory Mock Task Executor for sub-second, zero-network offline testing.
    """

    def __init__(self, default_project_id: int = 142):
        self.default_project_id = default_project_id
        self.tasks: Dict[int, TaskRecord] = {}
        self._next_id = 1001
        self.simulate_access_error = False
        self.simulate_timeout = False

    def healthcheck(self) -> bool:
        return not self.simulate_access_error and not self.simulate_timeout

    def create_project_task(
        self,
        title: str,
        description: str,
        definition_of_done: Optional[List[str]] = None,
        project_id: Optional[int] = None
    ) -> TaskRecord:
        if self.simulate_access_error:
            raise OdooAccessDeniedError("Odoo Access Denied: User lacks permission to create tasks on project.task.")
        if self.simulate_timeout:
            raise TimeoutError("Odoo XML-RPC request timed out after 30000ms.")

        target_project_id = project_id or self.default_project_id
        task_id = self._next_id
        self._next_id += 1

        escaped_desc = html.escape(description)
        html_content = f"<p>{escaped_desc}</p>"
        if definition_of_done:
            html_content += "<ul>" + "".join([f"<li>{html.escape(d)}</li>" for d in definition_of_done]) + "</ul>"

        task = TaskRecord(
            id=task_id,
            name=title.strip(),
            description=html_content,
            project_id=target_project_id,
            project_name="IH/AI/Odoo Tutor"
        )
        self.tasks[task_id] = task
        return task

    def get_project_task(self, task_id: int) -> Optional[TaskRecord]:
        return self.tasks.get(task_id)
