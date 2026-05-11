# Agent Handoff: Hermes Device Worker

This repository is the canonical source for the Hermes Device Worker plugin.

## Current Homelab State

- Central Hermes VM: `hermes`, reachable in Allen's homelab as SSH host `hermes`.
- Canonical repo clone on Hermes: `/home/allen/projects/hermes-device-worker`.
- Live/test plugin copy: `/home/allen/.hermes/hermes-agent/plugins/device_worker`.
- Distributable worker copy: `/home/allen/.hermes/device-worker`.
- Public GitHub repo: `https://github.com/AllenJvN/hermes-device-worker`.
- A Mac worker has been paired for testing as `macbook-allen`.
- Current registered node URL at time of writing: `ws://192.168.1.187:18888`.

## Source Of Truth

Edit the repo clone first:

```bash
cd /home/allen/projects/hermes-device-worker
```

Keep the public `README.md` generic and install-focused. Allen-specific paths,
live homelab sync notes, and testing context belong in this file unless they are
clearly labeled as examples.

After testing changes, keep these copies synchronized:

```bash
rsync -a --delete --exclude .git --exclude __pycache__ --exclude '*.pyc' ./ \
  ~/.hermes/hermes-agent/plugins/device_worker/
hermes device sync-dist
```

Then commit and push:

```bash
git status
git add .
git commit -m "Describe worker/plugin change"
git push
```

Do not treat `/tmp` copies as durable source. They were only used during the
initial bootstrap.

## Validation Checklist

Run syntax checks from the repo:

```bash
python3 -m py_compile $(find . -name '*.py')
bash -n worker_dist/*.sh
```

If the Mac worker is running, validate the live path:

```bash
hermes device node ping macbook-allen
hermes device node capabilities macbook-allen
```

Expected compact tool/coding status:

```json
{"tool_surface": "compact-v1", "workspace": true, "terminal_sessions": true, "computer_use": true}
```

Terminal sessions are intentionally persistent, but should not leak unbounded
memory: output buffers are capped, live session count is capped, exited sessions
are pruned after the retention window, and worker shutdown/SIGTERM/SIGINT kills
all sessions.

For capture mismatch testing, ask for an app with no visible window and confirm
the tool result includes a warning such as: requested `Finder`, captured
`Calendar`.

The public tool surface should stay compact:

- `device_node` for discovery/status
- `device_workspace` for files/repos/search/patch/git
- `device_terminal` for persistent command sessions
- `device_desktop` for GUI/app control

Do not re-expand the plugin into many single-action tools unless there is a
strong reason; tool schema bloat makes Hermes less predictable.

## Design Notes

- Plugin root is intentionally the repository root because Hermes supports:
  `hermes plugins install AllenJvN/hermes-device-worker --enable`.
- `worker_dist/` is the client folder copied to each worker machine.
- Worker tokens are generated locally on worker machines and must never be
  committed.
- The worker is LAN-first; do not expose it publicly without adding a stronger
  transport/security layer.
- Toolsets are split intentionally: `device_worker` registers node/desktop
  tools, and `device_coding` registers workspace/terminal tools.
