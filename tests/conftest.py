import sys
import pytest
import tempfile
import uuid
from pathlib import Path

# Ensure project root is on sys.path for absolute imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.repository import MemoryRepository
from src.storage.db import init_db
from src.extractor.service import BedrockExtractorService
from src.api.memory_tools import ProcessMemoryTools
from src.models.schemas import Client, ExtractionSession, CandidateRule, Principal
from src.models.enums import (
    RuleStatus, RuleType, Severity, EnforcementMode, DecisionType
)

# --- PYTEST MARKERS ---
def pytest_configure(config):
    config.addinivalue_line("markers", "ai: Tests requiring live AWS Bedrock (deselect with -m 'not ai')")
    config.addinivalue_line("markers", "slow: Tests taking > 10 seconds")

# --- FIXTURE: Temporary Database ---
@pytest.fixture
def temp_db():
    """Creates a fresh SQLite database for each test, cleaned up after."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    init_db(db_path)
    yield db_path
    if db_path.exists():
        try:
            db_path.unlink()
        except Exception:
            pass

# --- FIXTURE: Repository with seeded client ---
@pytest.fixture
def repo(temp_db):
    """Repository with a single seeded client 'test_client'."""
    r = MemoryRepository(db_path=temp_db)
    r.upsert_client(Client(client_id="test_client", client_name="Test Corp"))
    return r

# --- FIXTURE: Repository with two clients (multi-tenant tests) ---
@pytest.fixture
def repo_two_clients(temp_db):
    """Repository with two isolated tenants."""
    r = MemoryRepository(db_path=temp_db)
    r.upsert_client(Client(client_id="client_a", client_name="Company A"))
    r.upsert_client(Client(client_id="client_b", client_name="Company B"))
    return r

# --- FIXTURE: ProcessMemoryTools with deterministic offline extractor ---
@pytest.fixture
def memory_tools(temp_db):
    """Full tools stack with deterministic offline extractor for instant test runs."""
    r = MemoryRepository(db_path=temp_db)
    r.upsert_client(Client(client_id="test_client", client_name="Test Corp"))
    extractor = BedrockExtractorService(offline_mode=True)
    return ProcessMemoryTools(repo=r, extractor=extractor)

# --- FIXTURE: Helper to create and save a candidate ---
@pytest.fixture
def make_candidate(repo):
    """Factory fixture that creates and saves a candidate rule with valid session provenance."""
    def _make(
        candidate_id=None,
        client_id="test_client",
        process_name="general",
        rule_text="Test rule statement.",
        rule_type=RuleType.OPERATIONAL_CONSTRAINT,
        session_id=None
    ):
        cid = candidate_id or f"cand_{uuid.uuid4().hex}"
        sid = session_id or f"sess_{uuid.uuid4().hex}"

        session = ExtractionSession(
            session_id=sid,
            client_id=client_id,
            process_name=process_name,
            interaction_text="Test interaction text.",
            model_id="test-model",
            candidates_extracted=1
        )
        repo.create_session(session)

        cand = CandidateRule(
            candidate_id=cid,
            session_id=sid,
            client_id=client_id,
            process_name=process_name,
            rule_text=rule_text,
            rule_type=rule_type,
            severity=Severity.WARNING,
            enforcement_mode=EnforcementMode.ADVISORY,
            source_quote="Test interaction text",
            confidence=0.90,
            status=RuleStatus.PENDING_REVIEW
        )
        repo.save_candidates([cand])
        return cand
    return _make

# --- BENCHMARK DIALOGUE (reused across layers) ---
BENCHMARK_DIALOGUE = (
    "In this company, Manufacturing is only installed with approval from the Operations Lead. "
    "BOMs must include version numbers. Do not create duplicate components if the SKU already exists."
)
