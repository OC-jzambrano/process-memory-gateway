# Process Memory & Context Gateway for ERP (Odoo)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](LICENSE)
[![OpenAI](https://img.shields.io/badge/Provider-OpenAI-black.svg)](https://platform.openai.com/)
[![AWS Bedrock](https://img.shields.io/badge/Provider-AWS_Bedrock-orange.svg)](https://aws.amazon.com/bedrock/)
[![Pydantic v2](https://img.shields.io/badge/pydantic-v2-purple.svg)](https://docs.pydantic.dev/)

An enterprise-grade **Process Memory & Context Gateway** middleware designed to bridge conversational AI agents with ERP systems (Odoo). 

It continuously extracts tacit and explicit operational business rules from dialogue, maintains end-to-end extraction provenance, enforces strict multi-tenant governance with human-in-the-loop sign-off, and guarantees zero leakage of unapproved policies.

---

## Key Features

- **Multi-Provider LLM Strategy:** Pluggable provider interface supporting **direct OpenAI API (`gpt-4o-mini`, `gpt-4o`)**, **Amazon Bedrock (Claude 3.5 / 4.5, Amazon Nova)**, and **Deterministic Local Fallback**. Switching providers is pure configuration via `.env` (`LLM_PROVIDER=openai|bedrock|auto|local`).
- **Resilient Cascade Architecture:**
  - `Application` &rarr; `Direct OpenAI API` &rarr; `AWS Bedrock` &rarr; `Local Fallback`
- **Strict Governance Lifecycle:** Inferred rules land in a staged `pending_review` state. Only human-approved rules are promoted to canonical memory. Replay attacks and duplicate promotions are prevented at the database constraint level.
- **Append-Only Immutable Audit Trail:** Database triggers strictly prevent `UPDATE` or `DELETE` on the `review_events` table, creating a tamper-evident audit history.
- **Rule Versioning:** Canonical rules feature atomic versioning (`v1` &rarr; `v2` with `superseded` pointers) to prevent stale rule enforcement or divergent branching.
- **Tenant Authorization & Security Principal:** Every operation requires tenant validation via authenticated `Principal` context, preventing cross-tenant policy access or session hijacking.
- **PII & Credential Redaction:** Pre-transmission redaction utility to mask credit cards, API keys, emails, and phone numbers before cloud LLM ingestion.
- **Universal 5-Tool MCP Surface:** Exposes exactly 5 public tools over Model Context Protocol (MCP) with zero caller-controlled identity parameters.
- **Server-Resolved Identity & Tenant Isolation:** Caller identity (`company_id`, `user_id`, `role`) is extracted directly from the Cognito OAuth Bearer token—never passed as tool arguments.

---

## System Architecture & Workflow

```mermaid
flowchart TD
    subgraph "External AI Clients"
        A[Claude / Codex / Antigravity Agent]
    end

    subgraph "Public MCP Gateway (server.py / http_app.py)"
        B[HTTPS / Bearer Auth / Tenant Resolution]
        B --> C{5 Approved MCP Tools}
        C --> T1[remember_company_instruction]
        C --> T2[list_memory_candidates]
        C --> T3[review_memory_candidate]
        C --> T4[get_company_context]
        C --> T5[create_project_task]
    end

    subgraph "Governance & Policy Memory Core"
        T1 --> D[(memory_candidates: pending_review)]
        T2 --> D
        T3 -->|Approve / Promote| E[(canonical_rules: active v1/v2)]
        T3 -.->|Append-Only Audit| F[(review_events: immutable)]
        T4 --> E
    end

    subgraph "Managed Odoo Task Execution"
        T5 --> G[Task Readiness Validator]
        E -.->|Enforce Canonical Rules| G
        G -->|Validated| H[Odoo XML-RPC Adapter]
        H --> I[Odoo project.task]
        G -.->|Evidence Record| J[(execution_runs & execution_events)]
    end
```

---

## Approved Public MCP Toolset

The public MCP gateway exposes exactly these 5 tools. All caller identity parameters (`company_id`, `client_id`, `reviewer`, `role`) are server-resolved from the authenticated request context.

| Tool Name | Parameters | Purpose & Lifecycle State |
| :--- | :--- | :--- |
| `remember_company_instruction` | `instruction_text`, `context_hint=None` | Stages a proposed company instruction into `memory_candidates` (`pending_review`). Does **not** enforce until human sign-off. |
| `list_memory_candidates` | `status="pending_review"` | Lists staged candidates awaiting human review for the authenticated company. |
| `review_memory_candidate` | `candidate_id`, `decision` (`approve`/`edit`/`reject`), `edited_rule_text=None`, `edited_scope=None`, `edited_constraint=None`, `notes=None` | Human-in-the-loop review. Approving promotes the candidate into an active, versioned `CanonicalRule` and logs an append-only audit event. |
| `get_company_context` | `system="odoo"`, `application=None`, `resource=None`, `operation=None`, `fields=None` | Retrieves active canonical policies for the given action scope as a structured `MemoryPack`. Guarantees zero leakage of unapproved policies. |
| `create_project_task` | `title`, `description`, `definition_of_done=None`, `project_id=None`, `correlation_id=None` | Managed task creation gateway. Validates DoD and requirements against active canonical policies, records execution evidence, and executes Odoo XML-RPC write. |

### Internal vs. Public Tool API Delineation

- **Public MCP Gateway (`server.py`, `src/api/http_app.py`):** Exposes only the 5 approved tools above. Identity is resolved from the session context. Legacy extraction wrappers and raw query tools are omitted from the protocol surface.
- **Internal APIs (`src/api/memory_tools.py`, `src/storage/repository.py`):** `ProcessMemoryTools` and `MemoryRepository` remain available for internal Python components, test suites, and batch utilities requiring explicit `Principal` objects.

---

## Instruction Approval Lifecycle

1. **Staging:** An agent or user provides operational guidelines. Calling `remember_company_instruction` runs the extraction engine and stages candidate rules as `pending_review`.
2. **Review & Sign-Off:** Company owners review candidates via `list_memory_candidates` and approve or edit them via `review_memory_candidate`. This promotes them to active canonical rules with immutable audit events.
3. **Scoped Enforcement:** When performing ERP operations, agents retrieve active policies via `get_company_context` to understand constraints.
4. **Managed Execution:** The agent calls `create_project_task`. The gateway retrieves approved company memory internally, validates mandatory criteria (e.g. Definition of Done), records audit evidence, and only then calls Odoo.

---

## Quick Start

### 1. Installation

```bash
git clone https://github.com/OC-jzambrano/process-memory-gateway.git
cd process-memory-gateway
pip install -r requirements.txt
```

### 2. Configuration

Copy the example environment configuration:

```bash
cp .env.example .env
```

Configure your provider in `.env`:

```env
# Provider strategy: "openai", "bedrock", "auto", "local"
LLM_PROVIDER=openai

# OpenAI API Key (Direct API)
OPENAI_API_KEY=sk-...
OPENAI_MODEL_ID=gpt-4o-mini

# AWS Bedrock Settings (Optional / Secondary)
AWS_REGION=eu-north-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
BEDROCK_MODEL_ID=eu.anthropic.claude-haiku-4-5-20251001-v1:0
```

### 3. Run Automated Tests

```bash
# Run 82 offline tests (runs in ~1 second, zero cloud network calls):
pytest -m "not ai" -v

# Run with test coverage report:
pytest -m "not ai" --cov=src --cov-report=term-missing

# Run full suite including live cloud AI benchmarks (requires live API key):
pytest -v
```

### 4. Run Interactive Demo

```bash
python run_demo.py
```

---

## Project Structure

```
process-memory-gateway/
├── src/
│   ├── config.py                 # Environment & model settings (reads .env)
│   ├── models/
│   │   ├── enums.py              # RuleType, Severity, RuleStatus, LLMProviderType, ExtractionMode
│   │   └── schemas.py            # Pydantic models (Principal, CandidateRule, CanonicalRule, etc.)
│   ├── storage/
│   │   ├── db.py                 # SQLite schema with composite FKs, WAL mode, immutability triggers
│   │   └── repository.py         # MemoryRepository (atomic state transitions, tenant isolation, audit)
│   ├── extractor/
│   │   ├── prompt.py             # XML injection-hardened extraction prompts with sanitization
│   │   ├── service.py            # Multi-provider cascade orchestrator
│   │   └── providers/            # Pluggable LLM provider implementations
│   │       ├── base.py           # BaseLLMProvider interface
│   │       ├── openai_provider.py# Direct OpenAI API adapter
│   │       ├── bedrock_provider.py# AWS Bedrock adapter with model fallback
│   │       └── fallback_provider.py# Local deterministic heuristic parser
│   ├── utils/
│   │   └── privacy.py            # PII & secret credential redaction utility
│   └── api/
│       └── memory_tools.py       # 4 Core MCP-ready governance tools with Principal auth
├── tests/
│   ├── conftest.py               # Shared test fixtures & deterministic test mode
│   ├── unit/                     # Layer 1: Pure logic tests (schemas, enums, cleaner, privacy)
│   ├── integration/              # Layer 2: Real SQLite DB & atomic state transition tests
│   ├── contract/                 # Layer 3: MCP tool signatures, resilience, & security tests
│   ├── e2e/                      # Layer 4: Full lifecycle workflow tests
│   ├── regression/               # Layer 5 & 6: Pinned golden outputs & AI extraction benchmarks
│   └── fixtures/                 # Sample dialogues & golden expected JSON files
├── run_demo.py                   # Interactive CLI demonstration
├── requirements.txt              # Pinned project dependencies
├── .env.example                  # Environment template
└── LICENSE                       # Apache 2.0 License
```

---

## License

This project is licensed under the Apache 2.0 License - see the [LICENSE](LICENSE) file for details.
