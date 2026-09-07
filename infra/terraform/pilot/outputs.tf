output "elastic_ip" {
  value       = aws_eip.mcp_eip.public_ip
  description = "Public Elastic IP address for the DNS A record pointing to domain_name"
}

output "ec2_instance_id" {
  value       = aws_instance.mcp_host.id
  description = "EC2 instance ID for SSM Session Manager deployment"
}

output "ebs_volume_id" {
  value       = aws_ebs_volume.memory_data.id
  description = "Persistent 20 GiB EBS volume ID"
}

output "backup_bucket_name" {
  value       = aws_s3_bucket.backups.id
  description = "S3 bucket storing encrypted SQLite backups"
}

output "cognito_user_pool_id" {
  value       = aws_cognito_user_pool.pool.id
  description = "Cognito User Pool ID"
}

output "cognito_app_client_id" {
  value       = aws_cognito_user_pool_client.mcp_client.id
  description = "Cognito App Client ID (Public PKCE)"
}

output "cognito_domain" {
  value       = "${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
  description = "Cognito Managed Login domain"
}

output "cognito_discovery_url" {
  value       = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.pool.id}/.well-known/openid-configuration"
  description = "OpenID Connect discovery URL"
}

output "mcp_endpoint_url" {
  value       = "https://${var.domain_name}/companies/${var.company_slug}/mcp"
  description = "Hosted Streamable HTTP MCP endpoint URL"
}

output "ecr_repository_url" {
  value       = aws_ecr_repository.app.repository_url
  description = "Amazon ECR Repository URL"
}

