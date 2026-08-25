#!/usr/bin/env python3
import os
import aws_cdk as cdk
from infra.cdk_stack import OdooProcessMemoryStack

app = cdk.App()
OdooProcessMemoryStack(
    app, "OdooProcessMemoryStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT", os.getenv("AWS_ACCOUNT_ID")),
        region=os.getenv("CDK_DEFAULT_REGION", "eu-north-1")
    ),
    description="AWS-First Odoo Process Memory MCP Service Infrastructure"
)

app.synth()
