"""계정 — SQLite(<DATA_DIR>/accounts.db)에는 계정과 로그인 실패 기록만 둔다. 노트는 계정마다 파일 (userdata.py).

- 가입 화면은 없다. 관리자가 서버에서 `flask --app run add-user <아이디>` 로 만든다.
- 비밀번호는 해시로만 저장한다. 관리자도 앱에서 다른 계정의 노트를 볼 수 없다 (로그인한 계정의 폴더만 연다).
- "모든 기기에서 로그아웃" = 그 계정의 로그인 세대(auth_epoch)를 올려 전에 받은 쿠키를 모두 무효로.
- 로그인 실패는 IP 별로 기록해 잠근다 (서버를 다시 켜도, 프로세스가 여러 개여도 같은 기록).
- 처음 켤 때 계정이 하나도 없으면 LOGIN_USER(.env.auth 의 해시, 없으면 ACCESS_PASSWORD)로 관리자 계정을 만들고,
  예전 위치(<DATA_DIR>/notes 등)의 노트·그림·대화를 백업한 뒤 그 계정 폴더로 옮긴다.
"""

import os
import re
import shutil
import sqlite3
import time
from contextlib import closing
from datetime import datetime

from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from . import userdata

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")
MIN_PASSWORD = 8
_DUMMY_HASH = generate_password_hash("not-a-real-password")  # 없는 아이디도 같은 시간이 걸리게

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    auth_epoch INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    ip TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS login_failures_ip ON login_failures (ip, at);
"""


class AccountError(ValueError):
    pass


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(current_app.config["ACCOUNTS_DB"], timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _run(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with closing(_connect()) as conn, conn:
        return conn.execute(sql, args).fetchall()


def normalize(username: str) -> str:
    name = (username or "").strip().lower()
    if not USERNAME_RE.match(name):
        raise AccountError("아이디는 영문 소문자·숫자·._- 로 2~32자, 첫 글자는 영문이나 숫자여야 합니다.")
    return name


# ---------- 계정 ----------

def get(user_id) -> sqlite3.Row | None:
    rows = _run("SELECT * FROM users WHERE id = ?", (user_id,))
    return rows[0] if rows else None


def by_name(username: str) -> sqlite3.Row | None:
    rows = _run("SELECT * FROM users WHERE username = ?", ((username or "").strip().lower(),))
    return rows[0] if rows else None


def all_users() -> list[sqlite3.Row]:
    return _run("SELECT * FROM users ORDER BY id")


def _insert(username: str, password_hash: str, is_admin: bool) -> sqlite3.Row:
    try:
        _run(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, int(is_admin), datetime.now().isoformat(timespec="seconds")),
        )
    except sqlite3.IntegrityError:
        raise AccountError(f'"{username}" 계정이 이미 있습니다.') from None
    os.makedirs(userdata.user_root(username), exist_ok=True)
    return by_name(username)


def create(username: str, password: str, is_admin: bool = False) -> sqlite3.Row:
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(f"비밀번호는 {MIN_PASSWORD}자 이상이어야 합니다.")
    return _insert(normalize(username), generate_password_hash(password), is_admin)


def set_password(username: str, password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(f"비밀번호는 {MIN_PASSWORD}자 이상이어야 합니다.")
    user = by_name(username)
    if user is None:
        raise AccountError(f'"{username}" 계정이 없습니다.')
    # 비밀번호를 바꾸면 다른 기기의 로그인도 끝낸다
    _run("UPDATE users SET password_hash = ?, auth_epoch = auth_epoch + 1 WHERE id = ?", (generate_password_hash(password), user["id"]))


def remove(username: str) -> None:
    """계정만 지운다 — 노트 폴더(<DATA_DIR>/users/<아이디>/)는 그대로 남는다."""
    user = by_name(username)
    if user is None:
        raise AccountError(f'"{username}" 계정이 없습니다.')
    if user["is_admin"] and sum(u["is_admin"] for u in all_users()) == 1:
        raise AccountError("마지막 관리자 계정은 지울 수 없습니다.")
    _run("DELETE FROM users WHERE id = ?", (user["id"],))


def authenticate(username: str, password: str) -> sqlite3.Row | None:
    user = by_name(username)
    if user is None:
        check_password_hash(_DUMMY_HASH, password or "")
        return None
    return user if check_password_hash(user["password_hash"], password or "") else None


def bump_epoch(user_id: int) -> None:
    _run("UPDATE users SET auth_epoch = auth_epoch + 1 WHERE id = ?", (user_id,))


# ---------- 로그인 실패 잠금 ----------

def is_locked(ip: str) -> bool:
    config = current_app.config
    since = time.time() - config["LOGIN_LOCK_SECONDS"]
    _run("DELETE FROM login_failures WHERE at < ?", (since,))
    count = _run("SELECT COUNT(*) AS n FROM login_failures WHERE ip = ? AND at >= ?", (ip, since))[0]["n"]
    return count >= config["LOGIN_MAX_ATTEMPTS"]


def record_failure(ip: str) -> None:
    _run("INSERT INTO login_failures (ip, at) VALUES (?, ?)", (ip, time.time()))


def clear_failures(ip: str) -> None:
    _run("DELETE FROM login_failures WHERE ip = ?", (ip,))


# ---------- 처음 켤 때 ----------

def init(app) -> None:
    """표를 만들고, 계정이 하나도 없으면 관리자 계정 + 예전 데이터 옮기기."""
    with app.app_context():
        with closing(_connect()) as conn, conn:
            conn.executescript(SCHEMA)
        if all_users():
            return
        config = app.config
        username = normalize(config["LOGIN_USER"])
        password_hash = config.get("LOGIN_PASSWORD_HASH") or generate_password_hash(config["ACCESS_PASSWORD"])
        _insert(username, password_hash, is_admin=True)
        moved = migrate_legacy(username)
        if moved:
            app.logger.warning("예전 데이터를 관리자 계정 %s 의 폴더로 옮겼습니다 (백업: %s)", username, moved)


def migrate_legacy(username: str) -> str | None:
    """예전 위치(계정 나누기 전)의 노트·그림·대화 등을 백업한 뒤 그 계정 폴더로 옮긴다. 돌려줌: 백업 폴더 (옮긴 게 없으면 None)."""
    config = current_app.config
    target = userdata.paths_for(username)
    sources = {
        key: config[key]
        for key in userdata.KEYS
        if config.get(key) and os.path.exists(config[key]) and not os.path.exists(target[key]) and _has_content(config[key])
    }
    if not sources:
        return None
    backup = os.path.join(config["DATA_DIR"], "backups", f"before-accounts-{datetime.now():%Y%m%d-%H%M%S}")
    os.makedirs(backup, exist_ok=True)
    for key, src in sources.items():
        dest = os.path.join(backup, userdata.KEYS[key])
        if os.path.isdir(src):
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
    for key, src in sources.items():
        os.makedirs(os.path.dirname(target[key]), exist_ok=True)
        shutil.move(src, target[key])
    return backup


def _has_content(path: str) -> bool:
    return os.path.isfile(path) or bool(os.listdir(path))
