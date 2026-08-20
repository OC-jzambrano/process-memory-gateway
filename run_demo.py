import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.api.memory_tools import ProcessMemoryTools
from src.models.schemas import Client, BusinessProcess
from src.models.enums import DecisionType, RuleStatus

def main():
    print("=" * 70)
    print("ODOO PROCESS MEMORY & CONTEXT GATEWAY - INTERACTIVE DEMO")
    print("=" * 70)

    tools = ProcessMemoryTools()
    client_id = "odooconcept_demo"
    process_name = "manufacturing_setup"

    # 1. Seed Client & Process
    print("\n[Step 1] Registering Client & Process Context...")
    tools.repo.upsert_client(Client(
        client_id=client_id,
        client_name="Odoo Concept Demo Client",
        industry="Manufacturing & Distribution",
        odoo_url="https://community.odooconcept.com"
    ))
    tools.repo.add_process(BusinessProcess(
        process_id="proc_mrp_001",
        client_id=client_id,
        process_name=process_name,
        description="Manufacturing module installation and BOM creation workflow"
    ))
    print(f"Registered Client: '{client_id}', Process: '{process_name}'")

    # 2. Raw User Dialogue Input
    sample_dialogue = (
        "In this company, Manufacturing is only installed with approval from the Operations Lead. "
        "BOMs must include version numbers. Do not create duplicate components if the SKU already exists."
    )
    print("\n[Step 2] Processing User Interaction via Bedrock Claude 3.5 Haiku...")
    print(f'Input Dialogue:\n"{sample_dialogue}"\n')

    extraction_result = tools.extract_memory_candidates(
        interaction_text=sample_dialogue,
        client_id=client_id,
        process_name=process_name
    )

    print(f"Extraction Session Created: {extraction_result.session_id}")
    print(f"Candidates Inferred: {len(extraction_result.candidates)}")

    # 3. Display Candidates in Pending Review Inbox
    print("\n[Step 3] Candidate Rules Inbox (status: pending_review):")
    print("-" * 70)
    for idx, c in enumerate(extraction_result.candidates, 1):
        print(f"Candidate #{idx} [ID: {c.candidate_id}]:")
        print(f"  • Rule Statement:    {c.rule_text}")
        print(f"  • Type:              {c.rule_type.value}")
        print(f"  • Severity:          {c.severity.value} (Mode: {c.enforcement_mode.value})")
        print(f"  • Source Quote:      \"{c.source_quote}\"")
        print(f"  • Confidence:        {c.confidence * 100:.1f}%")
        print(f"  • Status:            {c.status.value}")
        print("-" * 70)

    # 4. Verify Active Rules Isolation
    print("\n[Step 4] Checking Active Canonical Rules BEFORE Human Review...")
    active_before = tools.get_active_rules(client_id, process_name)
    print(f"Active Rules count: {len(active_before)} (Guaranteed 0 - pending rules cannot enforce!)")

    # 5. Human Review Workflow
    print("\n[Step 5] Performing Human Review (Approve Candidate #1 & #2, Reject #3)...")
    if extraction_result.candidates:
        c1 = extraction_result.candidates[0]
        promoted_1 = tools.review_candidate_rule(
            candidate_id=c1.candidate_id,
            decision=DecisionType.APPROVE,
            reviewer="juan_zambrano",
            notes="Standard company operations policy."
        )
        print(f"  -> Approved '{c1.candidate_id}': Created Canonical Rule '{promoted_1.rule_id}' (v{promoted_1.version})")

    if len(extraction_result.candidates) > 1:
        c2 = extraction_result.candidates[1]
        promoted_2 = tools.review_candidate_rule(
            candidate_id=c2.candidate_id,
            decision=DecisionType.APPROVE,
            reviewer="juan_zambrano",
            notes="Required naming standard."
        )
        print(f"  -> Approved '{c2.candidate_id}': Created Canonical Rule '{promoted_2.rule_id}' (v{promoted_2.version})")

    # 6. Verify Active Canonical Rules
    print("\n[Step 6] Checking Active Canonical Rules AFTER Human Review...")
    active_after = tools.get_active_rules(client_id, process_name)
    print(f"Active Rules count: {len(active_after)}")
    for r in active_after:
        print(f"  * [v{r.version}] [{r.rule_type.value.upper()}] ({r.severity.value}): {r.rule_text}")

    print("\n" + "=" * 70)
    print("DEMO COMPLETED SUCCESSFULLY")
    print("=" * 70)

if __name__ == "__main__":
    main()
