resource "aws_security_group" "mcp_host" {
  name        = "odoo-process-memory-${var.environment}-sg"
  description = "Security group for MCP host: Public HTTP/HTTPS only, SSH strictly closed"
  vpc_id      = aws_vpc.pilot_vpc.id

  # HTTP (for Caddy ACME challenge & HTTP->HTTPS redirect)
  ingress {
    description      = "Public HTTP for ACME challenges and HTTPS redirects"
    from_port        = 80
    to_port          = 80
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  # HTTPS (for secure MCP endpoints)
  ingress {
    description      = "Public HTTPS for Streamable HTTP MCP server"
    from_port        = 443
    to_port          = 443
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  # HTTP/3 (UDP 443 for QUIC)
  ingress {
    description      = "Public HTTP/3 QUIC"
    from_port        = 443
    to_port          = 443
    protocol         = "udp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  # Note: Port 22 (SSH) is intentionally NOT present. Administration is performed via AWS SSM Session Manager.

  egress {
    description      = "Outbound internet access for Odoo XML-RPC, Bedrock API, and backups"
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = {
    Name = "odoo-process-memory-${var.environment}-sg"
  }
}
