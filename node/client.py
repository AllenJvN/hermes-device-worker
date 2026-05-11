"""Synchronous WebSocket client for approved device workers."""

from __future__ import annotations

from typing import Any, Dict

from plugins.device_worker.node import protocol as _proto


class DeviceNodeClient:
    def __init__(self, url: str, token: str, timeout: float = 20.0) -> None:
        if not url:
            raise ValueError("url must be non-empty")
        if not token:
            raise ValueError("token must be non-empty")
        self.url = url
        self.token = token
        self.timeout = float(timeout)

    def _rpc(self, type: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        try:
            from websockets.sync.client import connect  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Device worker client requires websockets. Install with: pip install websockets"
            ) from exc

        req = _proto.make_request(type, self.token, payload or {})
        with connect(self.url, open_timeout=self.timeout, close_timeout=self.timeout) as ws:
            ws.send(_proto.encode(req))
            raw = ws.recv(timeout=self.timeout)

        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        resp = _proto.decode(raw)
        if resp.get("type") == "error":
            raise RuntimeError(resp.get("error") or "worker error")
        if resp.get("id") != req["id"]:
            raise RuntimeError(f"response id mismatch: expected {req['id']}, got {resp.get('id')!r}")
        payload_out = resp.get("payload")
        if not isinstance(payload_out, dict):
            raise RuntimeError("response missing payload dict")
        return payload_out

    def ping(self) -> Dict[str, Any]:
        return self._rpc("ping")

    def capabilities(self) -> Dict[str, Any]:
        return self._rpc("capabilities")

    def status(self) -> Dict[str, Any]:
        return self._rpc("status")

    def shell(self, command: str, cwd: str | None = None, timeout: int = 60) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"command": command, "timeout": int(timeout)}
        if cwd:
            payload["cwd"] = cwd
        return self._rpc("shell", payload)

    def computer_use(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._rpc("computer_use", {"args": args})

    def list_apps(self) -> Dict[str, Any]:
        return self._rpc("list_apps")

