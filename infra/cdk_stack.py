import os
from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_rds as rds,
    aws_cognito as cognito,
    aws_secretsmanager as secretsmanager,
    aws_iam as iam,
    aws_logs as logs,
)

class OdooProcessMemoryStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. VPC & Networking
        vpc = ec2.Vpc(
            self, "ProcessMemoryVpc",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24),
                ec2.SubnetConfiguration(name="Isolated", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
            ]
        )

        # 2. Amazon Cognito User Pool & OAuth Client
        user_pool = cognito.UserPool(
            self, "ProcessMemoryUserPool",
            user_pool_name="odoo-process-memory-users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True, username=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True
            ),
            removal_policy=RemovalPolicy.RETAIN
        )

        user_pool_client = cognito.UserPoolClient(
            self, "ProcessMemoryAppClient",
            user_pool=user_pool,
            user_pool_client_name="process-memory-mcp-client",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_password=True, user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.EMAIL, cognito.OAuthScope.OPENID, cognito.OAuthScope.PROFILE],
                callback_urls=["https://localhost:3000/callback", "https://mcp.example.com/oauth/callback"]
            )
        )

        # 3. AWS Secrets Manager - Reference Existing Secret by ARN (No plain-text credentials in CDK)
        odoo_secret_arn = os.getenv("ODOO_SECRET_ARN", "")
        if odoo_secret_arn:
            odoo_secret = secretsmanager.Secret.from_secret_complete_arn(
                self, "OdooCredentialsSecret", odoo_secret_arn
            )
        else:
            # Create a placeholder secret construct without hardcoded values
            odoo_secret = secretsmanager.Secret(
                self, "OdooCredentialsSecretPlaceholder",
                secret_name="odoo-process-memory/pilot-credentials",
                description="Odoo 17 Dedicated Integration User secret (Populate in AWS Console or Secrets Manager CLI)",
                generate_secret_string=secretsmanager.SecretStringGenerator(
                    secret_string_template='{"url": "https://community.odooconcept.com", "database": "community", "username": "process-memory-pilot", "default_project_id": 142}',
                    generate_string_key="api_key"
                )
            )

        # 4. Aurora PostgreSQL Serverless v2 Database
        db_security_group = ec2.SecurityGroup(
            self, "AuroraSecurityGroup",
            vpc=vpc,
            description="Security group for Aurora PostgreSQL cluster",
            allow_all_outbound=True
        )

        db_cluster = rds.DatabaseCluster(
            self, "ProcessMemoryDatabase",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_15_4
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[db_security_group],
            serverless_v2_min_capacity=0.5,
            serverless_v2_max_capacity=2.0,
            writer=rds.ClusterInstance.serverless_v2("writer"),
            default_database_name="process_memory",
            removal_policy=RemovalPolicy.SNAPSHOT
        )

        # 5. CloudWatch Log Group
        log_group = logs.LogGroup(
            self, "ProcessMemoryLogGroup",
            log_group_name="/aws/ecs/odoo-process-memory-service",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY
        )

        # 6. ECS Fargate Cluster & Service
        ecs_cluster = ecs.Cluster(
            self, "ProcessMemoryCluster",
            vpc=vpc,
            cluster_name="odoo-process-memory-cluster"
        )

        task_role = iam.Role(
            self, "ProcessMemoryTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )

        # IAM Permissions: Bedrock EU Claude Haiku 4.5
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=["*"]
            )
        )

        # IAM Permissions: Secrets Manager read for specific secrets only
        odoo_secret.grant_read(task_role)
        db_cluster.secret.grant_read(task_role)

        # ECS Fargate Service with Application Load Balancer
        fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "ProcessMemoryFargateService",
            cluster=ecs_cluster,
            cpu=512,
            memory_limit_mib=1024,
            desired_count=1,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_asset("."),
                container_port=8000,
                task_role=task_role,
                log_driver=ecs.LogDrivers.aws_logs(
                    stream_prefix="mcp-server",
                    log_group=log_group
                ),
                environment={
                    "AWS_REGION": self.region,
                    "LLM_PROVIDER": "bedrock",
                    "BEDROCK_MODEL_ID": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
                    "COGNITO_USER_POOL_ID": user_pool.user_pool_id,
                    "COGNITO_APP_CLIENT_ID": user_pool_client.user_pool_client_id,
                    "ODOO_SECRET_ARN": odoo_secret.secret_arn
                }
            ),
            public_load_balancer=True
        )

        # Allow ECS container to access Aurora DB
        db_cluster.connections.allow_default_port_from(fargate_service.service.connections)

        # 7. Outputs
        CfnOutput(self, "MCPServiceURL", value=fargate_service.load_balancer.load_balancer_dns_name)
        CfnOutput(self, "CognitoUserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "CognitoAppClientId", value=user_pool_client.user_pool_client_id)
        CfnOutput(self, "OdooSecretArn", value=odoo_secret.secret_arn)
