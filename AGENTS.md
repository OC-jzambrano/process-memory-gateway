# Process Memory Gateway - Agent Instructions

When acting as an AI coding agent or ERP assistant (Codex, Claude, Antigravity, or Cursor), follow these operational guidelines:

## 1. Process Memory & Policy Governance (Public MCP Surface)
- **Approved Public MCP Tools (5 Only):**
  1. `remember_company_instruction`: Stage proposed operational rules or constraints from dialogue as `pending_review`.
  2. `list_memory_candidates`: List staged candidates awaiting human review for the authenticated company.
  3. `review_memory_candidate`: Submit owner/reviewer decision (`approve`, `edit`, `reject`) to promote candidates into active canonical rules.
  4. `get_company_context`: Retrieve approved active canonical policies (`MemoryPack`) for the target system and action scope.
  5. `create_project_task`: Managed entrypoint to validate criteria (Definition of Done) and create tasks in Odoo with correlation evidence.
- **Server-Resolved Identity & Clean Signatures:**
  - Never supply caller-controlled identity arguments (`client_id`, `company_id`, `reviewer`, `role`) to public MCP tools.
  - All tenant and user context is resolved cryptographically by the server from the Cognito OAuth 2.0 Bearer token.
- **Enforcement Rules:**
  - When an operational policy requires approval or specific criteria (e.g. Definition of Done), enforce it before action.
  - Always use `create_project_task` rather than bypassing process memory with unvalidated direct ERP writes.

## 2. Architecture & Code Integrity
- Public MCP tools are declared in `server.py` and served via `src/api/http_app.py`.
- Internal services (`ProcessMemoryTools`, `MemoryRepository`) reside under `src/api/` and `src/storage/` and require explicit `Principal` context.
- All database operations must go through `src/storage/repository.py` using `MemoryRepository`.
- Run tests via `pytest -m "not ai"` before committing.

