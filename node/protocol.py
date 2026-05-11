"""Wire protocol for Hermes <-> device worker RPC."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Tuple


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


def make_request(
    type: str,
    token: str,
    payload: Dict[str, Any] | None = None,
    req_id: str | None = None,
) -> Dict[str, Any]:
    if not isinstance(type, str) or type not in VALID_REQUEST_TYPES:
        raise ValueError(f"unknown request type: {type!r}")
    if not isinstance(token, str) or not token:
        raise ValueError("token must be a non-empty string")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    return {
        "type": type,
        "id": req_id or uuid.uuid4().hex,
        "token": token,
        "payload": payload,
    }


def make_response(req_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    return {"type": "response", "id": req_id, "payload": payload}


def make_error(req_id: str, error: str) -> Dict[str, Any]:
    return {"type": "error", "id": req_id, "error": str(error)}


def encode(msg: Dict[str, Any]) -> str:
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False)


def decode(raw: str) -> Dict[str, Any]:
    try:
        obj = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("envelope must be a JSON object")
    if "type" not in obj or not isinstance(obj["type"], str):
        raise ValueError("envelope missing string 'type'")
    if "id" not in obj or not isinstance(obj["id"], str):
        raise ValueError("envelope missing string 'id'")
    return obj


def validate_request(msg: Dict[str, Any], expected_token: str) -> Tuple[bool, str]:
    if not isinstance(msg, dict):
        return False, "envelope must be a dict"
    t = msg.get("type")
    if not isinstance(t, str) or t not in VALID_REQUEST_TYPES:
        return False, f"unknown request type: {t!r}"
    if not isinstance(msg.get("id"), str) or not msg.get("id"):
        return False, "missing or non-string 'id'"
    token = msg.get("token")
    if not isinstance(token, str) or not token:
        return False, "missing token"
    if token != expected_token:
        return False, "token mismatch"
    payload = msg.get("payload")
    if not isinstance(payload, dict):
        return False, "payload must be a dict"
    return True, ""
