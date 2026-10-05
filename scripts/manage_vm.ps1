<#
.SYNOPSIS
    Controls and cycles the Odoo Process Memory EC2 pilot host to eliminate idle compute costs.

.DESCRIPTION
    Allows developers to start, stop, monitor, and configure auto-shutdown for the
    pilot EC2 host (odoo-process-memory-pilot-host).
    Uses the authenticated AWS CLI credentials in ~/.aws/credentials.

.EXAMPLE
    .\scripts\manage_vm.ps1 status
    .\scripts\manage_vm.ps1 stop
    .\scripts\manage_vm.ps1 start
    .\scripts\manage_vm.ps1 enable-idle-autostop -IdleMinutes 45
    .\scripts\manage_vm.ps1 disable-idle-autostop
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $false)]
    [ValidateSet("status", "start", "stop", "enable-idle-autostop", "disable-idle-autostop")]
    [string]$Action = "status",

    [Parameter(Mandatory = $false)]
    [string]$Region = "eu-north-1",

    [Parameter(Mandatory = $false)]
    [string]$InstanceName = "odoo-process-memory-pilot-host",

    [Parameter(Mandatory = $false)]
    [string]$InstanceId = "",

    [Parameter(Mandatory = $false)]
    [int]$IdleMinutes = 45,

    [Parameter(Mandatory = $false)]
    [string]$ApiGatewayHealthUrl = "https://sjbs8r4vg0.execute-api.eu-north-1.amazonaws.com/health/live"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-TargetInstanceId {
    if ($InstanceId -ne "") {
        return $InstanceId.Trim()
    }

    $discovered = aws ec2 describe-instances `
        --region $Region `
        --filters "Name=tag:Name,Values=$InstanceName" "Name=instance-state-name,Values=running,stopped,stopping,pending" `
        --query "Reservations[0].Instances[0].InstanceId" `
        --output text

    if ($discovered -and $discovered -ne "None" -and $discovered.Trim().StartsWith("i-")) {
        return $discovered.Trim()
    }

    # Fallback to known pilot instance ID
    Write-Warning "Could not find instance by tag '$InstanceName'. Falling back to default pilot instance i-0b44703ed51aa8734."
    return "i-0b44703ed51aa8734"
}

$targetId = Get-TargetInstanceId

switch ($Action) {
    "status" {
        Write-Host "`n=== EC2 Pilot Host Status ($targetId) ===" -ForegroundColor Cyan
        $query = "Reservations[0].Instances[0].{State:State.Name,InstanceType:InstanceType,PublicIp:PublicIpAddress,LaunchTime:LaunchTime}"
        $infoJson = aws ec2 describe-instances --instance-ids $targetId --region $Region --query $query --output json
        $info = $infoJson | ConvertFrom-Json

        Write-Host "Instance ID : $targetId"
        Write-Host "Region      : $Region"
        Write-Host "State       : $($info.State)" -ForegroundColor $(if ($info.State -eq 'running') { 'Green' } else { 'Yellow' })
        Write-Host "Type        : $($info.InstanceType)"
        Write-Host "Public IP   : $($info.PublicIp)"

        # Check CloudWatch idle auto-stop alarm
        $alarmName = "odoo-process-memory-pilot-auto-stop-idle"
        $alarmQuery = "MetricAlarms[?AlarmName=='$alarmName'].[StateValue, StateReason]"
        $alarmJson = aws cloudwatch describe-alarms --alarm-names $alarmName --region $Region --query $alarmQuery --output json
        $alarmInfo = $alarmJson | ConvertFrom-Json
        if ($alarmInfo -and $alarmInfo.Count -gt 0) {
            Write-Host "Auto-Stop   : ENABLED (State: $($alarmInfo[0][0]))" -ForegroundColor Green
        } else {
            Write-Host "Auto-Stop   : DISABLED" -ForegroundColor DarkGray
        }

        if ($info.State -eq "running" -and $ApiGatewayHealthUrl) {
            Write-Host "`nTesting live API Gateway endpoint ($ApiGatewayHealthUrl)..." -NoNewline
            try {
                $response = Invoke-RestMethod -Uri $ApiGatewayHealthUrl -Method Get -TimeoutSec 5 -ErrorAction Stop
                Write-Host " [OK 200]" -ForegroundColor Green
                Write-Host "Response    : $($response | ConvertTo-Json -Compress)`n"
            } catch {
                Write-Host " [FAIL: $($_.Exception.Message)]`n" -ForegroundColor Red
            }
        }
    }

    "stop" {
        Write-Host "`nInitiating graceful stop for instance $targetId in $Region..." -ForegroundColor Yellow
        aws ec2 stop-instances --instance-ids $targetId --region $Region --output json | Out-Null

        Write-Host "Waiting for instance to reach 'stopped' state..." -ForegroundColor Cyan
        aws ec2 wait instance-stopped --instance-ids $targetId --region $Region
        Write-Host "Instance $targetId is now STOPPED. Compute charges ($0.0208/hr) are now paused.`n" -ForegroundColor Green
    }

    "start" {
        Write-Host "`nStarting instance $targetId in $Region..." -ForegroundColor Yellow
        aws ec2 start-instances --instance-ids $targetId --region $Region --output json | Out-Null

        Write-Host "Waiting for instance to reach 'running' state..." -ForegroundColor Cyan
        aws ec2 wait instance-running --instance-ids $targetId --region $Region
        Write-Host "Instance is running! Waiting for Docker containers and Caddy to initialize..." -ForegroundColor Cyan

        # Poll health endpoint
        $maxAttempts = 24 # Up to 2 minutes
        $healthy = $false
        for ($i = 1; $i -le $maxAttempts; $i++) {
            Start-Sleep -Seconds 5
            try {
                $resp = Invoke-RestMethod -Uri $ApiGatewayHealthUrl -Method Get -TimeoutSec 4 -ErrorAction Stop
                Write-Host "`nService is HEALTHY and ready! ($ApiGatewayHealthUrl)`n" -ForegroundColor Green
                $healthy = $true
                break
            } catch {
                Write-Host "." -NoNewline
            }
        }

        if (-not $healthy) {
            Write-Warning "`nInstance is running, but health check did not pass within 120 seconds. It may still be booting or initializing Docker.`n"
        }
    }

    "enable-idle-autostop" {
        $alarmName = "odoo-process-memory-pilot-auto-stop-idle"
        $stopActionArn = "arn:aws:automate:${Region}:ec2:stop"

        # 3 periods of ($IdleMinutes / 3 * 60) seconds
        $periodSeconds = [int](($IdleMinutes * 60) / 3)
        if ($periodSeconds -lt 60) { $periodSeconds = 60 }

        Write-Host "`nConfiguring CloudWatch Auto-Stop Alarm..." -ForegroundColor Cyan
        Write-Host "Alarm Name  : $alarmName"
        Write-Host "Instance ID : $targetId"
        Write-Host "Threshold   : Average CPU <= 2% for $IdleMinutes minutes"
        Write-Host "Action      : Stop Instance ($stopActionArn)"

        aws cloudwatch put-metric-alarm `
            --alarm-name $alarmName `
            --alarm-description "Automatically stop pilot host when idle for $IdleMinutes minutes to save costs" `
            --namespace "AWS/EC2" `
            --metric-name "CPUUtilization" `
            --statistic "Average" `
            --period $periodSeconds `
            --evaluation-periods 3 `
            --threshold 2.0 `
            --comparison-operator "LessThanOrEqualToThreshold" `
            --dimensions "Name=InstanceId,Value=$targetId" `
            --alarm-actions $stopActionArn `
            --region $Region

        Write-Host "Auto-stop alarm enabled successfully!`n" -ForegroundColor Green
    }

    "disable-idle-autostop" {
        $alarmName = "odoo-process-memory-pilot-auto-stop-idle"
        Write-Host "`nRemoving auto-stop alarm '$alarmName'..." -ForegroundColor Yellow
        aws cloudwatch delete-alarms --alarm-names $alarmName --region $Region
        Write-Host "Auto-stop alarm removed.`n" -ForegroundColor Green
    }
}
