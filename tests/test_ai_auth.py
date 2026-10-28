import os
import sys
import time

import pytest

from app.services import claude_auth, claude_runner

FAKE = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_claude.py")]
REMOTE = {"REMOTE_ADDR": "192.168.0.10"}


@pytest.fixture
def auth_env(app, auth_client, monkeypatch, tmp_path):
    """로그아웃 상태의 가짜 claude. state 파일이 생기면 로그인된 것으로 본다."""
    state = tmp_path / "claude-login"
    monkeypatch.setenv("FAKE_CLAUDE_STATE", str(state))
    monkeypatch.setattr(claude_runner, "_status_cache", None)
    monkeypatch.setattr(claude_auth, "_job", None)
    app.config["CLAUDE_COMMAND"] = FAKE
    yield auth_client, state
    claude_auth.cancel_login()


def _wait_for(client, states, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = client.get("/api/ai/login").get_json()
        if info["state"] in states:
            return info
        time.sleep(0.1)
    raise AssertionError(f"login state never reached {states}: {info}")


def test_login_shows_link_then_connects(auth_env):
    client, state = auth_env
    res = client.post("/api/ai/login").get_json()
    assert res["state"] == "waiting"
    assert res["url"] == "https://claude.ai/oauth/authorize?client_id=fake&state=xyz"

    (state.parent / (state.name + ".approve")).touch()  # 브라우저에서 로그인 완료
    assert _wait_for(client, {"done"})["message"] == "Claude 계정이 연결되었습니다."
    assert client.get("/api/ai/status").get_json()["state"] == "ready"


def test_login_refused_from_remote_computer(auth_env):
    client, _ = auth_env
    res = client.post("/api/ai/login", environ_base=REMOTE).get_json()
    assert res["state"] == "remote"
    assert res["url"] is None
    assert claude_auth.login_state()["state"] == "idle"  # claude 를 실행하지도 않음


@pytest.mark.parametrize(
    "host, remote_addr, same",
    [
        ("localhost:5000", "127.0.0.1", True),
        ("127.0.0.1:5000", "127.0.0.1", True),
        ("[::1]:5000", "::1", True),
        ("notes.example.ts.net", "127.0.0.1", False),  # 리버스 프록시 뒤 (외부 주소로 접속)
        ("localhost:5000", "192.168.0.10", False),
    ],
)
def test_same_computer_detection(app, host, remote_addr, same):
    from app.routes.ai import _is_same_computer

    with app.test_request_context("/api/ai/login", headers={"Host": host}, environ_base={"REMOTE_ADDR": remote_addr}):
        assert _is_same_computer() is same


def test_login_when_already_connected(auth_env):
    client, state = auth_env
    state.touch()
    assert client.post("/api/ai/login").get_json()["state"] == "already"


def test_login_ignores_links_to_other_sites(auth_env, monkeypatch):
    client, _ = auth_env
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "evil_url")
    monkeypatch.setattr(claude_auth, "LINK_WAIT", 1)
    res = client.post("/api/ai/login").get_json()
    assert res["state"] == "starting"
    assert res["url"] is None


def test_login_cancel(auth_env):
    client, _ = auth_env
    client.post("/api/ai/login")
    res = client.post("/api/ai/login/cancel").get_json()
    assert res["state"] == "cancelled"
    assert client.get("/api/ai/status?refresh=1").get_json()["state"] == "logged_out"


def test_second_login_request_reuses_running_job(auth_env):
    client, _ = auth_env
    first = client.post("/api/ai/login").get_json()
    second = client.post("/api/ai/login").get_json()
    assert first["url"] == second["url"]
    assert second["state"] == "waiting"


def test_logout(auth_env):
    client, state = auth_env
    state.touch()
    assert client.get("/api/ai/status?refresh=1").get_json()["state"] == "ready"
    res = client.post("/api/ai/logout").get_json()
    assert res["ok"] is True
    assert res["status"]["state"] == "logged_out"
    assert not state.exists()


def test_logout_warns_when_env_token_keeps_connection(auth_env, monkeypatch):
    client, _ = auth_env
    monkeypatch.setattr(
        claude_runner, "status", lambda configured=None, refresh=False: {"state": "ready", "auth_method": "oauth_token"}
    )
    res = client.post("/api/ai/logout").get_json()
    assert "CLAUDE_CODE_OAUTH_TOKEN" in res["message"]


def test_login_requires_app_login(client):
    assert client.post("/api/ai/login").status_code == 401
    assert client.post("/api/ai/logout").status_code == 401
