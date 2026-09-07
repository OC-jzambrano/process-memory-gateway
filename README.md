# Company Memory MCP Orchestrator

OPM captures company instructions, stages them for human approval, and supplies approved memory to Bedrock before invoking configured downstream MCP tools. The first downstream adapter is Odoo 17 XML-RPC.

## Architecture

```text
Authenticated company request
  -> candidate capture and human approval
  -> scoped active company memory
  -> Bedrock prompt with isolated memory and tool schemas
  -> protocol/schema validation
  -> downstream MCP or Odoo 17 XML-RPC adapter
```

OPM does not hard-code Definition of Done, acceptance criteria, project rules, or other business requirements. Approved natural-language instructions are general company memory. Authentication, tenant isolation, credential protection, protocol checks, and downstream allowlists remain platform safeguards.

## Public MCP tools

The server exposes exactly six tools. Identity is resolved from the authenticated Cognito request.

| Tool | Purpose |
| --- | --- |
| `remember_company_instruction` | Stage a natural-language instruction as `pending_review`. |
| `list_memory_candidates` | List candidates for the authenticated company. |
| `review_memory_candidate` | Approve, edit, or reject a candidate and record the audit event. |
| `get_company_context` | Retrieve active rules matching an explicit action context. |
| `register_downstream_mcp` | Configure a company-scoped downstream MCP server or adapter. |
| `run_downstream_request` | Retrieve scoped memory, ask Bedrock for one structured tool call, validate its protocol schema, and dispatch it. |

## Approval and orchestration

Rules are never active when first captured. An owner or reviewer must approve them. During orchestration, OPM retrieves only approved rules matching the supplied system, application, resource, operation, and fields. Bedrock receives those rules inside isolated boundaries together with the registered downstream tool schemas. OPM performs protocol and schema checks, then dispatches the model-selected tool.

## Odoo 17 adapter

The thin XML-RPC adapter authenticates against `/xmlrpc/2/common` and invokes `/xmlrpc/2/object`. It has no hard-coded project IDs, DoD checks, acceptance-criteria checks, business validation, or read-back verification. Downstream credentials should be referenced through AWS Secrets Manager.

## Development

```text
pip install -r requirements.txt
python -m pytest -m "not ai"
python -m ruff check .
python -m compileall -q src server.py tests
```

The offline suite covers approval lifecycle, tenant isolation, prompt boundaries, scoped memory, downstream allowlists, Odoo adapter behavior, secret resolution, and MCP protocol contracts.
