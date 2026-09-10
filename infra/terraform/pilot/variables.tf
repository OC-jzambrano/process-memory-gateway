variable "aws_region" {
  type        = string
  description = "AWS region for deployment"
  default     = "eu-north-1"
}

variable "environment" {
  type        = string
  description = "Environment identifier (e.g. pilot, production)"
  default     = "pilot"
}

variable "domain_name" {
  type        = string
  description = "Public domain name pointing to the MCP server Elastic IP (e.g. mcp.odooconcept.com)"
  default     = "mcp.example.com"
}

variable "company_slug" {
  type        = string
  description = "Identifier for the provisioned company"
  default     = "odooconcept_demo"
}

variable "odoo_secret_arn" {
  type        = string
  description = "ARN of the AWS Secrets Manager secret storing Odoo credentials (must not be in TF state)"
  default     = ""
}

variable "bedrock_model_id" {
  type        = string
  description = "Amazon Bedrock model ID for candidate extraction"
  default     = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "budget_alert_email" {
  type        = string
  description = "Email recipient for AWS budget notifications"
  default     = "jzambrano@odooconcept.com"
}

variable "budget_limit_usd" {
  type        = number
  description = "Monthly budget limit in USD"
  default     = 50
}

variable "ec2_instance_type" {
  type        = string
  description = "EC2 instance size for the low-cost pilot host"
  default     = "t3.small"
}

variable "ebs_volume_size_gb" {
  type        = number
  description = "Size of the persistent SQLite EBS data volume in GiB"
  default     = 20
}

variable "approved_callback_urls" {
  type        = list(string)
  description = "Approved OAuth 2.0 redirect callback URLs"
  default = [
    "http://localhost:3000/callback",
    "http://127.0.0.1/callback/AvYszWqvA9eg",
    "https://oauth.pstmn.io/v1/callback"
  ]
}
