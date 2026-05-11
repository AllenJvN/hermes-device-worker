#!/usr/bin/env python3
"""Standalone Hermes device worker.

Runs on an edge machine and exposes a tiny bearer-token-authenticated
WebSocket RPC surface to the central Hermes VM.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import platform
import pty
import re
import secrets
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROTOCOL_VERSION = "compact-v1"
MAX_TEXT_READ_BYTES = 200_000
MAX_TOOL_OUTPUT_BYTES = 200_000
TERMINAL_BUFFER_CHARS = 250_000


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


def _dim(text: str) -> str:
    return _c("2", text)


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _preview(value: str, limit: int = 90) -> str:
    value = " ".join(str(value).split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _request_summary(msg: Dict[str, Any]) -> str:
    payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
    t = str(msg.get("type") or "unknown")
    if t == "shell":
        command = _preview(str(payload.get("command") or ""))
        cwd = payload.get("cwd")
        return f"shell command={command!r}" + (f" cwd={cwd}" if cwd else "")
    if t == "computer_use":
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        action = args.get("action", "?")
        bits = [f"computer_use action={action}"]
        for key in ("app", "mode", "element", "direction", "keys"):
            if args.get(key) is not None:
                bits.append(f"{key}={args[key]!r}")
        if action == "type" and args.get("text") is not None:
            text_preview = _preview(args["text"], 40)
            bits.append(f"text={text_preview!r}")
        return " ".join(bits)
    if t in {"workspace", "terminal", "desktop"}:
        action = payload.get("action", "?")
        bits = [f"{t} action={action}"]
        for key in ("path", "cwd", "pattern", "name", "terminal_id", "app", "mode", "element", "direction", "keys"):
            if payload.get(key) is not None:
                bits.append(f"{key}={payload[key]!r}")
        if t == "desktop" and action == "type" and payload.get("text") is not None:
            bits.append(f"text={_preview(payload['text'], 40)!r}")
        if t == "terminal" and payload.get("input") is not None:
            bits.append(f"input={_preview(payload['input'], 40)!r}")
        return " ".join(bits)
    return t


def _response_ok(reply: Dict[str, Any]) -> bool:
    if reply.get("type") == "error":
        return False
    payload = reply.get("payload")
    if isinstance(payload, dict) and payload.get("ok") is False:
        return False
    return True


def _log_request(msg: Dict[str, Any], reply: Dict[str, Any], elapsed: float, verbosity: str) -> None:
    if verbosity == "quiet":
        return
    ok = _response_ok(reply)
    status = _ok("ok") if ok else _warn("fail")
    elapsed_text = _dim(f"{elapsed:.3f}s")
    rpc_label = _label("rpc")
    print(
        f"{_dim(_now())} {status} {rpc_label} {_request_summary(msg)} {elapsed_text}",
        flush=True,
    )
    if not ok:
        err = reply.get("error")
        payload = reply.get("payload")
        if not err and isinstance(payload, dict):
            err = payload.get("error")
        if err:
            error_label = _warn("error")
            print(f"  {error_label} {_preview(str(err), 160)}", flush=True)


VALID_REQUEST_TYPES = frozenset({
    "ping",
    "capabilities",
    "status",
    "shell",
    "computer_use",
    "list_apps",
    "workspace",
    "terminal",
    "desktop",
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
        "protocol_version": PROTOCOL_VERSION,
        "tool_surface": "compact-v1",
        "shell": True,
        "workspace": True,
        "terminal_sessions": sys.platform != "win32",
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


def _clip_text(text: str, max_bytes: int = MAX_TOOL_OUTPUT_BYTES) -> Dict[str, Any]:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return {"text": text, "truncated": False, "bytes_returned": len(raw)}
    clipped = raw[:max_bytes].decode("utf-8", errors="replace")
    return {"text": clipped, "truncated": True, "bytes_returned": max_bytes, "bytes_total": len(raw)}


def _worker_shell() -> str:
    if sys.platform == "darwin" and Path("/bin/zsh").exists():
        return "/bin/zsh"
    if os.environ.get("SHELL"):
        return str(os.environ["SHELL"])
    return "/bin/bash" if Path("/bin/bash").exists() else "/bin/sh"


def _resolve_path(path: str | None = None, cwd: str | None = None) -> Path:
    base = Path(cwd).expanduser() if cwd else Path.home()
    raw = Path(path).expanduser() if path else base
    if not raw.is_absolute():
        raw = base / raw
    return raw.resolve(strict=False)


def _is_binary_sample(data: bytes) -> bool:
    return b"\x00" in data[:4096]


SYSTEM_WRITE_PREFIXES = tuple(Path(p) for p in (
    "/System",
    "/Library",
    "/usr",
    "/bin",
    "/sbin",
    "/etc",
    "/var",
    "/private/etc",
))


def _is_system_path(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path
    return any(resolved == prefix or prefix in resolved.parents for prefix in SYSTEM_WRITE_PREFIXES)


def _refuse_system_write(path: Path, allow: bool) -> Dict[str, Any] | None:
    if allow or not _is_system_path(path):
        return None
    return {
        "ok": False,
        "error": f"refusing to write under system path {path}; set allow_system_path only when explicitly requested",
    }


def _run_captured(
    argv: List[str],
    cwd: Path,
    timeout: int = 60,
    input_text: str | None = None,
    max_bytes: int = MAX_TOOL_OUTPUT_BYTES,
) -> Dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            text=True,
            input=input_text,
            capture_output=True,
            timeout=timeout,
        )
        stdout = _clip_text(proc.stdout, max_bytes)
        stderr = _clip_text(proc.stderr, max_bytes)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout["text"],
            "stderr": stderr["text"],
            "stdout_truncated": stdout["truncated"],
            "stderr_truncated": stderr["truncated"],
            "duration_seconds": round(time.time() - started, 3),
            "cwd": str(cwd),
        }
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc), "cwd": str(cwd)}
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "ok": False,
            "error": f"command timed out after {timeout}s",
            "stdout": _clip_text(stdout, max_bytes)["text"],
            "stderr": _clip_text(stderr, max_bytes)["text"],
            "cwd": str(cwd),
        }


def _workspace_info(payload: Dict[str, Any]) -> Dict[str, Any]:
    root = _resolve_path(payload.get("path") or payload.get("cwd") or "~")
    return {
        "ok": True,
        "protocol_version": PROTOCOL_VERSION,
        "tool_surface": "compact-v1",
        "home": str(Path.home()),
        "cwd": str(root),
        "platform": platform.platform(),
        "has_git": bool(shutil.which("git")),
        "has_rg": bool(shutil.which("rg")),
        "path_exists": root.exists(),
        "path_is_dir": root.is_dir(),
    }


def _workspace_list(payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve_path(payload.get("path"), payload.get("cwd"))
    limit = max(1, min(int(payload.get("limit") or 100), 1000))
    if not path.exists():
        return {"ok": False, "error": f"path does not exist: {path}", "path": str(path)}
    if not path.is_dir():
        return {"ok": False, "error": f"path is not a directory: {path}", "path": str(path)}
    entries = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:limit]:
        try:
            st = child.stat()
            typ = "directory" if child.is_dir() else "file"
            entries.append({"name": child.name, "path": str(child), "type": typ, "size": st.st_size, "mtime": st.st_mtime})
        except OSError as exc:
            entries.append({"name": child.name, "path": str(child), "error": str(exc)})
    return {"ok": True, "path": str(path), "entries": entries, "count": len(entries)}


def _workspace_read(payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve_path(payload.get("path"), payload.get("cwd"))
    max_bytes = max(1, min(int(payload.get("max_bytes") or MAX_TEXT_READ_BYTES), 2_000_000))
    if not path.is_file():
        return {"ok": False, "error": f"not a file: {path}", "path": str(path)}
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": str(exc), "path": str(path)}
    if _is_binary_sample(data):
        return {"ok": False, "error": "binary file - refusing text read", "path": str(path), "size": len(data)}
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return {
        "ok": True,
        "path": str(path),
        "content": text,
        "encoding": "utf-8",
        "truncated": len(data) > max_bytes,
        "bytes_returned": min(len(data), max_bytes),
        "bytes_total": len(data),
    }


def _workspace_write(payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve_path(payload.get("path"), payload.get("cwd"))
    refused = _refuse_system_write(path, bool(payload.get("allow_system_path")))
    if refused:
        return refused
    if "content" not in payload:
        return {"ok": False, "error": "missing content for write"}
    content = str(payload.get("content") or "")
    if len(content.encode("utf-8")) > 2_000_000 and not payload.get("allow_large_write"):
        return {"ok": False, "error": "write content exceeds 2MB; refusing without allow_large_write"}
    if payload.get("create_parents"):
        path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if payload.get("append") else "w"
    try:
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "path": str(path)}
    return {"ok": True, "path": str(path), "bytes_written": len(content.encode("utf-8")), "append": bool(payload.get("append"))}


def _workspace_search(payload: Dict[str, Any]) -> Dict[str, Any]:
    pattern = str(payload.get("pattern") or "")
    if not pattern:
        return {"ok": False, "error": "missing pattern for search"}
    cwd = _resolve_path(payload.get("path") or payload.get("cwd") or ".")
    max_bytes = max(1, min(int(payload.get("max_bytes") or MAX_TOOL_OUTPUT_BYTES), 1_000_000))
    if shutil.which("rg"):
        argv = ["rg", "--line-number", "--no-heading", "--color", "never", "--smart-case", pattern, str(cwd)]
        result = _run_captured(
            argv,
            cwd if cwd.is_dir() else cwd.parent,
            timeout=int(payload.get("timeout") or 30),
            max_bytes=max_bytes,
        )
        result["engine"] = "rg"
        if result.get("returncode") == 1:
            result["ok"] = True
        return result
    matches: List[str] = []
    limit = max(1, min(int(payload.get("limit") or 100), 1000))
    root = cwd if cwd.is_dir() else cwd.parent
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", ".venv", "__pycache__"}]
        for filename in filenames:
            path = Path(dirpath) / filename
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if _is_binary_sample(data):
                continue
            for idx, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if pattern.lower() in line.lower():
                    matches.append(f"{path}:{idx}:{line}")
                    if len(matches) >= limit:
                        text = "\n".join(matches)
                        clipped = _clip_text(text, max_bytes)
                        return {"ok": True, "engine": "python", "stdout": clipped["text"], "truncated": clipped["truncated"]}
    text = "\n".join(matches)
    clipped = _clip_text(text, max_bytes)
    return {"ok": True, "engine": "python", "stdout": clipped["text"], "truncated": clipped["truncated"]}


def _workspace_apply_patch(payload: Dict[str, Any]) -> Dict[str, Any]:
    patch_text = str(payload.get("patch") or "")
    if not patch_text:
        return {"ok": False, "error": "missing patch for apply_patch"}
    cwd = _resolve_path(payload.get("cwd") or payload.get("path") or ".")
    refused = _refuse_system_write(cwd, bool(payload.get("allow_system_path")))
    if refused:
        return refused
    if not cwd.is_dir():
        return {"ok": False, "error": f"cwd is not a directory: {cwd}", "cwd": str(cwd)}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
        tmp.write(patch_text)
        tmp_path = tmp.name
    try:
        if shutil.which("git"):
            check = _run_captured(["git", "apply", "--check", tmp_path], cwd, timeout=30)
            if check.get("ok"):
                return _run_captured(["git", "apply", "--whitespace=nowarn", tmp_path], cwd, timeout=30)
            if shutil.which("patch"):
                fallback = _run_captured(["patch", "-p1", "-i", tmp_path], cwd, timeout=30)
                fallback["git_apply_check"] = check
                return fallback
            return check
        if shutil.which("patch"):
            return _run_captured(["patch", "-p1", "-i", tmp_path], cwd, timeout=30)
        return {"ok": False, "error": "neither git nor patch is available", "cwd": str(cwd)}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _workspace_git_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    cwd = _resolve_path(payload.get("cwd") or payload.get("path") or ".")
    return _run_captured(
        ["git", "status", "--short", "--branch"],
        cwd,
        timeout=30,
        max_bytes=int(payload.get("max_bytes") or MAX_TOOL_OUTPUT_BYTES),
    )


def _workspace_git_diff(payload: Dict[str, Any]) -> Dict[str, Any]:
    cwd = _resolve_path(payload.get("cwd") or ".")
    argv = ["git", "diff", "--"]
    if payload.get("path"):
        argv.append(str(_resolve_path(payload.get("path"), payload.get("cwd"))))
    return _run_captured(argv, cwd, timeout=30, max_bytes=int(payload.get("max_bytes") or MAX_TOOL_OUTPUT_BYTES))


def _run_workspace(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload.get("command") or payload.get("cmd"):
        return {"ok": False, "error": "Use `device_terminal` for commands."}
    action = str(payload.get("action") or "")
    handlers = {
        "info": _workspace_info,
        "list": _workspace_list,
        "read": _workspace_read,
        "write": _workspace_write,
        "search": _workspace_search,
        "apply_patch": _workspace_apply_patch,
        "git_status": _workspace_git_status,
        "git_diff": _workspace_git_diff,
    }
    handler = handlers.get(action)
    if handler is None:
        return {"ok": False, "error": f"unknown workspace action {action!r}"}
    return handler(payload)


class TerminalSession:
    def __init__(self, name: str, cwd: Path, command: str | None, cols: int, rows: int):
        if sys.platform == "win32":
            raise RuntimeError("terminal sessions require a POSIX PTY")
        self.id = uuid.uuid4().hex[:12]
        self.name = name or self.id
        self.cwd = str(cwd)
        self.created_at = time.time()
        self.last_activity = self.created_at
        self._lock = threading.Lock()
        self._buffer = ""
        self._base_cursor = 0
        self._closed = False

        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        shell = _worker_shell()
        proc = subprocess.Popen(
            [shell, "-l"],
            cwd=str(cwd),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            env={**os.environ, "TERM": os.environ.get("TERM") or "xterm-256color"},
        )
        os.close(slave_fd)
        self.proc = proc
        self.resize(cols, rows)
        self._reader = threading.Thread(target=self._reader_loop, name=f"hermes-term-{self.id}", daemon=True)
        self._reader.start()
        if command:
            time.sleep(0.05)
            self.write(command if command.endswith("\n") else command + "\n")

    def _reader_loop(self) -> None:
        while not self._closed:
            try:
                readable, _, _ = select.select([self._master_fd], [], [], 0.2)
                if not readable:
                    if self.proc.poll() is not None:
                        break
                    continue
                data = os.read(self._master_fd, 65536)
            except OSError:
                break
            if not data:
                break
            text = data.decode("utf-8", errors="replace")
            with self._lock:
                self._buffer += text
                if len(self._buffer) > TERMINAL_BUFFER_CHARS:
                    drop = len(self._buffer) - TERMINAL_BUFFER_CHARS
                    self._buffer = self._buffer[drop:]
                    self._base_cursor += drop
                self.last_activity = time.time()

    def alive(self) -> bool:
        return not self._closed and self.proc.poll() is None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            end_cursor = self._base_cursor + len(self._buffer)
        return {
            "id": self.id,
            "name": self.name,
            "cwd": self.cwd,
            "pid": self.proc.pid,
            "alive": self.alive(),
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "cursor": end_cursor,
        }

    def read(self, cursor: int | None = None, max_bytes: int = 20_000) -> Dict[str, Any]:
        with self._lock:
            base = self._base_cursor
            end = base + len(self._buffer)
            requested = base if cursor is None else int(cursor)
            clipped_cursor = max(base, min(requested, end))
            start = clipped_cursor - base
            text = self._buffer[start:]
        clipped = _clip_text(text, max_bytes)
        return {
            "ok": True,
            "terminal": self.snapshot(),
            "output": clipped["text"],
            "cursor": end,
            "cursor_was_clipped": cursor is not None and int(cursor) < base,
            "truncated": clipped["truncated"],
        }

    def write(self, data: str) -> Dict[str, Any]:
        if not self.alive():
            return {"ok": False, "error": f"terminal {self.id} is not alive"}
        raw = data.encode("utf-8")
        os.write(self._master_fd, raw)
        self.last_activity = time.time()
        return {"ok": True, "bytes_written": len(raw), "terminal": self.snapshot()}

    def resize(self, cols: int, rows: int) -> Dict[str, Any]:
        cols = max(20, int(cols or 120))
        rows = max(5, int(rows or 30))
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass
        return {"ok": True, "terminal": self.snapshot(), "cols": cols, "rows": rows}

    def kill(self) -> Dict[str, Any]:
        self._closed = True
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGHUP)
        except Exception:
            try:
                self.proc.terminate()
            except Exception:
                pass
        deadline = time.time() + 1.0
        while self.proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        if self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        try:
            os.close(self._master_fd)
        except OSError:
            pass
        return {"ok": True, "terminal": self.snapshot()}


class TerminalManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, TerminalSession] = {}

    def _get(self, payload: Dict[str, Any]) -> TerminalSession | None:
        term_id = payload.get("terminal_id")
        name = payload.get("name")
        with self._lock:
            if term_id and term_id in self._sessions:
                return self._sessions[term_id]
            if name:
                for session in self._sessions.values():
                    if session.name == name:
                        return session
        return None

    def handle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("path") or payload.get("content") or payload.get("patch"):
            return {"ok": False, "error": "Use `device_workspace` for file operations."}
        action = str(payload.get("action") or "")
        if action == "create":
            cwd = _resolve_path(payload.get("cwd") or ".")
            if not cwd.is_dir():
                return {"ok": False, "error": f"cwd is not a directory: {cwd}"}
            session = TerminalSession(
                name=str(payload.get("name") or ""),
                cwd=cwd,
                command=str(payload.get("command") or "") or None,
                cols=int(payload.get("cols") or 120),
                rows=int(payload.get("rows") or 30),
            )
            with self._lock:
                self._sessions[session.id] = session
            return {"ok": True, "terminal": session.snapshot()}
        if action == "list":
            with self._lock:
                sessions = [s.snapshot() for s in self._sessions.values()]
            return {"ok": True, "terminals": sessions}
        session = self._get(payload)
        if session is None:
            return {"ok": False, "error": "terminal not found; pass terminal_id or name from device_terminal action=list"}
        if action == "send":
            if "input" not in payload:
                return {"ok": False, "error": "missing input for terminal send"}
            return session.write(str(payload.get("input") or ""))
        if action == "read":
            return session.read(payload.get("cursor"), max_bytes=int(payload.get("max_bytes") or 20_000))
        if action == "resize":
            return session.resize(int(payload.get("cols") or 120), int(payload.get("rows") or 30))
        if action == "kill":
            result = session.kill()
            with self._lock:
                self._sessions.pop(session.id, None)
            return result
        return {"ok": False, "error": f"unknown terminal action {action!r}"}

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.kill()


TERMINALS = TerminalManager()


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


def _run_desktop(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload.get("path") or payload.get("cwd") or payload.get("patch"):
        return {"ok": False, "error": "Use `device_workspace` unless GUI control is required."}
    action = str(payload.get("action") or "")
    if action == "list_apps":
        return _list_apps()
    if action == "capture":
        args = {"action": "capture", "mode": payload.get("mode", "som"), "app": payload.get("app")}
        return _run_computer_use({"args": args})
    if action in {"click", "type", "key", "scroll"}:
        args = {k: v for k, v in payload.items() if v is not None}
        if action == "key":
            args["action"] = "key"
        return _run_computer_use({"args": args})
    return {"ok": False, "error": f"unknown desktop action {action!r}"}


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
                "protocol_version": PROTOCOL_VERSION,
                "tool_surface": "compact-v1",
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
        if t == "workspace":
            return make_response(req_id, _run_workspace(payload))
        if t == "terminal":
            return make_response(req_id, TERMINALS.handle(payload))
        if t == "desktop":
            return make_response(req_id, _run_desktop(payload))
    except Exception as exc:
        return make_error(req_id, f"{type(exc).__name__}: {exc}")
    return make_error(req_id, f"unhandled request: {t!r}")


async def serve(host: str, port: int, display_name: str, verbosity: str = "normal") -> None:
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
            started = time.time()
            try:
                msg = decode(raw if isinstance(raw, str) else raw.decode("utf-8"))
            except Exception as exc:
                reply = make_error("", f"decode: {exc}")
                await ws.send(json.dumps(reply))
                if verbosity != "quiet":
                    fail_label = _warn("fail")
                    rpc_label = _label("rpc")
                    zero_elapsed = _dim("0.000s")
                    print(f"{_dim(_now())} {fail_label} {rpc_label} decode {zero_elapsed}", flush=True)
                continue
            reply = await _dispatch(msg, token, display_name)
            _log_request(msg, reply, time.time() - started, verbosity)
            await ws.send(json.dumps(reply, ensure_ascii=False))

    try:
        async with websockets.serve(handler, host, port):
            await asyncio.Future()
    finally:
        TERMINALS.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes Device Worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18888)
    parser.add_argument("--display-name", default=socket.gethostname())
    parser.add_argument("--verbosity", choices=["quiet", "normal"], default=os.environ.get("HERMES_DEVICE_WORKER_VERBOSITY", "normal"))
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.host, args.port, args.display_name, args.verbosity))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(_warn(f"[device-worker] error: {exc}"), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
