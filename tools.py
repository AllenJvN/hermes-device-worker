"""Agent-facing tools for remote device workers."""

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


DEVICE_LIST_NODES_SCHEMA = {
    "name": "device_list_nodes",
    "description": "List approved remote device worker nodes.",
    "parameters": {"type": "object", "properties": {}},
}


def handle_device_list_nodes(args: Dict[str, Any], **kwargs) -> str:
    def run():
        from plugins.device_worker.node.registry import DeviceNodeRegistry
        nodes = DeviceNodeRegistry().list_all()
        public = [
            {
                "name": n["name"],
                "url": n["url"],
                "token_preview": (n.get("token") or "")[:6] + "…",
                "added_at": n.get("added_at"),
            }
            for n in nodes
        ]
        return {"ok": True, "count": len(public), "nodes": public}
    return _safe_call(run)


DEVICE_PING_NODE_SCHEMA = {
    "name": "device_ping_node",
    "description": "Ping a remote device worker node and return basic status.",
    "parameters": {
        "type": "object",
        "properties": {
            "node": {"type": "string", "description": "Worker name. Optional if exactly one node is registered."},
        },
    },
}


def handle_device_ping_node(args: Dict[str, Any], **kwargs) -> str:
    def run():
        client, entry = _client_for(args.get("node"))
        return {"ok": True, "node": entry["name"], **client.ping()}
    return _safe_call(run)


DEVICE_SHELL_SCHEMA = {
    "name": "device_shell",
    "description": (
        "Run a shell command on an approved remote device worker. Use this for "
        "local Mac commands, files, apps, AppleScript, and Shortcuts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "node": {"type": "string", "description": "Worker name. Optional if exactly one node is registered."},
            "command": {"type": "string", "description": "Shell command to run on the worker."},
            "cwd": {"type": "string", "description": "Optional working directory on the worker."},
            "timeout": {"type": "integer", "description": "Timeout in seconds. Default 60."},
        },
        "required": ["command"],
    },
}


def handle_device_shell(args: Dict[str, Any], **kwargs) -> str:
    def run():
        client, entry = _client_for(args.get("node"))
        return {"ok": True, "node": entry["name"], **client.shell(
            args["command"],
            cwd=args.get("cwd"),
            timeout=int(args.get("timeout") or 60),
        )}
    return _safe_call(run)


_NODE_PROP = {"type": "string", "description": "Worker name. Optional if exactly one node is registered."}
_APP_PROP = {"type": "string", "description": "Optional app name or bundle ID to target, e.g. Safari or com.apple.Safari."}
_CAPTURE_AFTER_PROP = {"type": "boolean", "description": "Return a fresh capture after the action."}


DEVICE_CAPTURE_SCHEMA = {
    "name": "device_capture",
    "description": "Capture a remote Mac worker screen/window via computer_use.",
    "parameters": {
        "type": "object",
        "properties": {
            "node": _NODE_PROP,
            "mode": {"type": "string", "enum": ["som", "vision", "ax"], "description": "Capture mode. Default som."},
            "app": _APP_PROP,
        },
    },
}


def _computer_use(args: Dict[str, Any], cu_args: Dict[str, Any]) -> str:
    def run():
        client, entry = _client_for(args.get("node"))
        response = {"ok": True, "node": entry["name"], **client.computer_use(cu_args)}
        _annotate_capture_app_mismatch(response, cu_args)
        return response
    return _safe_call(run)


def handle_device_capture(args: Dict[str, Any], **kwargs) -> str:
    return _computer_use(args, {"action": "capture", "mode": args.get("mode", "som"), "app": args.get("app")})


def _annotate_capture_app_mismatch(response: Dict[str, Any], cu_args: Dict[str, Any]) -> None:
    """Surface cua-driver's silent app fallback in the tool result.

    The macOS backend chooses another on-screen window when the requested app
    has no capturable window. That is useful fallback behavior, but the agent
    needs a clear warning so it does not assume it is looking at the requested
    app.
    """
    if cu_args.get("action") != "capture":
        return
    requested = (cu_args.get("app") or "").strip()
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
        "device_list_apps or open/focus the app before retrying."
    )
    response.setdefault("warnings", []).append(warning)
    result.setdefault("warnings", []).append(warning)


DEVICE_CLICK_SCHEMA = {
    "name": "device_click",
    "description": "Click on a remote Mac worker via computer_use. Prefer element index from a prior device_capture.",
    "parameters": {
        "type": "object",
        "properties": {
            "node": _NODE_PROP,
            "element": {"type": "integer", "description": "SOM element index to click."},
            "coordinate": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "app": _APP_PROP,
            "capture_after": _CAPTURE_AFTER_PROP,
        },
    },
}


def handle_device_click(args: Dict[str, Any], **kwargs) -> str:
    cu_args = {k: v for k, v in args.items() if k != "node" and v is not None}
    cu_args["action"] = "click"
    return _computer_use(args, cu_args)


DEVICE_TYPE_SCHEMA = {
    "name": "device_type",
    "description": "Type text on a remote Mac worker via computer_use.",
    "parameters": {
        "type": "object",
        "properties": {
            "node": _NODE_PROP,
            "text": {"type": "string", "description": "Text to type."},
            "app": _APP_PROP,
            "capture_after": _CAPTURE_AFTER_PROP,
        },
        "required": ["text"],
    },
}


def handle_device_type(args: Dict[str, Any], **kwargs) -> str:
    return _computer_use(args, {
        "action": "type",
        "text": args["text"],
        "app": args.get("app"),
        "capture_after": bool(args.get("capture_after")),
    })


DEVICE_KEY_SCHEMA = {
    "name": "device_key",
    "description": "Send a keyboard shortcut to a remote Mac worker via computer_use.",
    "parameters": {
        "type": "object",
        "properties": {
            "node": _NODE_PROP,
            "keys": {"type": "string", "description": "Shortcut like cmd+s, return, escape."},
            "app": _APP_PROP,
            "capture_after": _CAPTURE_AFTER_PROP,
        },
        "required": ["keys"],
    },
}


def handle_device_key(args: Dict[str, Any], **kwargs) -> str:
    return _computer_use(args, {
        "action": "key",
        "keys": args["keys"],
        "app": args.get("app"),
        "capture_after": bool(args.get("capture_after")),
    })


DEVICE_SCROLL_SCHEMA = {
    "name": "device_scroll",
    "description": "Scroll a remote Mac worker via computer_use.",
    "parameters": {
        "type": "object",
        "properties": {
            "node": _NODE_PROP,
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "description": "Scroll wheel ticks. Default 3."},
            "element": {"type": "integer"},
            "coordinate": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            "app": _APP_PROP,
            "capture_after": _CAPTURE_AFTER_PROP,
        },
        "required": ["direction"],
    },
}


def handle_device_scroll(args: Dict[str, Any], **kwargs) -> str:
    cu_args = {k: v for k, v in args.items() if k != "node" and v is not None}
    cu_args["action"] = "scroll"
    return _computer_use(args, cu_args)


DEVICE_LIST_APPS_SCHEMA = {
    "name": "device_list_apps",
    "description": "List running apps/windows on a remote Mac worker.",
    "parameters": {
        "type": "object",
        "properties": {"node": _NODE_PROP},
    },
}


def handle_device_list_apps(args: Dict[str, Any], **kwargs) -> str:
    def run():
        client, entry = _client_for(args.get("node"))
        return {"ok": True, "node": entry["name"], **client.list_apps()}
    return _safe_call(run)
