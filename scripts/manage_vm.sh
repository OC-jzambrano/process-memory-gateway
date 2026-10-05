#!/usr/bin/env bash
#
# manage_vm.sh
# Cycles and controls the Odoo Process Memory EC2 pilot host to avoid idle compute costs.
#
# Usage:
#   ./scripts/manage_vm.sh status
#   ./scripts/manage_vm.sh stop
#   ./scripts/manage_vm.sh start
#   ./scripts/manage_vm.sh enable-idle-autostop [idle_minutes]
#   ./scripts/manage_vm.sh disable-idle-autostop
#

set -euo pipefail

ACTION="${1:-status}"
REGION="${AWS_REGION:-eu-north-1}"
INSTANCE_NAME="odoo-process-memory-pilot-host"
API_GW_URL="https://sjbs8r4vg0.execute-api.eu-north-1.amazonaws.com/health/live"
ALARM_NAME="odoo-process-memory-pilot-auto-stop-idle"

# 1. Resolve instance ID
INSTANCE_ID=$(aws ec2 describe-instances \
  --region "$REGION" \
  --filters "Name=tag:Name,Values=$INSTANCE_NAME" "Name=instance-state-name,Values=running,stopped,stopping,pending" \
  --query "Reservations[0].Instances[0].InstanceId" \
  --output text 2>/dev/null || true)

if [ -z "$INSTANCE_ID" ] || [ "$INSTANCE_ID" = "None" ]; then
  INSTANCE_ID="i-0b44703ed51aa8734"
fi

case "$ACTION" in
  status)
    echo "=== EC2 Pilot Host Status ($INSTANCE_ID) ==="
    INFO=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --region "$REGION" \
      --query "Reservations[0].Instances[0].{State:State.Name,Type:InstanceType,IP:PublicIpAddress}" --output json)
    STATE=$(echo "$INFO" | jq -r '.State')
    TYPE=$(echo "$INFO" | jq -r '.Type')
    IP=$(echo "$INFO" | jq -r '.IP')

    echo "Instance ID : $INSTANCE_ID"
    echo "Region      : $REGION"
    echo "State       : $STATE"
    echo "Type        : $TYPE"
    echo "Public IP   : $IP"

    ALARM_STATE=$(aws cloudwatch describe-alarms --alarm-names "$ALARM_NAME" --region "$REGION" \
      --query "MetricAlarms[0].StateValue" --output text 2>/dev/null || echo "None")
    if [ "$ALARM_STATE" != "None" ] && [ -n "$ALARM_STATE" ]; then
      echo "Auto-Stop   : ENABLED (State: $ALARM_STATE)"
    else
      echo "Auto-Stop   : DISABLED"
    fi

    if [ "$STATE" = "running" ]; then
      echo -n "Live API Gateway Check: "
      if curl -fsS -m 5 "$API_GW_URL" > /dev/null 2>&1; then
        echo "[OK 200]"
      else
        echo "[UNREACHABLE / WARMING UP]"
      fi
    fi
    ;;

  stop)
    echo "Stopping instance $INSTANCE_ID in $REGION..."
    aws ec2 stop-instances --instance-ids "$INSTANCE_ID" --region "$REGION" > /dev/null
    echo "Waiting for instance to reach stopped state..."
    aws ec2 wait instance-stopped --instance-ids "$INSTANCE_ID" --region "$REGION"
    echo "Instance $INSTANCE_ID is now STOPPED. Compute billing has stopped."
    ;;

  start)
    echo "Starting instance $INSTANCE_ID in $REGION..."
    aws ec2 start-instances --instance-ids "$INSTANCE_ID" --region "$REGION" > /dev/null
    echo "Waiting for instance to reach running state..."
    aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$REGION"
    echo "Instance is running. Waiting for Docker and Caddy to pass health check..."

    HEALTHY=0
    for _ in {1..24}; do
      if curl -fsS -m 4 "$API_GW_URL" > /dev/null 2>&1; then
        HEALTHY=1
        break
      fi
      sleep 5
      echo -n "."
    done

    if [ "$HEALTHY" -eq 1 ]; then
      echo -e "\nService is HEALTHY and ready! ($API_GW_URL)"
    else
      echo -e "\nInstance started, but healthcheck timed out. Containers may still be initializing."
    fi
    ;;

  enable-idle-autostop)
    IDLE_MINUTES="${2:-45}"
    PERIOD_SECS=$(( (IDLE_MINUTES * 60) / 3 ))
    [ "$PERIOD_SECS" -lt 60 ] && PERIOD_SECS=60
    STOP_ACTION="arn:aws:automate:${REGION}:ec2:stop"

    echo "Enabling CloudWatch Auto-Stop for $INSTANCE_ID when CPU <= 2% for $IDLE_MINUTES min..."
    aws cloudwatch put-metric-alarm \
      --alarm-name "$ALARM_NAME" \
      --alarm-description "Auto-stop pilot host when idle for $IDLE_MINUTES min" \
      --namespace "AWS/EC2" \
      --metric-name "CPUUtilization" \
      --statistic "Average" \
      --period "$PERIOD_SECS" \
      --evaluation-periods 3 \
      --threshold 2.0 \
      --comparison-operator "LessThanOrEqualToThreshold" \
      --dimensions "Name=InstanceId,Value=$INSTANCE_ID" \
      --alarm-actions "$STOP_ACTION" \
      --region "$REGION"
    echo "Auto-stop alarm enabled."
    ;;

  disable-idle-autostop)
    echo "Disabling auto-stop alarm $ALARM_NAME..."
    aws cloudwatch delete-alarms --alarm-names "$ALARM_NAME" --region "$REGION"
    echo "Auto-stop alarm removed."
    ;;

  *)
    echo "Usage: $0 {status|start|stop|enable-idle-autostop|disable-idle-autostop}"
    exit 1
    ;;
esac
