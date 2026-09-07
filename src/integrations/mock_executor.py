import html
from typing import Any

from src.integrations.base_executor import TaskExecutor
from src.integrations.odoo17_xmlrpc import OdooAccessDeniedError, OdooExecutionError
from src.models.enums import ExecutionPhase
from src.models.schemas import CreateTaskOutcome, TaskRecord


class MockTaskExecutor(TaskExecutor):
    """
    In-memory Mock Task Executor for sub-second, zero-network offline testing.
    Supports phase-aware failure injection for all execution phases.
    """

    def __init__(self, default_project_id: int = 142):
        self.default_project_id = default_project_id
        self.tasks: dict[int, TaskRecord] = {}
        self._next_id = 1001
        self.simulate_access_error = False
        self.simulate_timeout = False
        self.failure_phase: ExecutionPhase | None = None
        self.failure_code: str | None = None
        self.failure_detail: str | None = None

    def healthcheck(self) -> bool:
        return not self.simulate_access_error and not self.simulate_timeout

    def create_project_task_phase_aware(
        self,
        title: str,
        description: str,
        definition_of_done: list[str] | None = None,
        project_id: int | None = None,
    ) -> CreateTaskOutcome:
        if "create_project_task" in self.__dict__:
            try:
                fn = self.__dict__["create_project_task"]
                res = fn(
                    title=title,
                    description=description,
                    definition_of_done=definition_of_done,
                    project_id=project_id,
                )
                if isinstance(res, TaskRecord):
                    return CreateTaskOutcome(
                        phase=ExecutionPhase.SUCCESS,
                        task_id=res.id,
                        task_record=res,
                        is_success=True,
                    )
            except OdooAccessDeniedError as e:
                return CreateTaskOutcome(
                    phase=ExecutionPhase.CREATE_REJECTED,
                    error_code="access_denied",
                    error_detail=str(e),
                )
            except TimeoutError as e:
                return CreateTaskOutcome(
                    phase=ExecutionPhase.UNCERTAIN_CREATE,
                    error_code="timeout",
                    error_detail=str(e),
                )
            except Exception as e:  # noqa: BLE001 - Boundary converts arbitrary provider failures to controlled outcomes.
                if "timed out" in str(e).lower():
                    return CreateTaskOutcome(
                        phase=ExecutionPhase.UNCERTAIN_CREATE,
                        error_code="timeout",
                        error_detail=str(e),
                    )
                return CreateTaskOutcome(
                    phase=ExecutionPhase.CREATE_REJECTED,
                    error_code="exception",
                    error_detail=str(e),
                )

        if self.simulate_access_error:
            return CreateTaskOutcome(
                phase=ExecutionPhase.CREATE_REJECTED,
                error_code="access_denied",
                error_detail="Odoo Access Denied: User lacks permission to create tasks on project.task.",
            )
        if self.simulate_timeout:
            return CreateTaskOutcome(
                phase=ExecutionPhase.UNCERTAIN_CREATE,
                error_code="timeout",
                error_detail="Odoo XML-RPC request timed out after 15000ms.",
            )

        if self.failure_phase == ExecutionPhase.BEFORE_CREATE:
            return CreateTaskOutcome(
                phase=ExecutionPhase.BEFORE_CREATE,
                error_code=self.failure_code or "before_create_failed",
                error_detail=self.failure_detail or "Simulated before_create failure.",
            )

        if self.failure_phase == ExecutionPhase.CREATE_REJECTED:
            return CreateTaskOutcome(
                phase=ExecutionPhase.CREATE_REJECTED,
                error_code=self.failure_code or "create_rejected",
                error_detail=self.failure_detail or "Simulated create rejection.",
            )

        if self.failure_phase == ExecutionPhase.UNCERTAIN_CREATE:
            return CreateTaskOutcome(
                phase=ExecutionPhase.UNCERTAIN_CREATE,
                error_code=self.failure_code or "uncertain_create",
                error_detail=self.failure_detail
                or "Simulated network timeout during create.",
            )

        target_project_id = (
            project_id if project_id is not None else self.default_project_id
        )
        task_id = self._next_id
        self._next_id += 1

        escaped_desc = html.escape(description)
        html_content = f"<p>{escaped_desc}</p>"
        if definition_of_done:
            html_content += (
                "<ul>"
                + "".join([f"<li>{html.escape(d)}</li>" for d in definition_of_done])
                + "</ul>"
            )

        task = TaskRecord(
            id=task_id,
            name=title.strip(),
            description=html_content,
            project_id=target_project_id,
            project_name="IH/AI/Odoo Tutor",
        )
        self.tasks[task_id] = task

        if self.failure_phase == ExecutionPhase.VERIFICATION_FAILED:
            return CreateTaskOutcome(
                phase=ExecutionPhase.VERIFICATION_FAILED,
                task_id=task_id,
                error_code=self.failure_code or "verification_failed",
                error_detail=self.failure_detail
                or f"Simulated verification failure for task #{task_id}.",
            )

        return CreateTaskOutcome(
            phase=ExecutionPhase.SUCCESS,
            task_id=task_id,
            task_record=task,
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
            raise OdooAccessDeniedError(outcome.error_detail or "Access denied")
        if outcome.error_code == "timeout":
            raise TimeoutError(outcome.error_detail or "Request timed out")
        raise OdooExecutionError(
            outcome.error_detail or f"Task creation failed at phase {outcome.phase}"
        )

    def get_project_task(self, task_id: int) -> TaskRecord | None:
        return self.tasks.get(task_id)

    def search_project_tasks(
        self, name: str, project_id: int | None = None
    ) -> list[TaskRecord]:
        target_name = name.strip().lower()
        results = []
        for t in self.tasks.values():
            if t.name.strip().lower() == target_name and (
                project_id is None or t.project_id == project_id
            ):
                results.append(t)
        return results

    def create_record(self, model: str, values: dict[str, Any]) -> dict[str, Any]:
        if self.simulate_access_error:
            raise OdooAccessDeniedError("Access denied")
        if self.simulate_timeout:
            raise TimeoutError("Request timed out")
        record_id = self._next_id
        self._next_id += 1
        name = values.get("name", "Task")
        project_id = values.get("project_id", self.default_project_id)
        description = values.get("description", "")
        self.tasks[record_id] = TaskRecord(
            id=record_id,
            name=name,
            description=description,
            project_id=project_id,
        )
        return {
            "id": record_id,
            "model": model,
            "values": values,
        }
