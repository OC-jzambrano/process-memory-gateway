import gzip
import sqlite3
import pytest
from pathlib import Path
from scripts.backup_sqlite import perform_consistent_backup, compress_file, restore_backup
from src.storage.db import init_db

def test_consistent_backup_and_restore(tmp_path):
    # 1. Create a source database with WAL mode and sample records
    src_db = tmp_path / "source.db"
    init_db(src_db)

    conn = sqlite3.connect(str(src_db))
    with conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("INSERT INTO clients (client_id, client_name) VALUES ('client_test', 'Test Client');")
        conn.execute("INSERT INTO companies (company_id, company_slug, name) VALUES ('co_1', 'co_1', 'Company 1');")
    conn.close()

    # 2. Perform online consistent backup
    backup_file = tmp_path / "backup.db"
    perform_consistent_backup(src_db, backup_file)
    assert backup_file.exists()

    # Verify backup integrity
    bconn = sqlite3.connect(str(backup_file))
    res = bconn.execute("PRAGMA integrity_check;").fetchone()
    assert res[0] == "ok"
    bconn.close()

    # 3. Compress
    backup_gz = tmp_path / "backup.db.gz"
    compress_file(backup_file, backup_gz)
    assert backup_gz.exists()

    # 4. Restore to a new location
    restored_db = tmp_path / "restored.db"
    restore_backup(backup_gz, restored_db)
    assert restored_db.exists()

    # 5. Verify restored database has identical records
    rconn = sqlite3.connect(str(restored_db))
    r_client = rconn.execute("SELECT client_name FROM clients WHERE client_id = 'client_test';").fetchone()
    assert r_client is not None
    assert r_client[0] == "Test Client"

    r_co = rconn.execute("SELECT name FROM companies WHERE company_id = 'co_1';").fetchone()
    assert r_co is not None
    assert r_co[0] == "Company 1"
    rconn.close()
