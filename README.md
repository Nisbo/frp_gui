# FRP Client Web GUI

Small Flask based web UI for managing an existing `frpc.ini` file.

Current app version:

```text
0.1.0-dev
```

The first version intentionally keeps FRP's current INI setup working. A later
step can migrate the config to TOML, which is the recommended FRP format for
newer versions.

## Features

- Edit `[common]` server settings
- Add, edit and delete proxy entries
- Validate basic fields before writing
- Create timestamped backups before every save
- Create manual backups with comments
- List, restore and delete backups
- Optional `frpc verify -c ...`
- Optional `systemctl restart frpc`
- Password login for production use
- No database and no compile step

## Local Development With pip

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open:

```text
http://127.0.0.1:8844
```

By default the app uses:

```text
sample/frpc.ini
```

## Debian Runtime Without pip

For a server install, the preferred simple path is apt packages:

```bash
apt update
apt install python3 python3-flask gunicorn nginx
```

Suggested install path:

```text
/opt/frp-gui
```

Copy this project there and run it with gunicorn:

```bash
cd /opt/frp-gui
gunicorn -w 2 -b 127.0.0.1:8844 'frp_gui:create_app()'
```

## Run Against The Real FRP Config

On your Debian server you can point the app at your real config:

```bash
export FRP_CONFIG_PATH=/opt/frp/frpc.ini
export FRPC_BINARY=/opt/frp/frpc
export FRP_GUI_PASSWORD='change-this-password'
export FRP_GUI_SECRET='change-this-long-random-secret'
python run.py
```

Restarting the FRP service from the UI is disabled by default. Enable it only
when the app runs with the right permissions and is protected by login and
Nginx:

```bash
export FRP_GUI_ALLOW_SYSTEMCTL=1
export FRPC_SERVICE=frpc
```

## Suggested Debian Packages

```bash
apt update
apt install python3 python3-venv nginx
```

## Example systemd Service

Create `/etc/systemd/system/frp-gui.service`:

```ini
[Unit]
Description=FRP Client Web GUI
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/frp-gui
Environment=FRP_CONFIG_PATH=/opt/frp/frpc.ini
Environment=FRPC_BINARY=/opt/frp/frpc
Environment=FRPC_SERVICE=frpc
Environment=FRP_GUI_ALLOW_SYSTEMCTL=1
Environment=FRP_GUI_PASSWORD=change-this-password
Environment=FRP_GUI_SECRET=change-this-long-random-secret
Environment=FRP_GUI_HOST=127.0.0.1
Environment=FRP_GUI_PORT=8844
Environment=FRP_GUI_PUBLIC_PORT=8844
ExecStart=/usr/bin/gunicorn -w 2 -b 127.0.0.1:8844 'frp_gui:create_app()'
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
```

`User=root` is the simplest option for a first private setup because the app
must write `/opt/frp/frpc.ini` and restart `frpc`. For a public freeware
release, a better model is a restricted user plus a small sudoers rule for only
`systemctl restart frpc`.

Service actions need elevated privileges. The GUI supports `start`, `stop`,
`restart`, `enable` and `disable` only when `FRP_GUI_ALLOW_SYSTEMCTL=1` and a
service name is configured. For a hardened install, run the app as a dedicated
user and allow only these commands through sudoers instead of giving broad root
access:

```text
frpgui ALL=NOPASSWD: /bin/systemctl start frpc, /bin/systemctl stop frpc, /bin/systemctl restart frpc, /bin/systemctl enable frpc, /bin/systemctl disable frpc
```

The app does not call sudo yet; this is the intended production direction for
the installer.

## Example Nginx Reverse Proxy

Default network layout:

```text
Browser -> nginx public port 8844 -> FRP Gui backend 127.0.0.1:8844
```

The backend should normally stay bound to `127.0.0.1`. Nginx is the public
entry point and can listen on `8844`, `80`, `443`, or another port you choose.
Port `8844` is the default to avoid taking over port `80` on servers that
already host other projects.

```nginx
server {
    listen 8844;
    server_name frp-gui.example.org;

    location / {
        proxy_pass http://127.0.0.1:8844;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

For real use, put HTTPS in front of it and do not expose the GUI publicly
without additional protection.

## Later TOML Migration

FRP supports TOML/YAML/JSON since v0.52.0 and marks INI as deprecated. A later
version of this project should:

- read existing INI
- show a conversion preview
- write `frpc.toml`
- run `frpc verify -c /opt/frp/frpc.toml`
- update the `frpc.service` command from `frpc.ini` to `frpc.toml`

## FRP Autodetect Prototype

The first detection helper is here:

```bash
python3 scripts/detect_frp.py
```

It checks common `frpc` systemd service names, extracts `-c /path/to/config`
from the unit or process list, and prints JSON that the installer or GUI can
use later.

## Updates

The app footer links to:

```text
https://github.com/Nisbo/frp_gui
```

Settings includes a manual update check against the latest GitHub release. The
Updates tab supports three flows:

- release check against the latest GitHub release
- git update for installations that are real git checkouts
- ZIP upload for release archives or manually prepared update packages

Git update runs:

```text
git fetch --tags --prune origin
git pull --ff-only
```

For git updates, install FRP Gui as a clone instead of copying loose files:

```bash
apt install -y git
git clone https://github.com/Nisbo/frp_gui.git /opt/frp-gui
cd /opt/frp-gui
```

An existing local directory can also be connected to GitHub after the first
commit:

```bash
git remote add origin https://github.com/Nisbo/frp_gui.git
git branch -M main
git push -u origin main
```

ZIP updates must contain the same project structure as the repository. The files
may be directly in the ZIP root or inside one top-level directory, as GitHub
source archives usually do:

```text
frp_gui/
run.py
requirements.txt
scripts/
sample/
README.md
```

To prepare a ZIP manually from a working tree:

```bash
zip -r frp-gui-update.zip frp_gui run.py requirements.txt scripts sample README.md .gitignore
```

Before git or ZIP updates, FRP Gui creates an application-file backup below:

```text
data/app-updates/backups/
```

After an update, restart the FRP Gui service so the running Python process loads
the new code.
