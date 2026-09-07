import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from src.integrations.odoo17_xmlrpc import Odoo17Connector, _sanitize_error_message
from src.models.enums import MCPTransport
from src.models.schemas import (
    DownstreamMCPServer,
    DownstreamToolDefinition,
    OrchestrationResult,
    OrchestrationToolCall,
)
from src.storage.base_repository import BaseRepository
from src.utils.privacy import sanitize_evidence

logger = logging.getLogger(__name__)


class SchemaValidationError(Exception):
    pass


def validate_protocol_and_schema(
    tool_def: DownstreamToolDefinition, arguments: dict[str, Any]
) -> None:
    """
    Validates tool call arguments against the registered tool's input schema.
    Performs protocol-level schema checks (presence of required fields and basic types).
    Does NOT perform business policy or criteria evaluation.
    """
    schema = tool_def.input_schema or {}
    required_fields = schema.get("required", [])
    if isinstance(required_fields, list):
        missing = [f for f in required_fields if f not in arguments]
        if missing:
            raise SchemaValidationError(
                f"Missing required parameter(s) for tool '{tool_def.name}': {', '.join(missing)}"
            )

    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for param_name, val in arguments.items():
            prop = properties.get(param_name)
            if not prop or not isinstance(prop, dict):
                continue
            expected_type = prop.get("type")
            if expected_type == "string" and not isinstance(val, str):
                raise SchemaValidationError(
                    f"Parameter '{param_name}' must be a string, got {type(val).__name__}."
                )
            elif expected_type == "integer" and not isinstance(val, int):
                raise SchemaValidationError(
                    f"Parameter '{param_name}' must be an integer, got {type(val).__name__}."
                )
            elif expected_type == "boolean" and not isinstance(val, bool):
                raise SchemaValidationError(
                    f"Parameter '{param_name}' must be a boolean, got {type(val).__name__}."
                )
            elif expected_type == "array" and not isinstance(val, list):
                raise SchemaValidationError(
                    f"Parameter '{param_name}' must be a list/array, got {type(val).__name__}."
                )
            elif expected_type == "object" and not isinstance(val, dict):
                raise SchemaValidationError(
                    f"Parameter '{param_name}' must be an object/dict, got {type(val).__name__}."
                )


class DownstreamDispatcher:
    """
    Dispatches validated tool calls to registered downstream MCP servers or adapters.
    Maintains tenant isolation, downstream allowlists, and credential protection.
    """

    def __init__(
        self,
        repo: BaseRepository,
        odoo_connector_factory: Callable[[DownstreamMCPServer], Any] | None = None,
        mock_handler: Callable[[DownstreamMCPServer, OrchestrationToolCall], Any] | None = None,
    ):
        self.repo = repo
        self.odoo_connector_factory = odoo_connector_factory
        self.mock_handler = mock_handler

    def _resolve_odoo_connector(self, server: DownstreamMCPServer) -> Any:
        if self.odoo_connector_factory:
            return self.odoo_connector_factory(server)

        # Default: build Odoo17Connector from endpoint and connection or secret
        # Check if secret_ref contains credentials JSON or secret ARN
        db = "community"
        login = None
        password = None

        if server.secret_ref:
            try:
                parsed = json.loads(server.secret_ref)
                if isinstance(parsed, dict):
                    db = parsed.get("db", db)
                    login = parsed.get("login")
                    password = parsed.get("password") or parsed.get("api_key")
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        if not login or not password:
            # Check company odoo connection configuration if present
            conn_config = self.repo.get_odoo_connection(server.company_id)
            if conn_config:
                db = conn_config.odoo_db or db

        return Odoo17Connector(
            url=server.endpoint,
            db=db,
            username=login or "",
            password=password or "",
        )

    def dispatch(
        self,
        company_id: str,
        tool_call: OrchestrationToolCall,
        correlation_id: str | None = None,
    ) -> OrchestrationResult:
        cid = correlation_id or f"corr_{uuid.uuid4().hex}"
        server_id = tool_call.server_id
        tool_name = tool_call.tool_name
        arguments = tool_call.arguments or {}

        # 1. Tenant & Allowlist Gate: Look up registered server for this company
        server = self.repo.get_downstream_mcp(company_id=company_id, server_id=server_id)
        if not server:
            return OrchestrationResult(
                success=False,
                correlation_id=cid,
                server_id=server_id,
                tool_name=tool_name,
                error=f"Downstream server '{server_id}' is not registered or not authorized for this company.",
            )

        # 2. Tool Allowlist Gate: Check that tool_name exists on this server
        tool_def = next((t for t in server.available_tools if t.name == tool_name), None)
        if not tool_def:
            return OrchestrationResult(
                success=False,
                correlation_id=cid,
                server_id=server_id,
                tool_name=tool_name,
                error=f"Tool '{tool_name}' is not registered in the allowlist for server '{server_id}'.",
            )

        # 3. Protocol & Schema Check
        try:
            validate_protocol_and_schema(tool_def, arguments)
        except SchemaValidationError as sve:
            return OrchestrationResult(
                success=False,
                correlation_id=cid,
                server_id=server_id,
                tool_name=tool_name,
                error=f"Protocol schema validation error: {sve}",
            )

        # 4. Invoke Downstream Adapter
        try:
            transport = server.transport
            if transport == MCPTransport.ODOO_XMLRPC:
                connector = self._resolve_odoo_connector(server)
                # Determine target model from arguments, schema, or default to project.task
                model = arguments.pop("model", None) or "project.task"
                result = connector.create_record(model=model, values=arguments)
                return OrchestrationResult(
                    success=True,
                    correlation_id=cid,
                    server_id=server_id,
                    tool_name=tool_name,
                    result=result,
                    metadata={"transport": "odoo_xmlrpc", "model": model},
                )
            elif transport == MCPTransport.INTERNAL_MOCK:
                if self.mock_handler:
                    result = self.mock_handler(server, tool_call)
                else:
                    result = {"id": 1001, "status": "mock_success", "received_arguments": sanitize_evidence(arguments)}
                return OrchestrationResult(
                    success=True,
                    correlation_id=cid,
                    server_id=server_id,
                    tool_name=tool_name,
                    result=result,
                    metadata={"transport": "internal_mock"},
                )
            elif transport == MCPTransport.STREAMABLE_HTTP:
                # HTTP MCP invocation stub/client
                result = {"status": "dispatched", "endpoint": server.endpoint, "tool": tool_name}
                return OrchestrationResult(
                    success=True,
                    correlation_id=cid,
                    server_id=server_id,
                    tool_name=tool_name,
                    result=result,
                    metadata={"transport": "streamable_http"},
                )
            else:
                return OrchestrationResult(
                    success=False,
                    correlation_id=cid,
                    server_id=server_id,
                    tool_name=tool_name,
                    error=f"Unsupported downstream transport '{transport}'.",
                )
        except Exception as e:  # noqa: BLE001 - Convert all downstream exceptions to sanitized result errors
            sanitized_err = _sanitize_error_message(str(e))
            logger.error("Downstream invocation failed for '%s/%s': %s", server_id, tool_name, sanitized_err)
            return OrchestrationResult(
                success=False,
                correlation_id=cid,
                server_id=server_id,
                tool_name=tool_name,
                error=f"Downstream tool execution failed: {sanitized_err}",
            )
