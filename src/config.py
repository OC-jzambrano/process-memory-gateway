import os
from pathlib import Path

from dotenv import load_dotenv

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

# Load environment variables from .env if present
load_dotenv(dotenv_path=ENV_PATH)

# Environment & Hosting Mode
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
HOSTED_MODE = (
    os.getenv("HOSTED_MODE", "false").lower() in ("true", "1", "yes")
    or ENVIRONMENT == "production"
)

# Persistent Storage & Database Configuration
DEFAULT_DATA_DIR = BASE_DIR / "data"
CUSTOM_DATA_DIR = os.getenv("PROCESS_MEMORY_DATA_DIR")

if CUSTOM_DATA_DIR:
    DATA_DIR = Path(CUSTOM_DATA_DIR)
else:
    DATA_DIR = DEFAULT_DATA_DIR

# Fail closed if hosted mode is enabled and the mount directory is missing
if HOSTED_MODE:
    if not DATA_DIR.exists() or not DATA_DIR.is_dir():
        raise RuntimeError(
            f"FATAL: Persistent memory volume mount missing at '{DATA_DIR}'. "
            "Server startup aborted to prevent creating an unattached ephemeral database."
        )
else:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

CUSTOM_DB_PATH = os.getenv("PROCESS_MEMORY_DB_PATH")
if CUSTOM_DB_PATH:
    DEFAULT_DB_PATH = Path(CUSTOM_DB_PATH)
else:
    DEFAULT_DB_PATH = DATA_DIR / "process_memory.db"

# SQLite Tunings
SQLITE_BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "5000"))

# Primary LLM Provider: "openai", "bedrock", "auto", "local"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "bedrock").lower()

# Direct OpenAI API Settings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_ID = os.getenv("OPENAI_MODEL_ID", "gpt-4.1-mini")

# AWS & Bedrock Settings
AWS_REGION = os.getenv("AWS_REGION", "eu-north-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")

BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
)
FALLBACK_MODEL_IDS = ["global.anthropic.claude-haiku-4-5-20251001-v1:0"]

# Amazon Cognito OAuth Settings
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_REGION = os.getenv("COGNITO_REGION", AWS_REGION)
COGNITO_APP_CLIENT_ID = os.getenv("COGNITO_APP_CLIENT_ID", "")
COGNITO_RESOURCE_SERVER_IDENTIFIER = os.getenv(
    "COGNITO_RESOURCE_SERVER_IDENTIFIER", "https://mcp.example.com"
)
COGNITO_REQUIRED_SCOPE = os.getenv("COGNITO_REQUIRED_SCOPE", "mcp:tools")
COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN", "")

# Domain & Transport Security Settings
DOMAIN_NAME = os.getenv("DOMAIN_NAME", "localhost")
_raw_allowed_hosts = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
ALLOWED_HOSTS = [h.strip() for h in _raw_allowed_hosts.split(",") if h.strip()]
if DOMAIN_NAME and DOMAIN_NAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(DOMAIN_NAME)


# Odoo Integration Secrets & Runtime Configuration (Loaded from Env / Secrets Manager ONLY)
ODOO_URL = os.getenv("ODOO_URL", "")
ODOO_DB = os.getenv("ODOO_DB", "")
ODOO_LOGIN = os.getenv("ODOO_LOGIN", "")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")
ODOO_API_KEY = os.getenv("ODOO_API_KEY", "")
ODOO_SECRET_ARN = os.getenv("ODOO_SECRET_ARN", "")
ODOO_DEFAULT_PROJECT_ID = int(os.getenv("ODOO_DEFAULT_PROJECT_ID", "142"))
ALLOW_LIVE_ODOO_WRITES = os.getenv("ALLOW_LIVE_ODOO_WRITES", "false").lower() in (
    "true",
    "1",
    "yes",
)
