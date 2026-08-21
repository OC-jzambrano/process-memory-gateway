import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.api.memory_tools import ProcessMemoryTools
from src.models.schemas import Principal

def live_chat_cli():
    tools = ProcessMemoryTools()
    client_id = "odooconcept_demo"
    process_name = "live_interaction"
    principal = Principal(client_id=client_id, user_id="cli_tester", role="consultant")

    print("=" * 75)
    print("ODOO PROCESS MEMORY - INTERACTIVE LIVE DIALOGUE TESTER")
    print(f"Connected to LLM Provider: {tools.extractor.provider_type.value.upper()}")
    print("=" * 75)
    print("Type any dialogue, consultant meeting notes, or Odoo instructions.")
    print("Type 'exit' or 'quit' to stop.\n")

    while True:
        try:
            user_input = input("\nEnter Odoo Dialogue / Notes > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                print("\nExiting Live Dialogue Tester. Goodbye!")
                break

            print("\nAnalyzing dialogue with LLM...")
            result = tools.extract_memory_candidates(
                interaction_text=user_input,
                client_id=client_id,
                process_name=process_name,
                principal=principal
            )

            print(f"\n[Extraction Mode: {result.extraction_mode.value.upper()}]")
            print(f"Session ID: {result.session_id}")
            print(f"Candidate Rules Found: {len(result.candidates)}")

            if not result.candidates:
                print("  No operational business rules detected (filtered out as general conversation / noise).")
            else:
                for idx, c in enumerate(result.candidates, 1):
                    print(f"\n  Candidate #{idx} [ID: {c.candidate_id[:16]}...]:")
                    print(f"    • Inferred Rule:   {c.rule_text}")
                    print(f"    • Category:        {c.rule_type.value}")
                    print(f"    • Severity:        {c.severity.value} (Enforcement: {c.enforcement_mode.value})")
                    print(f"    • Source Quote:    \"{c.source_quote}\"")
                    print(f"    • AI Confidence:   {c.confidence * 100:.1f}%")
                    print(f"    • Initial Status:  {c.status.value}")

            print("-" * 75)

        except KeyboardInterrupt:
            print("\nInterrupted. Exiting.")
            break
        except Exception as e:
            print(f"\n[Error]: {e}")

if __name__ == "__main__":
    live_chat_cli()
