from abc import ABC, abstractmethod

from src.models.schemas import CreateTaskOutcome, TaskRecord


class TaskExecutor(ABC):
    """
    Version-neutral task execution adapter interface.
    """

    @abstractmethod
    def healthcheck(self) -> bool:
        """Verifies connectivity, authentication, and project access to Odoo without writing data."""
        ...

    @abstractmethod
    def create_project_task(
        self,
        title: str,
        description: str,
        definition_of_done: list[str] | None = None,
        project_id: int | None = None,
    ) -> TaskRecord:
        """
        Creates a project.task record with safe HTML rendering and read-back verification.
        """
        ...

    @abstractmethod
    def create_project_task_phase_aware(
        self,
        title: str,
        description: str,
        definition_of_done: list[str] | None = None,
        project_id: int | None = None,
    ) -> CreateTaskOutcome:
        """
        Creates a task and returns a typed outcome capturing phase-specific states.
        """
        ...

    @abstractmethod
    def get_project_task(self, task_id: int) -> TaskRecord | None:
        """
        Reads back an existing task by ID to verify record integrity.
        """
        ...

    @abstractmethod
    def search_project_tasks(
        self, name: str, project_id: int | None = None
    ) -> list[TaskRecord]:
        """
        Searches for tasks matching title and optionally project_id.
        Used for safe operator reconciliation verification.
        """
        ...
