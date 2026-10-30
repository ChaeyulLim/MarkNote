import os

import pytest

from app import create_app
from app.services import accounts, chat_runs
from conftest import make_config


@pytest.fixture
def second(app):
    with app.app_context():
        accounts.create("mina", "mina-pw-123")
    client = app.test_client()
    assert client.post("/login", data={"username": "mina", "password": "mina-pw-123"}).status_code == 302
    return client


def _titles(client):
    def walk(nodes):
        for node in nodes:
            yield node["name"]
            yield from walk(node.get("children") or [])

    return set(walk(client.get("/api/tree").get_json()["nodes"]))


def test_each_account_sees_only_its_own_notes(app, auth_client, second):
    auth_client.post("/api/pages", json={"title": "관리자 노트"})
    second.post("/api/pages", json={"title": "미나 노트"})
    assert _titles(auth_client) == {"관리자 노트"}
    assert _titles(second) == {"미나 노트"}
    assert os.path.isfile(os.path.join(app.config["DATA_DIR"], "users", "mina", "notes", "미나 노트.md"))

    # 다른 계정의 노트 번호로 열어도 보이지 않는다 (번호는 계정마다 따로)
    mine = auth_client.post("/api/pages", json={"title": "비밀"}).get_json()["page"]
    other = second.get(f"/api/pages/{mine['id']}/source")
    assert other.status_code == 404 or "비밀" not in other.get_data(as_text=True)
    assert second.get(f"/p/{mine['id']}").status_code != 200 or "비밀" not in second.get(f"/p/{mine['id']}").get_data(as_text=True)


def test_settings_are_per_account(app, auth_client, second):
    from app.services.ai_usage import MODEL_CHOICES

    model = next(v for v, _ in MODEL_CHOICES if v)
    auth_client.post("/api/ai/prefs", json={"model": model})
    users = os.path.join(app.config["DATA_DIR"], "users")
    assert os.path.isfile(os.path.join(users, "netnsecu", "settings.json"))
    assert not os.path.exists(os.path.join(users, "mina", "settings.json"))


def test_claude_login_is_admin_only(auth_client, second):
    assert second.post("/api/ai/login").status_code == 403
    assert second.post("/api/ai/logout").status_code == 403
    assert second.post("/api/ai/login/cancel").status_code == 403
    assert auth_client.post("/api/ai/login").status_code != 403


def test_other_accounts_run_is_hidden(app, auth_client, second):
    run = chat_runs.Run("run-of-admin", "conv")
    with app.app_context():
        run.owner = accounts.by_name("netnsecu")["id"]
    chat_runs._runs[run.id] = run
    try:
        assert second.get(f"/api/ai/runs/{run.id}/events").status_code == 404
    finally:
        chat_runs._runs.pop(run.id, None)


def test_password_change_ends_other_logins(app, second):
    with app.app_context():
        accounts.set_password("mina", "new-pw-4567")
    assert second.get("/").status_code == 302


def test_lockout_survives_restart(tmp_path):
    config = make_config(tmp_path / "data")
    client = create_app(config).test_client()
    for _ in range(5):
        client.post("/login", data={"username": "netnsecu", "password": "wrong"})
    again = create_app(config).test_client()  # 서버를 다시 켜도 잠금 기록은 DB 에 남는다
    assert again.post("/login", data={"username": "netnsecu", "password": "temp"}).status_code == 429


def test_account_rules(app):
    with app.app_context():
        with pytest.raises(accounts.AccountError):
            accounts.create("mina", "short")
        with pytest.raises(accounts.AccountError):
            accounts.create("../evil", "long-enough-pw")
        accounts.create("Mina", "long-enough-pw")
        with pytest.raises(accounts.AccountError):
            accounts.create("mina", "long-enough-pw")
        with pytest.raises(accounts.AccountError):
            accounts.remove("netnsecu")  # 마지막 관리자
        accounts.remove("mina")
        assert [u["username"] for u in accounts.all_users()] == ["netnsecu"]


def test_first_start_moves_old_data_after_backup(tmp_path):
    data = tmp_path / "data"
    (data / "notes" / "회사").mkdir(parents=True)
    (data / "notes" / "회사" / "회의.md").write_text("예전 노트", encoding="utf-8")
    (data / "settings.json").write_text('{"ai_model": "opus"}', encoding="utf-8")
    # 계정 나누기 전 위치 (app.config 기본값) 그대로
    config = make_config(data, **{key: None for key in ("NOTES_DIR", "UPLOAD_FOLDER", "AI_CHAT_DIR", "DECKS_DIR",
                                                        "AI_EDITS_DIR", "SETTINGS_FILE", "USAGE_FILE")})
    app = create_app(config)

    moved = data / "users" / "netnsecu" / "notes" / "회사" / "회의.md"
    assert moved.read_text(encoding="utf-8") == "예전 노트"
    assert (data / "users" / "netnsecu" / "settings.json").exists()
    assert not (data / "notes").exists()
    backups = list((data / "backups").iterdir())
    assert len(backups) == 1 and (backups[0] / "notes" / "회사" / "회의.md").exists()

    client = app.test_client()
    client.post("/login", data={"username": "netnsecu", "password": "temp"})
    assert "회의" in str(client.get("/api/tree").get_json())

    create_app(config)  # 두 번째로 켤 때는 아무것도 옮기지 않는다
    assert len(list((data / "backups").iterdir())) == 1


def test_cli_add_user(app):
    from unittest import mock

    runner = app.test_cli_runner()
    with mock.patch("getpass.getpass", side_effect=["mina-pw-123", "mina-pw-123"]):
        result = runner.invoke(args=["add-user", "mina"])
    assert result.exit_code == 0, result.output
    listed = runner.invoke(args=["list-users"]).output
    assert "netnsecu" in listed and "mina" in listed
    client = app.test_client()
    assert client.post("/login", data={"username": "mina", "password": "mina-pw-123"}).status_code == 302
