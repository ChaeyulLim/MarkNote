"""관리 명령.

`flask --app run set-login` — 로그인 계정을 .env.auth 에 저장 (비밀번호는 해시로만)
"""

import getpass
import os

import click
from werkzeug.security import generate_password_hash

ENV_AUTH = ".env.auth"


def write_env_auth(path: str, username: str, password: str) -> None:
    password_hash = generate_password_hash(password)
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "# MarkNote 로그인 계정 — git·백업 공유 금지. 바꾸려면 `flask --app run set-login`\n"
            f"LOGIN_USER={username}\n"
            # $ 가 들어 있어 작은따옴표로 감쌈 (dotenv 가 변수로 해석하지 않게)
            f"LOGIN_PASSWORD_HASH='{password_hash}'\n"
        )
    try:
        os.chmod(path, 0o600)  # 서버(리눅스)에서 다른 사용자가 못 읽게
    except OSError:
        pass


def register_cli(app):
    @app.cli.command("set-login")
    @click.option("--user", default="netnsecu", show_default=True, help="로그인 아이디")
    def set_login(user):
        """로그인 아이디·비밀번호를 .env.auth 에 저장 (비밀번호는 화면에 안 보이게 입력)."""
        password = getpass.getpass("새 비밀번호: ")
        if len(password) < 8 or password != getpass.getpass("한 번 더: "):
            raise click.ClickException("8자 이상, 두 번 똑같이 입력하세요.")
        write_env_auth(ENV_AUTH, user, password)
        click.echo(f"{ENV_AUTH} 에 저장했습니다. 서버를 다시 시작하면 적용됩니다.")
