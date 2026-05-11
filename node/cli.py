"""`hermes device node ...` CLI."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from plugins.device_worker.node.client import DeviceNodeClient
from plugins.device_worker.node.registry import DeviceNodeRegistry


def _dist_source() -> Path:
    return Path(__file__).resolve().parents[1] / "worker_dist"


def _dist_target() -> Path:
    return Path(get_hermes_home()) / "device-worker"


def register_cli(subparser: argparse.ArgumentParser) -> None:
    sp = subparser.add_subparsers(dest="device_command", required=True)
    node = sp.add_parser("node", help="Manage approved device workers.")
    node_sp = node.add_subparsers(dest="node_cmd", required=True)

    approve = node_sp.add_parser("approve", help="Approve/register a worker node.")
    approve.add_argument("name")
    approve.add_argument("url")
    approve.add_argument("token")
    approve.set_defaults(func=device_command)

    rm = node_sp.add_parser("remove", aliases=["rm"], help="Remove a worker node.")
    rm.add_argument("name")
    rm.set_defaults(func=device_command)

    node_sp.add_parser("list", aliases=["ls"], help="List approved nodes.").set_defaults(func=device_command)

    ping = node_sp.add_parser("ping", aliases=["status"], help="Ping an approved node.")
    ping.add_argument("name")
    ping.set_defaults(func=device_command)

    cap = node_sp.add_parser("capabilities", aliases=["caps"], help="Show node capabilities.")
    cap.add_argument("name")
    cap.set_defaults(func=device_command)

    sync = sp.add_parser("sync-dist", help="Copy distributable worker client to ~/.hermes/device-worker.")
    sync.set_defaults(func=device_command)


def device_command(args: argparse.Namespace) -> int:
    if getattr(args, "device_command", None) == "sync-dist":
        src = _dist_source()
        dst = _dist_target()
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        print(f"synced device worker distribution to {dst}")
        return 0

    if getattr(args, "device_command", None) != "node":
        print("unknown device command")
        return 2

    reg = DeviceNodeRegistry()
    cmd = getattr(args, "node_cmd", "")

    if cmd == "approve":
        reg.add(args.name, args.url, args.token)
        print(f"approved device node {args.name!r} at {args.url}")
        return 0

    if cmd in ("remove", "rm"):
        ok = reg.remove(args.name)
        print(f"removed {args.name!r}" if ok else f"no such node: {args.name!r}")
        return 0 if ok else 1

    if cmd in ("list", "ls"):
        nodes = reg.list_all()
        if not nodes:
            print("no device nodes registered")
            return 0
        for node in nodes:
            token = node.get("token", "")
            print(f"{node['name']}\t{node['url']}\ttoken={token[:6]}…")
        return 0

    if cmd in ("ping", "status", "capabilities", "caps"):
        entry = reg.get(args.name)
        if entry is None:
            print(json.dumps({"ok": False, "error": f"no such node: {args.name!r}"}))
            return 1
        client = DeviceNodeClient(entry["url"], entry["token"])
        try:
            result = client.ping() if cmd in ("ping", "status") else client.capabilities()
        except Exception as exc:
            print(json.dumps({"ok": False, "node": args.name, "error": str(exc)}))
            return 1
        print(json.dumps({"ok": True, "node": args.name, **_coerce_dict(result)}, indent=2))
        return 0

    print(f"unknown node command: {cmd!r}")
    return 2


def _coerce_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {"result": value}

