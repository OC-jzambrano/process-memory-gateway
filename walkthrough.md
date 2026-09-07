# OPM Pilot Walkthrough

OPM is a company-memory MCP orchestrator. It captures natural-language company instructions, stages them as `pending_review`, and promotes only human-approved candidates into versioned active memory.

For a downstream request, the authenticated company supplies an explicit action context. OPM selects matching active rules, builds an isolated Bedrock prompt with company context, approved memory, registered downstream tool schemas, and the user request, then dispatches the model's structured tool call.

The public MCP tools are:

1. `remember_company_instruction`
2. `list_memory_candidates`
3. `review_memory_candidate`
4. `get_company_context`
5. `register_downstream_mcp`
6. `run_downstream_request`

OPM has no hard-coded Definition of Done, acceptance criteria, project workflow, or other business instruction. Protocol and schema checks, tenant isolation, authentication, credential redaction, and downstream allowlists are platform protections.

The first downstream adapter is a thin Odoo 17 XML-RPC connector. It authenticates, calls the requested Odoo model method, and returns the immediate result. It performs no business validation or read-back verification. Odoo credentials may be supplied through an AWS Secrets Manager ARN stored in the downstream registry.

The database migration creates the company-scoped downstream registry and removes deprecated execution-run and execution-event tables from upgraded installations. The offline verification command is:

```text
python -m pytest -m "not ai"
```

