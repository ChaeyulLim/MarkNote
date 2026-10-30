"""CSRF 토큰이 어긋나도 팝업 대신 새 토큰으로 다시 보낼 수 있어야 한다 (app.js fetch 재시도)."""


def test_csrf_error_is_flagged_and_fresh_token_works(app, auth_client):
    app.config["WTF_CSRF_ENABLED"] = True
    bad = auth_client.post("/api/pages", json={"title": "x", "kind": "folder"}, headers={"X-CSRFToken": "낡은토큰"})
    assert bad.status_code == 400 and bad.get_json()["csrf"] is True

    token = auth_client.get("/api/csrf").get_json()["token"]
    ok = auth_client.post("/api/pages", json={"title": "x", "kind": "folder"}, headers={"X-CSRFToken": token})
    assert ok.status_code == 201


def test_csrf_token_never_expires(app):
    assert app.config["WTF_CSRF_TIME_LIMIT"] is None


def test_fresh_token_requires_login(client):
    assert client.get("/api/csrf").status_code == 401
