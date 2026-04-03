"""Authentication and authorization middleware."""

import functools
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from flask import request, jsonify, session, redirect, url_for

from config import cfg


def check_session():
    """Return True if the current session is authenticated."""
    if not cfg.AUTH_ENABLED:
        return True
    return session.get("authenticated") is True and session.get("expires_at", "") > datetime.now(timezone.utc).isoformat()


def require_auth(f):
    """Decorator: require authentication for a route."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not cfg.AUTH_ENABLED:
            return f(*args, **kwargs)

        # Check session auth (browser)
        if check_session():
            return f(*args, **kwargs)

        # Check API key auth (programmatic)
        api_key = request.headers.get("X-Admin-Key")
        if api_key and secrets.compare_digest(api_key, cfg.SECRET_KEY):
            return f(*args, **kwargs)

        # JSON request → 401, browser → redirect to login
        if request.is_json or request.path.startswith("/api"):
            return jsonify({"error": "Authentication required"}), 401
        return redirect(url_for("login_page"))

    return wrapper


def login_user():
    """Validate credentials and create session. Returns (success, error)."""
    data = request.form if request.form else (request.json or {})
    username = data.get("username", "")
    password = data.get("password", "")

    if not secrets.compare_digest(username, cfg.ADMIN_USERNAME):
        return False, "Invalid credentials"
    if not secrets.compare_digest(password, cfg.ADMIN_PASSWORD):
        return False, "Invalid credentials"

    session["authenticated"] = True
    session["username"] = username
    session["expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=cfg.SESSION_LIFETIME_HOURS)).isoformat()
    return True, None


def logout_user():
    session.clear()
