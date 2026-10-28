"""로그인 — 공용 계정 하나 (나중에 네이버 로그인 + OTP 로 바꿀 예정, docs/roadmap.md).

- 계정: LOGIN_USER(기본 netnsecu) + LOGIN_PASSWORD_HASH (.env.auth 에 해시로만 저장, `flask set-login` 으로 만듦)
  해시가 없으면 예전 방식 ACCESS_PASSWORD(평문, 로컬 개발용)로 확인한다.
- "이 기기에서 로그인 유지": 체크하면 그 브라우저만 30일 유지(쿠키), 안 하면 브라우저를 닫으면 로그아웃.
  IP 로 판단하지 않는다 — 같은 와이파이·회사망을 쓰는 다른 사람까지 들어오게 되므로.
- "모든 기기에서 로그아웃": 로그인 세대(auth_epoch)를 올려 그전에 받은 쿠키를 모두 무효로.
- 로그인 실패: 같은 IP 에서 LOGIN_LOCK_SECONDS 안에 LOGIN_MAX_ATTEMPTS 번 실패하면 잠금.
"""

import hmac
import time

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from ..services.settings import get_value, set_values

bp = Blueprint("auth", __name__)

EPOCH_KEY = "auth_epoch"

# IP별 로그인 실패 기록 {ip: [실패 시각, ...]} — 단일 프로세스 메모리 기준
_failed_attempts: dict[str, list[float]] = {}


def _is_locked(ip: str) -> bool:
    window = current_app.config["LOGIN_LOCK_SECONDS"]
    now = time.time()
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < window]
    _failed_attempts[ip] = attempts
    return len(attempts) >= current_app.config["LOGIN_MAX_ATTEMPTS"]


def _safe_next(target: str | None) -> str:
    # 외부 사이트로의 리다이렉트 방지: 같은 사이트 내 상대 경로만 허용
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("pages.index")


def check_credentials(username: str, password: str) -> bool:
    config = current_app.config
    user_ok = hmac.compare_digest(username.strip().lower().encode(), config["LOGIN_USER"].lower().encode())
    password_hash = config.get("LOGIN_PASSWORD_HASH")
    if password_hash:
        password_ok = check_password_hash(password_hash, password)
    else:
        password_ok = hmac.compare_digest(password.encode(), config["ACCESS_PASSWORD"].encode())
    return user_ok and password_ok


def current_epoch() -> str:
    return str(get_value(EPOCH_KEY, "0"))


def session_is_valid() -> bool:
    return bool(session.get("authenticated")) and session.get("epoch", "0") == current_epoch()


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session_is_valid():
        return redirect(url_for("pages.index"))

    ip = request.remote_addr or "unknown"
    if request.method == "POST":
        if _is_locked(ip):
            minutes = current_app.config["LOGIN_LOCK_SECONDS"] // 60
            flash(f"로그인 시도가 너무 많습니다. {minutes}분 뒤에 다시 시도하세요.", "error")
            return render_template("login.html"), 429

        if check_credentials(request.form.get("username", ""), request.form.get("password", "")):
            _failed_attempts.pop(ip, None)
            session.clear()
            session["authenticated"] = True
            session["epoch"] = current_epoch()
            # 체크: 이 브라우저에 30일 유지 / 안 함: 브라우저를 닫으면 끝 (공용 PC)
            session.permanent = request.form.get("remember") == "1"
            return redirect(_safe_next(request.args.get("next")))

        _failed_attempts.setdefault(ip, []).append(time.time())
        flash("아이디 또는 암호가 올바르지 않습니다.", "error")
        return render_template("login.html"), 401

    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/logout/all", methods=["POST"])
def logout_all():
    """다른 기기(공용 PC 등)에 남은 로그인까지 모두 끊는다."""
    set_values({EPOCH_KEY: str(int(current_epoch() or "0") + 1)})
    session.clear()
    flash("모든 기기에서 로그아웃했습니다.", "info")
    return redirect(url_for("auth.login"))
