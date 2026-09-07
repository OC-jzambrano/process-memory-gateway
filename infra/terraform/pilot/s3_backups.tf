resource "aws_s3_bucket" "backups" {
  bucket        = "odoo-process-memory-backups-${data.aws_caller_identity.current.account_id}-${var.environment}"
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name    = "odoo-process-memory-backups-${var.environment}"
    Purpose = "Encrypted SQLite online backups"
  }
}

resource "aws_s3_bucket_versioning" "backup_versioning" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup_encryption" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "backup_public_block" {
  bucket = aws_s3_bucket.backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "backup_lifecycle" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "retention-policy"
    status = "Enabled"

    filter {}

    # Retain noncurrent versions for 7 days (hourly point-in-time recovery)
    noncurrent_version_expiration {
      noncurrent_days = 7
    }

    # Expire all backup objects after 30 days
    expiration {
      days = 30
    }
  }
}
