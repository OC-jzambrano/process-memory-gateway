resource "aws_cognito_user_pool" "pool" {
  name = "odoo-process-memory-${var.environment}-users"

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 7
  }

  auto_verified_attributes = ["email"]
  mfa_configuration        = "OFF"

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name = "odoo-process-memory-${var.environment}-user-pool"
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = "odoo-pm-${var.environment}-${substr(data.aws_caller_identity.current.account_id, -6, 6)}"
  user_pool_id = aws_cognito_user_pool.pool.id
}

# Resource Server defining resource-bound scope
resource "aws_cognito_resource_server" "mcp" {
  identifier   = "https://${var.domain_name}"
  name         = "Process Memory Gateway"
  user_pool_id = aws_cognito_user_pool.pool.id

  scope {
    scope_name        = "mcp:tools"
    scope_description = "Access Process Memory MCP Tools"
  }
}

# Public OAuth 2.0 Client with Authorization Code Flow + PKCE
resource "aws_cognito_user_pool_client" "mcp_client" {
  name         = "process-memory-${var.environment}-client"
  user_pool_id = aws_cognito_user_pool.pool.id

  generate_secret                      = false
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes = [
    "openid",
    "email",
    "${aws_cognito_resource_server.mcp.identifier}/mcp:tools"
  ]
  supported_identity_providers = ["COGNITO"]
  callback_urls                = var.approved_callback_urls
  logout_urls                  = ["http://localhost:3000/logout"]

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30
}
