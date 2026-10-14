# Claude Deck Dashboard

A self-hosted web dashboard for people who run [Claude Code](https://claude.com/claude-code) on a server (VPS, home lab). It shows your sessions, usage limits, costs and system health in one place, warns you before a limit runs out, and tells you when it resets.

- **Small footprint**: `server.py` plus a dependency-free `index.html`. No build step, no database, no npm.
- **Reads what Claude Code already writes**: session logs in `~/.claude/projects/` and the usage cache in `~/.claude.json`. No API token needed.
- **Phone friendly**: installable as a home-screen web app, Web Push notifications, layout tuned for modern iPhones.
- **Two languages: German and English.** Switch under **Konto → Darstellung → Sprache / Language**. Adding another language takes one file, see [Languages](#languages).

> **Security first.** The optional remote-control features can start sessions, edit Claude settings and run `tmux` commands on the host, and the original setup runs as root. Read [Security](#security) before you expose this to the internet.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Network](#network)
- [Notifications](#notifications)
- [Install as a phone app (iOS and Android)](#install-as-a-phone-app-ios-and-android)
- [Limits and automatic reset](#limits-and-automatic-reset)
- [Languages](#languages)
- [Security](#security)
- [Project layout](#project-layout)
- [Tests](#tests)
- [Known limitations](#known-limitations)

## Features

**Sessions and usage**
- Overview with 5-hour and weekly limits (ring gauges), running sessions with live activity ticker and context size estimate
- History, models, projects, tools, cost estimate (tokens, not money), 26-week activity heatmap, work-time Gantt chart
- Usage history with a forecast ("full in about N hours"), consumption share per session, error patterns
- Chat view per session (messenger style, coloured diffs for edits), filters, Markdown export, session compare
- Pin sessions with a note, copy `claude --resume <id>`
- **Custom widgets** on the overview: Customize → *Widget hinzufügen*. Pick one of 23 sources (limits, costs, tokens, sessions, errors, health, certificate, disk, memory, CPU, services, repositories, mailbox, notes, backups ...), a look (number, ring, line, bars, list), a range and a width, with a live preview. **Drag and drop like on the iPhone:** in *Anpassen* mode the widgets wiggle, grab one by its header bar and drag it, the others move out of the way and the widget snaps into the grid. Widgets and their order are stored on the server (`widgets.json`) and apply on every device

**Network** (see [Network](#network))
- Pages for devices on your home network, NAS storage and disks, router and internet, services and accessories; 13 matching overview widgets
- Works three ways: dashboard at home, dashboard on a server reached through Tailscale, or a small agent at home that pushes data
- 24 source types: SNMP (almost any NAS or router), Synology, TrueNAS, Fritz!Box, UPnP, UniFi, OpenWrt, MikroTik, OPNsense, any Linux NAS over SSH, Pi-hole, AdGuard, Home Assistant, Shelly, Tasmota, UPS (NUT), Immich, web, port and ping checks, certificate expiry, speed test, backup age

**Notifications**
- Warnings at 80 % and 95 % of a limit, a forecast warning, a message when a limit **resets** and when a watched session finishes
- Daily summary at 20:00 (Europe/Berlin), alert after repeated failed logins
- Channels: [ntfy.sh](https://ntfy.sh) and Web Push (RFC 8030 / 8291 / 8292)

**Vault and notes** (optional, needs an Obsidian vault and a graph file written by graphify)
- Interactive graph (drag, zoom, tap), note reader with full-text search, vault tree and health checks
- Create a note from a chat (asks first, never overwrites)

**System and operations**
- CPU, RAM, disk, service overview, certificate expiry, journal errors, provider status
- Local git repositories overview, GitHub issues via `gh` (if installed and logged in)
- Backups of the dashboard and your Claude settings, with partial restore

**Remote control** (every action needs a fresh password and 2FA code and is written to an audit log)
- View and operate `tmux` sessions, start and stop `deck-` sessions and teams
- Edit `settings.json`, permissions and hooks (diff preview and automatic backup), add and remove MCP servers

**Account**
- Login form, rate limiting (5 failures, then a 15-minute lock per IP), "stay signed in" for 30 days
- Change password in the dashboard (scrypt hash), TOTP two-factor with QR code, access log
- Language switch (German, English), light and dark theme, accent colours, Liquid-Glass inspired design, keyboard shortcuts (`?`, `/`, `Ctrl+K`)

## Requirements

- Linux with Python 3.9 or newer (uses `zoneinfo`)
- Python package `cryptography` (only needed for Web Push; the rest is standard library)
- Claude Code installed for the same user that runs the dashboard
- A reverse proxy with HTTPS if you want to reach it from outside (Caddy is shown below)
- Optional: `tmux`, `git`, `gh`

## Installation

A complete walk-through for a fresh Ubuntu or Debian server where Claude Code already runs. Commands assume a user with `sudo`; drop `sudo` if you are root. Every step has a check so you know it worked.

### 1. Check the prerequisites

```bash
python3 --version     # 3.9 or newer
claude --version      # Claude Code must be installed for THIS user
ls ~/.claude/projects # should list at least one project folder
```

If `claude` is missing, install Claude Code first (https://claude.com/claude-code) and start it once so that `~/.claude` exists. Note the full path of the binary, you need it in step 4:

```bash
which claude          # for example /home/you/.local/bin/claude
```

### 2. Install the system packages

```bash
sudo apt update
sudo apt install -y git python3 python3-cryptography tmux
```

`tmux` and `git` are only used by the optional views. `python3-cryptography` is required for Web Push. If you prefer pip: `pip install cryptography`.

### 3. Get the code

```bash
sudo mkdir -p /opt/claude-deck
sudo chown "$USER": /opt/claude-deck
git clone https://github.com/melcher535-cmd/claude-deck-dashboard.git /opt/claude-deck
cd /opt/claude-deck
```

### 4. Adapt the paths to your server

The defaults belong to the original server (user `root`). Open `server.py` and change these lines (use `grep -n` to find them if the line numbers moved):

| Line | Default | Set it to |
|---|---|---|
| `VAULT = ...` (line 31) | `/root/Obsidian-sync` | Your Obsidian vault, or any existing folder if you have none |
| `BACKUP_DIR = ...` (line 589) | `/root/deck-backups` | A folder the service user may write to, for example `/home/you/deck-backups` |
| `claude_bin=...` (line 595) | `/root/.local/bin/claude` | The output of `which claude` |
| `roots=[...]` (line 595) | `["/root"]` | The folders in which remote control may start sessions, for example `["/home/you"]` |
| glob `/root/*backup*` (line 518) | `/root` | Your home folder (only affects the backup list) |

Quick check that nothing personal is left:

```bash
grep -n "/root" server.py
```

### 5. Set the password

```bash
cp .env.example .env
nano .env             # set DECK_PASSWORD
chmod 600 .env
```

Add your public host name as `DECK_DOMAIN=deck.example.com`. Use at least 12 characters. If the value contains `&`, `$` or spaces, wrap it in single quotes: `DECK_PASSWORD='my&pass word'`. Add `DECK_NTFY_TOPIC=<unguessable-name>` now if you want ntfy messages (see [Notifications](#notifications)).

### 6. Test run in the foreground

```bash
set -a; . ./.env; set +a
python3 server.py
```

The process prints nothing when it works and stays in the foreground. In a second terminal check it:

```bash
curl -si http://127.0.0.1:8770/ | head -1     # HTTP/1.0 303 See Other (redirect to the login page)
```

To look at it from your own computer, open an SSH tunnel and browse to `http://127.0.0.1:8770`:

```bash
ssh -L 8770:127.0.0.1:8770 you@your-server
```

The login cookie is marked `Secure`. Chrome and Firefox accept it on `127.0.0.1`; Safari may not, so finish steps 7 to 9 and use HTTPS for daily use. Stop the test with `Ctrl+C`.

### 7. Run it as a service

Create `/etc/systemd/system/claude-deck.service` (replace `you` with the user from step 1):

```ini
[Unit]
Description=Claude Deck Dashboard
After=network.target

[Service]
User=you
WorkingDirectory=/opt/claude-deck
EnvironmentFile=/opt/claude-deck/.env
ExecStart=/usr/bin/python3 /opt/claude-deck/server.py
Restart=always
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now claude-deck
systemctl is-active claude-deck               # active
journalctl -u claude-deck -n 20 --no-pager    # look for errors
```

The service must run as the user whose `~/.claude` it should read; that is why `User=` is set. It restarts on crashes and at boot.

### 8. Point a domain at the server and add HTTPS

1. At your DNS provider create an `A` record, for example `deck.example.com`, with your server's public IP.
2. Install Caddy by following https://caddyserver.com/docs/install (official package repository for Debian and Ubuntu).
3. Put this in `/etc/caddy/Caddyfile` (same host name as `DECK_DOMAIN` in step 5):

```caddyfile
deck.example.com {
	reverse_proxy 127.0.0.1:8770
}
```

```bash
sudo systemctl reload caddy
```

Caddy requests and renews the Let's Encrypt certificate by itself. This needs ports 80 and 443 to be reachable from the internet.

### 9. Close everything else with the firewall

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

Allow SSH **before** you enable the firewall, or you lock yourself out. Port 8770 must stay closed; the server only listens on `127.0.0.1` anyway.

### 10. First login

1. Open `https://deck.example.com` and sign in with `DECK_PASSWORD`.
2. Go to **Konto** and set your own password (at least 12 characters). From then on the hash in `credentials.json` is used and `DECK_PASSWORD` is only a fallback.
3. In **Konto** enable two-factor: add the shown key (or scan the QR code) in an authenticator app and confirm with a code.
4. Optional: **Konto → Benachrichtigungen** for Web Push. On iPhone first add the page to the home screen (Share → Add to Home Screen) and sign in again inside that app.

### Update

```bash
cd /opt/claude-deck
git pull
sudo systemctl restart claude-deck
```

Your `.env`, `credentials.json` and other data files are not in git, so an update never touches them. If you edited `server.py` in step 4, `git pull` may report a conflict; run `git stash`, `git pull`, `git stash pop` and fix it.

### Uninstall

```bash
sudo systemctl disable --now claude-deck
sudo rm /etc/systemd/system/claude-deck.service
sudo rm -r /opt/claude-deck
```

Remove the host from `/etc/caddy/Caddyfile` and reload Caddy.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `DECK_PASSWORD fehlt` on start | `.env` is missing or not loaded. Check `EnvironmentFile=` in the service, or run `set -a; . ./.env; set +a` before a manual start. |
| Service is `failed` | `journalctl -u claude-deck -n 50 --no-pager` shows the Python error. |
| Login page loads but sign-in does nothing | The browser rejected the `Secure` cookie on plain HTTP. Use the HTTPS address. |
| Sessions list is empty | The service user differs from the Claude Code user, or `~/.claude/projects` is empty. |
| Limits show `0 %` or stale values | `~/.claude.json` has no `cachedUsageUtilization` yet. Run Claude Code and open `/usage` once. |
| Web Push switch does nothing | You need HTTPS, `DECK_DOMAIN` set to your host name, and on iOS the home-screen app. |
| `ModuleNotFoundError: cryptography` | `sudo apt install python3-cryptography` (or `pip install cryptography`). |
| Caddy cannot get a certificate | DNS record missing or wrong, or ports 80 and 443 are blocked. `journalctl -u caddy -n 50 --no-pager`. |
| Locked out after failed logins | Wait 15 minutes, or restart the service (`systemctl restart claude-deck`). |

## Configuration

### Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `DECK_PASSWORD` | yes, until you set one in the UI | Initial password. After you change it under **Konto**, the scrypt hash in `credentials.json` wins. Quote values that contain `&` or other shell characters: `DECK_PASSWORD='p&ss'`. |
| `DECK_DOMAIN` | yes for HTTPS and Web Push | Public host name, for example `deck.example.com`. Used as the Web Push subject and for the certificate check. Default: `localhost`. |
| `DECK_NTFY_TOPIC` | no | Enables ntfy.sh messages. Anyone who knows the topic can read it, so choose an unguessable name. |

### Constants in `server.py`

The paths and the domain are set for the original server. Adjust them before first use:

| Constant | Default | Meaning |
|---|---|---|
| `VAULT` | `/root/Obsidian-sync` | Obsidian vault (optional). |
| `BACKUP_DIR` | `/root/deck-backups` | Where backups go (last 10 are kept). |
| `PORT`, `HOST` | `8770`, `127.0.0.1` | Listen address. Keep `HOST` on loopback. |
| `roots`, `claude_bin` (in the `Features(...)` call) | `/root`, `/root/.local/bin/claude` | Where sessions may be started and which `claude` binary is used. |

### Files the dashboard creates

All of these hold private data, stay in the project folder and are listed in `.gitignore`. Do not commit them.

| File | Content |
|---|---|
| `.env` | Initial password, ntfy topic |
| `credentials.json` | Password hash and TOTP secret (mode 600) |
| `push-keys.json`, `push-subs.json` | VAPID keys and device subscriptions |
| `access-log.jsonl`, `audit.jsonl` | Login events, remote-control actions |
| `usage-history.jsonl` | One data point every 5 minutes at most |
| `pins.json`, `watch.json`, `widgets.json` | Pinned sessions, session alarms, overview layout and custom widgets |

## Network

Under **Netzwerk** the dashboard shows the devices on your home network, NAS systems with free space, disks and RAID state, your router and internet line, and everything around them (UPS, smart sockets, DNS filter, photo server, certificates, backups). All of it also exists as overview widgets (*Customize → Widget hinzufügen → Netzwerk: ...*).

| Page | Shows |
|---|---|
| Übersicht | Devices online, NAS storage, internet rate, power use, open notices, new devices, one card per NAS |
| Geräte | Every device with status, IP, MAC, vendor guess, LAN or Wi-Fi and signal. Rename, mark as known or important, wake up (Wake-on-LAN), search now |
| Speicher & Platten | Volumes as bars with a **"full in about N days"** forecast, disk temperature and SMART state, RAID, 7-day usage chart |
| Router & Internet | Connection state, public IP, current rate and line speed, 24-hour rate chart, ad-blocker statistics, speed test |
| Dienste & Zubehör | Web and port checks, UPS, power use of sockets, certificate expiry, backup age, Immich and Home Assistant values |
| Einstellungen | Operating mode, sources, alarms, agent access |

### Three ways to run it

Choose under **Netzwerk → Einstellungen → Betriebsart**. You can switch later; sources and history stay.

1. **Im Heimnetz.** The dashboard runs on a device in your home network and queries everything directly. Leave *Netze* empty, the networks of the machine are detected.
2. **Über Tailscale.** The dashboard runs on a server on the internet. Tailscale connects it to your home network, no port is opened. Install Tailscale on both sides first (https://tailscale.com/download), then:
   ```bash
   # on the server running the dashboard
   sudo tailscale up --accept-routes
   # at home, on a device that is always on (NAS, Raspberry Pi, mini PC)
   sudo tailscale up --advertise-routes=192.168.1.0/24
   echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-tailscale.conf && sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
   ```
   Approve the route in the Tailscale admin console (*Edit route settings*), then enter your home network under *Netze*. Wake-on-LAN, UPnP queries and the speed test only work inside the home network, so add the agent as well if you need them.
3. **Mit Agent.** A small program at home (`netz_agent.py`, standard library only) queries the devices and sends the result to the dashboard over HTTPS. It only calls out, so nothing at home has to be reachable. Under *Einstellungen → Agent* create the access, download `netz-agent.zip` and save the generated configuration as `netz-agent.json` next to the agent:
   ```bash
   python3 netz_agent.py --trocken      # test: print what would be sent
   python3 netz_agent.py                # run permanently (or as a systemd service)
   python3 netz_agent.py --entdecken    # list devices in the network with a type suggestion
   ```
   The dashboard queues commands for the agent (Wake-on-LAN, "search now") and hands them out on its next report. Agent pushes are accepted in every mode as soon as an access exists.

### What it can read

Any NAS or router works through at least one of the generic paths: **SNMP** (v1 and v2c), **SSH** (a fixed read-only script that reads `/proc`, `df`, `smartctl`, `mdstat` and `zpool`; covers UGREEN, Unraid, OpenMediaVault, TrueNAS SCALE, Proxmox, Raspberry Pi, OpenWrt and any other Linux box), or the agent running on the device itself (type *Dieser Rechner*). On top of that there are vendor types:

| Group | Types |
|---|---|
| NAS and routers | SNMP (with Synology and QNAP extras: disk temperature, RAID), SSH, this computer, Synology DSM, TrueNAS, Fritz!Box (TR-064), router over UPnP, UniFi, OpenWrt, MikroTik RouterOS 7, OPNsense |
| Services | Pi-hole (v5 and v6), AdGuard Home, Immich, web check, port check, ping, certificate expiry, speed test, backup age |
| Smart home | Home Assistant, Shelly (Gen 1 and 2+), Tasmota, UPS over NUT |

Each type has a form with a short setup hint, and a **Test** button.

**Verification status.** The SNMP client was checked against a real `snmpd`. The vendor APIs are implemented from their documented interfaces and tested against local fake servers, not against real devices of every brand; if a field is missing on your model, the other values still work. QNAP and UGREEN have no official REST API here; use SNMP, SSH or the agent there.

### Alarms and history

Messages go out through the channels you set up under Konto (ntfy, Web Push) once per event, plus one message when the problem is gone: a **new device** (the first scan counts as known), a **volume above 90 %**, a **hot disk**, a **SMART error**, a **degraded RAID**, a **device you marked important** going offline, a **source that stops answering**, **internet down**, **UPS on battery**, a **certificate close to expiry**, a **missing backup** and a **silent agent**. Thresholds are in the settings. Every five minutes a data point is stored (`netz-verlauf.jsonl`, 30 days); the storage forecast fits the growth of the last seven days.

### Safety

- The dashboard only queries addresses in private ranges, loopback and the Tailscale range (`100.64.0.0/10`), never public addresses or the link-local cloud metadata address. Only the certificate check and the speed test may reach public hosts.
- Changing sources, testing a source, searching the network and creating agent access all need the **step-up** (password and code) and are written to the audit log. Saving shows a diff first.
- Passwords and keys of sources are stored in `netzwerk.json` (file mode 600) and are never sent back to the browser (shown as `********`). Use read-only accounts on your devices wherever the device allows it. The generated agent configuration contains these secrets: keep it only on the agent machine.
- The agent token is stored only as a hash; creating a new one invalidates the old. Ten wrong tokens lock the sender for 15 minutes.
- Data from an agent is cut down to a fixed schema before it is stored or shown.

## Notifications

**ntfy.sh**: set `DECK_NTFY_TOPIC` in `.env`, install the ntfy app on your phone and subscribe to the same topic. Restart the service.

**Web Push**: open the dashboard, go to **Konto → Benachrichtigungen** and switch it on. On iOS this works only from the home-screen web app (Share → Add to Home Screen) on iOS 16.4 or newer. The home-screen app has its own cookies, so sign in again there.

Messages you can get:

| Event | When |
|---|---|
| Limit warning | A limit reaches 80 % or 95 % |
| Forecast | At the current pace a limit is full in less than 6 hours and before its reset |
| **Limit reset** | The reset time has passed, for example "Session-Limit (5 Std) zurückgesetzt (war 96 %)" |
| Session finished | You set an alarm on a running session |
| Daily report | 20:00 Europe/Berlin (ntfy only) |
| IP locked | Too many failed logins |

## Install as a phone app (iOS and Android)

The dashboard is a progressive web app (PWA): a web manifest, home-screen icons and a service worker are included. Installed, it opens full screen without browser bars, has its own icon, and can receive push notifications. Nothing to build and no app store needed.

**Before you start**
- The dashboard must be reachable over **HTTPS** with a valid certificate (see [Installation](#installation) steps 8 and 9). Over plain HTTP the install option and Web Push do not work.
- `DECK_DOMAIN` in `.env` must be your host name (it is used for Web Push).
- Open the address in the phone's own browser and sign in once.

### iPhone and iPad (iOS 16.4 or newer)

1. Open `https://deck.example.com` in **Safari**. Other iOS browsers can add it too (iOS 16.4+), but Safari is the safest choice.
2. Tap the **Share** button (square with an arrow).
3. Scroll down and tap **Add to Home Screen**. In newer iOS versions keep *Open as Web App* switched on.
4. Tap **Add**. The Claude Deck icon appears on the home screen.
5. Open the app from the home screen and **sign in again**: the home-screen app has its own cookies and does not share the Safari login. Switch on *Stay signed in* to avoid typing the password often.
6. For push messages: in the app go to **Konto → Benachrichtigungen** and switch it on, then allow notifications in the iOS prompt.

Tips and limits
- Push on iOS works **only** from the home-screen app, never from a normal Safari tab.
- The icon is cached by iOS. If you change the icon, delete the app from the home screen and add it again.
- If the layout looks zoomed out, close the app completely and reopen it.

### Android (Chrome, Edge, Samsung Internet)

1. Open `https://deck.example.com` in **Chrome** and sign in.
2. Tap the **⋮ menu** and choose **Install app** (on some versions: **Add to Home screen**). Confirm with **Install**.
3. Open the app from the launcher or the home screen and sign in again if asked. Switch on *Stay signed in*.
4. For push messages: in the app go to **Konto → Benachrichtigungen**, switch it on and allow notifications.

Tips and limits
- Samsung Internet and Edge offer the same via the menu (*Add page to* → *Home screen*, or *Install*).
- On Android push also works in a normal Chrome tab, but the installed app is more reliable.
- Battery optimisation can delay push. If messages arrive late, set the app to *Unrestricted* in the Android battery settings.

### Check that it works

- Open the app: there should be no browser address bar.
- **Konto → Benachrichtigungen → Testnachricht**: a notification should arrive within seconds.
- Nothing arrives? Check that the domain uses HTTPS, `DECK_DOMAIN` is correct, notifications are allowed for the app in the phone settings, and (iOS) that you opened the home-screen app, not Safari.

### Remove the app

- **iOS:** touch and hold the icon → *Remove App* → *Delete App*.
- **Android:** touch and hold the icon → *Uninstall* (or *Remove*).

The data stays on your server; only the shortcut and the local login are removed.

## Limits and automatic reset

Claude Code keeps the numbers shown by `/usage` in `~/.claude.json` (`cachedUsageUtilization`, percentages only). The dashboard reads that cache; it never calls an API.

Claude Code refreshes the cache only while it is running. So that a finished 5-hour or weekly window does not show a stale value, the server checks `resets_at` on every read: once the time has passed, the limit is displayed as 0 % and one reset notification is sent. As soon as Claude Code writes fresh data, the real values replace this.

## Languages

The source texts are German. The browser translates the interface at runtime with a dictionary, so no page needs a second copy.

- **Switch:** Konto → Darstellung → *Sprache / Language*. The choice is stored in the browser (`localStorage`, key `deck-sprache`) and the page reloads. Without a choice the browser language is used if a pack exists for it, otherwise German.
- **What is translated:** menus, titles, buttons, hints, tooltips, dialogs and messages of the interface. Dates and times follow the language. The login page is translated as well.
- **What is not translated:** your own data (session titles, notes, file names, commands, chat text), and messages the server sends outside the page (ntfy and Web Push notifications, the daily report). Those stay German.

### Add a language

1. Copy `i18n-en.js` to `i18n-<code>.js` (for example `i18n-fr.js`) and change `.en` to `.fr`, `name` and `locale`.
2. Translate the values. Keep the keys (the German source text) exactly as they are. `{0}`, `{1}` stand for numbers and names that are filled in and translated on their own, for example `"Reset in {0}": "Resets in {0}"`. A translation may reorder them.
3. Load the file before `i18n.js` in `index.html` and `login.html`: `<script src="/i18n-fr.js"></script>`.
4. Allow the file without sign-in for the login page: add `"/i18n-fr.js": (f"{HERE}/i18n-fr.js", "text/javascript")` to `PUBLIC` in `server.py`.
5. Run `node test_i18n.js`. It checks that every translation keeps the placeholders of its key.

Texts without an entry stay German, so a pack can grow step by step.

## Security

Treat this like an admin panel for your server.

- The server listens on loopback and expects a reverse proxy with HTTPS. Cookies are `HttpOnly`, `Secure` and `SameSite=Strict`.
- All write endpoints require a session and the header `X-Deck: 1` (protection against cross-site requests).
- Remote-control actions need the **step-up**: password and TOTP code again, valid for 5 minutes, with its own lockout. Each action shows a preview first and is logged in `audit.jsonl`. Without the step-up the server answers `403`.
- Notes are read with path checks (no `..`, no symlinks out of the vault, no `.obsidian`).
- Secrets are masked in the configuration view. The shortcut API key is stored only as a hash.
- **Enable 2FA** under Konto and use a long password. Anyone who gets past the login and the step-up controls the host.
- The original service runs as root. Prefer a dedicated user if you can; remote control then reaches only what that user may do.
- Never commit `.env`, `credentials.json`, `push-keys.json`, `push-subs.json`, `netzwerk.json` or `netz-agent.json`. If one of them ever leaks, change the password, delete the file and let the dashboard create a new one.

Found a vulnerability? Please open a private security advisory on GitHub instead of a public issue.

## Project layout

| File | Purpose |
|---|---|
| `server.py` | HTTP server, routes, usage sampler, alerts |
| `auth.py` | Login, sessions, rate limit, TOTP, step-up |
| `features.py`, `analytics.py`, `insights.py`, `upkeep.py` | Live monitor, statistics, rankings, backups and health |
| `push.py`, `sw.js` | Web Push (VAPID, payload encryption) and service worker |
| `notes.py` | Safe note reading, search, mobile layout for the graph page |
| `viewer.py` | Read-only views: tmux, git, issues, provider status, config |
| `control.py`, `control.js` | Remote control with step-up and audit |
| `storage.py` | Small JSON file helper |
| `index.html`, `features.js`, `login.html` | The whole front end, no framework |
| `widgets.py`, `widgets.js` | Overview layout storage and validation; custom widget sources, views and the builder dialog |
| `netz.py` | Network module: configuration, three operating modes, polling, device list, alarms, history and forecast, agent push, Wake-on-LAN |
| `netz_typen.py`, `netz_geraete.py`, `netz_dienste.py`, `netz_linux.py`, `netz_snmp.py` | Source types (registry, NAS and routers, services, Linux over SSH or locally, SNMP client) |
| `netz_scan.py`, `netz_http.py` | Device discovery, vendor and type guess, Wake-on-LAN, Tailscale status; HTTP helper with the private-address check |
| `netz_agent.py` | The agent for the home network |
| `netz.js`, `netz-einstellungen.js` | Network pages, widget sources, settings, source dialog |
| `i18n.js`, `i18n-en.js` | Language switch: runtime translation engine and the English dictionary |
| `qrcode.js` | [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) 1.4.4 (MIT), draws the 2FA QR code in the browser |
| `manifest.webmanifest`, `icon-*.png`, `favicon-*.png` | Home-screen app metadata and icons |
| `test_*.py`, `test_i18n.js` | Tests |

## Tests

The tests use plain `assert` statements and need no pytest. A tiny runner:

The language engine has its own test: `node test_i18n.js`.

```bash
python3 - <<'EOF'
import importlib, sys, traceback
sys.path.insert(0, ".")
failed = 0
for name in ("test_server", "test_features", "test_viewer", "test_control", "test_widgets", "test_netz"):
    module = importlib.import_module(name)
    for test in dir(module):
        if test.startswith("test_"):
            try:
                getattr(module, test)()
            except Exception:
                failed += 1
                print("FAIL", name, test)
                traceback.print_exc()
print("failed:", failed)
EOF
```

## Known limitations

- Network: SNMP v3 is not supported (v1 and v2c only). Shelly Gen 2+ with device protection (digest login) cannot be read. Wake-on-LAN needs the dashboard or an agent in the same network. The speed test measures from the machine running the dashboard or agent. Names of devices come from the router, then from reverse DNS; phones with a private Wi-Fi address show no vendor.

- Costs are token based estimates, not money. The context bar is relative to 1 million tokens because the real window size per model is not known.
- Limit values are only as fresh as the Claude Code cache.
- Only the interface is translated; server messages such as push notifications stay German (see [Languages](#languages)).
- The layout is checked with Chrome screenshots, not on every device.
- The graph view needs a graph file written by graphify; without a vault the vault pages stay empty.
