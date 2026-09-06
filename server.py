import json
import logging
from typing import Optional, List, Dict, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from src.storage.repository import MemoryRepository
from src.extractor.service import ProcessMemoryExtractorService
from src.api.service import HostedProcessMemoryService
from src.models.schemas import (
    ActionContext,
    DeterministicConstraint,
    CandidateResult,
    ReviewResult,
    TaskCreationResult,
    MemoryPack,
    CandidateRule
)

logger = logging.getLogger(__name__)

# Initialize singletons
repo = MemoryRepository()
extractor = ProcessMemoryExtractorService()
service = HostedProcessMemoryService(repo=repo, extractor=extractor)

# Create FastMCP Server Instance with exactly five clean public tools
mcp = FastMCP(
    "AWS-Process-Memory-Gateway",
    dependencies=["pydantic", "fastmcp"],
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)

# --- TOOL 1: REMEMBER COMPANY INSTRUCTION ---
@mcp.tool()
def remember_company_instruction(
    instruction_text: str,
    context_hint: Optional[Dict[str, Any]] = None
) -> str:
    """
    Stages a proposed company instruction or business rule into memory_candidates (pending_review).
    Does NOT activate or enforce the rule until approved by a company owner or reviewer.

    Args:
        instruction_text: Natural language statement of the company policy, constraint, or naming rule.
        context_hint: Optional structured dictionary indicating target system, application, resource, or field.

    Returns:
        JSON string containing CandidateResult with candidate ID, previewed scope, constraint, and status.
    """
    scope = ActionContext(**context_hint) if context_hint else None
    result = service.remember_company_instruction(
        instruction_text=instruction_text,
        context_hint=scope
    )
    return result.model_dump_json(indent=2)

# --- TOOL 2: LIST MEMORY CANDIDATES ---
@mcp.tool()
def list_memory_candidates(
    status: str = "pending_review"
) -> str:
    """
    Lists staged memory candidates for the authenticated company awaiting human review.

    Args:
        status: Status filter, defaults to 'pending_review'.

    Returns:
        JSON string containing list of Candidate rules.
    """
    candidates = service.list_memory_candidates(status=status)
    return json.dumps([c.model_dump() for c in candidates], indent=2)

# --- TOOL 3: REVIEW MEMORY CANDIDATE ---
@mcp.tool()
def review_memory_candidate(
    candidate_id: str,
    decision: Literal["approve", "edit", "reject"],
    edited_rule_text: Optional[str] = None,
    edited_scope: Optional[Dict[str, Any]] = None,
    edited_constraint: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None
) -> str:
    """
    Processes human sign-off on a staged memory candidate.
    Approving promotes the candidate into an active, versioned Canonical Rule.

    Args:
        candidate_id: ID of the candidate to review (e.g. 'cand_abc123').
        decision: 'approve', 'edit', or 'reject'.
        edited_rule_text: Refined rule text if decision is 'edit'.
        edited_scope: Optional modified scope dictionary.
        edited_constraint: Optional modified deterministic constraint dictionary.
        notes: Optional audit notes explaining the review rationale.

    Returns:
        JSON string containing ReviewResult.
    """
    scope = ActionContext(**edited_scope) if edited_scope else None
    result = service.review_memory_candidate(
        candidate_id=candidate_id,
        decision=decision,
        edited_rule_text=edited_rule_text,
        edited_scope=scope,
        edited_constraint=edited_constraint,
        notes=notes
    )
    return result.model_dump_json(indent=2)

# --- TOOL 4: GET COMPANY CONTEXT (MEMORY PACK) ---
@mcp.tool()
def get_company_context(
    system: str = "odoo",
    application: Optional[str] = None,
    resource: Optional[str] = None,
    operation: Optional[str] = None,
    fields: Optional[List[str]] = None
) -> str:
    """
    Retrieves a small, relevant Memory Pack of active canonical rules for the authenticated company.

    Args:
        system: Target system, defaults to 'odoo'.
        application: Target application, e.g. 'project'.
        resource: Target resource/model, e.g. 'project.task'.
        operation: Target operation, e.g. 'create'.
        fields: Optional list of field names, e.g. ['definition_of_done'].

    Returns:
        JSON string containing MemoryPack with active rules, versions, scopes, and constraints.
    """
    pack = service.get_company_context(
        system=system,
        application=application,
        resource=resource,
        operation=operation,
        fields=fields
    )
    return pack.model_dump_json(indent=2)

# --- TOOL 5: CREATE PROJECT TASK (MANAGED ODOO WRITE TOOL) ---
@mcp.tool()
def create_project_task(
    title: str,
    description: str,
    definition_of_done: Optional[List[str]] = None,
    project_id: Optional[int] = None,
    correlation_id: Optional[str] = None
) -> str:
    """
    Managed Odoo Task Creation Tool.
    Retrieves approved company memory internally, validates required fields (e.g. Definition of Done),
    creates an execution evidence record, and executes the task creation in Odoo with read-back verification.

    Args:
        title: Task name / title.
        description: Task description in plain text.
        definition_of_done: Optional list of measurable Definition of Done checklist items.
        project_id: Odoo project ID, defaults to 142 (IH/AI/Odoo Tutor).
        correlation_id: Unique idempotency key to prevent duplicate creation on retry.

    Returns:
        JSON string containing TaskCreationResult with run_id, correlation_id, status, odoo_task_id, and URL.
    """
    result = service.create_project_task(
        title=title,
        description=description,
        definition_of_done=definition_of_done,
        project_id=project_id,
        correlation_id=correlation_id
    )
    return result.model_dump_json(indent=2)

if __name__ == "__main__":
    mcp.run()
