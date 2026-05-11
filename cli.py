"""Top-level `hermes device ...` CLI wrapper."""

from __future__ import annotations

from plugins.device_worker.node.cli import device_command, register_cli

__all__ = ["device_command", "register_cli"]

