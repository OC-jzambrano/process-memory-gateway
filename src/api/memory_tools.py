from typing import List, Optional, Union

from src.models.schemas import (
    CandidateRule,
    CanonicalRule,
    ExtractionResult,
    ExtractionSession,
    Principal
)
from src.models.enums import (
    RuleStatus,
    DecisionType,
    SourceType
)
from src.storage.repository import MemoryRepository
from src.extractor.service import BedrockExtractorService

class ProcessMemoryTools:
    """
    Core MCP-compatible toolset for Process Memory capture, governance, and retrieval.
    Enforces tenant authorization and immutable state transitions.
    """
    def __init__(
        self,
        repo: Optional[MemoryRepository] = None,
        extractor: Optional[BedrockExtractorService] = None
    ):
        self.repo = repo or MemoryRepository()
        self.extractor = extractor or BedrockExtractorService()

    def _resolve_principal(
        self,
        client_id: Optional[str] = None,
        principal: Optional[Union[Principal, str]] = None
    ) -> Principal:
        """Resolves and validates the security principal and tenant ID."""
        if isinstance(principal, Principal):
            if client_id and principal.client_id != client_id:
                raise PermissionError(f"Cross-tenant access forbidden: principal is '{principal.client_id}', requested '{client_id}'.")
            return principal
        elif isinstance(principal, str):
            tid = client_id or principal
            return Principal(client_id=tid, user_id=principal)
        elif client_id:
            return Principal(client_id=client_id, user_id="system_user")
        else:
            raise ValueError("Either client_id or an authenticated Principal must be provided.")

    # --- TOOL 1: Extract Memory Candidates from Conversation ---
    def extract_memory_candidates(
        self,
        interaction_text: str,
        client_id: str,
        process_name: str = "general",
        source_type: SourceType = SourceType.USER_INTERACTION,
        principal: Optional[Union[Principal, str]] = None
    ) -> ExtractionResult:
        """
        Analyzes conversational dialogue, infers candidate business rules, 
        persists extraction session provenance, and saves candidates in 'pending_review' status.
        """
        resolved = self._resolve_principal(client_id=client_id, principal=principal)
        effective_client_id = resolved.client_id

        result = self.extractor.extract_from_text(
            interaction_text=interaction_text,
            client_id=effective_client_id,
            process_name=process_name,
            source_type=source_type
        )

        # 1. Record provenance session in DB
        session = ExtractionSession(
            session_id=result.session_id,
            client_id=effective_client_id,
            process_name=process_name,
            source_type=source_type,
            interaction_text=interaction_text,
            model_id=self.extractor.model_id,
            candidates_extracted=len(result.candidates)
        )
        self.repo.create_session(session)

        # 2. Persist candidates as pending_review
        if result.candidates:
            self.repo.save_candidates(result.candidates)

        return result

    # --- TOOL 2: List Candidate Rules (Memory Inbox) ---
    def get_candidate_rules(
        self,
        client_id: str,
        status: Optional[RuleStatus] = RuleStatus.PENDING_REVIEW,
        process_name: Optional[str] = None,
        principal: Optional[Union[Principal, str]] = None
    ) -> List[CandidateRule]:
        """
        Retrieves candidate rules awaiting human review for a given client/tenant.
        """
        resolved = self._resolve_principal(client_id=client_id, principal=principal)
        return self.repo.list_candidates(
            client_id=resolved.client_id,
            status=status,
            process_name=process_name
        )

    # --- TOOL 3: Review Candidate Rule (Approve / Reject / Edit) ---
    def review_candidate_rule(
        self,
        candidate_id: str,
        decision: Union[DecisionType, str],
        reviewer: str,
        client_id: Optional[str] = None,
        edited_rule_text: Optional[str] = None,
        notes: Optional[str] = None,
        principal: Optional[Union[Principal, str]] = None
    ) -> Optional[CanonicalRule]:
        """
        Processes human sign-off on a candidate rule:
        - 'approve': Promotes candidate to active canonical rule (version 1).
        - 'edit': Promotes edited rule text to active canonical rule.
        - 'reject': Marks candidate as rejected.
        Records an immutable audit event in review_events.
        """
        if isinstance(decision, str):
            decision = DecisionType(decision.lower())

        resolved_client_id = None
        if principal:
            resolved = self._resolve_principal(client_id=client_id, principal=principal)
            resolved_client_id = resolved.client_id
        elif client_id:
            resolved_client_id = client_id

        return self.repo.review_candidate(
            candidate_id=candidate_id,
            decision=decision,
            reviewer=reviewer,
            client_id=resolved_client_id,
            edited_rule_text=edited_rule_text,
            notes=notes
        )

    # --- TOOL 4: Get Active Canonical Rules (Context Retrieval) ---
    def get_active_rules(
        self,
        client_id: str,
        process_name: Optional[str] = None,
        principal: Optional[Union[Principal, str]] = None
    ) -> List[CanonicalRule]:
        """
        Retrieves approved active business rules for a given client/process.
        Guarantees that pending_review candidate rules are NEVER returned.
        """
        resolved = self._resolve_principal(client_id=client_id, principal=principal)
        return self.repo.get_active_rules(
            client_id=resolved.client_id,
            process_name=process_name
        )
