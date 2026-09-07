#!/usr/bin/env python3
import argparse
import gzip
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def perform_consistent_backup(src_db_path: Path, backup_output_path: Path) -> None:
    """
    Creates a consistent SQLite backup using sqlite3.Connection.backup API.
    Does not lock out active WAL readers or writers.
    """
    if not src_db_path.exists():
        raise FileNotFoundError(f"Source database file not found: {src_db_path}")

    backup_output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_backup = backup_output_path.with_suffix(".tmp")
    if temp_backup.exists():
        temp_backup.unlink()

    src_conn = sqlite3.connect(str(src_db_path), timeout=30.0)
    dest_conn = sqlite3.connect(str(temp_backup))
    try:
        # Stepwise online backup without blocking readers/writers
        src_conn.backup(dest_conn, pages=250)
    finally:
        dest_conn.close()
        src_conn.close()

    # Verify integrity of the backup file before completing
    verify_conn = sqlite3.connect(str(temp_backup))
    try:
        cursor = verify_conn.cursor()
        res = cursor.execute("PRAGMA integrity_check;").fetchone()
        if not res or res[0] != "ok":
            raise RuntimeError(f"Integrity check failed for backup: {res}")
    finally:
        verify_conn.close()

    # Move to destination
    if backup_output_path.exists():
        backup_output_path.unlink()
    temp_backup.rename(backup_output_path)
    logger.info("Consistent SQLite backup created at %s", backup_output_path)


def compress_file(input_path: Path, output_gz_path: Path) -> Path:
    """Compresses the backup file with gzip."""
    with open(input_path, "rb") as f_in, gzip.open(output_gz_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    return output_gz_path


def upload_to_s3(file_path: Path, bucket: str, s3_key: str) -> None:
    """Uploads backup file to S3 with server-side encryption enabled."""
    import boto3

    s3 = boto3.client("s3")
    logger.info(
        "Uploading %s to s3://%s/%s (AES256 encrypted)...", file_path, bucket, s3_key
    )
    s3.upload_file(
        str(file_path), bucket, s3_key, ExtraArgs={"ServerSideEncryption": "AES256"}
    )
    logger.info("Upload completed successfully.")


def restore_backup(backup_gz_path: Path, target_db_path: Path) -> None:
    """Restores database from compressed or plain backup and verifies integrity."""
    logger.info("Restoring backup from %s to %s...", backup_gz_path, target_db_path)
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = target_db_path.with_suffix(".restoring.tmp")

    if str(backup_gz_path).endswith(".gz"):
        with gzip.open(backup_gz_path, "rb") as f_in, open(temp_dest, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    else:
        shutil.copyfile(backup_gz_path, temp_dest)

    # Verify integrity
    conn = sqlite3.connect(str(temp_dest))
    try:
        cursor = conn.cursor()
        res = cursor.execute("PRAGMA integrity_check;").fetchone()
        if not res or res[0] != "ok":
            raise RuntimeError(f"Integrity check failed on restored database: {res}")
        logger.info("Integrity check passed.")
    finally:
        conn.close()

    if target_db_path.exists():
        backup_old = target_db_path.with_suffix(
            f".pre_restore_{int(datetime.now(timezone.utc).timestamp())}"
        )
        target_db_path.rename(backup_old)
        logger.info("Existing database moved to %s", backup_old)

    temp_dest.rename(target_db_path)
    logger.info("Database restoration complete at %s", target_db_path)


def main():
    parser = argparse.ArgumentParser(
        description="Process Memory SQLite Consistent Backup and Restore Utility"
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("PROCESS_MEMORY_DB_PATH", "data/process_memory.db"),
        help="Path to SQLite DB",
    )
    parser.add_argument(
        "--s3-bucket",
        default=os.getenv("BACKUP_S3_BUCKET"),
        help="S3 bucket for backups",
    )
    parser.add_argument(
        "--local-dir", default="data/backups", help="Local backup directory"
    )
    parser.add_argument("--restore", help="Path to backup file (.db or .gz) to restore")
    parser.add_argument("--restore-target", help="Target path for restored database")

    args = parser.parse_args()

    if args.restore:
        target = Path(args.restore_target or args.db_path)
        restore_backup(Path(args.restore), target)
        return

    db_path = Path(args.db_path).resolve()
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    local_dir = Path(args.local_dir).resolve()
    backup_file = local_dir / f"process_memory_{ts}.db"
    backup_gz = local_dir / f"process_memory_{ts}.db.gz"

    # Step 1: Online consistent backup
    perform_consistent_backup(db_path, backup_file)

    # Step 2: Compress
    compress_file(backup_file, backup_gz)
    backup_file.unlink()  # Remove uncompressed file to save disk

    # Step 3: Upload to S3 if configured
    if args.s3_bucket:
        s3_key = f"backups/{now.year:04d}/{now.month:02d}/{now.day:02d}/process_memory_{ts}.db.gz"
        upload_to_s3(backup_gz, args.s3_bucket, s3_key)

    logger.info("Backup workflow finished. Stored at %s", backup_gz)


if __name__ == "__main__":
    main()
