# FRP Gui

FRP Gui is a small web interface for managing an existing FRP client config.
TOML is the editable format; existing INI configs can be migrated in the GUI.

Current app version:

```text
0.1.24
```

## Quick Install On Debian 12

These steps assume FRP is already installed and `frpc` already runs as a
systemd service.

Run the commands as `root`.

### 1. Install git

```bash
apt update
apt install -y git
```

### 2. Download FRP Gui

```bash
cd /opt
git clone https://github.com/Nisbo/frp_gui.git frp-gui
cd /opt/frp-gui
```

### 3. Run The Installer

```bash
./scripts/install_debian.sh
```

The installer does the rest:

- installs Python, Flask, Gunicorn and nginx
- detects common FRP client settings
- creates `/etc/frp-gui.env`
- creates the `frp-gui` systemd service
- creates and enables the nginx config
- starts FRP Gui
- prints the login password

### 4. Open The GUI

After the installer finishes, open:

```text
http://YOUR-SERVER-IP:8844
```

Log in with the password printed by the installer.

To change the login password later, open:

```text
Settings -> Security
```

The GUI updates `FRP_GUI_PASSWORD` in `/etc/frp-gui.env` and asks you to sign
in again.

## What Is Gunicorn?

FRP Gui is written with Flask, a Python web framework. Flask has a built-in
development server, but that is not meant to run as a real Linux service.

Gunicorn is the small production server that starts FRP Gui in the background.
nginx accepts the browser connection and forwards it to Gunicorn.

The default setup looks like this:

```text
Browser -> nginx :8844 -> Gunicorn/FRP Gui 127.0.0.1:8845 -> frpc.toml
```

## What Is FRP_GUI_SECRET?

`FRP_GUI_SECRET` is not your login password.

It is a random internal key used by Flask to protect browser sessions and form
security tokens. The installer creates it automatically and stores it in:

```text
/etc/frp-gui.env
```

Normal users do not need to edit it.

## Updating FRP Gui

FRP Gui uses GitHub releases as the official update channel.

Recommended release workflow:

```text
1. Check for updates in Settings -> Updates
2. Install the latest release directly from the GUI
3. Restart FRP Gui from the same page
```

The release check compares your installed version with the latest GitHub
release tag, for example `0.1.24`. When updates are available, the GUI shows
release notes for every official release newer than your installed version.

If the server cannot download the release directly, use the manual fallback:
download the official ZIP from the GitHub release page and upload it in
`Settings -> Updates -> Upload release ZIP`.

Advanced git updates are still available when FRP Gui was installed with
`git clone`:

```text
Settings -> Updates -> Update from git
```

This fetches `origin` and moves the local checkout to the configured branch,
normally `origin/main`. A backup is created first. Use it only for testing or
development systems, because `main` can contain changes that are newer than
the latest official release.

After any update, restart FRP Gui:

```bash
systemctl restart frp-gui
```

You can also update from the shell:

```bash
cd /opt/frp-gui
git pull --ff-only
systemctl restart frp-gui
```

## Alternative Download Without git Updates

Use this only if you do not want git-based updates:

```bash
apt update
apt install -y wget unzip
cd /opt
wget -O frp-gui.zip https://github.com/Nisbo/frp_gui/archive/refs/heads/main.zip
unzip frp-gui.zip
mv frp_gui-main frp-gui
cd /opt/frp-gui
./scripts/install_debian.sh
```

With this method, the advanced `Update from git` button will not be available.
Release ZIP updates can still be uploaded in:

```text
Settings -> Updates -> Upload release ZIP
```

## Manual Configuration

Most users should use the installer. This section is only for troubleshooting
or custom setups.

The installer writes this file:

```text
/etc/frp-gui.env
```

Example:

```text
FRP_CONFIG_PATH=/opt/frp/frpc.ini
FRPC_BINARY=/opt/frp/frpc
FRPC_SERVICE=frpc
FRP_GUI_ALLOW_SYSTEMCTL=1
FRP_GUI_PASSWORD=generated-password
FRP_GUI_SECRET=generated-secret
FRP_GUI_SERVICE=frp-gui
FRP_GUI_HOST=127.0.0.1
FRP_GUI_PORT=8845
FRP_GUI_PUBLIC_PORT=8844
```

Important values:

- `FRP_CONFIG_PATH`: config file edited by the GUI
- `FRPC_BINARY`: path to the `frpc` binary
- `FRPC_SERVICE`: systemd service controlled by the GUI
- `FRP_GUI_PASSWORD`: login password
- `FRP_GUI_SECRET`: internal Flask session secret
- `FRP_GUI_SERVICE`: systemd service restarted after GUI updates
- `FRP_GUI_PORT`: internal Gunicorn port
- `FRP_GUI_PUBLIC_PORT`: public nginx port

`FRP_GUI_PORT` and `FRP_GUI_PUBLIC_PORT` should not be the same when nginx is
used as reverse proxy. The installer defaults to internal port `8845` and public
port `8844`.

After changing `/etc/frp-gui.env`, restart FRP Gui:

```bash
systemctl restart frp-gui
```

## Manual nginx Config

The installer creates nginx automatically. If you need to repair it manually,
the default file is:

```text
/etc/nginx/sites-available/frp-gui.conf
```

Default config:

```nginx
server {
    listen 8844;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8845;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Apply manual nginx changes:

```bash
nginx -t
systemctl reload nginx
```

## Features

- edit `[common]` server settings
- add, edit, copy, disable and delete proxy entries
- sort proxy entries by name, status, IP and domain
- create automatic backups before config writes
- create manual config backups with comments
- preview, restore and delete backups
- start, stop, restart, enable and disable the configured systemd service
- inspect the configured FRP Client systemd unit and start command, limited to services that start `frpc`
- check GitHub releases
- check the installed FRP Client version against the latest official FRP release
- update by release ZIP upload
- advanced git branch update for testing systems
- password login
- dark mode
- no database
- no compile step

## Local Development

For development on your workstation:

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

## TOML Migration

FRP supports TOML/YAML/JSON since v0.52.0 and marks INI as deprecated. FRP Gui
therefore treats INI as read-only and TOML as the editable format.

Open:

```text
Settings -> Migration
```

The migration step:

- reads the active INI file
- creates a backup
- writes a TOML file, normally `frpc.toml`
- runs `frpc verify -c /opt/frp/frpc.toml`
- saves the TOML path for FRP Gui
- shows the systemd update step when the `frpc` service still starts with INI

FRP Gui also compares its active config path with the config passed in the
configured `frpc` systemd service. If the service still starts with
`frpc.ini`, the GUI shows a red mismatch warning and keeps editing locked until
both paths point to the same TOML file. After the systemd path is updated,
restart the FRP Client service from the GUI.

Runtime settings are stored in `/etc/frp-gui.env`. FRP Gui reloads these values
on every request, so systems running multiple Gunicorn workers show the same
config path immediately after migration.

## Supported TOML Options

FRP Gui keeps the default forms small and shows advanced options in collapsible
sections. Empty optional fields are not written to the config.
Proxy forms show standard settings first and keep them open by default. Advanced
options are grouped below with a short explanation so new users can ignore them.
The proxy wizard guides non-expert users through the common web-domain setup and
then uses the same validation and save path as the expert form.

Server options include:

- server address, server port, token and TLS
- transport protocol and TLS server name
- client user and login-fail behavior
- log level, log file and log retention

Proxy options include:

- local address, local port, domains and remote port
- HTTP/HTTPS subdomain, locations and Host header rewrite
- TCP proxy protocol headers
- health checks
- load-balancer group and group key

## Security Notes

Do not expose FRP Gui without a login password. For public access, put HTTPS in
front of nginx.

The installer currently runs FRP Gui as `root`. This is simple for private
setups because the GUI can edit `/opt/frp/frpc.ini` and restart `frpc`. A later
hardened install should use a dedicated user and restricted sudo rules.
