"""Compact agent-facing tools for remote device workers."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


def check_device_worker_requirements() -> bool:
    try:
        import websockets  # noqa: F401
    except ImportError:
        return False
    return True


def _client_for(node: Optional[str]):
    from plugins.device_worker.node.client import DeviceNodeClient
    from plugins.device_worker.node.registry import DeviceNodeRegistry

    reg = DeviceNodeRegistry()
    entry = reg.resolve(node)
    if entry is None:
        nodes = reg.list_all()
        if nodes:
            names = ", ".join(n["name"] for n in nodes)
            raise RuntimeError(f"ambiguous or unknown node {node!r}; choose one of: {names}")
        raise RuntimeError("no device workers registered; run `hermes device node approve <name> <url> <token>`")
    return DeviceNodeClient(entry["url"], entry["token"]), entry


def _json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _safe_call(fn):
    try:
        return _json(fn())
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)})


_NODE_PROP = {"type": "string", "description": "Worker name. Required when multiple workers are registered."}
_PATH_PROP = {"type": "string", "description": "Path on the worker. Relative paths resolve against cwd or the worker home."}
_CWD_PROP = {"type": "string", "description": "Working directory on the worker."}


DEVICE_NODE_SCHEMA = {
    "name": "device_node",
    "description": (
        "Discover or check approved device workers. Use only for worker list/status/capabilities. "
        "Do not use for files, coding, commands, or GUI control."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "status", "capabilities", "approve_hint"]},
            "node": _NODE_PROP,
            "name": {"type": "string", "description": "Worker name for approve_hint."},
            "url": {"type": "string", "description": "Worker ws:// URL for approve_hint."},
            "token": {"type": "string", "description": "Optional worker token for preview only; full token is not returned."},
        },
        "required": ["action"],
    },
}


def handle_device_node(args: Dict[str, Any], **kwargs) -> str:
    def run():
        from plugins.device_worker.node.registry import DeviceNodeRegistry

        action = args["action"]
        reg = DeviceNodeRegistry()
        if action == "list":
            nodes = reg.list_all()
            return {
                "ok": True,
                "count": len(nodes),
                "nodes": [
                    {
                        "name": n["name"],
                        "url": n["url"],
                        "token_preview": (n.get("token") or "")[:6] + "...",
                        "added_at": n.get("added_at"),
                    }
                    for n in nodes
                ],
            }
        if action == "approve_hint":
            name = args.get("name") or "<node-name>"
            url = args.get("url") or "ws://<worker-ip>:18888"
            token_preview = ((args.get("token") or "")[:6] + "...") if args.get("token") else None
            return {
                "ok": True,
                "command": f"hermes device node approve {name} {url} <token>",
                "token_preview": token_preview,
                "follow_up": [
                    f"hermes device node ping {name}",
                    f"hermes device node capabilities {name}",
                ],
            }
        client, entry = _client_for(args.get("node"))
        if action == "status":
            return {"ok": True, "node": entry["name"], **client.ping()}
        if action == "capabilities":
            return {"ok": True, "node": entry["name"], **client.capabilities()}
        return {"ok": False, "error": f"unknown device_node action {action!r}"}
    return _safe_call(run)


DEVICE_WORKSPACE_SCHEMA = {
    "name": "device_workspace",
    "description": (
        "Inspect and edit files/repos on a worker. Use for repo work: info/list/read/write/search/"
        "apply_patch/git_status/git_diff. Do not use for long-running commands or GUI control."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["info", "list", "read", "write", "search", "apply_patch", "git_status", "git_diff"],
            },
            "node": _NODE_PROP,
            "path": _PATH_PROP,
            "cwd": _CWD_PROP,
            "content": {"type": "string", "description": "Text content for write."},
            "patch": {"type": "string", "description": "Unified diff for apply_patch."},
            "pattern": {"type": "string", "description": "Search pattern for search."},
            "limit": {"type": "integer", "description": "Maximum results/entries. Default 100."},
            "max_bytes": {"type": "integer", "description": "Maximum output bytes. Default depends on action."},
            "append": {"type": "boolean", "description": "Append instead of overwrite for write."},
            "create_parents": {"type": "boolean", "description": "Create parent directories for write."},
            "allow_system_path": {"type": "boolean", "description": "Allow writes under system paths. Default false."},
        },
        "required": ["action"],
    },
}


def handle_device_workspace(args: Dict[str, Any], **kwargs) -> str:
    def run():
        if args.get("command") or args.get("cmd"):
            return {"ok": False, "error": "Use `device_terminal` for commands."}
        client, entry = _client_for(args.get("node"))
        return {"ok": True, "node": entry["name"], **client.workspace({k: v for k, v in args.items() if k != "node"})}
    return _safe_call(run)


DEVICE_TERMINAL_SCHEMA = {
    "name": "device_terminal",
    "description": (
        "Manage persistent terminals on a worker. Use for tests, dev servers, REPLs, and long-running "
        "or interactive commands. Do not use for direct file edits; use device_workspace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "send", "read", "resize", "list", "kill"]},
            "node": _NODE_PROP,
            "name": {"type": "string", "description": "Human-readable terminal session name."},
            "terminal_id": {"type": "string", "description": "Terminal session id returned by create/list."},
            "cwd": _CWD_PROP,
            "command": {"type": "string", "description": "Optional command to start inside the terminal."},
            "input": {"type": "string", "description": "Raw input to send to the terminal."},
            "cursor": {"type": "integer", "description": "Read output after this cursor."},
            "max_bytes": {"type": "integer", "description": "Maximum output bytes to return. Default 20000."},
            "cols": {"type": "integer", "description": "Terminal columns. Default 120."},
            "rows": {"type": "integer", "description": "Terminal rows. Default 30."},
        },
        "required": ["action"],
    },
}


def handle_device_terminal(args: Dict[str, Any], **kwargs) -> str:
    def run():
        if args.get("path") or args.get("content") or args.get("patch"):
            return {"ok": False, "error": "Use `device_workspace` for file operations."}
        client, entry = _client_for(args.get("node"))
        return {"ok": True, "node": entry["name"], **client.terminal({k: v for k, v in args.items() if k != "node"})}
    return _safe_call(run)


DEVICE_DESKTOP_SCHEMA = {
    "name": "device_desktop",
    "description": (
        "Control a worker's GUI/apps. Use only for list_apps/capture/click/type/key/scroll. "
        "Do not use for repo or file work; use device_workspace unless GUI control is required."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list_apps", "capture", "click", "type", "key", "scroll"]},
            "node": _NODE_PROP,
            "app": {"type": "string", "description": "Optional app name or bundle ID, e.g. Safari."},
            "mode": {"type": "string", "enum": ["som", "vision", "ax"], "description": "Capture mode. Default som."},
            "element": {"type": "integer", "description": "Element index from a prior capture."},
            "coordinate": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "text": {"type": "string", "description": "Text to type."},
            "keys": {"type": "string", "description": "Shortcut like cmd+s, return, escape."},
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "description": "Scroll wheel ticks. Default 3."},
            "capture_after": {"type": "boolean", "description": "Return a fresh capture after the action."},
        },
        "required": ["action"],
    },
}


def handle_device_desktop(args: Dict[str, Any], **kwargs) -> str:
    def run():
        if args.get("path") or args.get("cwd") or args.get("patch"):
            return {"ok": False, "error": "Use `device_workspace` unless GUI control is required."}
        client, entry = _client_for(args.get("node"))
        desktop_args = {k: v for k, v in args.items() if k != "node" and v is not None}
        response = {"ok": True, "node": entry["name"], **client.desktop(desktop_args)}
        _annotate_capture_app_mismatch(response, desktop_args)
        return response
    return _safe_call(run)


def _annotate_capture_app_mismatch(response: Dict[str, Any], desktop_args: Dict[str, Any]) -> None:
    if desktop_args.get("action") != "capture":
        return
    requested = (desktop_args.get("app") or "").strip()
    if not requested:
        return
    result = response.get("result")
    if not isinstance(result, dict):
        return

    captured = str(result.get("app") or "").strip()
    text_summary = str(result.get("text_summary") or result.get("summary") or "")
    if not captured and " app=" in text_summary:
        captured = text_summary.split(" app=", 1)[1].split(" ", 1)[0].strip("'\"")
    if not captured or requested.lower() in captured.lower():
        return

    warning = (
        f"Requested app {requested!r}, but captured {captured!r}. "
        "The requested app may have no visible/capturable window; call "
        "device_desktop action=list_apps or open/focus the app before retrying."
    )
    response.setdefault("warnings", []).append(warning)
    result.setdefault("warnings", []).append(warning)
