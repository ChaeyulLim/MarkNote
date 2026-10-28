import json
import os
import sys

import pytest

from app import create_app
from app.config import Config
from app.routes import auth
from app.services import claude_runner, deck_tools
from app.services import decks as deck_service
from app.services.note_tools import ProposalConflict, ToolError

FAKE = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_claude.py")]


def _deck(client, title="발표"):
    return client.post("/api/decks", json={"title": title}).get_json()["deck"]


def _title_text(app, deck_id):
    with app.app_context():
        return deck_service.get(deck_id).slides[0].elements[0]["text"]


def test_read_deck_returns_ids_and_elements(app, auth_client):
    deck = _deck(auth_client)
    with app.app_context():
        data = deck_tools.read_deck(deck["id"])
    assert data["size"] == {"w": 1280, "h": 720}
    assert data["slides"][0]["number"] == 1
    assert [e["name"] for e in data["slides"][0]["elements"]] == ["제목 1", "부제목 2"]
    assert "title_content" in data["layouts"]


def test_edit_applies_immediately_with_numbers_and_names(app, auth_client):
    deck = _deck(auth_client)
    ops = [
        {"op": "update_element", "slide": 1, "element": "제목 1", "set": {"text": "새 제목", "style": {"bold": True}}},
        {"op": "add_slide", "after": 1, "layout": "title_content", "texts": {"title": "요약", "body": "<div>하나</div><div>둘</div>"}},
        {"op": "add_element", "slide": 1, "element": {"type": "shape", "shape": "ellipse", "x": 10, "y": 10, "w": 50, "h": 50}},
    ]
    with app.app_context():
        result = deck_tools.edit_deck("run-1", deck["id"], ops, "제목 바꾸고 요약 추가")
        assert result["applied"] is True and result["slides"] == 2
        saved = deck_service.get(deck["id"])
        title = saved.slides[0].elements[0]
        assert title["text"] == "새 제목" and title["style"]["bold"] is True
        assert title["style"]["fontSize"] == 60  # 바꾸지 않은 서식은 그대로
        assert saved.slides[0].elements[-1]["shape"] == "ellipse"
        assert [e["text"] for e in saved.slides[1].elements] == ["요약", "<div>하나</div><div>둘</div>"]
        (edit,) = deck_tools.by_run("run-1")
        assert edit.run_id == "run-1" and edit.op_count == 3 and edit.status == "applied"


def test_bad_op_changes_nothing(app, auth_client):
    deck = _deck(auth_client)
    ops = [
        {"op": "update_element", "slide": 1, "element": "제목 1", "set": {"text": "바뀌면 안 됨"}},
        {"op": "delete_element", "slide": 1, "element": "없는 개체"},
    ]
    with app.app_context():
        with pytest.raises(ToolError, match="아무것도 바뀌지 않음"):
            deck_tools.edit_deck("run-1", deck["id"], ops)
        with pytest.raises(ToolError, match="5 번이 없습니다"):
            deck_tools.edit_deck("run-1", deck["id"], [{"op": "delete_slide", "slide": 5}])
        assert deck_tools.by_run("run-1") == []
    assert _title_text(app, deck["id"]) == ""


def test_undo_restores_slides_and_checks_later_changes(app, auth_client):
    deck = _deck(auth_client)
    with app.app_context():
        deck_tools.edit_deck("run-1", deck["id"], [
            {"op": "update_element", "slide": 1, "element": "제목 1", "set": {"text": "AI"}},
            {"op": "add_slide", "layout": "blank"},
        ])
    # 그 뒤 사용자가 또 고치면 되돌리기 전에 확인
    slide = auth_client.get(f"/api/decks/{deck['id']}").get_json()["deck"]["slides"][0]
    auth_client.post(f"/api/decks/{deck['id']}/ops", json={"ops": [{"op": "update_slide", "slide": slide["id"], "set": {"notes": "메모"}}]})
    with app.app_context():
        edit_id = deck_tools.by_run("run-1")[0].id
        with pytest.raises(ProposalConflict):
            deck_tools.undo_edit(deck_tools.get(edit_id))
    res = auth_client.post(f"/api/ai/deck-edits/{edit_id}/undo", json={})
    assert res.status_code == 409 and res.get_json()["conflict"] is True

    res = auth_client.post(f"/api/ai/deck-edits/{edit_id}/undo", json={"force": True})
    assert res.get_json()["edit"]["status"] == "undone"
    restored = auth_client.get(f"/api/decks/{deck['id']}").get_json()["deck"]
    assert len(restored["slides"]) == 1 and restored["slides"][0]["id"] == slide["id"]
    assert restored["slides"][0]["elements"][0]["text"] == "" and restored["slides"][0]["notes"] == ""
    assert auth_client.post(f"/api/ai/deck-edits/{edit_id}/undo", json={}).status_code == 400


def test_undo_brings_back_deleted_slide(app, auth_client):
    deck = _deck(auth_client)
    auth_client.post(f"/api/decks/{deck['id']}/ops", json={"ops": [{"op": "add_slide", "layout": "title_only"}]})
    with app.app_context():
        deck_tools.edit_deck("run-1", deck["id"], [{"op": "delete_slide", "slide": 1}])
        deck_tools.undo_edit(deck_tools.by_run("run-1")[0])
        restored = deck_service.get(deck["id"])
        assert [s.layout for s in restored.slides] == ["title_slide", "title_only"]


def test_trashed_deck_is_hidden(app, auth_client):
    deck = _deck(auth_client)
    auth_client.post(f"/api/decks/{deck['id']}/trash")
    with app.app_context():
        assert deck_tools.list_decks() == {"decks": []}
        with pytest.raises(ToolError):
            deck_tools.edit_deck("r", deck["id"], [{"op": "delete_slide", "slide": 1}])


def test_system_prompt_mentions_deck_permission():
    assert "edit_deck" in claude_runner.system_prompt_for(True, True, "", True)
    assert "disabled in settings" in claude_runner.system_prompt_for(True, True, "", False)


# ---------- 채팅에서 (가짜 claude 가 실제 deck_tools 를 호출) ----------

@pytest.fixture
def file_client(tmp_path, monkeypatch):
    """가짜 claude 가 같은 파일(노트·덱)을 열 수 있는 앱."""

    class FileConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        ACCESS_PASSWORD = "temp"
        LOGIN_PASSWORD_HASH = None
        DATA_DIR = str(tmp_path / "data")
        NOTES_DIR = UPLOAD_FOLDER = AI_CHAT_DIR = DECKS_DIR = AI_EDITS_DIR = SETTINGS_FILE = USAGE_FILE = None
        CLAUDE_COMMAND = FAKE

    auth._failed_attempts.clear()
    monkeypatch.setattr(claude_runner, "_status_cache", None)
    client = create_app(FileConfig).test_client()
    client.post("/login", data={"username": "netnsecu", "password": "temp"})
    return client


def _chat(client, **body):
    text = client.post("/api/ai/chat", json=body).get_data(as_text=True)
    return [json.loads(line[6:]) for line in text.split("\n") if line.startswith("data: ")]


def _first_title(client, deck_id):
    return client.get(f"/api/decks/{deck_id}").get_json()["deck"]["slides"][0]["elements"][0]["text"]


def test_chat_edits_deck_and_returns_undo_card(file_client):
    deck = _deck(file_client)
    events = _chat(file_client, message="슬라이드 제목 바꿔줘", deck_id=deck["id"])
    assert "슬라이드 수정: 발표" in [e["label"] for e in events if e["type"] == "tool"]
    result = events[-1]
    assert result["deck_edits"] == [
        {"id": 1, "deck_id": deck["id"], "deck_title": "발표", "summary": "첫 슬라이드 제목 변경", "op_count": 1, "status": "applied"}
    ]
    assert "mcp__marknote__edit_deck" in result["text"]  # 허용 도구에 포함
    assert _first_title(file_client, deck["id"]) == "AI가 고친 제목"

    file_client.post("/api/ai/deck-edits/1/undo", json={})
    assert _first_title(file_client, deck["id"]) == ""


def test_deck_edit_can_be_turned_off(file_client):
    file_client.post("/settings", data={"ai_initial_prompt": "", "ai_notes_read": "1"})  # 프레젠테이션 수정 끔
    deck = _deck(file_client)
    result = _chat(file_client, message="슬라이드 제목 바꿔줘", deck_id=deck["id"])[-1]
    assert result["deck_edits"] == [] and "edit_deck" not in result["text"]
    assert _first_title(file_client, deck["id"]) == ""
