import os

import pytest

from app import create_app
from app.config import Config
from app.services import page_tree as tree
from app.services import store, userdata


def make_config(data_dir, **overrides):
    """테스트 설정 — 모든 데이터는 data_dir 아래 (실제 instance/ 는 건드리지 않음).
    처음 켤 때 만드는 관리자 계정 netnsecu / temp 로 로그인한다. 앱의 기본 저장 위치(app.config)도
    그 계정의 폴더로 두어, 로그인 없이 app_context 에서 만든 노트가 로그인한 화면에도 보이게 한다."""
    data_dir = str(data_dir)
    attrs = {
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "ACCESS_PASSWORD": "temp",
        "LOGIN_USER": "netnsecu",
        "LOGIN_PASSWORD_HASH": None,  # 실제 .env.auth 가 있어도 테스트는 테스트 암호로
        "DATA_DIR": data_dir,
        "ACCOUNTS_DB": None,
        **{key: os.path.join(data_dir, "users", "netnsecu", name) for key, name in userdata.KEYS.items()},
    }
    attrs.update(overrides)
    return type("TestConfig", (Config,), attrs)


@pytest.fixture
def app(tmp_path):
    return create_app(make_config(tmp_path / "data"))


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(client):
    client.post("/login", data={"username": "netnsecu", "password": "temp"})
    return client


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


def make_page(parent=None, title="", markdown="", kind=None):
    """테스트용 폴더·노트: parent 가 없으면 맨 위 폴더, 있으면 노트 (kind="folder" 면 폴더)."""
    kind = kind or ("folder" if parent is None else "note")
    page = tree.create_page(parent, title, unique=True, kind=kind)
    if markdown:
        store.replace_source(page, markdown)
    return store.get(page.id)


def folder(parent, title):
    return make_page(parent, title, kind="folder")


def note(title="노트", markdown="", project="프로젝트"):
    """프로젝트 안의 노트 하나 (프로젝트가 없으면 만듦)."""
    parent = tree.name_conflict(None, project) or make_page(None, project)
    return make_page(parent, title, markdown)


def api_note(client, title="", project="프로젝트"):
    """API 로 프로젝트(폴더) 안에 노트 만들기 → 페이지 dict."""
    found = client.post("/api/pages", json={"title": project, "kind": "folder"})
    parent = found.get_json()["page"] if found.status_code == 201 else None
    if parent is None:  # 이미 있음
        with client.application.app_context():
            parent = {"id": tree.name_conflict(None, project).id}
    return client.post("/api/pages", json={"title": title, "parent_id": parent["id"]}).get_json()["page"]
