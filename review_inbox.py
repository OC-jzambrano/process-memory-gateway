import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.api.memory_tools import ProcessMemoryTools
from src.models.schemas import Principal
from src.models.enums import DecisionType, RuleStatus

def interactive_review_session():
    tools = ProcessMemoryTools()
    
    print("=" * 70)
    print("PROCESS MEMORY - INTERACTIVE HUMAN REVIEW CLI")
    print("=" * 70)
    
    reviewer_name = input("Enter your name (Reviewer ID) [default: juan_zambrano]: ").strip() or "juan_zambrano"
    client_id = input("Enter Client ID [default: odooconcept_demo]: ").strip() or "odooconcept_demo"
    
    principal = Principal(client_id=client_id, user_id=reviewer_name, role="human_reviewer")
    
    pending_candidates = tools.get_candidate_rules(
        client_id=client_id,
        status=RuleStatus.PENDING_REVIEW,
        principal=principal
    )
    
    if not pending_candidates:
        print(f"\nNo pending candidate rules found for client '{client_id}'. All rules are reviewed!")
        return

    print(f"\nFound {len(pending_candidates)} candidate rule(s) awaiting review for '{client_id}'.\n")

    for idx, cand in enumerate(pending_candidates, 1):
        print("=" * 70)
        print(f"Candidate {idx} of {len(pending_candidates)} [ID: {cand.candidate_id}]")
        print(f"  • Process Context:  {cand.process_name}")
        print(f"  • Inferred Rule:    {cand.rule_text}")
        print(f"  • Rule Type:        {cand.rule_type.value}")
        print(f"  • Severity:         {cand.severity.value} (Mode: {cand.enforcement_mode.value})")
        print(f"  • Source Quote:     \"{cand.source_quote}\"")
        print(f"  • AI Confidence:    {cand.confidence * 100:.1f}%")
        print("-" * 70)
        
        choice = input("Action: [A]pprove | [R]eject | [E]dit & Approve | [S]kip : ").strip().upper()
        
        if choice == "A":
            notes = input("Optional review notes: ").strip() or None
            rule = tools.review_candidate_rule(
                candidate_id=cand.candidate_id,
                decision=DecisionType.APPROVE,
                reviewer=reviewer_name,
                client_id=client_id,
                notes=notes,
                principal=principal
            )
            print(f"  [SUCCESS] Approved! Created Canonical Rule '{rule.rule_id}' (version {rule.version}).\n")
            
        elif choice == "R":
            notes = input("Rejection reason / notes: ").strip() or None
            tools.review_candidate_rule(
                candidate_id=cand.candidate_id,
                decision=DecisionType.REJECT,
                reviewer=reviewer_name,
                client_id=client_id,
                notes=notes,
                principal=principal
            )
            print(f"  [SUCCESS] Rejected candidate '{cand.candidate_id}'. Marked as rejected.\n")
            
        elif choice == "E":
            print(f"Current text: {cand.rule_text}")
            edited_text = input("Enter new edited rule statement: ").strip()
            if not edited_text:
                print("  [CANCELLED] Edit text cannot be blank. Skipped.")
                continue
            notes = input("Optional review notes: ").strip() or None
            rule = tools.review_candidate_rule(
                candidate_id=cand.candidate_id,
                decision=DecisionType.EDIT,
                reviewer=reviewer_name,
                client_id=client_id,
                edited_rule_text=edited_text,
                notes=notes,
                principal=principal
            )
            print(f"  [SUCCESS] Edited & Approved! Created Canonical Rule '{rule.rule_id}' with your refined text.\n")
            
        elif choice == "S":
            print("  Skipped candidate.\n")
            
        else:
            print("  Invalid choice. Skipped candidate.\n")

    print("=" * 70)
    print("Review session completed. Active rules updated.")
    print("=" * 70)

if __name__ == "__main__":
    interactive_review_session()
