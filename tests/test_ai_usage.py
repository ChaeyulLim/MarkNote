import json
import os
import sys

import pytest

from app.services import ai_usage, claude_runner

FAKE = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_claude.py")]


@pytest.fixture
def ai_client(app, auth_client, monkeypatch):
    app.config["CLAUDE_COMMAND"] = FAKE
    monkeypatch.setattr(claude_runner, "_status_cache", None)
    monkeypatch.setattr(ai_usage, "_limits", {})
    return auth_client


def _result(client, **body):
    text = client.post("/api/ai/chat", json={"message": "hi", **body}).get_data(as_text=True)
    return [json.loads(line[6:]) for line in text.split("\n") if line.startswith("data: ")]


@pytest.mark.parametrize(
    "model_id, label",
    [
        ("claude-opus-5-5", "Opus 5.5"),
        ("claude-sonnet-4-5-20250929", "Sonnet 4.5"),
        ("claude-opus-4-20250514", "Opus 4"),
        ("claude-fable-5-1[1m]", "Fable 5.1"),
        ("claude-3-5-haiku-20241022", "Haiku 3.5"),
        ("custom-model", "custom-model"),
        (None, ""),
    ],
)
def test_model_label(model_id, label):
    assert ai_usage.model_label(model_id) == label


def test_chat_result_reports_model_context_today_and_limits(ai_client):
    events = _result(ai_client)
    assert events[1]["type"] == "session" and events[1]["model_label"] == "Opus 5.5"
    assert all(e["type"] not in ("usage", "limit") for e in events)  # 내부용 이벤트는 결과에 합쳐짐
    usage = events[-1]["usage"]
    assert usage["model_label"] == "Opus 5.5"
    assert usage["context_tokens"] == 12 + 3000 + 500 + 88
    assert usage["context_window"] == 200000
    assert (usage["input_tokens"], usage["output_tokens"]) == (3512, 88)
    assert usage["today"] == {"requests": 1, "input_tokens": 3512, "output_tokens": 88}
    assert usage["limits"] == [
        {"type": "five_hour", "name": "5시간", "status": "allowed", "percent": 42.0, "resets_at": 1793120000, "resets_text": "", "overage": False}
    ]

    _result(ai_client)
    overview = ai_client.get("/api/ai/usage").get_json()
    assert overview["today"]["requests"] == 2
    assert overview["last_model_label"] == "Opus 5.5"


def test_model_and_effort_choice_is_passed_to_claude(ai_client):
    res = ai_client.post("/api/ai/prefs", json={"model": "sonnet", "effort": "high"}).get_json()
    assert (res["model"], res["effort"]) == ("sonnet", "high")
    result = _result(ai_client)[-1]
    assert "model=sonnet effort=high" in result["text"]
    assert result["usage"]["model_label"] == "Sonnet 5.5"

    # 허용 목록 밖의 값은 무시, 빈 값은 기본으로
    res = ai_client.post("/api/ai/prefs", json={"model": "gpt-4", "effort": ""}).get_json()
    assert (res["model"], res["effort"]) == ("sonnet", "")
    assert "effort=None" in _result(ai_client)[-1]["text"]


def test_usage_requires_login(client):
    assert client.get("/api/ai/usage").status_code == 401


def test_parse_usage_lines():
    text = (
        "Current session: 90% used · resets Oct 28, 12:19am (Asia/Seoul)\n"
        "Current week (all models): 40% used · resets Oct 30, 3:59am (Asia/Seoul)\n"
        "Current week (Opus only): 100% used · resets 3pm (Asia/Seoul)\n"
        "아무 줄\n"
    )
    rows = ai_usage.parse_usage(text)
    assert [r["type"] for r in rows] == ["session", "week_all", "week_opus"]
    assert rows[0]["percent"] == 90 and rows[0]["status"] == "allowed_warning"
    assert rows[0]["resets_text"] == "10월 28일 오전 12:19"
    assert rows[2]["status"] == "rejected" and rows[2]["resets_text"] == "오후 3시"
    assert ai_usage.parse_usage("") == []
