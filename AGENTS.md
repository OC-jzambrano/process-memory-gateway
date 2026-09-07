# Process Memory Gateway - Agent Instructions

When acting as an AI coding agent or assistant (Codex, Claude, Antigravity, or Cursor), follow these operational guidelines:

## 1. Process Memory & MCP Orchestration (Public MCP Surface)
- **Approved Public MCP Tools (6 Only):**
  1. `remember_company_instruction`: Stage proposed operational rules, conventions, or constraints from dialogue as `pending_review`.
  2. `list_memory_candidates`: List staged candidates awaiting human review for the authenticated company.
  3. `review_memory_candidate`: Submit owner/reviewer decision (`approve`, `edit`, `reject`) to promote candidates into active canonical rules.
  4. `get_company_context`: Retrieve approved active canonical policies (`MemoryPack`) for the target system and action scope.
  5. `register_downstream_mcp`: Configure downstream MCP servers/adapters, schemas, and supported action contexts per company.
  6. `run_downstream_request`: Safe orchestration entrypoint that injects approved company memory into the Bedrock prompt, requires a structured tool call, validates schemas, and invokes downstream tools.
- **Server-Resolved Identity & Clean Signatures:**
  - Never supply caller-controlled identity arguments (`client_id`, `company_id`, `reviewer`, `role`) to public MCP tools.
  - All tenant and user context is resolved cryptographically by the server from the Cognito OAuth 2.0 Bearer token.
- **Enforcement Rules:**
  - Approved rules act as authoritative company memory injected into model prompts; OPM contains zero hard-coded DoD or ERP business logic.
  - Use `run_downstream_request` to invoke downstream actions safely with approved company instructions injected.

## 2. Architecture & Code Integrity
- Public MCP tools are declared in `server.py` and served via `src/api/http_app.py`.
- Internal services (`HostedProcessMemoryService`, `MemoryRepository`, `DownstreamDispatcher`, `BedrockOrchestrator`) require authenticated request context or explicit principal context.
- All database operations must go through `src/storage/repository.py` using `MemoryRepository`.
- Run tests via `pytest -m "not ai"` before committing.
