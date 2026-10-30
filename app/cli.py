"""관리 명령 (서버에서 실행 — 가입 화면은 없다).

`flask --app run add-user <아이디> [--admin]`  계정 만들기 (비밀번호는 화면에 안 보이게 입력)
`flask --app run set-password <아이디>`        비밀번호 바꾸기 (그 계정의 다른 기기 로그인도 끝남)
`flask --app run list-users`                   계정 목록
`flask --app run remove-user <아이디>`         계정 지우기 (노트 폴더는 남는다)
`flask --app run set-login`                    처음 켤 때 만들 관리자 계정을 .env.auth 에 저장 (계정이 이미 있으면 안 씀)
"""

import getpass
import os

import click
from werkzeug.security import generate_password_hash

from .services import accounts

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


def _ask_password() -> str:
    password = getpass.getpass("새 비밀번호: ")
    if len(password) < accounts.MIN_PASSWORD or password != getpass.getpass("한 번 더: "):
        raise click.ClickException(f"{accounts.MIN_PASSWORD}자 이상, 두 번 똑같이 입력하세요.")
    return password


def register_cli(app):
    @app.cli.command("add-user")
    @click.argument("username")
    @click.option("--admin", is_flag=True, help="관리자 (Claude 로그인을 바꿀 수 있음)")
    def add_user(username, admin):
        """계정 만들기. 그 계정은 자기 노트만 본다."""
        try:
            user = accounts.create(username, _ask_password(), is_admin=admin)
        except accounts.AccountError as err:
            raise click.ClickException(str(err)) from None
        click.echo(f'"{user["username"]}" 계정을 만들었습니다{" (관리자)" if admin else ""}.')

    @app.cli.command("set-password")
    @click.argument("username")
    def set_password(username):
        """비밀번호 바꾸기 — 그 계정으로 다른 기기에 남은 로그인도 끝난다."""
        try:
            accounts.set_password(username, _ask_password())
        except accounts.AccountError as err:
            raise click.ClickException(str(err)) from None
        click.echo("비밀번호를 바꿨습니다.")

    @app.cli.command("list-users")
    def list_users():
        """계정 목록."""
        for user in accounts.all_users():
            click.echo(f'{user["username"]:<32} {"관리자" if user["is_admin"] else "-":<4} {user["created_at"]}')

    @app.cli.command("remove-user")
    @click.argument("username")
    @click.confirmation_option(prompt="계정을 지울까요? (노트 폴더는 서버에 남습니다)")
    def remove_user(username):
        """계정 지우기. 노트 폴더(<DATA_DIR>/users/<아이디>/)는 그대로 둔다."""
        try:
            accounts.remove(username)
        except accounts.AccountError as err:
            raise click.ClickException(str(err)) from None
        click.echo("계정을 지웠습니다.")

    @app.cli.command("set-login")
    @click.option("--user", default="netnsecu", show_default=True, help="로그인 아이디")
    def set_login(user):
        """처음 켤 때 만들 관리자 계정을 .env.auth 에 저장. 계정이 이미 있으면 set-password 를 쓰세요."""
        password = _ask_password()
        write_env_auth(ENV_AUTH, user, password)
        click.echo(f"{ENV_AUTH} 에 저장했습니다. 서버를 다시 시작하면 적용됩니다.")
