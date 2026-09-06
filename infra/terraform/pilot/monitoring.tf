resource "aws_cloudwatch_metric_alarm" "instance_health" {
  alarm_name          = "odoo-process-memory-${var.environment}-instance-check"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Triggers when the MCP host EC2 instance status check fails"

  dimensions = {
    InstanceId = aws_instance.mcp_host.id
  }
}

resource "aws_budgets_budget" "monthly_spend" {
  count = var.budget_alert_email != "" ? 1 : 0

  name              = "odoo-process-memory-${var.environment}-budget"
  budget_type       = "COST"
  limit_amount      = tostring(var.budget_limit_usd)
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-01-01_00:00"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
