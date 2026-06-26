# FRP Gui

FRP Gui is a small web interface for managing an existing FRP Client setup.
TOML is the editable config format. Existing INI configs can be migrated in the
GUI.

Current app version:

```text
1.0.0
```

## Features

- guided proxy wizard for common web-domain setups
- edit FRP server connection settings
- add, edit, copy, disable and delete proxy entries
- TOML support for common and advanced FRP Client options
- read-only INI mode with guided INI-to-TOML migration
- config verification through `frpc verify`
- automatic config backups before writes
- manual config backups with comments
- backup preview, restore and delete
- start, stop, restart, enable and disable the configured `frpc` systemd service
- read-only view of the configured `frpc` systemd unit and start command
- FRP Gui update check and release update from GitHub
- manual release ZIP update fallback
- advanced git branch update for testing systems
- FRP Client version check against the latest official FRP release
- password login
- dark mode
- no database
- no compile step

## Quick Install On Debian 12

These steps assume FRP is already installed and `frpc` already runs as a
systemd service.

> **Run the commands as `root`.** The installer checks this and stops if it is
> not started with root permissions.

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

The installer:

- installs Python, Flask, Gunicorn and nginx from Debian packages
- detects common FRP Client settings
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

## Updating FRP Gui

FRP Gui uses GitHub releases as the normal update channel.

### Recommended: update directly from the GUI

Open:

```text
Settings -> Updates
```

Use this flow:

```text
1. Check releases
2. Install update
3. Restart FRP Gui
```

The release check compares your installed version with the latest GitHub release
tag. When updates are available, the GUI shows release notes for every release
newer than your installed version.

### Manual fallback: upload a release ZIP

Use this when the server cannot download the release directly.

1. Download the official ZIP from the GitHub release page.
2. Open `Settings -> Updates`.
3. Upload the ZIP under `Upload release ZIP`.
4. Restart FRP Gui.

This uses the same app backup and file replacement logic as the direct release
update.

### Advanced: update from git

Use this only for testing or development systems.

```text
Settings -> Updates -> Update from git
```

This fetches `origin` and resets the local app files to the configured remote
branch, normally `origin/main`. A backup is created first. This can install code
that is newer than the latest official release.

### Shell update

If you installed with `git clone`, you can also update from the shell:

```bash
cd /opt/frp-gui
git pull --ff-only
systemctl restart frp-gui
```

## TOML Migration

FRP supports TOML/YAML/JSON since v0.52.0 and marks INI as deprecated. FRP Gui
therefore treats INI as read-only and TOML as the editable format.

Open:

```text
Settings -> Migration
```

The migration step migrates:

- `server_addr` to `serverAddr`
- `server_port` to `serverPort`
- `token` to `[auth] token`
- `tls_enable` to `[transport.tls] enable`
- proxy name and type
- local IP and local port
- custom domains
- remote port for TCP/UDP proxies
- disabled proxy entries recognized by FRP Gui comments

The migration step does this:

- reads the active INI file
- creates a backup of the INI file
- writes a TOML file, normally `frpc.toml`
- runs `frpc verify -c /opt/frp/frpc.toml`
- saves the TOML path for FRP Gui in `/etc/frp-gui.env`
- shows a systemd update step when the `frpc` service still starts with INI

FRP Gui also compares its active config path with the config passed in the
configured `frpc` systemd service. If the service still starts with `frpc.ini`,
the GUI shows a mismatch warning and keeps editing locked until both paths point
to the same TOML file. After the systemd path is updated, restart the FRP Client
service from the GUI.

Not migrated automatically:

- unsupported or unknown INI keys
- comments that are not disabled proxy entries
- custom formatting
- server-side `frps` settings
- nginx or DNS settings

## Supported TOML Options

FRP Gui keeps the default forms small and shows advanced options in collapsible
sections. Empty optional fields are not written to the config.

Server options:

- server address
- server port
- token
- TLS enable
- TLS server name
- transport protocol
- client user
- login-fail behavior
- log level
- log file
- log retention

Proxy options:

- enabled or disabled state
- name
- type
- local address
- local port
- custom domains
- remote port
- HTTP/HTTPS subdomain
- HTTP/HTTPS locations
- Host header rewrite
- TCP proxy protocol headers
- health check type, path, interval, timeout and max failures
- load-balancer group and group key

The proxy wizard guides non-expert users through the common web-domain setup and
then uses the same validation and save path as the expert form.

## Manual Configuration

Most users should use the installer. This section is for troubleshooting or
custom setups.

The installer writes:

```text
/etc/frp-gui.env
```

Example:

```text
FRP_CONFIG_PATH=/opt/frp/frpc.toml
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
- `FRP_GUI_ALLOW_SYSTEMCTL`: enables service control buttons in the GUI
- `FRP_GUI_PASSWORD`: login password
- `FRP_GUI_SECRET`: internal Flask session secret
- `FRP_GUI_SERVICE`: systemd service restarted after GUI updates
- `FRP_GUI_HOST`: internal Gunicorn listen address
- `FRP_GUI_PORT`: internal Gunicorn port
- `FRP_GUI_PUBLIC_PORT`: public nginx port

`FRP_GUI_PORT` and `FRP_GUI_PUBLIC_PORT` should not be the same when nginx is
used as reverse proxy. The installer defaults to internal port `8845` and public
port `8844`.

FRP Gui reloads `/etc/frp-gui.env` on every request, so systems running multiple
Gunicorn workers show the same runtime settings immediately after changes.

After manual changes to `/etc/frp-gui.env`, restart FRP Gui:

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

## Security Notes

Do not expose FRP Gui without a login password. For public access, put HTTPS in
front of nginx.

The installer currently runs FRP Gui as `root`. This is simple for private
setups because the GUI can edit `/opt/frp/frpc.toml`, update systemd service
paths and restart `frpc`. A later hardened install should use a dedicated user
and restricted sudo rules.

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
