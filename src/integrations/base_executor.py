from abc import ABC, abstractmethod
from typing import Optional, List
from src.models.schemas import TaskRecord

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
        definition_of_done: Optional[List[str]] = None,
        project_id: int = 142
    ) -> TaskRecord:
        """
        Creates a project.task record with safe HTML rendering and read-back verification.
        """
        ...

    @abstractmethod
    def get_project_task(self, task_id: int) -> Optional[TaskRecord]:
        """
        Reads back an existing task by ID to verify record integrity.
        """
        ...
