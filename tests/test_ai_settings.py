import json
import os
import sys
from conftest import api_note

import pytest

from app.services import note_edits
from app.routes import ai as ai_routes
from app.services import claude_runner

FAKE = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_claude.py")]


@pytest.fixture
def ai_client(app, auth_client, monkeypatch):
    app.config["CLAUDE_COMMAND"] = FAKE
    monkeypatch.setattr(claude_runner, "_status_cache", None)
    return auth_client


def _result(client, **body):
    text = client.post("/api/ai/chat", json={"message": "hi", **body}).get_data(as_text=True)
    events = [json.loads(line[6:]) for line in text.split("\n") if line.startswith("data: ")]
    return events


def _save(client, prompt="", read=True, edit=True, delete=True):
    form = {"ai_initial_prompt": prompt}
    if read:
        form["ai_notes_read"] = "1"
    if edit:
        form["ai_notes_edit"] = "1"
    if delete:
        form["ai_notes_delete"] = "1"
    return client.post("/settings", data=form)


def test_settings_page_saves_values(ai_client):
    assert ai_client.get("/settings").status_code == 200
    res = _save(ai_client, prompt="MY-RULE 존댓말", read=True, edit=False)
    assert res.status_code == 302
    html = ai_client.get("/settings").get_data(as_text=True)
    assert "MY-RULE 존댓말" in html
    assert 'id="ai_notes_edit" ' in html and 'id="ai_notes_edit" checked' not in html


def test_edit_requires_read(ai_client, app):
    from app.services.settings import get_settings

    _save(ai_client, read=False, edit=True)
    with app.app_context():
        assert get_settings()["ai_notes_edit"] is False


def test_default_enables_note_tools_and_reports_tool_use(ai_client):
    events = _result(ai_client)
    tool = next(e for e in events if e["type"] == "tool")
    assert tool["label"] == "노트 검색: 회의"
    assert tool["icon"] == "search"
    text = events[-1]["text"]
    assert "mcp=-m app.mcp_server" in text
    assert "mcp__marknote__edit_page" in text  # 노트 바로 수정 도구 허용
    assert "mcp__marknote__trash_page" in text  # 삭제(휴지통으로)도 기본으로 허용
    assert "custom=False" in text


def test_read_only_permission_hides_edit_tools(ai_client):
    _save(ai_client, read=True, edit=False)
    text = _result(ai_client)[-1]["text"]
    assert "mcp__marknote__read_page" in text
    assert "edit_page" not in text and "create_page" not in text


def test_delete_permission_can_be_turned_off(ai_client, app):
    from app.services.settings import get_settings

    _save(ai_client, delete=False)
    text = _result(ai_client)[-1]["text"]
    assert "mcp__marknote__edit_page" in text and "trash_page" not in text
    _save(ai_client, read=False, delete=True)
    with app.app_context():
        assert get_settings()["ai_notes_delete"] is False  # 읽기가 꺼지면 삭제도 꺼짐


def test_no_permission_disables_mcp(ai_client):
    _save(ai_client, read=False, edit=False)
    text = _result(ai_client)[-1]["text"]
    assert "mcp=None" in text and "allowed=None" in text


def test_initial_prompt_is_sent(ai_client):
    _save(ai_client, prompt="MY-RULE 항상 존댓말")
    assert "custom=True" in _result(ai_client)[-1]["text"]


def test_system_prompt_wording():
    assert "User's custom instructions" in claude_runner.system_prompt_for(True, True, "규칙")
    assert "edit_page" in claude_runner.system_prompt_for(True, True)
    assert "edit_page" not in claude_runner.system_prompt_for(True, False)
    assert "trash_page" in claude_runner.system_prompt_for(True, True, "", False, True)
    assert "trash_page" not in claude_runner.system_prompt_for(True, True, "", False, False)
    assert "no tools" in claude_runner.system_prompt_for(False, False)


def test_result_includes_note_edits_from_this_run(ai_client, app, monkeypatch):
    page = api_note(ai_client, "회의록")
    ai_client.put(f"/api/pages/{page['id']}/source", json={"markdown": "원래"})

    class FixedUUID:
        hex = "fixedrun"

    monkeypatch.setattr(ai_routes.uuid, "uuid4", lambda: FixedUUID())
    with app.app_context():
        note_edits.edit_page("fixedrun", page["id"], "바뀜", "수정")
        note_edits.edit_page("other", page["id"], "또 바뀜", "")

    edits = _result(ai_client)[-1]["note_edits"]
    assert len(edits) == 1
    assert edits[0]["page_title"] == "회의록" and edits[0]["file"] == "프로젝트/회의록.md"
    assert {"t": "add", "s": "바뀜"} in edits[0]["diff"]


def test_blocks_endpoint_for_editor_reload(ai_client):
    page = api_note(ai_client, "p")
    ai_client.put(f"/api/pages/{page['id']}/source", json={"markdown": "**굵게**"})
    data = ai_client.get(f"/api/pages/{page['id']}/blocks").get_json()
    assert data["page"]["title"] == "p"
    assert data["blocks"][0]["html"] == "<p><strong>굵게</strong></p>"
