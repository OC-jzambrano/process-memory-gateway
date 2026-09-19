import json
import logging
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import anyio

from src.config import AWS_REGION
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
        db = None
        login = None
        password = None

        parsed = self._load_secret(server.secret_ref or "")
        if server.secret_ref and parsed is None:
            raise ValueError("downstream credentials secret could not be read")
        if isinstance(parsed, dict):
            db = parsed.get("db") or parsed.get("database") or parsed.get("ODOO_DB") or db
            login = parsed.get("login") or parsed.get("username") or parsed.get("ODOO_LOGIN")
            password = parsed.get("password") or parsed.get("api_key") or parsed.get("ODOO_PASSWORD") or parsed.get("ODOO_API_KEY")

        if not login or not password:
            if server.secret_ref:
                raise ValueError("downstream credentials secret is missing username or password")
            # Check company odoo connection configuration if present
            conn_config = self.repo.get_odoo_connection(server.company_id)
            if conn_config:
                db = conn_config.odoo_db or db

        db = db or self._derive_odoo_db_from_url(server.endpoint)

        return Odoo17Connector(
            url=server.endpoint,
            db=db or "",
            username=login or "",
            password=password or "",
        )

    @staticmethod
    def _derive_odoo_db_from_url(endpoint: str) -> str | None:
        """Best-effort DB name derivation for hosted Odoo URLs such as myco.odoo.com."""
        host = urlparse(endpoint).hostname or ""
        parts = host.split(".")
        if len(parts) >= 3 and parts[-2:] in (["odoo", "com"], ["odoo", "sh"]):
            return parts[0] or None
        return None

    @staticmethod
    def _load_secret(secret_ref: str) -> dict[str, Any] | None:
        """Resolve an AWS Secrets Manager ARN without exposing secret contents."""
        if not secret_ref:
            return None
        try:
            if secret_ref.startswith("arn:"):
                import boto3

                value = boto3.client("secretsmanager", region_name=AWS_REGION).get_secret_value(
                    SecretId=secret_ref
                ).get("SecretString")
            else:
                value = secret_ref
            parsed = json.loads(value) if value else None
            return parsed if isinstance(parsed, dict) else None
        except Exception as exc:  # noqa: BLE001 - boundary converts secret failures
            logger.error("Downstream secret resolution failed: %s", _sanitize_error_message(str(exc)))
            return None

    @staticmethod
    async def _call_streamable_http(
        endpoint: str,
        tool_name: str,
        arguments: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> Any:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(endpoint, headers=headers or {}) as streams, ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments)
                if getattr(result, "isError", False):
                    raise RuntimeError(f"Downstream MCP tool '{tool_name}' returned an error")
                if getattr(result, "structuredContent", None) is not None:
                    return result.structuredContent
                content = getattr(result, "content", [])
                return [item.model_dump() if hasattr(item, "model_dump") else str(item) for item in content]

    def _dispatch_streamable_http(
        self, server: DownstreamMCPServer, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        secret = self._load_secret(server.secret_ref or "")
        headers = secret.get("headers", {}) if isinstance(secret, dict) else {}
        if isinstance(secret, dict):
            token = secret.get("access_token") or secret.get("token")
            if token:
                headers = {**headers, "Authorization": f"Bearer {token}"}
        return anyio.run(
            self._call_streamable_http,
            server.endpoint,
            tool_name,
            arguments,
            headers,
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

        # 4. Broker the already-approved call. Agents never receive downstream
        # credentials and cannot reach this execution path without OPM policy.
        try:
            if server.transport == MCPTransport.ODOO_XMLRPC:
                connector = self._resolve_odoo_connector(server)
                model = arguments.get("model", "")
                if tool_name == "create_record":
                    result = connector.create_record(model=model, values=arguments.get("values", {}))
                else:
                    result = connector.execute_kw(
                        model=model,
                        method=arguments.get("method", tool_name),
                        args=arguments.get("args", []),
                        kwargs=arguments.get("kwargs", {}),
                    )
            elif server.transport == MCPTransport.STREAMABLE_HTTP:
                result = self._dispatch_streamable_http(server, tool_name, arguments)
            elif server.transport == MCPTransport.INTERNAL_MOCK:
                result = self.mock_handler(server, tool_call) if self.mock_handler else {
                    "status": "mock_success",
                    "received_arguments": sanitize_evidence(arguments),
                }
            else:
                raise ValueError(f"Unsupported downstream transport '{server.transport}'.")
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            sanitized_err = _sanitize_error_message(str(exc))
            logger.error("Downstream broker execution failed for '%s/%s': %s", server_id, tool_name, sanitized_err)
            return OrchestrationResult(
                success=False,
                correlation_id=cid,
                server_id=server_id,
                tool_name=tool_name,
                error=f"Downstream execution failed: {sanitized_err}",
                metadata={"brokered_by_opm": True},
            )

        return OrchestrationResult(
            success=True,
            correlation_id=cid,
            server_id=server_id,
            tool_name=tool_name,
            result=result,
            metadata={"brokered_by_opm": True, "transport": server.transport.value},
        )

    async def _probe_streamable_http(self, endpoint: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(endpoint) as streams, ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [tool.model_dump() if hasattr(tool, "model_dump") else {"name": getattr(tool, "name", "")} for tool in tools.tools]

    def probe(self, server: DownstreamMCPServer) -> dict[str, Any]:
        """Verify credentials and discover downstream capabilities without executing a tool."""
        if server.transport == MCPTransport.ODOO_XMLRPC:
            connector = self._resolve_odoo_connector(server)
            if not connector.healthcheck():
                raise RuntimeError("Odoo authentication or connectivity check failed")
            return {"connected": True, "tools": [tool.name for tool in server.available_tools]}
        if server.transport == MCPTransport.STREAMABLE_HTTP:
            secret = self._load_secret(server.secret_ref or "") or {}
            headers = secret.get("headers", {}) if isinstance(secret, dict) else {}
            token = secret.get("access_token") or secret.get("token") if isinstance(secret, dict) else None
            if token:
                headers = {**headers, "Authorization": f"Bearer {token}"}
            tools = anyio.run(self._probe_streamable_http, server.endpoint, headers)
            return {"connected": True, "tools": tools}
        raise RuntimeError(f"Connection probing is not supported for transport '{server.transport.value}'")
