# FRP Gui

FRP Gui is a small Flask web interface for managing an existing FRP client
configuration file, usually `frpc.ini`.

Current app version:

```text
0.1.0
```

The first version keeps INI support because many existing FRP installations
still use `frpc.ini`. TOML migration is planned for a later version.

## What Is Gunicorn?

Flask is the Python web framework used by FRP Gui. Flask includes a development
server, but that development server should not be used as a real background
service on Debian.

Gunicorn is the small production web server that starts the Flask app and keeps
it running behind nginx.

In this setup:

```text
Browser -> nginx :8844 -> gunicorn/FRP Gui 127.0.0.1:8844 -> frpc.ini
```

## Quick Install On Debian 12

These steps assume:

- FRP is already installed.
- `frpc` is already running as a systemd service.
- Your active config is `/opt/frp/frpc.ini`.
- You want to open the GUI on port `8844`.

Run the commands as `root`.

### 1. Install Packages

```bash
apt update
apt install -y git python3 python3-flask gunicorn nginx
```

### 2. Download FRP Gui From GitHub

```bash
cd /opt
git clone https://github.com/Nisbo/frp_gui.git frp-gui
cd /opt/frp-gui
```

Using `git clone` is recommended because the GUI can later update itself with
`git pull`.

Alternative without git updates:

```bash
apt install -y wget unzip
cd /opt
wget -O frp-gui.zip https://github.com/Nisbo/frp_gui/archive/refs/heads/main.zip
unzip frp-gui.zip
mv frp_gui-main frp-gui
cd /opt/frp-gui
```

Use the git method if possible. The ZIP method works, but the `Update from git`
button will not be available.

### 3. Create A Secret Key And Password

Choose your own password:

```bash
openssl rand -hex 32
```

Copy the generated value. It will be used as `FRP_GUI_SECRET`.

### 4. Create The FRP Gui Service

Create `/etc/systemd/system/frp-gui.service`:

```ini
[Unit]
Description=FRP Gui
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

Replace:

```text
change-this-password
change-this-long-random-secret
```

For a first private setup, `User=root` is the simplest option because the GUI
must write the FRP config and restart `frpc`. A later hardened setup should use
a dedicated user plus restricted sudo rules.

### 5. Start FRP Gui

```bash
systemctl daemon-reload
systemctl enable --now frp-gui
systemctl status frp-gui
```

### 6. Create The nginx Site

Create `/etc/nginx/sites-available/frp-gui.conf`:

```nginx
server {
    listen 8844;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8844;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable the site:

```bash
ln -s /etc/nginx/sites-available/frp-gui.conf /etc/nginx/sites-enabled/frp-gui.conf
nginx -t
systemctl reload nginx
```

### 7. Open The GUI

Open:

```text
http://YOUR-SERVER-IP:8844
```

Log in with the password from `FRP_GUI_PASSWORD`.

After changing FRP config entries in the GUI, restart the FRP client from the
GUI or with:

```bash
systemctl restart frpc
```

## Updating FRP Gui

If you installed with `git clone`, update from the GUI:

```text
Settings -> Updates -> Update from git
```

Or update from the shell:

```bash
cd /opt/frp-gui
git pull --ff-only
systemctl restart frp-gui
```

The GUI also supports ZIP uploads in:

```text
Settings -> Updates -> Upload ZIP update
```

ZIP files must contain the same structure as the repository:

```text
frp_gui/
run.py
requirements.txt
scripts/
sample/
README.md
```

FRP Gui creates application backups before git and ZIP updates:

```text
/opt/frp-gui/data/app-updates/backups/
```

## Manual Configuration Reference

These environment variables are used by the systemd service:

```text
FRP_CONFIG_PATH=/opt/frp/frpc.ini
FRPC_BINARY=/opt/frp/frpc
FRPC_SERVICE=frpc
FRP_GUI_ALLOW_SYSTEMCTL=1
FRP_GUI_PASSWORD=change-this-password
FRP_GUI_SECRET=change-this-long-random-secret
FRP_GUI_HOST=127.0.0.1
FRP_GUI_PORT=8844
FRP_GUI_PUBLIC_PORT=8844
```

Notes:

- `FRP_CONFIG_PATH` is the config file edited by the GUI.
- `FRPC_BINARY` is used for future FRP validation commands.
- `FRPC_SERVICE` is the systemd service controlled by the GUI.
- `FRP_GUI_ALLOW_SYSTEMCTL=1` enables start, stop, restart, enable and disable.
- `FRP_GUI_HOST=127.0.0.1` keeps the backend private behind nginx.
- `FRP_GUI_PUBLIC_PORT=8844` is the port shown in generated nginx config.

## Features

- Edit `[common]` server settings.
- Add, edit, copy, disable and delete proxy entries.
- Sort proxy entries by name, status, IP and domain.
- Create automatic backups before config writes.
- Create manual config backups with comments.
- Preview, restore and delete backups.
- Start, stop, restart, enable and disable the configured systemd service.
- Check GitHub releases.
- Update by git or ZIP upload.
- Password login.
- Dark mode.
- No database.
- No compile step.

## Local Development

For development on your workstation, use a virtual environment:

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

By default the development app uses:

```text
sample/frpc.ini
```

## FRP Autodetect Prototype

There is an early helper script:

```bash
python3 scripts/detect_frp.py
```

It checks common `frpc` systemd service names, extracts `-c /path/to/config`
from the unit or process list, and prints JSON for a future installer flow.

## Planned TOML Migration

FRP supports TOML/YAML/JSON since v0.52.0 and marks INI as deprecated. A later
version of FRP Gui should:

- read existing INI
- show a conversion preview
- write `frpc.toml`
- run `frpc verify -c /opt/frp/frpc.toml`
- update the `frpc.service` command from `frpc.ini` to `frpc.toml`

## Security Notes

Do not expose FRP Gui without a login password. For public access, put HTTPS in
front of nginx.

The first simple install uses `User=root`. This is convenient for private use
because it can edit `/opt/frp/frpc.ini` and restart `frpc`. A hardened install
should run as a dedicated user and allow only the required systemctl commands.
