# Process Memory Gateway - Agent Instructions

When acting as an AI coding agent or ERP assistant (Codex, Claude, Antigravity, or Cursor), follow these operational guidelines:

## 1. Process Memory & Policy Governance
- **Pre-Execution Check:** Before creating or modifying Odoo records, always invoke `get_active_rules(client_id, process_name)` to retrieve the company's approved canonical policies.
- **Enforcement Rules:**
  - If a policy requires approval (e.g. MRP installation, high-value orders), do NOT execute autonomously. Block the action and ask for confirmation.
  - Apply naming conventions (e.g. BOM versions, SKU formatting) strictly as defined in active canonical rules.
- **Continuous Memory Extraction:** Whenever a user gives a business rule, constraint, or preference in natural language, call `extract_memory_candidates` to stage the rule for human review.

## 2. Architecture & Code Integrity
- All extraction logic resides under `src/extractor/`.
- All database operations must go through `src/storage/repository.py` using `MemoryRepository`.
- All tool signatures must adhere to MCP standards in `server.py` and `src/api/memory_tools.py`.
- Run tests via `pytest -m "not ai"` before committing.
