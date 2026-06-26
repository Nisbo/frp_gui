from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for

from .app_updates import (
    create_app_backup,
    delete_app_backup,
    list_app_backups,
    restore_app_backup,
    update_from_git,
    update_from_release,
    update_from_zip,
    update_status,
)
from .backups import create_backup, delete_backup, get_backup, list_backups, read_backup_content, restore_backup
from .config_io import (
    FrpConfig,
    backup_config,
    read_config,
    read_ini,
    validate_common,
    validate_proxy,
    write_config,
    write_toml,
)
from .network import NetworkConfig, check_network, render_nginx_config, test_nginx_config, write_nginx_config
from .updates import check_for_update, update_status_to_dict
from .version import APP_NAME, APP_VERSION, REPO_URL


DEFAULT_CONFIG = Path("sample/frpc.ini")
SERVICE_ACTIONS = {"start", "stop", "restart", "enable", "disable"}


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FRP_GUI_SECRET", "dev-change-me"),
        FRP_CONFIG_PATH=Path(os.environ.get("FRP_CONFIG_PATH", DEFAULT_CONFIG)),
        FRP_BACKUP_DIR=Path(os.environ.get("FRP_BACKUP_DIR", "data/backups")),
        FRPC_BINARY=os.environ.get("FRPC_BINARY", "/opt/frp/frpc"),
        FRPC_SERVICE=os.environ.get("FRPC_SERVICE", "frpc"),
        ALLOW_SYSTEMCTL=os.environ.get("FRP_GUI_ALLOW_SYSTEMCTL", "0") == "1",
        ADMIN_PASSWORD=os.environ.get("FRP_GUI_PASSWORD", ""),
        ENV_FILE=Path(os.environ.get("FRP_GUI_ENV_FILE", "/etc/frp-gui.env")),
        INSTALL_PATH=Path(os.environ.get("FRP_GUI_INSTALL_PATH", "/opt/frp-gui")),
        FRP_GUI_SERVICE=os.environ.get("FRP_GUI_SERVICE", "frp-gui"),
        FRP_GUI_HOST=os.environ.get("FRP_GUI_HOST", "127.0.0.1"),
        FRP_GUI_PORT=int(os.environ.get("FRP_GUI_PORT", "8845")),
        FRP_GUI_PUBLIC_PORT=int(os.environ.get("FRP_GUI_PUBLIC_PORT", "8844")),
        FRP_GUI_SERVER_NAME=os.environ.get("FRP_GUI_SERVER_NAME", "_"),
        NGINX_SITE_PATH=Path(os.environ.get("FRP_GUI_NGINX_SITE_PATH", "/etc/nginx/sites-available/frp-gui.conf")),
        MAX_CONTENT_LENGTH=int(os.environ.get("FRP_GUI_MAX_UPLOAD_MB", "32")) * 1024 * 1024,
    )

    @app.before_request
    def require_login_and_csrf():
        _sync_runtime_settings_from_env(app)
        if request.endpoint in {"login", "login_post", "static"}:
            return None

        if app.config["ADMIN_PASSWORD"] and not session.get("logged_in"):
            return redirect(url_for("login"))

        if request.method == "POST":
            token = session.get("csrf_token")
            submitted = request.form.get("csrf_token")
            if not token or not submitted or not secrets.compare_digest(token, submitted):
                flash("Security token is invalid. Please try again.", "error")
                return redirect(url_for("index"))
        return None

    @app.context_processor
    def inject_globals():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        diagnostics = session.pop("diagnostics", None)
        app_update_pending_restart = session.get("app_update_pending_restart", False)
        pending_version = session.get("app_update_pending_version")
        if app_update_pending_restart and pending_version and pending_version == APP_VERSION:
            session.pop("app_update_pending_restart", None)
            session.pop("app_update_pending_version", None)
            app_update_pending_restart = False
        return {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "repo_url": REPO_URL,
            "csrf_token": session["csrf_token"],
            "diagnostics": diagnostics,
            "restart_required": _restart_required_visible(app),
            "frpc_service_control": _service_control_available(app),
            "app_update_pending_restart": app_update_pending_restart,
        }

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/login")
    def login():
        if not app.config["ADMIN_PASSWORD"]:
            flash("Login is disabled because FRP_GUI_PASSWORD is not set.", "error")
            return redirect(url_for("index"))
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        expected = app.config["ADMIN_PASSWORD"]
        password = request.form.get("password", "")
        if expected and secrets.compare_digest(password, expected):
            session.clear()
            session["logged_in"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            flash("Signed in.", "success")
            return redirect(url_for("index"))

        flash("Password is incorrect.", "error")
        return render_template("login.html"), 401

    @app.post("/logout")
    def logout():
        session.clear()
        flash("Signed out.", "success")
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        config, error = _try_load_config(app)
        if error:
            flash(error, "error")
            return redirect(url_for("settings"))
        frpc_unit = _frpc_systemd_unit(app.config["FRPC_SERVICE"])
        config_state = _config_state(Path(app.config["FRP_CONFIG_PATH"]), frpc_unit)
        sort_key = request.args.get("sort", "name")
        direction = request.args.get("direction", "asc")
        proxies_view = _sort_proxies([_proxy_view(proxy) for proxy in config.proxies], sort_key, direction)
        return render_template(
            "index.html",
            config=config,
            config_state=config_state,
            frpc_status=_service_status(app),
            frpc_version=_frpc_version(app),
            gui_running_label=f"{APP_VERSION} running",
            config_path=Path(app.config["FRP_CONFIG_PATH"]),
            service_control=_service_control_available(app),
            service_control_label=_service_control_label(app),
            frpc_enabled_status=_systemd_enabled_status(app.config["FRPC_SERVICE"]),
            app_service_control=bool(shutil.which("systemctl")),
            proxy_counts=_proxy_counts(config),
            proxies_view=proxies_view,
            sort_key=sort_key,
            direction=direction,
        )

    @app.get("/server")
    def edit_server():
        if not _require_editable_config(app):
            return redirect(url_for("index"))
        config, error = _try_load_config(app)
        if error:
            flash(error, "error")
            return redirect(url_for("settings"))
        return render_template("server_form.html", common=config.common)

    @app.get("/settings")
    def settings():
        config_path = Path(app.config["FRP_CONFIG_PATH"])
        active_tab = request.args.get("tab", "general")
        if active_tab not in {"general", "network", "security", "updates", "migration"}:
            active_tab = "general"
        form_values = session.pop("settings_form_values", {})
        shown_config_path = Path(form_values.get("config_path") or config_path)
        shown_frpc_binary = form_values.get("frpc_binary") or app.config["FRPC_BINARY"]
        shown_frpc_service = form_values.get("frpc_service") or app.config["FRPC_SERVICE"]
        frpc_unit = _frpc_systemd_unit(shown_frpc_service)
        config_state = _config_state(shown_config_path, frpc_unit)
        return render_template(
            "settings.html",
            active_tab=active_tab,
            config_path=shown_config_path,
            config_format=config_state["extension_format"],
            config_state=config_state,
            frpc_binary=shown_frpc_binary,
            frpc_service=shown_frpc_service,
            frpc_unit=frpc_unit,
            app_root=Path(app.root_path).parent,
            configured_install_path=app.config["INSTALL_PATH"],
            env_file=app.config["ENV_FILE"],
            password_enabled=bool(app.config["ADMIN_PASSWORD"]),
            systemctl_enabled=app.config["ALLOW_SYSTEMCTL"],
            network=_network_config(app),
            nginx_preview=render_nginx_config(_network_config(app)),
            update_status=session.pop("update_status", None),
            frpc_update_status=session.pop("frpc_update_status", None),
            app_update_status=update_status(Path(app.root_path).parent),
            app_update_result=session.pop("app_update_result", None),
            app_update_backups=list_app_backups(Path(app.root_path).parent),
            migration_target=shown_config_path.with_suffix(".toml"),
        )

    @app.post("/settings")
    def save_settings():
        config_path = Path(request.form.get("config_path", "").strip())
        if not config_path:
            flash("Config path is required.", "error")
            return redirect(url_for("settings", tab="general"))
        if config_path.suffix.lower() not in {".ini", ".toml"}:
            flash("Only .ini and .toml config paths are supported in the UI for now.", "error")
            return redirect(url_for("settings", tab="general"))
        if not config_path.exists():
            flash(f"Config file does not exist: {config_path}", "error")
            return redirect(url_for("settings", tab="general"))

        previous_path = app.config["FRP_CONFIG_PATH"]
        app.config["FRP_CONFIG_PATH"] = config_path
        _, error = _try_load_config(app)
        if error:
            app.config["FRP_CONFIG_PATH"] = previous_path
            flash(error, "error")
            return redirect(url_for("settings", tab="general"))

        frpc_binary = request.form.get("frpc_binary", "").strip() or app.config["FRPC_BINARY"]
        frpc_service = request.form.get("frpc_service", "").strip() or app.config["FRPC_SERVICE"]
        service_error = _frpc_service_validation_error(frpc_service)
        if service_error:
            session["settings_form_values"] = {
                "config_path": str(config_path),
                "frpc_binary": frpc_binary,
                "frpc_service": frpc_service,
            }
            flash(service_error, "error")
            return redirect(url_for("settings", tab="general"))

        app.config["FRPC_BINARY"] = frpc_binary
        app.config["FRPC_SERVICE"] = frpc_service
        app.config["ALLOW_SYSTEMCTL"] = request.form.get("allow_systemctl") == "on"
        try:
            _persist_runtime_settings(app)
        except OSError as exc:
            flash(f"Runtime settings updated, but the environment file could not be saved: {exc}", "warning")
            return redirect(url_for("settings", tab="general"))
        flash("Settings saved.", "success")
        return redirect(url_for("settings", tab="general"))

    @app.post("/settings/migration/convert-to-toml")
    def convert_to_toml():
        source_path = Path(app.config["FRP_CONFIG_PATH"])
        target_path = Path(request.form.get("target_path", "").strip() or source_path.with_suffix(".toml"))
        if source_path.suffix.lower() != ".ini":
            flash("The active config is not an INI file. No TOML migration is required.", "warning")
            return redirect(url_for("settings", tab="migration"))
        if target_path.suffix.lower() != ".toml":
            flash("The migration target must end with .toml.", "error")
            return redirect(url_for("settings", tab="migration"))

        try:
            config = read_ini(source_path)
            backup_config(source_path, Path(app.config["FRP_BACKUP_DIR"]))
            write_toml(target_path, config)
        except (OSError, ValueError) as exc:
            flash(f"TOML migration failed: {exc}", "error")
            return redirect(url_for("settings", tab="migration"))

        previous_path = app.config["FRP_CONFIG_PATH"]
        app.config["FRP_CONFIG_PATH"] = target_path
        ok, output = _verify_config(app)
        if not ok:
            app.config["FRP_CONFIG_PATH"] = previous_path
            flash(output or "TOML migration failed because frpc verify did not accept the generated file.", "error")
            return redirect(url_for("settings", tab="migration"))

        try:
            _update_env_value(Path(app.config["ENV_FILE"]), "FRP_CONFIG_PATH", str(target_path))
        except OSError as exc:
            flash(f"TOML config created, but the environment file could not be saved: {exc}", "warning")
            return redirect(url_for("settings", tab="migration"))

        flash("TOML config created and selected. Next, update the systemd config path before restarting FRP Client.", "success")
        return redirect(url_for("settings", tab="migration"))

    @app.post("/settings/migration/update-systemd-config")
    def update_systemd_config_path():
        target_path = Path(app.config["FRP_CONFIG_PATH"])
        try:
            backup_path = _update_frpc_service_config_path(app.config["FRPC_SERVICE"], target_path)
        except OSError as exc:
            flash(f"systemd service update failed: {exc}", "error")
            return redirect(url_for("settings", tab="migration"))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings", tab="migration"))

        _mark_restart_required()
        flash(f"systemd service config path updated. Unit backup: {backup_path}", "success")
        return redirect(url_for("settings", tab="migration"))

    @app.post("/settings/check-frpc-update")
    def check_frpc_update():
        status = _frpc_update_status(app)
        session["frpc_update_status"] = status
        if status["error"]:
            flash("FRP Client update check failed. See details below.", "error")
        elif status["update_available"]:
            flash("A newer FRP Client version is available.", "warning")
        else:
            flash("FRP Client is running the latest known version.", "success")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/security/password")
    def change_password():
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if app.config["ADMIN_PASSWORD"] and not secrets.compare_digest(current_password, app.config["ADMIN_PASSWORD"]):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("settings", tab="security"))
        if new_password != confirm_password:
            flash("New passwords do not match.", "error")
            return redirect(url_for("settings", tab="security"))
        if len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
            return redirect(url_for("settings", tab="security"))
        if any(char.isspace() for char in new_password):
            flash("New password must not contain whitespace.", "error")
            return redirect(url_for("settings", tab="security"))

        try:
            _update_env_value(Path(app.config["ENV_FILE"]), "FRP_GUI_PASSWORD", new_password)
        except OSError as exc:
            flash(f"Password could not be saved: {exc}", "error")
            return redirect(url_for("settings", tab="security"))

        app.config["ADMIN_PASSWORD"] = new_password
        session.clear()
        flash("Password changed. Please sign in again.", "success")
        return redirect(url_for("login"))

    @app.post("/settings/network")
    def save_network_settings():
        try:
            network = _network_from_form()
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings", tab="network"))

        _apply_network_to_app(app, network)
        try:
            _persist_network_settings(app)
        except OSError as exc:
            flash(f"Network settings updated, but the environment file could not be saved: {exc}", "warning")
            return redirect(url_for("settings", tab="network"))
        flash("Network settings updated for this app process.", "success")
        return redirect(url_for("settings", tab="network"))

    @app.post("/settings/network/check")
    def check_network_settings():
        try:
            network = _network_from_form()
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings", tab="network"))

        _apply_network_to_app(app, network)
        diagnostics = check_network(network)
        session["diagnostics"] = diagnostics
        summary = _diagnostics_summary(diagnostics)
        flash(summary["message"], summary["category"])
        return redirect(url_for("settings", tab="network"))

    @app.post("/settings/network/apply-nginx")
    def apply_nginx_settings():
        try:
            network = _network_from_form()
            _apply_network_to_app(app, network)
            _persist_network_settings(app)
            write_nginx_config(network)
            ok, output = test_nginx_config()
        except OSError as exc:
            flash(f"Network settings saved, but nginx config apply failed: {exc}", "error")
            return redirect(url_for("settings", tab="network"))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings", tab="network"))

        message = output or ("Network settings saved and nginx config test passed." if ok else "Network settings saved, but nginx config test failed.")
        flash(message, "success" if ok else "error")
        return redirect(url_for("settings", tab="network"))

    @app.post("/settings/check")
    def check_settings():
        config_value = request.form.get("config_path")
        binary_value = request.form.get("frpc_binary")
        service_value = request.form.get("frpc_service")
        config_path = Path(config_value.strip()) if config_value is not None else Path(app.config["FRP_CONFIG_PATH"])
        frpc_binary = binary_value.strip() if binary_value is not None else app.config["FRPC_BINARY"]
        frpc_service = service_value.strip() if service_value is not None else app.config["FRPC_SERVICE"]
        session["settings_form_values"] = {
            "config_path": str(config_path),
            "frpc_binary": frpc_binary,
            "frpc_service": frpc_service,
        }
        diagnostics = _settings_diagnostics(config_path, frpc_binary, frpc_service)
        session["diagnostics"] = diagnostics
        summary = _diagnostics_summary(diagnostics)
        flash(summary["message"], summary["category"])
        return redirect(url_for("settings", tab="general"))

    @app.post("/settings/check-update")
    def check_update():
        status = check_for_update()
        session["update_status"] = update_status_to_dict(status)
        if status.error:
            flash("Update check failed. See details below.", "error")
        elif status.no_releases:
            flash("No GitHub releases have been published yet.", "warning")
        elif status.update_available:
            flash("A new version is available.", "success")
        else:
            flash("You are running the latest known version.", "success")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/git")
    def apply_git_update():
        result = update_from_git(Path(app.root_path).parent)
        _store_app_update_result(result)
        if result.ok:
            session["app_update_pending_restart"] = True
        flash(result.message, "success" if result.ok else "error")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/release")
    def apply_release_update():
        status = check_for_update(timeout=15)
        session["update_status"] = update_status_to_dict(status)
        if status.error:
            flash("Release update failed because the update check failed.", "error")
            return redirect(url_for("settings", tab="updates"))
        if status.no_releases:
            flash("No GitHub releases have been published yet.", "warning")
            return redirect(url_for("settings", tab="updates"))
        if not status.update_available:
            flash("No newer official release is available.", "success")
            return redirect(url_for("settings", tab="updates"))
        if not status.zipball_url or not status.latest_version:
            flash("Latest release does not provide a downloadable ZIP archive.", "error")
            return redirect(url_for("settings", tab="updates"))

        result = update_from_release(Path(app.root_path).parent, status.zipball_url, status.latest_version)
        _store_app_update_result(result)
        if result.ok:
            session["app_update_pending_restart"] = True
            session["app_update_pending_version"] = status.latest_version
            session["update_status"] = {
                **update_status_to_dict(status),
                "update_available": False,
                "release_notes": [],
            }
        flash(result.message, "success" if result.ok else "error")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/zip")
    def apply_zip_update():
        upload = request.files.get("update_zip")
        if not upload or not upload.filename:
            flash("Choose a ZIP file before starting the update.", "error")
            return redirect(url_for("settings", tab="updates"))
        if not upload.filename.lower().endswith(".zip"):
            flash("Only ZIP update files are supported.", "error")
            return redirect(url_for("settings", tab="updates"))

        result = update_from_zip(Path(app.root_path).parent, upload.stream)
        _store_app_update_result(result)
        if result.ok:
            session["app_update_pending_restart"] = True
        flash(result.message, "success" if result.ok else "error")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/backups")
    def create_app_update_backup():
        comment = request.form.get("comment", "").strip()
        try:
            backup_path = create_app_backup(Path(app.root_path).parent, "Manual app backup", comment)
        except OSError as exc:
            flash(f"App backup failed: {exc}", "error")
            return redirect(url_for("settings", tab="updates"))

        flash(f"App backup created: {backup_path}", "success")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/backups/<backup_id>/restore")
    def restore_app_update_backup(backup_id: str):
        result = restore_app_backup(Path(app.root_path).parent, backup_id)
        _store_app_update_result(result)
        if result.ok:
            session["app_update_pending_restart"] = True
        flash(result.message, "success" if result.ok else "error")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/backups/<backup_id>/delete")
    def delete_app_update_backup(backup_id: str):
        try:
            delete_app_backup(Path(app.root_path).parent, backup_id)
        except (OSError, ValueError) as exc:
            flash(f"App update backup delete failed: {exc}", "error")
            return redirect(url_for("settings", tab="updates"))

        flash("App update backup deleted.", "success")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/settings/update/restart-app")
    def restart_app_service():
        systemctl = shutil.which("systemctl")
        if not systemctl:
            flash("systemctl is not available in this environment.", "error")
            return redirect(url_for("settings", tab="updates"))

        service = app.config["FRP_GUI_SERVICE"]
        session.pop("app_update_pending_restart", None)
        session.pop("app_update_pending_version", None)
        command = f"sleep 1; exec {shlex.quote(systemctl)} restart {shlex.quote(service)}"
        subprocess.Popen(["/bin/sh", "-c", command], start_new_session=True)
        flash("FRP Gui restart requested. Reload the page in a few seconds.", "success")
        return redirect(url_for("settings", tab="updates"))

    @app.post("/restart-required/dismiss")
    def dismiss_restart_required():
        session.pop("restart_required", None)
        return redirect(request.referrer or url_for("index"))

    @app.post("/backups")
    def create_config_backup():
        config_path = Path(app.config["FRP_CONFIG_PATH"])
        comment = request.form.get("comment", "").strip()
        try:
            create_backup(config_path, Path(app.config["FRP_BACKUP_DIR"]), comment)
        except OSError as exc:
            flash(f"Backup failed: {exc}", "error")
            return redirect(url_for("settings"))

        flash("Backup created.", "success")
        return redirect(request.referrer or url_for("index"))

    @app.get("/backups")
    def backups():
        return render_template(
            "backups.html",
            backups=list_backups(Path(app.config["FRP_BACKUP_DIR"])),
            backup_dir=Path(app.config["FRP_BACKUP_DIR"]),
            config_path=Path(app.config["FRP_CONFIG_PATH"]),
        )

    @app.get("/backups/<backup_id>")
    def backup_detail(backup_id: str):
        backup_dir = Path(app.config["FRP_BACKUP_DIR"])
        try:
            backup = get_backup(backup_dir, backup_id)
            content = read_backup_content(backup_dir, backup_id)
        except OSError as exc:
            flash(f"Backup preview failed: {exc}", "error")
            return redirect(url_for("backups"))

        return render_template("backup_detail.html", backup=backup, content=content)

    @app.post("/backups/<backup_id>/restore")
    def restore_config_backup(backup_id: str):
        config_path = Path(app.config["FRP_CONFIG_PATH"])
        backup_dir = Path(app.config["FRP_BACKUP_DIR"])
        try:
            create_backup(config_path, backup_dir, "Automatic backup before restore")
            restore_backup(backup_dir, backup_id, config_path)
        except OSError as exc:
            flash(f"Restore failed: {exc}", "error")
            return redirect(url_for("backups"))

        _mark_restart_required()
        flash("Backup restored. Restart FRP Client for the restored config to take effect.", "success")
        return redirect(url_for("index"))

    @app.post("/backups/<backup_id>/delete")
    def delete_config_backup(backup_id: str):
        try:
            delete_backup(Path(app.config["FRP_BACKUP_DIR"]), backup_id)
        except OSError as exc:
            flash(f"Delete failed: {exc}", "error")
            return redirect(url_for("backups"))

        flash("Backup deleted.", "success")
        return redirect(url_for("backups"))

    @app.post("/common")
    def save_common():
        if not _require_editable_config(app):
            return redirect(url_for("index"))
        config = _load_config(app)
        common = {
            "server_addr": request.form.get("server_addr", "").strip(),
            "server_port": request.form.get("server_port", "").strip(),
            "token": request.form.get("token", "").strip(),
            "tls_enable": "true" if request.form.get("tls_enable") == "on" else "false",
            "tls_server_name": request.form.get("tls_server_name", "").strip(),
            "transport_protocol": request.form.get("transport_protocol", "").strip(),
            "log_level": request.form.get("log_level", "").strip(),
            "log_file": request.form.get("log_file", "").strip(),
            "log_max_days": request.form.get("log_max_days", "").strip(),
            "user": request.form.get("user", "").strip(),
            "login_fail_exit": "true" if request.form.get("login_fail_exit") == "on" else "",
        }
        errors = validate_common(common)
        if errors:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("edit_server"))

        config.common.update(common)
        _save_config(app, config)
        _mark_restart_required()
        flash("Server settings saved.", "success")
        return redirect(url_for("index"))

    @app.get("/proxy/new")
    def new_proxy():
        if not _require_editable_config(app):
            return redirect(url_for("index"))
        return render_template("proxy_form.html", proxy={}, mode="new")

    @app.post("/proxy")
    def create_proxy():
        if not _require_editable_config(app):
            return redirect(url_for("index"))
        config = _load_config(app)
        proxy = _proxy_from_form()
        existing = {item["name"] for item in config.proxies}
        errors = validate_proxy(proxy, existing)
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("proxy_form.html", proxy=proxy, mode="new"), 400

        config.proxies.append(proxy)
        _save_config(app, config)
        _mark_restart_required()
        flash("Proxy created.", "success")
        return redirect(url_for("index"))

    @app.get("/proxy/<name>/edit")
    def edit_proxy(name: str):
        if not _require_editable_config(app):
            return redirect(url_for("index"))
        config = _load_config(app)
        proxy = _find_proxy(config, name)
        if proxy is None:
            flash("Proxy not found.", "error")
            return redirect(url_for("index"))
        return render_template("proxy_form.html", proxy=proxy, mode="edit")

    @app.get("/proxy/<name>/copy")
    def copy_proxy(name: str):
        if not _require_editable_config(app):
            return redirect(url_for("index"))
        config = _load_config(app)
        proxy = _find_proxy(config, name)
        if proxy is None:
            flash("Proxy not found.", "error")
            return redirect(url_for("index"))

        copied = dict(proxy)
        copied["name"] = _next_copy_name(config, name)
        copied["enabled"] = "false"
        return render_template("proxy_form.html", proxy=copied, mode="copy")

    @app.post("/proxy/<name>")
    def update_proxy(name: str):
        if not _require_editable_config(app):
            return redirect(url_for("index"))
        config = _load_config(app)
        index = _find_proxy_index(config, name)
        if index is None:
            flash("Proxy not found.", "error")
            return redirect(url_for("index"))

        proxy = _proxy_from_form(config.proxies[index])
        existing = {item["name"] for item in config.proxies if item["name"] != name}
        errors = validate_proxy(proxy, existing)
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("proxy_form.html", proxy=proxy, mode="edit"), 400

        config.proxies[index] = proxy
        _save_config(app, config)
        _mark_restart_required()
        flash("Proxy saved.", "success")
        return redirect(url_for("index"))

    @app.post("/proxy/<name>/toggle")
    def toggle_proxy(name: str):
        if not _require_editable_config(app):
            return redirect(url_for("index"))
        config = _load_config(app)
        proxy = _find_proxy(config, name)
        if proxy is None:
            flash("Proxy not found.", "error")
            return redirect(url_for("index"))

        enabled = str(proxy.get("enabled", "true")).lower() == "true"
        proxy["enabled"] = "false" if enabled else "true"
        _save_config(app, config)
        _mark_restart_required()
        flash(f"Proxy {'disabled' if enabled else 'enabled'}.", "success")
        return redirect(url_for("index"))

    @app.post("/proxy/<name>/delete")
    def delete_proxy(name: str):
        if not _require_editable_config(app):
            return redirect(url_for("index"))
        config = _load_config(app)
        before = len(config.proxies)
        config.proxies = [proxy for proxy in config.proxies if proxy["name"] != name]
        if len(config.proxies) == before:
            flash("Proxy not found.", "error")
            return redirect(url_for("index"))

        _save_config(app, config)
        _mark_restart_required()
        flash("Proxy deleted.", "success")
        return redirect(url_for("index"))

    @app.post("/verify")
    def verify():
        ok, output = _verify_config(app)
        flash(output or ("Config is valid." if ok else "Config is invalid."), "success" if ok else "error")
        return redirect(url_for("index"))

    @app.get("/config/download")
    def download_config():
        config_path = Path(app.config["FRP_CONFIG_PATH"])
        if not config_path.is_absolute():
            config_path = (Path.cwd() / config_path).resolve()
        if not config_path.exists() or not config_path.is_file():
            flash(f"Config file does not exist: {config_path}", "error")
            return redirect(url_for("index"))
        return send_file(config_path, as_attachment=True, download_name=config_path.name)

    @app.post("/service/<action>")
    def service_action(action: str):
        if action not in SERVICE_ACTIONS:
            flash("Unknown service action.", "error")
            return redirect(url_for("index"))

        if not _service_control_available(app):
            flash("Service control is not available. Set a systemd service and enable it in settings.", "error")
            return redirect(url_for("index"))

        if action in {"restart", "start"}:
            ok, output = _verify_config(app)
        else:
            ok, output = True, ""
        if not ok:
            flash(output or "Config is invalid, restart aborted.", "error")
            return redirect(url_for("index"))

        systemctl = shutil.which("systemctl")
        if not systemctl:
            flash("systemctl is not available in this environment.", "error")
            return redirect(url_for("index"))

        result = subprocess.run(
            [systemctl, action, app.config["FRPC_SERVICE"]],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            if action == "restart":
                session.pop("restart_required", None)
            flash(f"FRP Client service {action} completed.", "success")
        else:
            flash(result.stderr.strip() or f"Service {action} failed.", "error")
        return redirect(url_for("index"))

    return app


def _load_config(app: Flask) -> FrpConfig:
    return read_config(Path(app.config["FRP_CONFIG_PATH"]))


def _try_load_config(app: Flask) -> tuple[FrpConfig | None, str | None]:
    path = Path(app.config["FRP_CONFIG_PATH"])
    try:
        return read_config(path), None
    except FileNotFoundError:
        return None, f"Config file not found: {path}"
    except OSError as exc:
        return None, f"Config file cannot be read: {exc}"
    except Exception as exc:
        return None, f"Config file cannot be parsed: {exc}"


def _save_config(app: Flask, config: FrpConfig) -> None:
    path = Path(app.config["FRP_CONFIG_PATH"])
    backup_config(path, Path(app.config["FRP_BACKUP_DIR"]))
    write_config(path, config)


def _require_editable_config(app: Flask) -> bool:
    state = _config_state(Path(app.config["FRP_CONFIG_PATH"]), _frpc_systemd_unit(app.config["FRPC_SERVICE"]))
    if state["editable"]:
        return True
    flash(str(state["edit_message"]), "error")
    return False


def _mark_restart_required() -> None:
    session["restart_required"] = True


def _restart_required_visible(app: Flask) -> bool:
    if not session.get("restart_required", False):
        return False
    state = _config_state(Path(app.config["FRP_CONFIG_PATH"]), _frpc_systemd_unit(app.config["FRPC_SERVICE"]))
    return not bool(state["systemd_mismatch"])


def _proxy_from_form(existing: dict[str, str] | None = None) -> dict[str, str]:
    existing_enabled = (existing or {}).get("enabled", "true")
    enabled = "true" if request.form.get("enabled") == "on" else str(existing_enabled)
    if request.form.get("enabled_form") == "1":
        enabled = "true" if "enabled" in request.form else "false"
    return {
        "name": request.form.get("name", "").strip(),
        "type": request.form.get("type", "").strip().lower(),
        "local_ip": request.form.get("local_ip", "").strip(),
        "local_port": request.form.get("local_port", "").strip(),
        "custom_domains": request.form.get("custom_domains", "").strip(),
        "remote_port": request.form.get("remote_port", "").strip(),
        "subdomain": request.form.get("subdomain", "").strip(),
        "locations": request.form.get("locations", "").strip(),
        "host_header_rewrite": request.form.get("host_header_rewrite", "").strip(),
        "proxy_protocol_version": request.form.get("proxy_protocol_version", "").strip(),
        "health_check_type": request.form.get("health_check_type", "").strip(),
        "health_check_path": request.form.get("health_check_path", "").strip(),
        "health_check_interval": request.form.get("health_check_interval", "").strip(),
        "health_check_timeout": request.form.get("health_check_timeout", "").strip(),
        "health_check_max_failed": request.form.get("health_check_max_failed", "").strip(),
        "load_balancer_group": request.form.get("load_balancer_group", "").strip(),
        "load_balancer_group_key": request.form.get("load_balancer_group_key", "").strip(),
        "enabled": enabled,
    }


def _find_proxy(config: FrpConfig, name: str) -> dict[str, str] | None:
    index = _find_proxy_index(config, name)
    return None if index is None else config.proxies[index]


def _find_proxy_index(config: FrpConfig, name: str) -> int | None:
    for index, proxy in enumerate(config.proxies):
        if proxy["name"] == name:
            return index
    return None


def _next_copy_name(config: FrpConfig, name: str) -> str:
    existing = {proxy["name"] for proxy in config.proxies}
    base = f"{name}-copy"
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


def _verify_config(app: Flask) -> tuple[bool, str]:
    binary = Path(app.config["FRPC_BINARY"])
    config_path = Path(app.config["FRP_CONFIG_PATH"])
    if not binary.exists():
        return False, f"frpc not found: {binary}"

    result = subprocess.run(
        [str(binary), "verify", "-c", str(config_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    return result.returncode == 0, output


def _service_status(app: Flask) -> str:
    if not app.config["ALLOW_SYSTEMCTL"]:
        return "Systemd control disabled"
    if not _service_control_available(app):
        return "Systemd unavailable"

    return _systemd_status(app.config["FRPC_SERVICE"])


def _systemd_status(service: str) -> str:
    if not service:
        return "Not configured"

    systemctl = shutil.which("systemctl")
    if not systemctl:
        return "Systemd unavailable"

    try:
        result = subprocess.run(
            [systemctl, "is-active", service],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return "Systemd unavailable"
    return result.stdout.strip() or "unknown"


def _systemd_enabled_status(service: str) -> str:
    if not service:
        return "unknown"

    systemctl = shutil.which("systemctl")
    if not systemctl:
        return "unknown"

    try:
        result = subprocess.run(
            [systemctl, "is-enabled", service],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _frpc_systemd_unit(service: str) -> dict[str, str | bool]:
    if not service:
        return {
            "available": False,
            "message": "No systemd service configured.",
            "service": "",
            "fragment_path": "",
            "drop_in_paths": "",
            "exec_start": "",
            "config_path": "",
            "config_format": "Unknown",
            "content": "",
        }

    systemctl = shutil.which("systemctl")
    if not systemctl:
        return {
            "available": False,
            "message": "systemctl is not available in this environment.",
            "service": service,
            "fragment_path": "",
            "drop_in_paths": "",
            "exec_start": "",
            "config_path": "",
            "config_format": "Unknown",
            "content": "",
        }

    show = subprocess.run(
        [systemctl, "show", service, "--property=FragmentPath", "--property=DropInPaths", "--property=ExecStart", "--no-pager"],
        check=False,
        capture_output=True,
        text=True,
    )
    if show.returncode != 0:
        return {
            "available": False,
            "message": show.stderr.strip() or f"systemd service not found: {service}",
            "service": service,
            "fragment_path": "",
            "drop_in_paths": "",
            "exec_start": "",
            "config_path": "",
            "config_format": "Unknown",
            "content": "",
        }

    values = _parse_systemctl_properties(show.stdout)
    cat = subprocess.run(
        [systemctl, "cat", service, "--no-pager"],
        check=False,
        capture_output=True,
        text=True,
    )
    content = cat.stdout.strip() if cat.returncode == 0 else cat.stderr.strip()
    exec_start = values.get("ExecStart", "")
    config_path = _extract_frpc_config_path(exec_start)
    if exec_start and not _looks_like_frpc_exec(exec_start):
        return {
            "available": False,
            "message": f"Configured service '{service}' does not appear to start frpc.",
            "service": service,
            "fragment_path": values.get("FragmentPath", ""),
            "drop_in_paths": values.get("DropInPaths", ""),
            "exec_start": exec_start,
            "config_path": config_path,
            "config_format": _config_format(Path(config_path)) if config_path else "Unknown",
            "content": "",
        }
    return {
        "available": True,
        "message": "",
        "service": service,
        "fragment_path": values.get("FragmentPath", ""),
        "drop_in_paths": values.get("DropInPaths", ""),
        "exec_start": exec_start,
        "config_path": config_path,
        "config_format": _config_format(Path(config_path)) if config_path else "Unknown",
        "content": content,
    }


def _parse_systemctl_properties(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def _frpc_service_validation_error(service: str) -> str | None:
    unit = _frpc_systemd_unit(service)
    if not unit["available"] and unit["message"] and "does not appear to start frpc" in str(unit["message"]):
        return str(unit["message"])
    return None


def _looks_like_frpc_exec(value: str) -> bool:
    return bool(re.search(r"(?<![\w.-])frpc(?![\w.-])", value))


def _extract_frpc_config_path(exec_start: str) -> str:
    if not exec_start:
        return ""
    argv_match = re.search(r"argv\[]=(.*?)(?:\s+;\s+|$)", exec_start)
    command = argv_match.group(1) if argv_match else exec_start
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    for index, part in enumerate(parts):
        if part in {"-c", "--config"} and index + 1 < len(parts):
            return parts[index + 1].strip("'\"")
        if part.startswith("-c=") or part.startswith("--config="):
            return part.split("=", 1)[1].strip("'\"")
    regex_match = re.search(r"(?:^|[\s;])(?:-c|--config)\s+([^\s;]+)", exec_start)
    return regex_match.group(1).strip("'\"") if regex_match else ""


def _frpc_version(app: Flask) -> str | None:
    binary = _resolve_binary(app.config["FRPC_BINARY"])
    if not binary:
        return None

    try:
        result = subprocess.run(
            [str(binary), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0].strip() if output else None


def _frpc_update_status(app: Flask) -> dict[str, str | bool | None]:
    installed = _frpc_version(app)
    latest = None
    error = None
    try:
        request = urllib.request.Request(
            "https://api.github.com/repos/fatedier/frp/releases/latest",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "frp-gui-frpc-update-check",
            },
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latest = str(payload.get("tag_name") or payload.get("name") or "").strip().removeprefix("v")
        if not latest:
            error = "Latest FRP release did not include a version tag."
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        error = str(exc)

    update_available = bool(installed and latest and _version_key(latest) > _version_key(installed))
    return {
        "installed": installed or "Unknown",
        "latest": latest or "Unknown",
        "update_available": update_available,
        "error": error,
        "release_url": f"https://github.com/fatedier/frp/releases/tag/v{latest}" if latest else None,
    }


def _resolve_binary(binary: str) -> Path | None:
    binary_path = Path(binary)
    if binary_path.exists() and binary_path.is_file():
        return binary_path
    found = shutil.which(binary)
    return Path(found) if found else None


def _version_key(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts) or (0,)


def _proxy_counts(config: FrpConfig) -> dict[str, int]:
    total = len(config.proxies)
    disabled = sum(1 for proxy in config.proxies if str(proxy.get("enabled", "true")).lower() == "false")
    enabled = total - disabled
    return {"enabled": enabled, "disabled": disabled, "total": total}


def _config_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix.upper() if suffix else "Unknown"


def _config_state(path: Path, frpc_unit: dict[str, str | bool] | None = None) -> dict[str, object]:
    extension_format = _config_format(path)
    content_format = "Unknown"
    messages: list[dict[str, str]] = []
    editable = False

    if not path.exists():
        messages.append({"status": "error", "label": "Config file", "message": f"Config file not found: {path}"})
    elif not path.is_file():
        messages.append({"status": "error", "label": "Config file", "message": f"Config path is not a file: {path}"})
    else:
        try:
            read_config(path)
            content_format = extension_format
            messages.append({"status": "ok", "label": "Config file", "message": f"{extension_format} config can be parsed."})
        except Exception as exc:
            messages.append({"status": "error", "label": "Config file", "message": f"Config cannot be parsed as {extension_format}: {exc}"})

    if extension_format == "INI":
        messages.append({
            "status": "warning",
            "label": "Migration required",
            "message": "INI configs are shown read-only. Convert to TOML before editing proxies or server settings.",
        })
    elif extension_format != "TOML":
        messages.append({
            "status": "error",
            "label": "Unsupported format",
            "message": "FRP Gui edits only TOML configs. INI can be migrated; other formats are unsupported.",
        })

    unit_config_path = ""
    unit_config_format = "Unknown"
    systemd_mismatch = False
    if frpc_unit and frpc_unit.get("available"):
        unit_config_path = str(frpc_unit.get("config_path") or "")
        unit_config_format = str(frpc_unit.get("config_format") or "Unknown")
        if unit_config_path:
            if _normalized_path(unit_config_path) != _normalized_path(str(path)):
                systemd_mismatch = True
                messages.append({
                    "status": "error",
                    "label": "systemd config mismatch",
                    "message": f"FRP Gui is using {path}, but the service starts frpc with {unit_config_path}.",
                })
            elif unit_config_format != "TOML":
                messages.append({
                    "status": "warning",
                    "label": "systemd uses INI",
                    "message": "The FRP Client service still starts with an INI config. Migrate to TOML before editing.",
                })
        else:
            messages.append({
                "status": "warning",
                "label": "systemd config unknown",
                "message": "The service ExecStart does not expose a -c/--config path, so FRP Gui cannot compare it.",
            })

    editable = (
        extension_format == "TOML"
        and content_format == "TOML"
        and not any(message["status"] == "error" for message in messages)
        and not any(message["label"] in {"systemd uses INI", "Migration required"} for message in messages)
    )
    edit_message = "Config editing is locked until the active config is TOML and matches the frpc systemd command."
    if extension_format == "INI":
        edit_message = "Config editing is locked in INI mode. Open Settings -> Migration and convert the config to TOML first."
    elif any(message["label"] == "systemd config mismatch" for message in messages):
        edit_message = "Config editing is locked because FRP Gui and the frpc systemd service point to different config files."

    severity = "ok"
    if any(message["status"] == "error" for message in messages):
        severity = "error"
    elif any(message["status"] == "warning" for message in messages):
        severity = "warning"

    return {
        "extension_format": extension_format,
        "content_format": content_format,
        "editable": editable,
        "edit_message": edit_message,
        "messages": messages,
        "severity": severity,
        "systemd_config_path": unit_config_path,
        "systemd_config_format": unit_config_format,
        "systemd_mismatch": systemd_mismatch,
    }


def _normalized_path(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve(strict=False))
    except OSError:
        return value


def _update_frpc_service_config_path(service: str, target_path: Path) -> Path:
    unit = _frpc_systemd_unit(service)
    if not unit.get("available"):
        raise ValueError(str(unit.get("message") or "Configured FRP Client service is unavailable."))
    old_path = str(unit.get("config_path") or "")
    if not old_path:
        raise ValueError("The FRP Client service ExecStart does not contain a -c/--config path that FRP Gui can update.")
    if not _looks_like_frpc_exec(str(unit.get("exec_start") or "")):
        raise ValueError(f"Configured service '{service}' does not appear to start frpc.")

    fragment_path = Path(str(unit.get("fragment_path") or ""))
    if not fragment_path.exists() or not fragment_path.is_file():
        raise ValueError(f"systemd unit file cannot be edited: {fragment_path}")

    content = fragment_path.read_text(encoding="utf-8")
    if old_path not in content:
        raise ValueError(f"The current config path was detected as {old_path}, but it was not found in {fragment_path}.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = fragment_path.with_name(f"{fragment_path.name}.frp-gui-{stamp}.bak")
    shutil.copy2(fragment_path, backup_path)
    fragment_path.write_text(content.replace(old_path, str(target_path), 1), encoding="utf-8")

    systemctl = shutil.which("systemctl")
    if systemctl:
        subprocess.run([systemctl, "daemon-reload"], check=False, capture_output=True, text=True)

    return backup_path


def _service_control_available(app: Flask) -> bool:
    return bool(app.config["ALLOW_SYSTEMCTL"] and app.config["FRPC_SERVICE"] and shutil.which("systemctl"))


def _service_control_label(app: Flask) -> str:
    if not app.config["ALLOW_SYSTEMCTL"]:
        return "Systemd service disabled"
    if not app.config["FRPC_SERVICE"]:
        return "No systemd service configured"
    if not shutil.which("systemctl"):
        return "Systemd unavailable"
    return "Systemd service enabled"


def _settings_diagnostics(config_path: Path, frpc_binary: str, frpc_service: str) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []

    if not config_path.exists():
        diagnostics.append({"label": "Config file", "status": "error", "message": f"Not found: {config_path}"})
    elif not config_path.is_file():
        diagnostics.append({"label": "Config file", "status": "error", "message": f"Not a file: {config_path}"})
    else:
        try:
            read_config(config_path)
            diagnostics.append({"label": "Config file", "status": "ok", "message": f"Readable: {config_path}"})
        except Exception as exc:
            diagnostics.append({"label": "Config file", "status": "error", "message": f"Cannot parse: {exc}"})

    binary_path = Path(frpc_binary)
    if binary_path.exists() and binary_path.is_file():
        diagnostics.append({"label": "frpc binary", "status": "ok", "message": f"Found: {binary_path}"})
    elif shutil.which(frpc_binary):
        diagnostics.append({"label": "frpc binary", "status": "ok", "message": f"Found in PATH: {frpc_binary}"})
    else:
        diagnostics.append({"label": "frpc binary", "status": "error", "message": f"Not found: {frpc_binary}"})

    if not frpc_service.strip():
        diagnostics.append({"label": "systemd service", "status": "warning", "message": "No service name configured."})
    elif not shutil.which("systemctl"):
        diagnostics.append({"label": "systemd service", "status": "warning", "message": "systemctl is not available in this environment."})
    else:
        systemctl = shutil.which("systemctl")
        result = subprocess.run(
            [systemctl, "status", frpc_service],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode in {0, 3}:
            diagnostics.append({"label": "systemd service", "status": "ok", "message": f"Service exists: {frpc_service}"})
        else:
            diagnostics.append({"label": "systemd service", "status": "error", "message": result.stderr.strip() or f"Service not found: {frpc_service}"})

        service_error = _frpc_service_validation_error(frpc_service)
        if service_error:
            diagnostics.append({"label": "FRP Client service", "status": "error", "message": service_error})
        else:
            diagnostics.append({"label": "FRP Client service", "status": "ok", "message": "Service ExecStart appears to use frpc."})

        state = _config_state(config_path, _frpc_systemd_unit(frpc_service))
        for message in state["messages"]:
            if message["status"] != "ok":
                diagnostics.append({
                    "label": str(message["label"]),
                    "status": str(message["status"]),
                    "message": str(message["message"]),
                })

    return diagnostics


def _diagnostics_summary(diagnostics: list[dict[str, str]]) -> dict[str, str]:
    if any(item["status"] == "error" for item in diagnostics):
        return {"category": "error", "message": "Settings check found errors."}
    if any(item["status"] == "warning" for item in diagnostics):
        return {"category": "warning", "message": "Settings check found warnings."}
    return {"category": "success", "message": "Settings check passed."}


def _network_config(app: Flask) -> NetworkConfig:
    return NetworkConfig(
        internal_host=app.config["FRP_GUI_HOST"],
        internal_port=int(app.config["FRP_GUI_PORT"]),
        public_port=int(app.config["FRP_GUI_PUBLIC_PORT"]),
        server_name=app.config["FRP_GUI_SERVER_NAME"],
        nginx_site_path=Path(app.config["NGINX_SITE_PATH"]),
    )


def _network_from_form() -> NetworkConfig:
    internal_host = request.form.get("internal_host", "").strip() or "127.0.0.1"
    server_name = request.form.get("server_name", "").strip() or "_"
    nginx_site_path = Path(request.form.get("nginx_site_path", "").strip() or "/etc/nginx/sites-available/frp-gui.conf")
    return NetworkConfig(
        internal_host=internal_host,
        internal_port=_form_port("internal_port"),
        public_port=_form_port("public_port"),
        server_name=server_name,
        nginx_site_path=nginx_site_path,
    )


def _form_port(name: str) -> int:
    value = request.form.get(name, "").strip()
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError(f"{name.replace('_', ' ').title()} must be a number.") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name.replace('_', ' ').title()} must be between 1 and 65535.")
    return port


def _apply_network_to_app(app: Flask, network: NetworkConfig) -> None:
    app.config["FRP_GUI_HOST"] = network.internal_host
    app.config["FRP_GUI_PORT"] = network.internal_port
    app.config["FRP_GUI_PUBLIC_PORT"] = network.public_port
    app.config["FRP_GUI_SERVER_NAME"] = network.server_name
    app.config["NGINX_SITE_PATH"] = network.nginx_site_path


def _store_app_update_result(result) -> None:
    session["app_update_result"] = {
        "ok": result.ok,
        "message": result.message,
        "details": result.details,
        "backup_path": str(result.backup_path) if result.backup_path else None,
    }


def _sync_runtime_settings_from_env(app: Flask) -> None:
    env_values = _read_env_file(Path(app.config["ENV_FILE"]))
    if not env_values:
        return

    if env_values.get("FRP_CONFIG_PATH"):
        app.config["FRP_CONFIG_PATH"] = Path(env_values["FRP_CONFIG_PATH"])
    if env_values.get("FRPC_BINARY"):
        app.config["FRPC_BINARY"] = env_values["FRPC_BINARY"]
    if env_values.get("FRPC_SERVICE"):
        app.config["FRPC_SERVICE"] = env_values["FRPC_SERVICE"]
    if "FRP_GUI_ALLOW_SYSTEMCTL" in env_values:
        app.config["ALLOW_SYSTEMCTL"] = env_values["FRP_GUI_ALLOW_SYSTEMCTL"] == "1"
    if "FRP_GUI_PASSWORD" in env_values:
        app.config["ADMIN_PASSWORD"] = env_values["FRP_GUI_PASSWORD"]
    if env_values.get("FRP_GUI_HOST"):
        app.config["FRP_GUI_HOST"] = env_values["FRP_GUI_HOST"]
    if env_values.get("FRP_GUI_PORT"):
        app.config["FRP_GUI_PORT"] = _env_int(env_values["FRP_GUI_PORT"], app.config["FRP_GUI_PORT"])
    if env_values.get("FRP_GUI_PUBLIC_PORT"):
        app.config["FRP_GUI_PUBLIC_PORT"] = _env_int(env_values["FRP_GUI_PUBLIC_PORT"], app.config["FRP_GUI_PUBLIC_PORT"])
    if env_values.get("FRP_GUI_SERVER_NAME"):
        app.config["FRP_GUI_SERVER_NAME"] = env_values["FRP_GUI_SERVER_NAME"]
    if env_values.get("FRP_GUI_NGINX_SITE_PATH"):
        app.config["NGINX_SITE_PATH"] = Path(env_values["FRP_GUI_NGINX_SITE_PATH"])


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _persist_runtime_settings(app: Flask) -> None:
    env_file = Path(app.config["ENV_FILE"])
    _update_env_value(env_file, "FRP_CONFIG_PATH", str(app.config["FRP_CONFIG_PATH"]))
    _update_env_value(env_file, "FRPC_BINARY", str(app.config["FRPC_BINARY"]))
    _update_env_value(env_file, "FRPC_SERVICE", str(app.config["FRPC_SERVICE"]))
    _update_env_value(env_file, "FRP_GUI_ALLOW_SYSTEMCTL", "1" if app.config["ALLOW_SYSTEMCTL"] else "0")


def _persist_network_settings(app: Flask) -> None:
    env_file = Path(app.config["ENV_FILE"])
    _update_env_value(env_file, "FRP_GUI_HOST", str(app.config["FRP_GUI_HOST"]))
    _update_env_value(env_file, "FRP_GUI_PORT", str(app.config["FRP_GUI_PORT"]))
    _update_env_value(env_file, "FRP_GUI_PUBLIC_PORT", str(app.config["FRP_GUI_PUBLIC_PORT"]))
    _update_env_value(env_file, "FRP_GUI_SERVER_NAME", str(app.config["FRP_GUI_SERVER_NAME"]))
    _update_env_value(env_file, "FRP_GUI_NGINX_SITE_PATH", str(app.config["NGINX_SITE_PATH"]))


def _update_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replacement = f"{key}={value}"
    updated = False
    output: list[str] = []

    for line in lines:
        if line.startswith(f"{key}="):
            output.append(replacement)
            updated = True
        else:
            output.append(line)

    if not updated:
        output.append(replacement)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def _proxy_view(proxy: dict[str, str]) -> dict[str, str]:
    view = dict(proxy)
    view["local_url"] = _local_url(proxy)
    view["domain_urls"] = _domain_urls(proxy)
    view["domain_sort"] = ", ".join(item["label"] for item in view["domain_urls"])
    return view


def _sort_proxies(proxies: list[dict[str, str]], sort_key: str, direction: str) -> list[dict[str, str]]:
    sorters = {
        "name": lambda proxy: proxy.get("name", "").lower(),
        "status": lambda proxy: (proxy.get("enabled") == "false", proxy.get("name", "").lower()),
        "ip": lambda proxy: (proxy.get("local_ip", ""), int(proxy.get("local_port") or 0), proxy.get("name", "").lower()),
        "domain": lambda proxy: (proxy.get("domain_sort", "").lower(), proxy.get("remote_port", ""), proxy.get("name", "").lower()),
    }
    key_func = sorters.get(sort_key, sorters["name"])
    return sorted(proxies, key=key_func, reverse=direction == "desc")


def _local_url(proxy: dict[str, str]) -> str:
    host = proxy.get("local_ip", "").strip()
    port = proxy.get("local_port", "").strip()
    proxy_type = proxy.get("type", "").strip().lower()
    if not host or not port:
        return ""
    scheme = "https" if proxy_type == "https" else "http"
    return f"{scheme}://{host}:{port}"


def _domain_urls(proxy: dict[str, str]) -> list[dict[str, str]]:
    proxy_type = proxy.get("type", "").strip().lower()
    domains = proxy.get("custom_domains", "").strip()
    remote_port = proxy.get("remote_port", "").strip()
    result: list[dict[str, str]] = []

    if domains:
        scheme = "https" if proxy_type == "https" else "http"
        for domain in re_split_domains(domains):
            result.append({"label": domain, "url": f"{scheme}://{domain}"})
        return result

    if remote_port:
        host = proxy.get("local_ip", "").strip()
        label = f":{remote_port}"
        result.append({"label": label, "url": f"http://{quote(host)}:{remote_port}" if host else ""})

    return result


def re_split_domains(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
