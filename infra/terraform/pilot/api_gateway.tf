# Public HTTPS entry point for the single EC2 pilot host.
# API Gateway terminates client TLS and proxies the MCP path to Caddy over HTTP.
resource "aws_apigatewayv2_api" "mcp" {
  name          = "odoo-process-memory-${var.environment}"
  protocol_type = "HTTP"
  description   = "HTTPS MCP entry point for the Odoo Process Memory pilot"
}

resource "aws_apigatewayv2_integration" "mcp_ec2" {
  api_id                 = aws_apigatewayv2_api.mcp.id
  integration_type       = "HTTP_PROXY"
  integration_method     = "ANY"
  integration_uri        = "http://${aws_eip.mcp_eip.public_ip}"
  payload_format_version = "1.0"
  timeout_milliseconds    = 29000

  request_parameters = {
    "overwrite:header.Host" = aws_eip.mcp_eip.public_ip
  }
}

resource "aws_apigatewayv2_route" "mcp_proxy" {
  api_id    = aws_apigatewayv2_api.mcp.id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.mcp_ec2.id}"
}

resource "aws_apigatewayv2_stage" "mcp_default" {
  api_id      = aws_apigatewayv2_api.mcp.id
  name        = "$default"
  auto_deploy = true
}
