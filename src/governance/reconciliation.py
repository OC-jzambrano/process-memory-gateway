import logging
import uuid

from src.integrations.base_executor import TaskExecutor
from src.models.enums import ExecutionEventType, RunStatus
from src.models.schemas import ExecutionEventRecord, ExecutionRunRecord
from src.storage.base_repository import BaseRepository
from src.utils.privacy import sanitize_evidence

logger = logging.getLogger(__name__)


class ReconciliationError(Exception):
    pass


def reconcile_run_by_operator(
    repo: BaseRepository,
    company_id: str,
    run_id: str,
    executor: TaskExecutor,
    operator_user_id: str = "operator",
) -> ExecutionRunRecord:
    """
    Internal operator reconciliation workflow for runs marked RECONCILIATION_REQUIRED.
    Performs read-only verification against recorded connection snapshot and canonical input.
    """
    run = repo.get_execution_run(run_id=run_id, company_id=company_id)
    if not run:
        raise ReconciliationError(
            f"Execution run '{run_id}' not found for company '{company_id}'."
        )

    if run.status != RunStatus.RECONCILIATION_REQUIRED:
        raise ReconciliationError(
            f"Run '{run_id}' is in status '{run.status.value}', not 'reconciliation_required'."
        )

    # Verify if task exists in Odoo
    task_found = None
    if run.odoo_task_id:
        task_found = executor.get_project_task(run.odoo_task_id)

    now = repo._now() if hasattr(repo, "_now") else ""
    event_id = f"eev_{uuid.uuid4().hex}"

    if task_found:
        # Task was created and verified in Odoo
        resolved_event = ExecutionEventRecord(
            event_id=event_id,
            run_id=run.run_id,
            event_type=ExecutionEventType.RECONCILIATION_RESOLVED,
            details=sanitize_evidence(
                {
                    "operator": operator_user_id,
                    "resolution": "verified_task_exists",
                    "odoo_task_id": task_found.id,
                    "task_name": task_found.name,
                }
            ),
            created_at=now,
        )
        # Update run to CREATED
        run.status = RunStatus.CREATED
        run.odoo_task_id = task_found.id
        run.result_payload = {
            "task_name": task_found.name,
            "project_id": task_found.project_id,
        }
        run.error_code = None
        run.error_detail = None
        repo.update_execution_run(run)
        repo.add_execution_event(resolved_event)
        return run
    else:
        # Task was confirmed not created or absent
        failed_event = ExecutionEventRecord(
            event_id=event_id,
            run_id=run.run_id,
            event_type=ExecutionEventType.RECONCILIATION_RESOLVED,
            details=sanitize_evidence(
                {"operator": operator_user_id, "resolution": "confirmed_task_absent"}
            ),
            created_at=now,
        )
        run.status = RunStatus.FAILED
        run.error_code = "reconciliation_confirmed_not_created"
        run.error_detail = (
            "Operator reconciliation verified task was not created in Odoo."
        )
        repo.update_execution_run(run)
        repo.add_execution_event(failed_event)
        return run
