# Hermes Device Worker

Lightweight LAN worker for a central Hermes VM.

## Quick Start

On a new Mac:

```bash
scp -r hermes:/home/allen/.hermes/device-worker ~/device-worker
cd ~/device-worker
./install.sh --install-cua
./check_permissions.sh
./run.sh --display-name macbook-allen --host 0.0.0.0 --port 18888
```

When the worker prints a token, approve it on the Hermes VM:

```bash
hermes device node approve macbook-allen ws://<worker-lan-ip>:18888 <token>
hermes device node ping macbook-allen
hermes device node capabilities macbook-allen
```

If you do not know the worker LAN IP, run this on the worker Mac:

```bash
ipconfig getifaddr en0 || ipconfig getifaddr en1
```

## macOS GUI Control

Shell access works without GUI permissions. Desktop/app control requires:

- `cua-driver`
- Accessibility permission
- Screen Recording permission

The helper opens the right panes and triggers likely prompts:

```bash
./check_permissions.sh
```

macOS still requires you to approve manually. Grant permissions to the terminal
or launcher running the worker, and to Python/cua-driver if macOS shows them.
Restart the worker after changing permissions.

Check readiness from Hermes:

```bash
hermes device node capabilities macbook-allen
```

Expected:

```text
"computer_use": true
```

## Launchd

Install as a background service:

```bash
./install_launchd.sh macbook-allen
launchctl kickstart -k gui/$UID/com.hermes.device-worker
```

Logs:

```bash
tail -f ~/Library/Logs/hermes-device-worker.log
```
