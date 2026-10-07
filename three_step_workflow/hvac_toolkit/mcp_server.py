"""Dependency-free MCP stdio server for the audited HVAC workflow tools.

The transport follows MCP's newline-delimited JSON-RPC framing used by local
stdio clients.  Engineering work stays in :mod:`hvac_toolkit.mcp_tools`; this
module only provides discovery, input schemas, dispatch, and protocol errors.
"""

from __future__ import annotations

import json
import argparse
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from hvac_toolkit.evidence_card import build_evidence_card

SERVER_INFO = {"name": "openstudio-hvac-audit", "version": "1.0.0"}
PROTOCOL_VERSION = "2025-06-18"


def _engineering_call(name: str, **kwargs: Any) -> Any:
    # Project context and OpenStudio paths are intentionally loaded only when a
    # model operation is called. MCP initialize/tools-list must work before a
    # project has been selected.
    from hvac_toolkit import mcp_tools
    return getattr(mcp_tools, name)(**kwargs)


def inspect_osm_hvac_topology(**kwargs: Any) -> Any:
    return _engineering_call("inspect_osm_hvac_topology", **kwargs)


def validate_hvac_tool_plan(**kwargs: Any) -> Any:
    return _engineering_call("validate_hvac_tool_plan", **kwargs)


def apply_hvac_tool_plan(**kwargs: Any) -> Any:
    return _engineering_call("apply_hvac_tool_plan", **kwargs)


def simulate_hvac_case(**kwargs: Any) -> Any:
    return _engineering_call("simulate_hvac_case", **kwargs)


def run_hvac_tool_plan(**kwargs: Any) -> Any:
    return _engineering_call("run_hvac_tool_plan", **kwargs)


def rank_hvac_candidates(**kwargs: Any) -> Any:
    return _engineering_call("rank_hvac_candidates", **kwargs)


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[..., Any]]] = {
    "inspect_osm_hvac_topology": (
        "Inspect actual OpenStudio HVAC objects and topology in an OSM.",
        _object_schema({"osm_path": {"type": "string"}}, ["osm_path"]),
        inspect_osm_hvac_topology,
    ),
    "validate_hvac_tool_plan": (
        "Validate and normalize a proposed HVAC tool plan without modifying a model.",
        _object_schema({"plan": {"type": "object"}}, ["plan"]),
        validate_hvac_tool_plan,
    ),
    "apply_hvac_tool_plan": (
        "Apply a validated HVAC plan to an OSM and write an auditable derived model.",
        _object_schema({"plan": {"type": "object"}, "input_osm": {"type": "string"}, "output_root": {"type": "string"}}, ["plan", "input_osm", "output_root"]),
        apply_hvac_tool_plan,
    ),
    "simulate_hvac_case": (
        "Run the fixed-timestep EnergyPlus workflow and verify that requested tool effects reached the IDF.",
        _object_schema({"case_id": {"type": "string"}, "osm_path": {"type": "string"}, "output_root": {"type": "string"}, "plan": {"type": "object"}, "timesteps_per_hour": {"type": "integer", "enum": [6], "default": 6}}, ["case_id", "osm_path", "output_root", "plan"]),
        simulate_hvac_case,
    ),
    "run_hvac_tool_plan": (
        "Validate, apply, simulate, and audit one HVAC plan end to end.",
        _object_schema({"plan": {"type": "object"}, "input_osm": {"type": "string"}, "output_root": {"type": "string"}}, ["plan", "input_osm", "output_root"]),
        run_hvac_tool_plan,
    ),
    "rank_hvac_candidates": (
        "Rank only evidence-complete, physically valid, tool-effect-verified HVAC results that pass operational-service gates.",
        _object_schema({"results": {"type": "array", "items": {"type": "object"}}}, ["results"]),
        rank_hvac_candidates,
    ),
    "build_hvac_evidence_card": (
        "Create one compact publication-facing evidence row while retaining links to machine audit files.",
        _object_schema({"plan": {"type": "object"}, "result": {"type": "object"}, "topology": {"type": "object"}, "diagnostic": {"type": "object"}}, ["plan", "result"]),
        build_evidence_card,
    ),
}


def tool_descriptions() -> list[dict[str, Any]]:
    return [{"name": name, "description": entry[0], "inputSchema": entry[1]} for name, entry in TOOLS.items()]


def _result(request_id: Any, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    if data is not None:
        payload["error"]["data"] = data
    return payload


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:  # JSON-RPC notification
        return None
    if method == "initialize":
        return _result(request_id, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": tool_descriptions()})
    if method == "tools/call":
        params = message.get("params") or {}
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            return _error(request_id, -32602, f"Unknown tool: {name}")
        try:
            value = TOOLS[name][2](**arguments)
            is_error = isinstance(value, dict) and value.get("ok") is False
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "structuredContent": value, "isError": is_error})
        except (TypeError, ValueError, OSError) as exc:
            return _result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:  # keep protocol stdout clean; diagnostics go to stderr
            traceback.print_exc(file=sys.stderr)
            return _result(request_id, {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True})
    return _error(request_id, -32601, f"Method not found: {method}")


def handle_payload(payload: Any) -> Any:
    if isinstance(payload, list):
        responses = [response for item in payload if isinstance(item, dict) for response in [handle_message(item)] if response is not None]
        return responses or None
    if not isinstance(payload, dict):
        return _error(None, -32600, "Invalid Request")
    return handle_message(payload)


def run_stdio() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = handle_payload(json.loads(line))
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, "Parse error", str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


def run_http(host: str, port: int) -> int:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            if self.path.rstrip("/") != "/mcp":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                response = handle_payload(payload)
            except (ValueError, json.JSONDecodeError) as exc:
                response = _error(None, -32700, "Parse error", str(exc))
            if response is None:
                self.send_response(202)
                self.end_headers()
                return
            body = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stateless server has no SSE stream
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            print(format % args, file=sys.stderr)

    print(f"MCP Streamable HTTP endpoint: http://{host}:{port}/mcp", file=sys.stderr)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenStudio HVAC MCP server")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    return run_stdio() if args.transport == "stdio" else run_http(args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
