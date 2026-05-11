"""Device worker plugin.

Keeps Hermes on a central server while routing local-machine actions to
lightweight workers running on Macs or future edge machines.
"""

from __future__ import annotations

from plugins.device_worker.cli import device_command, register_cli
from plugins.device_worker.tools import (
    DEVICE_CAPTURE_SCHEMA,
    DEVICE_CLICK_SCHEMA,
    DEVICE_KEY_SCHEMA,
    DEVICE_LIST_APPS_SCHEMA,
    DEVICE_LIST_NODES_SCHEMA,
    DEVICE_PING_NODE_SCHEMA,
    DEVICE_SCROLL_SCHEMA,
    DEVICE_SHELL_SCHEMA,
    DEVICE_TYPE_SCHEMA,
    check_device_worker_requirements,
    handle_device_capture,
    handle_device_click,
    handle_device_key,
    handle_device_list_apps,
    handle_device_list_nodes,
    handle_device_ping_node,
    handle_device_scroll,
    handle_device_shell,
    handle_device_type,
)


_TOOLS = (
    ("device_list_nodes", DEVICE_LIST_NODES_SCHEMA, handle_device_list_nodes, "🖥️"),
    ("device_ping_node", DEVICE_PING_NODE_SCHEMA, handle_device_ping_node, "🖥️"),
    ("device_shell", DEVICE_SHELL_SCHEMA, handle_device_shell, "💻"),
    ("device_capture", DEVICE_CAPTURE_SCHEMA, handle_device_capture, "📸"),
    ("device_click", DEVICE_CLICK_SCHEMA, handle_device_click, "🖱️"),
    ("device_type", DEVICE_TYPE_SCHEMA, handle_device_type, "⌨️"),
    ("device_key", DEVICE_KEY_SCHEMA, handle_device_key, "⌨️"),
    ("device_scroll", DEVICE_SCROLL_SCHEMA, handle_device_scroll, "🖱️"),
    ("device_list_apps", DEVICE_LIST_APPS_SCHEMA, handle_device_list_apps, "🧭"),
)


def register(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="device_worker",
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
            "shells and macOS GUI/device surfaces on approved machines."
        ),
    )

