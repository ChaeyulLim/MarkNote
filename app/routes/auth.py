"""로그인 — 계정마다 따로 (services/accounts.py, 계정·잠금 기록은 SQLite).

- 계정은 관리자가 서버에서 `flask --app run add-user <아이디>` 로 만든다 (가입 화면 없음).
- 로그인하면 그 계정의 노트·그림·대화만 보인다 (<DATA_DIR>/users/<아이디>/).
- "이 기기에서 로그인 유지": 체크하면 그 브라우저만 30일 유지(쿠키), 안 하면 브라우저를 닫으면 로그아웃.
  IP 로 판단하지 않는다 — 같은 와이파이·회사망을 쓰는 다른 사람까지 들어오게 되므로.
- "모든 기기에서 로그아웃": 그 계정의 로그인 세대(auth_epoch)를 올려 그전에 받은 쿠키를 모두 무효로.
- 로그인 실패: 같은 IP 에서 LOGIN_LOCK_SECONDS 안에 LOGIN_MAX_ATTEMPTS 번 실패하면 잠금.
"""

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for

from ..services import accounts

bp = Blueprint("auth", __name__)


def _safe_next(target: str | None) -> str:
    # 외부 사이트로의 리다이렉트 방지: 같은 사이트 내 상대 경로만 허용
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("pages.index")


def current_user():
    """세션의 계정 (없거나, 지워졌거나, "모든 기기에서 로그아웃" 전에 받은 쿠키면 None)."""
    user_id = session.get("uid")
    user = accounts.get(user_id) if user_id is not None else None
    if user is None or session.get("epoch") != user["auth_epoch"]:
        return None
    return user


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user() is not None:
        return redirect(url_for("pages.index"))

    ip = request.remote_addr or "unknown"
    if request.method == "POST":
        if accounts.is_locked(ip):
            minutes = current_app.config["LOGIN_LOCK_SECONDS"] // 60
            flash(f"로그인 시도가 너무 많습니다. {minutes}분 뒤에 다시 시도하세요.", "error")
            return render_template("login.html"), 429

        user = accounts.authenticate(request.form.get("username", ""), request.form.get("password", ""))
        if user is not None:
            accounts.clear_failures(ip)
            session.clear()
            session["uid"] = user["id"]
            session["epoch"] = user["auth_epoch"]
            # 체크: 이 브라우저에 30일 유지 / 안 함: 브라우저를 닫으면 끝 (공용 PC)
            session.permanent = request.form.get("remember") == "1"
            return redirect(_safe_next(request.args.get("next")))

        accounts.record_failure(ip)
        flash("아이디 또는 암호가 올바르지 않습니다.", "error")
        return render_template("login.html"), 401

    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/logout/all", methods=["POST"])
def logout_all():
    """이 계정으로 다른 기기(공용 PC 등)에 남은 로그인까지 모두 끊는다."""
    accounts.bump_epoch(g.user["id"])
    session.clear()
    flash("모든 기기에서 로그아웃했습니다.", "info")
    return redirect(url_for("auth.login"))
