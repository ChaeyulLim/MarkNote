import json
import os
import sys
import threading
import time
from conftest import api_note

import pytest

from app.services import claude_runner

FAKE = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_claude.py")]


@pytest.fixture
def ai_client(app, auth_client, monkeypatch):
    app.config["CLAUDE_COMMAND"] = FAKE
    monkeypatch.setattr(claude_runner, "_status_cache", None)
    return auth_client


def _events(res):
    text = res.get_data(as_text=True)
    return [json.loads(line[6:]) for line in text.split("\n") if line.startswith("data: ")]


def _chat(client, **body):
    return client.post("/api/ai/chat", json=body)


def test_status_ready(ai_client):
    info = ai_client.get("/api/ai/status?refresh=1").get_json()
    assert info["state"] == "ready"
    assert info["version"] == "9.9.9 (Claude Code)"
    assert info["auth_method"] == "claude.ai"


@pytest.mark.parametrize("mode, state", [("logged_out", "logged_out"), ("api_key", "api_billing")])
def test_status_problems(ai_client, monkeypatch, mode, state):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    assert ai_client.get("/api/ai/status?refresh=1").get_json()["state"] == state


def test_status_not_installed(ai_client, app, monkeypatch):
    app.config["CLAUDE_COMMAND"] = None
    monkeypatch.setattr(claude_runner, "find_command", lambda configured=None: None)
    assert ai_client.get("/api/ai/status?refresh=1").get_json()["state"] == "not_installed"


def test_chat_streams_and_renders(ai_client):
    page = api_note(ai_client, "회의록")
    ai_client.put(f"/api/pages/{page['id']}/source", json={"markdown": "안건 내용"})

    res = _chat(ai_client, message="요약해줘", page_id=page["id"])
    assert res.mimetype == "text/event-stream"
    events = _events(res)
    types = [e["type"] for e in events]
    assert types[:2] == ["run", "session"] and "delta" in types and types[-1] == "result"

    result = events[-1]
    assert result["ok"] is True
    assert result["session_id"] == "sess-1"
    assert "<strong>받음</strong>" in result["html"]
    assert "context=0" not in result["text"]  # 페이지 내용이 전달됨
    assert "tools=''" in result["text"]  # 도구 모두 끔
    assert "".join(e["text"] for e in events if e["type"] == "delta") == result["text"]


def test_every_message_rereads_latest_page_but_skips_unchanged(ai_client, monkeypatch):
    from app.services import ai_context

    monkeypatch.setattr(ai_context, "_sent", ai_context.OrderedDict())
    page = api_note(ai_client, "회의록")
    ai_client.put(f"/api/pages/{page['id']}/source", json={"markdown": "첫 내용"})

    first = _events(_chat(ai_client, message="요약", page_id=page["id"]))[-1]["text"]
    assert "첫 내용" in first

    # 같은 대화, 페이지 그대로 → 전체 대신 "바뀐 내용 없음"
    second = _events(_chat(ai_client, message="다시", page_id=page["id"], session_id="sess-1"))[-1]["text"]
    assert "resume=sess-1" in second
    assert "첫 내용" not in second and "바뀐 내용 없음" in second

    # 페이지가 바뀌면 (입력 중 자동 저장 포함) 다음 질문에 최신 내용이 다시 들어감
    ai_client.put(f"/api/pages/{page['id']}/source", json={"markdown": "고친 내용"})
    third = _events(_chat(ai_client, message="또", page_id=page["id"], session_id="sess-1"))[-1]["text"]
    assert "고친 내용" in third


def test_location_and_selection_are_sent_as_text(ai_client):
    parent = ai_client.post("/api/pages", json={"title": "프로젝트"}).get_json()["page"]
    child = ai_client.post("/api/pages", json={"title": "회의록", "parent_id": parent["id"]}).get_json()["page"]
    location = {"view": "page", "page_id": child["id"], "editing_block": 3, "block_count": 5, "selection": "다음 주까지 확정"}
    text = _events(_chat(ai_client, message="이 부분 다듬어줘", page_id=child["id"], location=location))[-1]["text"]
    assert '노트 페이지 "회의록" (경로: 프로젝트 / 회의록' in text
    assert "3번째 블록을 편집 중" in text
    assert "<selection>다음 주까지 확정</selection>" in text


def test_location_without_page(ai_client):
    text = _events(_chat(ai_client, message="hi", location={"view": "settings"}))[-1]["text"]
    assert "<location>사용자가 지금 보고 있는 곳: 설정</location>" in text


def test_deck_context_and_location(ai_client):
    deck = ai_client.post("/api/decks", json={"title": "발표"}).get_json()["deck"]
    slide = deck["slides"][0]
    elements = slide["elements"]
    elements[0]["text"] = "<b>MarkNote</b>"
    elements[1]["text"] = "<ul><li>하나</li><li>둘</li></ul>"
    ai_client.post(f"/api/decks/{deck['id']}/ops", json={"ops": [{"op": "set_elements", "slide": slide["id"], "elements": elements}]})
    location = {"view": "deck", "deck_id": deck["id"], "slide_index": 1, "selected": ["제목 1"]}
    text = _events(_chat(ai_client, message="어때?", deck_id=deck["id"], location=location))[-1]["text"]
    assert '프레젠테이션 "발표"' in text and "슬라이드 1/1 보는 중" in text and "선택한 개체: 제목 1" in text
    assert "← 사용자가 지금 보는 슬라이드" in text
    assert "제목 1 (text 60.0pt) 위치 160,118 크기 960×250: MarkNote" in text
    assert "• 하나 ⏎ • 둘" in text


def test_folder_context_has_every_note(app, ai_client):
    from app.services import store
    from app.services.ai_context import page_context, working_note

    note = api_note(ai_client, "회의록")
    ai_client.put(f"/api/pages/{note['id']}/source", json={"markdown": "안건"})
    with app.app_context():
        project = store.get(note["parent_id"])
        sub = store.create(project, "하위", kind="folder")
        store.create(sub, "메모", "메모 내용")
        text = page_context(store.get(project.id))
        assert text.count("<note ") == 2 and "안건" in text and "메모 내용" in text
        assert page_context(store.get(note["id"])).count("<page ") == 1
        assert working_note(store.get(project.id)).count("<note ") == 2


def test_plain_text_from_slide_html():
    from app.services.ai_context import plain_text

    assert plain_text("<div>첫 줄</div><div>둘째 &amp; 줄</div>") == "첫 줄\n둘째 & 줄"
    assert plain_text("<ul><li>a</li><li><b>b</b></li></ul>") == "• a\n• b"


def test_api_key_is_removed_from_child_env(ai_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-should-not-leak")
    result = _events(_chat(ai_client, message="hi"))[-1]
    assert "api_key=False" in result["text"]


def test_cli_failure_reports_error(ai_client, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    events = _events(_chat(ai_client, message="hi"))
    assert events[-1]["type"] == "error"
    assert "boom" in events[-1]["message"]


def test_rejects_empty_and_busy(ai_client, monkeypatch):
    assert _chat(ai_client, message="  ").status_code == 400
    monkeypatch.setattr(claude_runner, "is_busy", lambda: True)
    res = _chat(ai_client, message="hi")
    assert res.status_code == 409


def test_cancel_stops_running_process(app, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow")
    events = []

    def run():
        events.extend(claude_runner.stream_chat("hi", "", None, str(tmp_path), configured=FAKE))

    worker = threading.Thread(target=run)
    worker.start()
    for _ in range(100):  # 프로세스가 뜰 때까지 대기
        if claude_runner._current is not None:
            break
        time.sleep(0.05)
    time.sleep(0.5)
    assert claude_runner.cancel() is True
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert events[-1] == {"type": "error", "message": "중단했습니다."}
    assert not claude_runner.is_busy()


def test_chat_requires_login(client):
    assert client.post("/api/ai/chat", json={"message": "hi"}).status_code == 401
