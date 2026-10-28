"""AI 도우미 대화 기록: 파일(JSONL)에 남고, 페이지를 옮겨도(새로 불러와도) 이어진다."""
from conftest import api_note

import json
import os
import sys
import threading
import time

import pytest

from app.services import ai_usage, chat_runs, chat_store, claude_runner

FAKE = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_claude.py")]


@pytest.fixture
def ai_client(app, auth_client, monkeypatch):
    app.config["CLAUDE_COMMAND"] = FAKE
    monkeypatch.setattr(claude_runner, "_status_cache", None)
    monkeypatch.setattr(ai_usage, "_limits", {})
    return auth_client


def _events(res):
    return [json.loads(line[6:]) for line in res.get_data(as_text=True).split("\n") if line.startswith("data: ")]


def _chat(client, **body):
    events = _events(client.post("/api/ai/chat", json=body))
    chat_runs.wait_all()  # 대화 파일 기록은 백그라운드에서 끝남
    return events


def test_conversation_is_recorded_and_restored(ai_client, app):
    page = api_note(ai_client, "회의록")
    first = _chat(ai_client, message="요약해줘", page_id=page["id"], location={"view": "page", "page_id": page["id"], "selection": "이 부분"})
    assert first[0]["type"] == "run"
    conv_id = first[0]["conversation_id"]

    # 다른 페이지를 연 것처럼: 서버에서 지금 대화를 다시 받음
    conv = ai_client.get("/api/ai/conversation").get_json()
    assert conv["id"] == conv_id and conv["session_id"] == "sess-1" and conv["running"] is None
    user, assistant = conv["messages"]
    assert user == {"role": "user", "id": user["id"], "text": "요약해줘", "selection": "이 부분"}
    assert assistant["role"] == "assistant" and assistant["ok"] is True
    assert "<strong>받음</strong>" in assistant["html"]
    assert assistant["tools"] == [{"icon": "search", "label": "노트 검색: 회의"}]

    # 이어서 물으면 같은 Claude 대화를 이어 감 (브라우저가 session_id 를 몰라도)
    second = _chat(ai_client, message="더 짧게")
    assert "resume=sess-1" in second[-1]["text"]
    assert len(ai_client.get("/api/ai/conversation").get_json()["messages"]) == 4

    # 파일은 한 줄 = 한 레코드
    path = os.path.join(app.config["AI_CHAT_DIR"], f"{conv_id}.jsonl")
    kinds = [json.loads(line)["t"] for line in open(path, encoding="utf-8")]
    assert kinds[0] == "meta" and kinds.count("user") == 2 and kinds.count("assistant") == 2


def test_new_conversation_starts_fresh(ai_client):
    first = _chat(ai_client, message="hi")
    new = ai_client.post("/api/ai/conversation/new").get_json()
    assert new["id"] != first[0]["conversation_id"] and new["messages"] == []
    after = _chat(ai_client, message="hi")
    assert after[0]["conversation_id"] == new["id"]
    assert "resume=None" in after[-1]["text"]  # 새 Claude 대화


def test_note_edit_status_is_current_when_restored(ai_client, app):
    from app.services import note_edits

    page = api_note(ai_client, "메모")
    with app.app_context():
        edit_id = note_edits.edit_page("x", page["id"], "새 글", "s")["edit_id"]
    conv_id = chat_store_current(app)
    with app.app_context():
        chat_store.append(conv_id, {"t": "assistant", "id": "a1", "ok": True, "text": "t", "html": "t",
                                    "note_edit_ids": [edit_id], "proposal_ids": [1]})  # 예전 제안 번호는 무시
    ai_client.post(f"/api/ai/note-edits/{edit_id}/confirm", json={})
    restored = ai_client.get("/api/ai/conversation").get_json()["messages"][-1]
    assert restored["note_edits"][0]["status"] == "confirmed"


def chat_store_current(app):
    with app.app_context():
        return chat_store.current_id()


def test_answer_keeps_running_after_disconnect_and_can_be_followed(ai_client, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow_reply")
    res = ai_client.post("/api/ai/chat", json={"message": "길게"}, buffered=False)
    first = next(res.response)  # 첫 이벤트만 받고 끊음 (= 다른 페이지로 이동)
    run_id = json.loads(first.decode()[6:])["run_id"]
    res.close()

    conv = ai_client.get("/api/ai/conversation").get_json()
    assert conv["running"] == {"run_id": run_id}
    # 새 페이지에서 이어 받기: 처음부터 다시 받아 끝까지
    events = _events(ai_client.get(f"/api/ai/runs/{run_id}/events?after=0"))
    assert events[0]["type"] == "run" and events[-1]["type"] == "result" and events[-1]["ok"] is True
    chat_runs.wait_all()
    assert ai_client.get("/api/ai/conversation").get_json()["messages"][-1]["role"] == "assistant"
    assert ai_client.get(f"/api/ai/runs/{run_id}/events?after=999").status_code == 200  # 끝난 실행도 잠시 남음


def test_busy_while_running(ai_client, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow_reply")
    res = ai_client.post("/api/ai/chat", json={"message": "길게"}, buffered=False)
    next(res.response)
    assert ai_client.post("/api/ai/chat", json={"message": "또"}).status_code == 409
    assert ai_client.post("/api/ai/conversation/new").status_code == 409
    res.close()
    chat_runs.wait_all()


def test_run_follow_waits_for_new_events():
    run = chat_runs.Run("r1", "c1")
    got = []
    t = threading.Thread(target=lambda: got.extend(e for e in run.follow() if e))
    t.start()
    run.add({"n": 1})
    time.sleep(0.05)
    run.add({"n": 2})
    run.finish()
    t.join(timeout=5)
    assert got == [{"n": 1}, {"n": 2}]


def test_broken_lines_are_skipped(app):
    with app.app_context():
        conv_id = chat_store.new_conversation()
        chat_store.append(conv_id, {"t": "user", "id": "u1", "text": "안녕"})
        with open(os.path.join(app.config["AI_CHAT_DIR"], f"{conv_id}.jsonl"), "a", encoding="utf-8") as f:
            f.write("{깨진 줄\n")
        assert [m["text"] for m in chat_store.load(conv_id)["messages"]] == ["안녕"]
        with pytest.raises(ValueError):
            chat_store.load("../../etc/passwd")


# ---------- /resume ----------

def test_resume_lists_switches_and_continues(ai_client):
    first = _chat(ai_client, message="예산 회의 정리")[0]["conversation_id"]
    second = ai_client.post("/api/ai/conversation/new").get_json()["id"]
    _chat(ai_client, message="여행 계획")

    rows = ai_client.get("/api/ai/conversations").get_json()
    assert [r["id"] for r in rows["conversations"]] == [second, first]  # 최근 것부터
    assert rows["conversations"][0]["current"] is True
    assert rows["conversations"][1]["title"] == "예산 회의 정리" and rows["conversations"][1]["messages"] == 2

    found = ai_client.get("/api/ai/conversations?q=예산").get_json()["conversations"]
    assert [r["id"] for r in found] == [first]

    # 예전 대화로 바꾸면 메시지가 돌아오고, 다음 질문은 그 Claude 대화를 이어 감
    resumed = ai_client.post(f"/api/ai/conversations/{first}/resume").get_json()
    assert resumed["id"] == first and resumed["messages"][0]["text"] == "예산 회의 정리"
    assert ai_client.get("/api/ai/conversation").get_json()["id"] == first
    after = _chat(ai_client, message="이어서")
    assert after[0]["conversation_id"] == first and "resume=sess-1" in after[-1]["text"]
    # 바꾸기 전 대화도 그대로 남아 있음
    assert len(ai_client.post(f"/api/ai/conversations/{second}/resume").get_json()["messages"]) == 2


def test_rename_and_delete(ai_client, app):
    conv = _chat(ai_client, message="hi")[0]["conversation_id"]
    assert ai_client.post(f"/api/ai/conversations/{conv}/rename", json={"title": "  내  대화 "}).get_json()["title"] == "내 대화"
    assert ai_client.post(f"/api/ai/conversations/{conv}/rename", json={"title": " "}).status_code == 400
    row = ai_client.get("/api/ai/conversations").get_json()["conversations"][0]
    assert row["title"] == "내 대화" and row["named"] is True

    res = ai_client.delete(f"/api/ai/conversations/{conv}").get_json()
    assert res["current"] != conv  # 지금 대화를 지우면 새 대화로
    assert os.path.exists(os.path.join(app.config["AI_CHAT_DIR"], "trash", f"{conv}.jsonl"))
    assert ai_client.post(f"/api/ai/conversations/{conv}/resume").status_code == 404
    assert ai_client.post("/api/ai/conversations/..%2Fx/resume").status_code == 404


def test_resume_when_claude_forgot_the_session(ai_client, app):
    """Claude 쪽 대화 기록이 지워졌으면 지난 대화를 글로 붙여 새 Claude 대화로 이어 간다."""
    page = api_note(ai_client, "회의록")
    ai_client.put(f"/api/pages/{page['id']}/source", json={"markdown": "안건 내용"})
    conv = _chat(ai_client, message="예산은 얼마였지", page_id=page["id"])[0]["conversation_id"]
    with app.app_context():
        chat_store.append(conv, {"t": "session", "session_id": "gone-123"})

    events = _chat(ai_client, message="이어서 정리해줘", page_id=page["id"])
    result = events[-1]
    assert result["type"] == "result" and result["ok"] is True
    assert {"type": "tool", "icon": "refresh", "label": "지난 대화를 다시 불러와 이어 가기"} in events
    assert "resume=None" in result["text"]
    assert "previous_conversation" in result["text"] and "예산은 얼마였지" in result["text"]
    assert "안건 내용" in result["text"]  # 페이지 내용도 "바뀐 것 없음" 대신 전체를 다시 보냄
    assert ai_client.get("/api/ai/conversation").get_json()["session_id"] == "sess-1"


def test_resume_blocked_while_answering(ai_client, monkeypatch):
    conv = _chat(ai_client, message="hi")[0]["conversation_id"]
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow_reply")
    res = ai_client.post("/api/ai/chat", json={"message": "길게"}, buffered=False)
    next(res.response)
    assert ai_client.post(f"/api/ai/conversations/{conv}/resume").status_code == 409
    res.close()
    chat_runs.wait_all()


def test_rename_keeps_list_order(ai_client):
    older = _chat(ai_client, message="첫 대화")[0]["conversation_id"]
    ai_client.post("/api/ai/conversation/new")
    newer = _chat(ai_client, message="둘째 대화")[0]["conversation_id"]
    ai_client.post(f"/api/ai/conversations/{older}/rename", json={"title": "이름만 바꿈"})
    rows = ai_client.get("/api/ai/conversations").get_json()["conversations"]
    assert [r["id"] for r in rows[:2]] == [newer, older] and rows[1]["title"] == "이름만 바꿈"
