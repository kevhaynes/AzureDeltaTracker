"""Local admin and Entra ID (OIDC auth code flow) authentication."""
import functools
import hmac
import logging
import secrets
from urllib.parse import quote

import msal
from flask import (Blueprint, current_app, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash

log = logging.getLogger(__name__)
bp = Blueprint("auth", __name__)


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication required"}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _check_csrf():
    token = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
    expected = session.get("csrf", "")
    return bool(token and expected) and hmac.compare_digest(token, expected)


def _safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("main.index")


def _verify_local(username, password):
    cfg = current_app.config
    if not cfg["LOCAL_ADMIN_ENABLED"]:
        return False
    if not hmac.compare_digest(username.encode(), cfg["LOCAL_ADMIN_USERNAME"].encode()):
        return False
    if cfg["LOCAL_ADMIN_PASSWORD_HASH"]:
        return check_password_hash(cfg["LOCAL_ADMIN_PASSWORD_HASH"], password)
    if cfg["LOCAL_ADMIN_PASSWORD"]:
        return hmac.compare_digest(password.encode(), cfg["LOCAL_ADMIN_PASSWORD"].encode())
    return False


def _msal_app():
    cfg = current_app.config
    return msal.ConfidentialClientApplication(
        cfg["ENTRA_CLIENT_ID"],
        client_credential=cfg["ENTRA_CLIENT_SECRET"],
        authority=f"{cfg['AZURE_AUTHORITY_HOST'].rstrip('/')}/{cfg['ENTRA_TENANT_ID']}",
    )


def _redirect_uri():
    cfg = current_app.config
    return cfg["ENTRA_REDIRECT_URI"] or url_for("auth.callback", _external=True)


@bp.route("/login", methods=["GET", "POST"])
def login():
    cfg = current_app.config
    if request.method == "POST":
        if not _check_csrf():
            flash("Your session expired, please try again.", "error")
            return redirect(url_for("auth.login"))
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if _verify_local(username, password):
            session.clear()
            session["user"] = {"name": username, "upn": username, "source": "local"}
            log.info("Local login succeeded for %s", username)
            return redirect(_safe_next(request.args.get("next")))
        log.warning("Local login failed for %s from %s", username, request.remote_addr)
        flash("Invalid username or password.", "error")
    return render_template(
        "login.html",
        local_enabled=cfg["LOCAL_ADMIN_ENABLED"],
        entra_enabled=cfg["ENTRA_AUTH_ENABLED"],
        next=request.args.get("next", ""),
    )


@bp.route("/auth/entra")
def entra_login():
    if not current_app.config["ENTRA_AUTH_ENABLED"]:
        return redirect(url_for("auth.login"))
    flow = _msal_app().initiate_auth_code_flow(["User.Read"], redirect_uri=_redirect_uri())
    session["auth_flow"] = flow
    session["auth_next"] = request.args.get("next", "")
    return redirect(flow["auth_uri"])


@bp.route("/auth/callback")
def callback():
    if not current_app.config["ENTRA_AUTH_ENABLED"]:
        return redirect(url_for("auth.login"))
    flow = session.pop("auth_flow", None)
    if not flow:
        flash("Sign-in session expired, please try again.", "error")
        return redirect(url_for("auth.login"))
    result = _msal_app().acquire_token_by_auth_code_flow(flow, request.args.to_dict())
    if "error" in result:
        log.warning("Entra sign-in failed: %s", result.get("error_description"))
        flash(f"Sign-in failed: {result.get('error_description', result['error'])}", "error")
        return redirect(url_for("auth.login"))

    claims = result.get("id_token_claims", {})
    cfg = current_app.config
    groups = set(claims.get("groups", []))
    roles = set(claims.get("roles", []))
    if cfg["ENTRA_ALLOWED_GROUPS"] or cfg["ENTRA_ALLOWED_ROLES"]:
        if not (groups & set(cfg["ENTRA_ALLOWED_GROUPS"]) or roles & set(cfg["ENTRA_ALLOWED_ROLES"])):
            log.warning("Entra user %s denied: not in an allowed group/role", claims.get("preferred_username"))
            flash("Your account is not authorised to use this application.", "error")
            return redirect(url_for("auth.login"))

    next_url = session.pop("auth_next", "")
    session.clear()
    session["user"] = {
        "name": claims.get("name") or claims.get("preferred_username"),
        "upn": claims.get("preferred_username"),
        "source": "entra",
    }
    return redirect(_safe_next(next_url))


@bp.route("/logout", methods=["POST"])
def logout():
    if not _check_csrf():
        return redirect(url_for("main.index"))
    source = (session.get("user") or {}).get("source")
    session.clear()
    cfg = current_app.config
    if source == "entra":
        return redirect(
            f"{cfg['AZURE_AUTHORITY_HOST'].rstrip('/')}/{cfg['ENTRA_TENANT_ID']}/oauth2/v2.0/logout"
            f"?post_logout_redirect_uri={quote(url_for('auth.login', _external=True), safe='')}"
        )
    return redirect(url_for("auth.login"))
