from enum import Enum

class RuleType(str, Enum):
    APPROVAL_POLICY = "approval_policy"
    NAMING_CONVENTION = "naming_convention"
    DATA_VALIDATION = "data_validation"
    BUSINESS_PREFERENCE = "business_preference"
    OPERATIONAL_CONSTRAINT = "operational_constraint"
    SECURITY_RESTRICTION = "security_restriction"

class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"

class RuleStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"

class EnforcementMode(str, Enum):
    BLOCKING = "blocking"
    REQUIRES_APPROVAL = "requires_approval"
    ADVISORY = "advisory"

class DecisionType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    SUPERSEDE = "supersede"
    ESCALATE = "escalate"

class EventType(str, Enum):
    CANDIDATE_REVIEW = "candidate_review"
    RULE_SUPERSEDED = "rule_superseded"
    RULE_ARCHIVED = "rule_archived"

class SourceType(str, Enum):
    USER_INTERACTION = "user_interaction"
    MEETING_TRANSCRIPT = "meeting_transcript"
    TICKET_COMMENT = "ticket_comment"
    SLACK_MESSAGE = "slack_message"
    DOCUMENT_UPLOAD = "document_upload"

class LLMProviderType(str, Enum):
    OPENAI = "openai"
    BEDROCK = "bedrock"
    AUTO = "auto"
    LOCAL = "local"

class ExtractionMode(str, Enum):
    OPENAI_LLM = "openai_llm"
    BEDROCK_LLM = "bedrock_llm"
    LOCAL_FALLBACK = "local_fallback"

class RoleType(str, Enum):
    OWNER = "owner"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    AUDITOR = "auditor"
    MEMBER = "member"

class RunStatus(str, Enum):
    NEEDS_CLARIFICATION = "needs_clarification"
    CREATED = "created"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"

class ConstraintKind(str, Enum):
    REQUIRED_NONEMPTY_LIST = "required_nonempty_list"
    PATTERN_MATCH = "pattern_match"
    THRESHOLD_LIMIT = "threshold_limit"

class CompanyStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"

class MembershipStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    INVITED = "invited"

class ExecutionEventType(str, Enum):
    RUN_STARTED = "run_started"
    VALIDATION_FAILED = "validation_failed"
    TASK_CREATED = "task_created"
    RECONCILIATION_FLAGGED = "reconciliation_flagged"
