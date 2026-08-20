import sqlite3
import pytest

def test_sql_injection_in_client_id_is_harmless(repo):
    """SQL injection via client_id should return empty results, not crash or leak data."""
    malicious_id = "'; DROP TABLE clients; --"
    result = repo.list_candidates(malicious_id)
    assert result == []

    # Table clients must still exist
    fetched = repo.get_client("test_client")
    assert fetched is not None

def test_fk_constraint_prevents_orphan_candidate(temp_db):
    """A candidate referencing a non-existent session_id must be rejected by FK."""
    from src.storage.db import get_connection
    conn = get_connection(temp_db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO memory_candidates 
            (candidate_id, session_id, client_id, rule_text, rule_type, source_quote, confidence) 
            VALUES ('orphan', 'nonexistent_session', 'test_client', 'test', 'approval_policy', 'test', 0.5)
            """
        )
    conn.close()
