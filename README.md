# Hermes Device Worker

Remote device workers for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

This plugin lets one central Hermes instance, for example a Proxmox/Linux VM,
control approved LAN worker machines. The first supported worker target is
macOS, with:

- remote shell commands
- running app discovery
- macOS desktop/app capture
- background click/type/key/scroll via `cua-driver`

The design keeps Hermes centralized while letting each Mac act as a local
"hands and eyes" node.

## Install The Hermes Plugin

On the central Hermes machine:

```bash
hermes plugins install AllenJvN/hermes-device-worker --enable
hermes device sync-dist
```

This installs/enables the plugin and writes the distributable worker client to:

```text
~/.hermes/device-worker
```

If the plugin is already installed:

```bash
hermes plugins install AllenJvN/hermes-device-worker --enable --force
hermes device sync-dist
```

## Start A Mac Worker

On the Mac you want Hermes to control:

```bash
scp -r hermes:~/.hermes/device-worker ~/device-worker
cd ~/device-worker
./install.sh --install-cua
./check_permissions.sh
./run.sh --display-name macbook-allen --host 0.0.0.0 --port 18888
```

The worker prints a token. On the central Hermes machine, approve it:

```bash
hermes device node approve macbook-allen ws://<mac-lan-ip>:18888 <token>
hermes device node ping macbook-allen
hermes device node capabilities macbook-allen
```

Expected GUI-ready capability:

```json
{
  "computer_use": true
}
```

If you do not know the Mac's LAN IP:

```bash
ipconfig getifaddr en0 || ipconfig getifaddr en1
```

## Use From Hermes

The plugin registers these tools:

- `device_list_nodes`
- `device_ping_node`
- `device_shell`
- `device_capture`
- `device_click`
- `device_type`
- `device_key`
- `device_scroll`
- `device_list_apps`

Example prompts:

```text
On macbook-allen, run hostname.
On macbook-allen, list running apps.
On macbook-allen, capture Safari.
On macbook-allen, open VS Code using the shell.
```

For GUI work, capture first, then act on element indexes:

```text
On macbook-allen, capture Notes.
Click element 12 on macbook-allen.
Type "hello from Hermes" on macbook-allen.
```

## macOS Permissions

macOS requires manual approval for desktop control. The worker cannot grant
these permissions automatically.

Run:

```bash
./check_permissions.sh
```

Approve the terminal app or launcher running the worker in:

- System Settings -> Privacy & Security -> Accessibility
- System Settings -> Privacy & Security -> Screen Recording

Also approve Python or `cua-driver` if macOS shows them.

Restart the worker after changing permissions.

## Run As A macOS Service

On the Mac:

```bash
cd ~/device-worker
./install_launchd.sh macbook-allen
launchctl kickstart -k gui/$UID/com.hermes.device-worker
```

Logs:

```bash
tail -f ~/Library/Logs/hermes-device-worker.log
tail -f ~/Library/Logs/hermes-device-worker.err.log
```

## Security Model

- LAN-first. Do not expose workers directly to the public internet.
- Each worker generates a local bearer token at first start.
- The central Hermes registry stores approved node URLs and tokens at
  `~/.hermes/workspace/device_nodes/nodes.json`.
- Pairing implies trust: after approval, Hermes can run shell and GUI actions on
  that worker.
- The worker still blocks obvious destructive shell patterns and dangerous
  system shortcuts.

## Development

The repo root is the Hermes plugin. `worker_dist/` is the distributable client
folder copied to `~/.hermes/device-worker` by:

```bash
hermes device sync-dist
```

Basic local checks:

```bash
python3 -m py_compile $(find . -name '*.py')
bash -n worker_dist/*.sh
```

