# Cost guardrail (plan R7): email when this month's AWS usage passes each amount.
# include_credit = false -> counts usage before the promotional credits, otherwise the
# credits net the cost to ~0 and no alert would ever fire.
# Budgets is a global service; the first 2 budgets of an account are free.
resource "aws_budgets_budget" "monthly" {
  count        = var.budget_limit_usd > 0 && var.alert_email != "" ? 1 : 0
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit = false
    include_refund = false
  }

  dynamic "notification" {
    for_each = toset(var.budget_alert_usd)
    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = "ACTUAL"
      threshold                  = notification.value
      threshold_type             = "ABSOLUTE_VALUE"
      subscriber_email_addresses = [var.alert_email]
    }
  }

  # early warning: the month is on track to pass the whole budget
  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "FORECASTED"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.alert_email]
  }
}
