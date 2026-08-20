import pytest
from pathlib import Path
import tempfile

from src.api.memory_tools import ProcessMemoryTools
from src.storage.repository import MemoryRepository
from src.extractor.service import BedrockExtractorService
from src.models.schemas import Client
from src.models.enums import RuleStatus, RuleType

@pytest.fixture
def memory_tools():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    repo = MemoryRepository(db_path=db_path)
    client = Client(client_id="benchmark_client", client_name="Benchmark Client Ltd.")
    repo.upsert_client(client)
    
    extractor = BedrockExtractorService()
    tools = ProcessMemoryTools(repo=repo, extractor=extractor)
    yield tools
    if db_path.exists():
        db_path.unlink()

def test_bedrock_extract_three_benchmark_rules(memory_tools):
    """
    Tests the real Bedrock extraction pipeline on the benchmark dialogue:
    - Rule 1: Approval policy for Manufacturing installation.
    - Rule 2: Naming convention for BOMs (versioning).
    - Rule 3: Data validation (no duplicate SKU components).
    """
    dialogue = (
        "In this company, Manufacturing is only installed with approval from the Operations Lead. "
        "BOMs must include version numbers. Do not create duplicate components if the SKU already exists."
    )

    result = memory_tools.extract_memory_candidates(
        interaction_text=dialogue,
        client_id="benchmark_client",
        process_name="manufacturing_setup"
    )

    assert result.session_id is not None
    assert len(result.candidates) >= 3, f"Expected at least 3 candidates, got {len(result.candidates)}"

    # Check rule types extracted
    rule_types = [c.rule_type for c in result.candidates]
    assert RuleType.APPROVAL_POLICY in rule_types or any("approval" in c.rule_text.lower() for c in result.candidates)
    
    # Check that all candidates start in pending_review
    for cand in result.candidates:
        assert cand.status == RuleStatus.PENDING_REVIEW
        assert cand.confidence >= 0.70
        assert len(cand.source_quote) > 0

    # Verify pending candidates are in database inbox
    pending_inbox = memory_tools.get_candidate_rules("benchmark_client")
    assert len(pending_inbox) == len(result.candidates)

    # Verify active rules are 0 before review
    active = memory_tools.get_active_rules("benchmark_client")
    assert len(active) == 0
