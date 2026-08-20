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
    ESCALATE = "escalate"

class SourceType(str, Enum):
    USER_INTERACTION = "user_interaction"
    MEETING_TRANSCRIPT = "meeting_transcript"
    TICKET_COMMENT = "ticket_comment"
    SLACK_MESSAGE = "slack_message"
    DOCUMENT_UPLOAD = "document_upload"
