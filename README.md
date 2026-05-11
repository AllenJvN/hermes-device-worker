# Hermes Device Worker

Hermes Device Worker lets one central [Hermes Agent](https://github.com/NousResearch/hermes-agent)
instance control trusted local machines over a private LAN or VPN WebSocket
connection.

The central Hermes machine stays the brain. Each worker machine runs a small
local process that provides shell access, app discovery, and optional macOS GUI
control. The first supported worker target is macOS.

Current capabilities:

- Compact node/status tools for approved worker machines
- Workspace tools for repo inspection, search, patching, and git status/diff
- Named persistent terminal sessions for tests, dev servers, and REPLs
- Running app discovery
- macOS desktop/app capture
- Background click, type, key, and scroll actions through `cua-driver`
- Multiple named workers, for example `macbook-allen` or `imac-office`

This is intentionally LAN/private-network first. Do not expose worker ports
directly to the public internet.

## Architecture

Hermes Device Worker has two parts:

- **Hermes plugin:** installed on the central Hermes Agent machine. It adds the
  `hermes device ...` CLI commands and the agent tools.
- **Worker client:** copied from the Hermes machine to each trusted worker
  machine. It listens for authenticated WebSocket requests from Hermes.

The plugin install does not magically install anything on your Mac. After
installing the Hermes plugin, you must copy the generated worker client folder
to the machine you want Hermes to control. `scp` is the recommended first path,
but any trusted file-copy method works.

## Prerequisites

On the central Hermes machine:

- Hermes Agent is installed and working.
- The `hermes` CLI is available.
- The Hermes machine can reach the worker machine over LAN, Tailscale, or
  another private network.
- Enable toolset `device_worker` for node/desktop control and `device_coding`
  for workspace/terminal coding tools.

On the worker Mac:

- Python 3.10 or newer.
- SSH access to the Hermes machine, or another way to copy files from Hermes.
- macOS Accessibility and Screen Recording approval for GUI control.
- `cua-driver` for desktop/app capture and control. The install script can
  install it with `--install-cua`.

## Install The Plugin On Hermes

Run this on the central Hermes machine:

```bash
hermes plugins install AllenJvN/hermes-device-worker --enable
hermes device sync-dist
```

`hermes device sync-dist` writes the distributable worker client to:

```text
~/.hermes/device-worker
```

If you are reinstalling or updating the plugin:

```bash
hermes plugins install AllenJvN/hermes-device-worker --enable --force
hermes device sync-dist
```

Confirm the CLI is available:

```bash
hermes device node list
```

If no workers are approved yet, this should print:

```text
no device nodes registered
```

## Copy The Worker To A Mac

Run this on the Mac you want Hermes to control:

```bash
scp -r <hermes-ssh-host>:~/.hermes/device-worker ~/device-worker
cd ~/device-worker
```

`<hermes-ssh-host>` must be something your Mac can SSH to. It can be an SSH
alias or a full `user@host` target.

If you have an SSH alias named `hermes`:

```bash
scp -r hermes:~/.hermes/device-worker ~/device-worker
```

If you do not have an SSH alias, use the username and IP or hostname:

```bash
scp -r allen@192.168.1.130:/home/allen/.hermes/device-worker ~/device-worker
```

The example above is only an example. Replace `allen`, `192.168.1.130`, and the
remote path with your Hermes machine's actual SSH username, host, and home
directory.

If `scp` says `Could not resolve hostname hermes`, your Mac does not know what
`hermes` means. Either configure an SSH alias in `~/.ssh/config` or use
`user@ip-address`.

## Start And Approve A Worker

On the worker Mac:

```bash
cd ~/device-worker
./install.sh --install-cua
./check_permissions.sh
./run.sh --display-name <node-name> --host 0.0.0.0 --port 18888
```

Use a stable, human-readable node name such as `macbook-allen`, `studio-mac`,
or `imac-office`.

The worker prints a local bearer token and an approval command. Keep the worker
running while you approve it.

Find the Mac's LAN IP if you do not already know it:

```bash
ipconfig getifaddr en0 || ipconfig getifaddr en1
```

Back on the central Hermes machine:

```bash
hermes device node approve <node-name> ws://<worker-ip>:18888 <token>
hermes device node ping <node-name>
hermes device node capabilities <node-name>
```

Example:

```bash
hermes device node approve macbook-allen ws://192.168.1.187:18888 f051...
hermes device node ping macbook-allen
hermes device node capabilities macbook-allen
```

Expected GUI-ready capability:

```json
{
  "computer_use": true
}
```

If `computer_use` is `false`, shell access may still work, but macOS GUI
capture/control is not ready yet.

## What Should I See?

In the worker terminal, startup should show:

- The display name
- The listening address, usually `ws://0.0.0.0:18888`
- A token to copy to Hermes
- The exact `hermes device node approve ...` command shape

After Hermes connects, the worker logs RPC activity by default, for example:

```text
17:32:11 ok rpc ping 0.001s
17:32:14 ok rpc capabilities 0.423s
17:32:20 ok rpc workspace action=git_status cwd='/Users/me/project' 0.031s
17:32:25 ok rpc terminal action=read name='dev-server' 0.002s
17:32:26 ok rpc desktop action=capture app='Terminal' mode='ax' 0.812s
```

The worker startup banner also prints the protocol version and terminal policy:

```text
runtime   compact-v1
terminal  max=24 dead-retain=1800s buffer=250000
```

On Hermes, `ping` should return worker host/platform information. Capabilities
should include `workspace`, `terminal_sessions`, and, when permissions are
ready, `computer_use`.

## Use From Hermes

The plugin exposes four compact agent tools:

- `device_node`: discover and check approved workers.
- `device_workspace`: inspect/edit files and repos.
- `device_terminal`: manage persistent terminals for commands, tests, REPLs, and servers.
- `device_desktop`: control GUI apps through capture/click/type/key/scroll.

Useful Hermes prompts:

```text
List my approved device workers.
On macbook-allen, show git status for ~/src/my-app.
On macbook-allen, search ~/src/my-app for "TODO".
On macbook-allen, start a terminal named dev-server in ~/src/my-app and run npm run dev.
Read the latest output from the dev-server terminal on macbook-allen.
On macbook-allen, list running apps.
On macbook-allen, capture Safari.
```

For GUI work, capture first, then act on element indexes:

```text
On macbook-allen, capture Notes.
Click element 12 on macbook-allen.
Type "hello from Hermes" on macbook-allen.
```

The tools are intentionally domain-specific:

- Use `device_node` only for discovery/status.
- Use `device_workspace` for files, repos, search, and patches.
- Use `device_terminal` for commands and long-running processes.
- Use `device_desktop` only when GUI control is required.

Version `0.2.0` replaced the older many-tool surface
(`device_shell`, `device_capture`, `device_click`, and friends) with these four
compact tools to reduce context bloat and wrong-tool selection.

Terminal lifecycle defaults:

- Terminals are persistent by design until killed or the worker stops.
- Worker shutdown, SIGINT, and SIGTERM close all terminal sessions.
- Terminal output is stored in a bounded ring buffer per session.
- Exited terminal sessions are retained briefly for log reads, then pruned.
- Duplicate live terminal names are rejected to avoid accidental session piles.
- The default cap is 24 terminal sessions per worker.

You can also test directly with the CLI:

```bash
hermes device node list
hermes device node ping <node-name>
hermes device node capabilities <node-name>
```

## Networking

Hermes initiates WebSocket requests to the worker. That means the central
Hermes machine must be able to reach:

```text
ws://<worker-ip>:18888
```

Recommended network options:

- Same LAN
- Tailscale
- WireGuard or another private VPN

Avoid exposing the worker port through a public reverse proxy. Pairing a worker
grants powerful access to that machine, so keep the transport private.

If `ping` times out:

- Confirm the worker is still running.
- Confirm the registered IP is the Mac's current LAN or VPN IP.
- Confirm the port matches the worker's `--port`.
- Check that the Mac firewall allows inbound connections to the terminal,
  Python, or launcher running the worker.

## macOS Permissions

macOS requires manual approval for desktop control. The worker cannot grant
these permissions automatically.

Run this on the worker Mac:

```bash
./check_permissions.sh
```

Approve the terminal app or launcher running the worker in:

- System Settings -> Privacy & Security -> Accessibility
- System Settings -> Privacy & Security -> Screen Recording

Also approve Python or `cua-driver` if macOS shows them.

Restart the worker after changing permissions, then re-check from Hermes:

```bash
hermes device node capabilities <node-name>
```

## Run As A macOS Service

After the worker runs successfully in a terminal, you can install it as a
LaunchAgent:

```bash
cd ~/device-worker
./install_launchd.sh <node-name>
launchctl kickstart -k gui/$UID/com.hermes.device-worker
```

Logs:

```bash
tail -f ~/Library/Logs/hermes-device-worker.log
tail -f ~/Library/Logs/hermes-device-worker.err.log
```

## Security Model

Treat approved workers as trusted machines.

- Pairing grants Hermes workspace, terminal, and GUI control on that worker.
- Worker tokens are generated locally and should never be committed.
- The central Hermes registry stores approved node URLs and tokens at
  `~/.hermes/workspace/device_nodes/nodes.json`.
- LAN/private-network operation is the intended v1 deployment model.
- Built-in shell, write-path, terminal, and GUI safety blocks are guardrails,
  not a security sandbox.
- Only approve workers that you personally control and trust.

## Troubleshooting

### `scp: Could not resolve hostname hermes`

`hermes` is only an SSH alias. Configure it in `~/.ssh/config`, or use a full
SSH target:

```bash
scp -r user@192.168.1.130:/home/user/.hermes/device-worker ~/device-worker
```

### `./run.sh: ... .venv/bin/python: No such file or directory`

The worker environment has not been installed yet. Run:

```bash
./install.sh --install-cua
```

### `computer_use` is `false`

Run the permissions helper, approve Accessibility and Screen Recording, then
restart the worker:

```bash
./check_permissions.sh
```

### `hermes device node ping` times out

Check that:

- The worker process is running.
- Hermes can reach the worker IP.
- The registered URL uses the correct port.
- macOS firewall is not blocking inbound worker connections.

### Capture uses a different app than requested

If the requested app has no visible or capturable window, the macOS backend may
capture another available app and return a warning. Open or focus the intended
app, then capture again.

### Hermes seems to choose the wrong device tool

The compact tool surface is intentionally split by domain. Rephrase with the
domain if needed:

- “Use the workspace tool to read/edit/search files.”
- “Use the terminal tool to run tests or keep a server alive.”
- “Use the desktop tool to click/type/capture GUI apps.”
- “Use the node tool to list or check workers.”

## Development

The repo root is the Hermes plugin. `worker_dist/` is the client folder copied
to worker machines by `hermes device sync-dist`.

Basic checks:

```bash
python3 -m py_compile $(find . -name '*.py')
bash -n worker_dist/*.sh
```

Maintainer and homelab-specific continuation notes live in
[`AGENTS.md`](AGENTS.md).
