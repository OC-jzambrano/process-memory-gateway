import sys
import sqlite3
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import DEFAULT_DB_PATH

def inspect_database():
    db_path = DEFAULT_DB_PATH
    if not db_path.exists():
        print(f"Database not found at: {db_path}")
        return

    print("=" * 80)
    print("PROCESS MEMORY GATEWAY - DATABASE INSPECTOR")
    print(f"File Location: {db_path}")
    print("=" * 80)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Extraction Sessions
    print("\n--- 1. EXTRACTION SESSIONS (Provenance Records) ---")
    sessions = cursor.execute("SELECT session_id, client_id, process_name, model_id, candidates_extracted, extracted_at FROM extraction_sessions ORDER BY extracted_at DESC").fetchall()
    if not sessions:
        print("  (No sessions recorded yet)")
    for s in sessions:
        print(f"  • Session ID: {s['session_id']}")
        print(f"    Client: {s['client_id']} | Process: {s['process_name']} | Model: {s['model_id']}")
        print(f"    Candidates Extracted: {s['candidates_extracted']} | Timestamp: {s['extracted_at']}\n")

    # 2. Memory Candidates
    print("--- 2. MEMORY CANDIDATES (AI Inferred Rules) ---")
    candidates = cursor.execute("SELECT candidate_id, client_id, rule_text, rule_type, severity, confidence, status, source_quote FROM memory_candidates ORDER BY created_at DESC").fetchall()
    if not candidates:
        print("  (No candidates recorded yet)")
    for c in candidates:
        print(f"  • Candidate [{c['candidate_id'][:16]}...] Status: [{c['status'].upper()}]")
        print(f"    Statement: {c['rule_text']}")
        print(f"    Type: {c['rule_type']} | Severity: {c['severity']} | Confidence: {c['confidence']*100:.1f}%")
        print(f"    Source Quote: \"{c['source_quote']}\"\n")

    # 3. Canonical Rules (Active Knowledge)
    print("--- 3. CANONICAL RULES (Active Approved Memory) ---")
    rules = cursor.execute("SELECT rule_id, client_id, process_name, version, rule_type, severity, rule_text, approved_by, status FROM canonical_rules ORDER BY version DESC, created_at DESC").fetchall()
    if not rules:
        print("  (No canonical rules approved yet)")
    for r in rules:
        print(f"  • Rule [{r['rule_id'][:16]}...] Version: v{r['version']} Status: [{r['status'].upper()}]")
        print(f"    Client: {r['client_id']} | Process: {r['process_name']}")
        print(f"    Statement: {r['rule_text']}")
        print(f"    Approved By: {r['approved_by']} | Type: {r['rule_type']} | Severity: {r['severity']}\n")

    # 4. Review Events (Immutable Audit Trail)
    print("--- 4. REVIEW EVENTS (Append-Only Audit Trail) ---")
    events = cursor.execute("SELECT event_id, client_id, candidate_id, rule_id, event_type, reviewer, decision, notes, created_at FROM review_events ORDER BY created_at DESC").fetchall()
    if not events:
        print("  (No review events recorded yet)")
    for e in events:
        print(f"  • Event [{e['event_id'][:16]}...] Decision: [{e['decision'].upper()}] by {e['reviewer']}")
        print(f"    Type: {e['event_type']} | Client: {e['client_id']} | Target Candidate/Rule: {e['candidate_id'] or e['rule_id']}")
        print(f"    Notes: {e['notes'] or 'N/A'} | Timestamp: {e['created_at']}\n")

    conn.close()
    print("=" * 80)

if __name__ == "__main__":
    inspect_database()
