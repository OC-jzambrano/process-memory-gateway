"""Narrow, model-facing tool definitions for the built-in Odoo XML-RPC adapter."""

from src.models.schemas import DownstreamToolDefinition


def default_odoo_xmlrpc_tools() -> list[DownstreamToolDefinition]:
    """Return the only operations exposed by the built-in Odoo adapter."""
    return [
        DownstreamToolDefinition(
            name="search_records",
            description=(
                "Read matching Odoo records using search_read. This operation is "
                "read-only and supports explicit model, domain, fields, limit, and order."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "domain": {"type": "array", "items": {"type": "array"}},
                    "fields": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer"},
                    "order": {"type": "string"},
                },
                "required": ["model", "domain", "fields"],
            },
        ),
        DownstreamToolDefinition(
            name="create_record",
            description="Create an Odoo record using an explicit model and values object.",
            input_schema={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "values": {"type": "object"},
                },
                "required": ["model", "values"],
            },
        ),
    ]
