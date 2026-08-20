import pytest
import sqlite3
from pathlib import Path

from src.storage.db import get_connection
from src.storage.repository import MemoryRepository
from src.models.schemas import Client, BusinessProcess

def test_schema_tables_created(temp_db):
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    table_names = [t["name"] for t in tables]
    conn.close()

    expected = [
        "clients",
        "business_processes",
        "extraction_sessions",
        "memory_candidates",
        "canonical_rules",
        "review_events"
    ]
    for table in expected:
        assert table in table_names, f"Table '{table}' was not created."

def test_indexes_created(temp_db):
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    indexes = cursor.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()
    index_names = [i["name"] for i in indexes]
    conn.close()

    expected_indexes = [
        "idx_candidates_client_status",
        "idx_candidates_process",
        "idx_rules_client_active",
        "idx_rules_process",
        "idx_review_candidate"
    ]
    for idx in expected_indexes:
        assert idx in index_names, f"Index '{idx}' was not created."
