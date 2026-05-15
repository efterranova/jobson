"""Auth simple para la app del revisor: sessions firmadas + hash de password.

Diseño minimal sin librerías extra: usa flask.session (cookie firmada con SECRET_KEY)
y werkzeug.security.{generate,check}_password_hash.

Roles:
    admin     → todo (incl. sincronización a WP, gestión revisores)
    reviewer  → solo aprobar/rechazar pending

Uso:
    from jobson.web.auth import login_required, admin_required, current_user
    @app.get("/review")
    @login_required
    def review_page(): ...
"""
from __future__ import annotations

import os
from functools import wraps
from typing import Any, Callable

from flask import abort, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from jobson.storage.base import BaseRepository


SESSION_KEY = "jobson_user"


def hash_password(plain: str) -> str:
    return generate_password_hash(plain, method="pbkdf2:sha256", salt_length=16)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return check_password_hash(hashed, plain)
    except Exception:  # noqa: BLE001
        return False


def authenticate(repo: BaseRepository, email: str, password: str) -> dict[str, Any] | None:
    user = repo.get_reviewer_by_email(email)
    if not user:
        return None
    if not verify_password(password, user.get("password_hash", "")):
        return None
    return user


def login_user(user: dict[str, Any]) -> None:
    session[SESSION_KEY] = {
        "id":    str(user.get("id")),
        "email": user.get("email"),
        "name":  user.get("name") or user.get("email"),
        "role":  user.get("role") or "reviewer",
    }
    session.permanent = True


def logout_user() -> None:
    session.pop(SESSION_KEY, None)


def current_user() -> dict[str, Any] | None:
    return session.get(SESSION_KEY)


def login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login_page", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login_page", next=request.path))
        if user.get("role") != "admin":
            return abort(403)
        return view(*args, **kwargs)
    return wrapper


def get_secret_key() -> str:
    """Devuelve FLASK_SECRET_KEY del env. Si no hay, genera una temporal
    para dev (cambia tras reiniciar — fuerza re-login)."""
    key = os.getenv("FLASK_SECRET_KEY", "").strip()
    if key:
        return key
    import secrets
    return secrets.token_urlsafe(32)
