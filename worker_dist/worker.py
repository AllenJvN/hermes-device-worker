#!/usr/bin/env python3
"""Standalone Hermes device worker.

Runs on an edge machine and exposes a tiny bearer-token-authenticated
WebSocket RPC surface to the central Hermes VM.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Tuple


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    if not _use_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def _label(text: str) -> str:
    return _c("36;1", text)


def _ok(text: str) -> str:
    return _c("32;1", text)


def _warn(text: str) -> str:
    return _c("33;1", text)


def _secret(text: str) -> str:
    return _c("35;1", text)


VALID_REQUEST_TYPES = frozenset({
    "ping",
    "capabilities",
    "status",
    "shell",
    "computer_use",
    "list_apps",
})

BLOCKED_SHELL_PATTERNS = [
    re.compile(r"curl\s+[^|]*\|\s*(bash|sh)", re.I),
    re.compile(r"wget\s+[^|]*\|\s*(bash|sh)", re.I),
    re.compile(r"\bsudo\s+rm\s+-[rf]", re.I),
    re.compile(r"\brm\s+-rf\s+/\s*$", re.I),
    re.compile(r":\s*\(\)\s*\{\s*:\|:\s*&\s*\}", re.I),
]


def _state_dir() -> Path:
    return Path(os.environ.get("HERMES_DEVICE_WORKER_HOME", Path.home() / ".hermes-device-worker"))


def _token_path() -> Path:
    return _state_dir() / "node_token.json"


def ensure_token() -> str:
    path = _token_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tok = data.get("token")
            if isinstance(tok, str) and tok:
                return tok
        except (OSError, json.JSONDecodeError):
            pass
    tok = secrets.token_hex(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"token": tok, "generated_at": time.time()}, indent=2), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
    return tok


def make_response(req_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "response", "id": req_id, "payload": payload}


def make_error(req_id: str, error: str) -> Dict[str, Any]:
    return {"type": "error", "id": req_id, "error": str(error)}


def decode(raw: str) -> Dict[str, Any]:
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("envelope must be an object")
    return obj


def validate_request(msg: Dict[str, Any], expected_token: str) -> Tuple[bool, str]:
    if msg.get("type") not in VALID_REQUEST_TYPES:
        return False, f"unknown request type: {msg.get('type')!r}"
    if not isinstance(msg.get("id"), str) or not msg.get("id"):
        return False, "missing id"
    if msg.get("token") != expected_token:
        return False, "token mismatch"
    if not isinstance(msg.get("payload"), dict):
        return False, "payload must be a dict"
    return True, ""


def _computer_use_status() -> Dict[str, Any]:
    if sys.platform != "darwin":
        return {"available": False, "error": "computer_use is macOS-only"}
    try:
        from tools.computer_use.tool import check_computer_use_requirements
        if not check_computer_use_requirements():
            return {
                "available": False,
                "error": "cua-driver not found",
                "hint": "Run ./install.sh --install-cua, then ./check_permissions.sh",
            }
    except Exception as exc:
        return {"available": False, "error": f"requirement check failed: {exc}"}

    probe = _run_computer_use({"args": {"action": "list_apps"}})
    if probe.get("ok"):
        return {"available": True}
    result = probe.get("result") if isinstance(probe.get("result"), dict) else {}
    return {
        "available": False,
        "error": result.get("error") or probe.get("error") or "computer_use probe failed",
        "hint": (
            "Grant Accessibility and Screen Recording permissions, then restart "
            "the worker. Run ./check_permissions.sh for guided setup."
        ),
    }


def _computer_use_available() -> bool:
    return bool(_computer_use_status().get("available"))


def _capabilities() -> Dict[str, Any]:
    computer_use = _computer_use_status()
    return {
        "shell": True,
        "computer_use": bool(computer_use.get("available")),
        "computer_use_detail": computer_use,
        "list_apps": True,
        "platform": platform.system(),
        "hostname": socket.gethostname(),
    }


def _blocked_shell(command: str) -> str | None:
    for pat in BLOCKED_SHELL_PATTERNS:
        if pat.search(command):
            return pat.pattern
    return None


def _run_shell(payload: Dict[str, Any]) -> Dict[str, Any]:
    command = str(payload.get("command") or "")
    if not command:
        return {"ok": False, "error": "missing command"}
    blocked = _blocked_shell(command)
    if blocked:
        return {"ok": False, "error": f"blocked shell pattern: {blocked}"}
    timeout = int(payload.get("timeout") or 60)
    cwd = payload.get("cwd") or str(Path.home())
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            executable="/bin/zsh" if sys.platform == "darwin" and Path("/bin/zsh").exists() else None,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-20000:],
            "stderr": proc.stderr[-20000:],
            "duration_seconds": round(time.time() - started, 3),
            "cwd": str(cwd),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"command timed out after {timeout}s",
            "stdout": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-20000:] if isinstance(exc.stderr, str) else "",
        }


def _run_computer_use(payload: Dict[str, Any]) -> Dict[str, Any]:
    args = payload.get("args")
    if not isinstance(args, dict):
        return {"ok": False, "error": "payload.args must be a dict"}
    try:
        from tools.computer_use.tool import handle_computer_use
    except Exception as exc:
        return {"ok": False, "error": f"computer_use unavailable: {exc}"}
    result = handle_computer_use(args)
    if _result_has_recoverable_computer_use_error(result):
        try:
            from tools.computer_use.tool import reset_backend_for_tests
            reset_backend_for_tests()
            result = handle_computer_use(args)
        except Exception:
            pass
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            parsed = {"text": result}
        return {"ok": not bool(parsed.get("error")) if isinstance(parsed, dict) else True, "result": parsed}
    return {"ok": True, "result": result}


def _result_has_recoverable_computer_use_error(result: Any) -> bool:
    if isinstance(result, str):
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            return False
    elif isinstance(result, dict):
        data = result
    else:
        return False
    text = json.dumps(data).lower()
    return "session not started" in text or "backend unavailable" in text


def _list_apps() -> Dict[str, Any]:
    if _computer_use_available():
        return _run_computer_use({"args": {"action": "list_apps"}})
    if sys.platform == "darwin":
        script = 'tell application "System Events" to get name of every process whose background only is false'
        proc = subprocess.run(["osascript", "-e", script], text=True, capture_output=True, timeout=10)
        apps = [a.strip() for a in proc.stdout.split(",") if a.strip()]
        return {"ok": proc.returncode == 0, "apps": apps, "stderr": proc.stderr}
    proc = subprocess.run("ps -eo comm= | sort -u | head -100", shell=True, text=True, capture_output=True, timeout=10)
    return {"ok": proc.returncode == 0, "apps": [x for x in proc.stdout.splitlines() if x], "stderr": proc.stderr}


async def _dispatch(msg: Dict[str, Any], token: str, display_name: str) -> Dict[str, Any]:
    ok, reason = validate_request(msg, token)
    req_id = str(msg.get("id") or "")
    if not ok:
        return make_error(req_id, reason)

    t = msg["type"]
    payload = msg["payload"]
    try:
        if t == "ping":
            return make_response(req_id, {
                "display_name": display_name,
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "ts": time.time(),
            })
        if t == "capabilities":
            return make_response(req_id, _capabilities())
        if t == "status":
            return make_response(req_id, {"ok": True, "capabilities": _capabilities()})
        if t == "shell":
            return make_response(req_id, _run_shell(payload))
        if t == "computer_use":
            return make_response(req_id, _run_computer_use(payload))
        if t == "list_apps":
            return make_response(req_id, _list_apps())
    except Exception as exc:
        return make_error(req_id, f"{type(exc).__name__}: {exc}")
    return make_error(req_id, f"unhandled request: {t!r}")


async def serve(host: str, port: int, display_name: str) -> None:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("Install dependencies first: ./install.sh") from exc

    token = ensure_token()
    print(_ok("[device-worker] ready"), flush=True)
    print(f"{_label('display')}   {display_name}", flush=True)
    print(f"{_label('listen')}    ws://{host}:{port}", flush=True)
    print(f"{_label('token')}     {_secret(token)}", flush=True)
    print(_warn("approve on Hermes:"), flush=True)
    print(
        f"  hermes device node approve {display_name} "
        f"ws://<worker-ip>:{port} {token}",
        flush=True,
    )
    print(f"{_label('check')}     hermes device node capabilities {display_name}", flush=True)

    async def handler(ws):
        async for raw in ws:
            try:
                msg = decode(raw if isinstance(raw, str) else raw.decode("utf-8"))
            except Exception as exc:
                await ws.send(json.dumps(make_error("", f"decode: {exc}")))
                continue
            reply = await _dispatch(msg, token, display_name)
            await ws.send(json.dumps(reply, ensure_ascii=False))

    async with websockets.serve(handler, host, port):
        await asyncio.Future()


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes Device Worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18888)
    parser.add_argument("--display-name", default=socket.gethostname())
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.host, args.port, args.display_name))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(_warn(f"[device-worker] error: {exc}"), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
