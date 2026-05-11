"""File-backed registry of approved device workers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home


def _default_path() -> Path:
    return Path(get_hermes_home()) / "workspace" / "device_nodes" / "nodes.json"


class DeviceNodeRegistry:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else _default_path()

    def _load(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return {"nodes": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"nodes": {}}
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), dict):
            return {"nodes": {}}
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        entry = self._load()["nodes"].get(name)
        if entry is None:
            return None
        return {"name": name, **entry}

    def add(self, name: str, url: str, token: str) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("node name must be non-empty")
        if not isinstance(url, str) or not url.startswith(("ws://", "wss://")):
            raise ValueError("url must start with ws:// or wss://")
        if not isinstance(token, str) or not token:
            raise ValueError("token must be non-empty")
        data = self._load()
        data["nodes"][name] = {
            "url": url,
            "token": token,
            "added_at": time.time(),
        }
        self._save(data)

    def remove(self, name: str) -> bool:
        data = self._load()
        if name not in data["nodes"]:
            return False
        del data["nodes"][name]
        self._save(data)
        return True

    def list_all(self) -> List[Dict[str, Any]]:
        data = self._load()
        return [{"name": name, **entry} for name, entry in sorted(data["nodes"].items())]

    def resolve(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        if name:
            return self.get(name)
        nodes = self.list_all()
        if len(nodes) == 1:
            return nodes[0]
        return None

