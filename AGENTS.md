# Process Memory Gateway - Agent Instructions

When acting as an AI coding agent or assistant (Codex, Claude, Antigravity, or Cursor), follow these operational guidelines:

## Fast Path For Odoo Requests
- If the user asks to create, update, search, assign, schedule, or modify anything in Odoo, use the `odoo-process-memory.process_execute_action` MCP tool directly.
- Do not search for Odoo plugins, local credentials, `.codex` backups, XML-RPC scripts, JSON-RPC scripts, or repository utilities before trying `process_execute_action`.
- Do not call `process_get_context` before execution unless the user explicitly asks to inspect, audit, or explain company rules.
- Do not manually duplicate canonical rules in the request. `process_execute_action` retrieves and injects approved matching rules automatically.
- Always pass a complete `action_context` with `system`, `application`, `resource`, `operation`, and relevant `fields`.
- When Odoo IDs are required, include the lookup requirement in the natural-language `user_request`; do not bypass the gateway to discover IDs.
- Verify the result after every mutating downstream action, preferably through `process_execute_action` with a read/search context or by using the returned result when it already includes the created/updated record.

## 1. Process Memory & MCP Orchestration (Public MCP Surface)
- **Approved Public MCP Tools (8 Only):**
  1. `process_remember_instruction`: Stage proposed operational rules, conventions, or constraints from dialogue as `pending_review`.
  2. `process_list_candidates`: List staged candidates awaiting human review for the authenticated company.
  3. `process_list_rules`: List canonical rules for audit/review without action-scope matching.
  4. `process_review_candidate`: Submit owner/reviewer decision (`approve`, `edit`, `reject`) to promote candidates into active canonical rules.
  5. `process_set_rule_status`: Owner/reviewer-controlled toggle between `approved` and `archived`; archived rules are excluded from Memory Packs and prompts.
  6. `process_get_context`: Retrieve approved active canonical policies (`MemoryPack`) for the target system and action scope.
  7. `process_register_downstream_mcp`: Configure downstream MCP servers/adapters, schemas, and supported action contexts per company.
  8. `process_execute_action`: Safe orchestration entrypoint that injects approved company memory into the Bedrock prompt, requires a structured tool call, validates schemas, and invokes downstream tools.
- **Server-Resolved Identity & Clean Signatures:**
  - Never supply caller-controlled identity arguments (`client_id`, `company_id`, `reviewer`, `role`) to public MCP tools.
  - All tenant and user context is resolved cryptographically by the server from the Cognito OAuth 2.0 Bearer token.
- **Enforcement Rules:**
  - Approved rules act as authoritative company memory injected into model prompts; OPM contains zero hard-coded DoD or ERP business logic.
  - For Odoo actions, always use `process_execute_action` with a complete `action_context`. The orchestrator automatically retrieves and injects approved canonical rules matching that context.
  - Do not call `process_get_context` separately before execution unless the rules need to be inspected or explained. Do not duplicate canonical rules manually in the request.
  - `action_context` must include `system`, `application`, `resource`, `operation`, and relevant `fields`.
  - Perform prerequisite lookups through `process_execute_action` when IDs are required, such as resolving Odoo project or user IDs.
  - Do not execute Odoo actions directly outside `process_execute_action`.
  - Verify the result after every mutating downstream action.

## 2. Architecture & Code Integrity
- Public MCP tools are declared in `server.py` and served via `src/api/http_app.py`.
- Internal services (`HostedProcessMemoryService`, `MemoryRepository`, `DownstreamDispatcher`, `BedrockOrchestrator`) require authenticated request context or explicit principal context.
- All database operations must go through `src/storage/repository.py` using `MemoryRepository`.
- Run tests via `pytest -m "not ai"` before committing.
