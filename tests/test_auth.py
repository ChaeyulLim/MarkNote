def test_redirects_to_login_when_not_authenticated(client):
    res = client.get("/")
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]


def test_api_requires_login(client):
    res = client.post("/api/pages", json={})
    assert res.status_code == 401


def test_files_require_login(client):
    assert client.get("/files/abc").status_code == 302


def test_wrong_password_rejected(client):
    res = client.post("/login", data={"username": "netnsecu", "password": "wrong"})
    assert res.status_code == 401
    assert client.get("/").status_code == 302


def test_correct_password_grants_access(client):
    res = client.post("/login?next=/", data={"username": "netnsecu", "password": "temp"})
    assert res.status_code == 302
    assert client.get("/").status_code == 200


def test_logout_revokes_access(client):
    client.post("/login", data={"username": "netnsecu", "password": "temp"})
    client.post("/logout")
    assert client.get("/").status_code == 302


def test_external_next_is_ignored(client):
    res = client.post("/login?next=//evil.com", data={"username": "netnsecu", "password": "temp"})
    assert res.headers["Location"] == "/"


def test_lockout_after_too_many_failures(client):
    for _ in range(5):
        client.post("/login", data={"username": "netnsecu", "password": "wrong"})
    res = client.post("/login", data={"username": "netnsecu", "password": "temp"})
    assert res.status_code == 429


def test_username_required_and_hash_login(app, client):
    from werkzeug.security import generate_password_hash

    assert client.post("/login", data={"username": "other", "password": "temp"}).status_code == 401
    app.config["LOGIN_PASSWORD_HASH"] = generate_password_hash("s3cret-pw")
    assert client.post("/login", data={"username": "netnsecu", "password": "temp"}).status_code == 401  # 해시가 있으면 평문 암호 안 됨
    res = client.post("/login", data={"username": "NetnSecu", "password": "s3cret-pw"})
    assert res.status_code == 302


def test_remember_device_and_logout_all(app):
    a, b = app.test_client(), app.test_client()
    a.post("/login", data={"username": "netnsecu", "password": "temp", "remember": "1"})
    b.post("/login", data={"username": "netnsecu", "password": "temp"})
    assert "Expires=" in (a.get_cookie("session").expires and "Expires=" or "")
    assert b.get_cookie("session").expires is None  # 공용 PC: 브라우저를 닫으면 끝
    assert a.get("/").status_code == 200 and b.get("/").status_code == 200

    a.post("/logout/all")
    assert b.get("/").status_code == 302  # 다른 기기도 끊김
    assert a.get("/").status_code == 302


def test_write_env_auth(tmp_path):
    from dotenv import dotenv_values
    from werkzeug.security import check_password_hash

    from app.cli import write_env_auth

    path = tmp_path / ".env.auth"
    write_env_auth(str(path), "netnsecu", "pw-12345678")
    values = dotenv_values(path)
    assert values["LOGIN_USER"] == "netnsecu" and check_password_hash(values["LOGIN_PASSWORD_HASH"], "pw-12345678")
    assert "pw-12345678" not in path.read_text(encoding="utf-8")
