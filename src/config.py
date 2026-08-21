import os
from pathlib import Path
from dotenv import load_dotenv

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DB_PATH = DATA_DIR / "process_memory.db"

# Load environment variables from .env
load_dotenv(dotenv_path=ENV_PATH)

# Primary LLM Provider: "openai", "bedrock", "auto", "local"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()

# Direct OpenAI API Settings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_ID = os.getenv("OPENAI_MODEL_ID", "gpt-4o-mini")

# AWS & Bedrock Settings
AWS_REGION = os.getenv("AWS_REGION", "eu-north-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")

# Bedrock Model Chain: Claude Haiku 4.5 -> Claude 3.5 Haiku -> Amazon Nova Lite
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
)
FALLBACK_MODEL_IDS = [
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "eu.anthropic.claude-sonnet-4-6",
    "global.anthropic.claude-sonnet-4-6",
    "eu.amazon.nova-lite-v1:0",
    "amazon.nova-lite-v1:0"
]
