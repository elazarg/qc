"""Optional local MCP access to the same read-only discovery interface as the CLI."""

import json
from typing import Any

from lab.qc import discovery
from lab.qc.wire_spec import schema


def server(*, simulated=False, desk_open):
    from mcp.server.mcpserver import MCPServer

    app = MCPServer("Tone Lab Quad Cortex")

    @app.resource("qc://capabilities")
    def capabilities() -> str:
        """Installed protocol schemas, SDK methods and MCP tool names."""
        return json.dumps(discovery.capabilities())

    @app.resource("qc://models")
    def models() -> str:
        """Models, parameters and display units from the installed model catalog."""
        return json.dumps(discovery.model_catalog())

    @app.resource("qc://wire-spec/{version}")
    def wire_spec(version: str) -> str:
        """Return the nested wire schema for CorOS 4.0 or 4.1."""
        if version not in ("4.0", "4.1"):
            raise ValueError("Choose recovered schema 4.0 or 4.1.")
        return json.dumps(schema(version))

    def observe():
        if not simulated and desk_open():
            raise RuntimeError("The Tone Lab desk has the Quad Cortex.")
        return discovery.observer(simulated=simulated)

    @app.tool(structured_output=True)
    def get_snapshot() -> dict[str, Any]:
        """Read the selected preset and scene, including unsaved edits. Does not recall or save a preset."""
        with observe() as (device, correlated):
            return discovery.snapshot(device, correlated=correlated).record()

    @app.tool(structured_output=True)
    def get_settings() -> dict[str, Any]:
        """Read selected device settings and identify any that did not answer."""
        with observe() as (device, correlated):
            return discovery.read_settings(device, correlated=correlated)

    @app.tool(structured_output=True)
    def preview_parameter(snapshot: dict[str, Any], row: int, column: int, index: int, value: float) -> dict[str, Any]:
        """Preview a parameter in display units using a snapshot. Rows and columns are zero-based."""
        return discovery.preview_parameter(snapshot, row, column, index, value)

    return app
