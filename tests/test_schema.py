import pytest
import sqlite3
from pathlib import Path
import tempfile

from src.storage.db import init_db, get_connection
from src.storage.repository import MemoryRepository
from src.models.schemas import Client, BusinessProcess

@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    init_db(db_path)
    yield db_path
    if db_path.exists():
        db_path.unlink()

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

def test_client_and_process_crud(temp_db):
    repo = MemoryRepository(db_path=temp_db)
    client = Client(
        client_id="client_test_01",
        client_name="Test Enterprise Inc.",
        industry="manufacturing",
        odoo_url="https://test.odoo.com"
    )
    repo.upsert_client(client)

    fetched = repo.get_client("client_test_01")
    assert fetched is not None
    assert fetched.client_name == "Test Enterprise Inc."

    process = BusinessProcess(
        process_id="proc_mrp_01",
        client_id="client_test_01",
        process_name="manufacturing_setup",
        description="MRP module and BOM configuration"
    )
    repo.add_process(process)
