resource "aws_iam_role" "ec2_role" {
  name = "odoo-process-memory-${var.environment}-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "odoo-process-memory-${var.environment}-ec2-role"
  }
}

# 1. AWS Systems Manager Managed Instance Core (no SSH required)
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# 2. Least-privilege application policy
resource "aws_iam_policy" "app_policy" {
  name        = "odoo-process-memory-${var.environment}-app-policy"
  description = "Permissions for Bedrock, Secrets Manager, S3 backups, and ECR pull"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        # Amazon Bedrock
        {
          Sid    = "BedrockInvoke"
          Effect = "Allow"
          Action = [
            "bedrock:InvokeModel",
            "bedrock:InvokeModelWithResponseStream"
          ]
          Resource = "*"
        },
        # S3 Encrypted Backups
        {
          Sid    = "S3BackupAccess"
          Effect = "Allow"
          Action = [
            "s3:PutObject",
            "s3:GetObject",
            "s3:ListBucket"
          ]
          Resource = [
            aws_s3_bucket.backups.arn,
            "${aws_s3_bucket.backups.arn}/*"
          ]
        },
        # Amazon ECR image pull
        {
          Sid    = "ECRImagePull"
          Effect = "Allow"
          Action = [
            "ecr:GetAuthorizationToken",
            "ecr:BatchCheckLayerAvailability",
            "ecr:GetDownloadUrlForLayer",
            "ecr:BatchGetImage"
          ]
          Resource = "*"
        },
        # CloudWatch Logs
        {
          Sid    = "CloudWatchLogs"
          Effect = "Allow"
          Action = [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents",
            "logs:DescribeLogStreams"
          ]
          Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/ec2/odoo-process-memory-*"
        }
      ],
      var.odoo_secret_arn != "" ? [
        {
          Sid      = "SecretsManagerOdooAccess"
          Effect   = "Allow"
          Action   = ["secretsmanager:GetSecretValue"]
          Resource = var.odoo_secret_arn
        }
      ] : []
    )
  })
}

resource "aws_iam_role_policy_attachment" "app_policy_attach" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.app_policy.arn
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "odoo-process-memory-${var.environment}-instance-profile"
  role = aws_iam_role.ec2_role.name
}
