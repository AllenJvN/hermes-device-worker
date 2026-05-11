"""Device worker plugin.

Keeps Hermes on a central server while routing local-machine actions to
lightweight workers running on Macs or future edge machines.
"""

from __future__ import annotations

from plugins.device_worker.cli import device_command, register_cli
from plugins.device_worker.tools import (
    DEVICE_DESKTOP_SCHEMA,
    DEVICE_NODE_SCHEMA,
    DEVICE_TERMINAL_SCHEMA,
    DEVICE_WORKSPACE_SCHEMA,
    check_device_worker_requirements,
    handle_device_desktop,
    handle_device_node,
    handle_device_terminal,
    handle_device_workspace,
)


_TOOLS = (
    ("device_node", "device_worker", DEVICE_NODE_SCHEMA, handle_device_node, "🖥️"),
    ("device_desktop", "device_worker", DEVICE_DESKTOP_SCHEMA, handle_device_desktop, "🖱️"),
    ("device_workspace", "device_coding", DEVICE_WORKSPACE_SCHEMA, handle_device_workspace, "🧰"),
    ("device_terminal", "device_coding", DEVICE_TERMINAL_SCHEMA, handle_device_terminal, "💻"),
)


def register(ctx) -> None:
    for name, toolset, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_device_worker_requirements,
            emoji=emoji,
        )

    ctx.register_cli_command(
        name="device",
        help="Remote device workers (approve, list, ping, sync dist)",
        setup_fn=register_cli,
        handler_fn=device_command,
        description=(
            "Manage lightweight LAN workers that let central Hermes control "
            "shells, workspaces, terminals, and macOS GUI surfaces on approved machines."
        ),
    )
