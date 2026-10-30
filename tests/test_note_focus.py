from conftest import make_page

from app.services import note_focus

from test_ai import _chat, _events, ai_client  # noqa: F401 (픽스처)


def _page(title, markdown="", parent=None):
    return make_page(parent, title, markdown)


def test_mentioned_note_picks_the_named_project(ctx):
    a, b = _page("가나 프로젝트"), _page("다라 프로젝트")
    _page("회의록", parent=a)
    note_b = _page("회의록", parent=b)
    _page("회의", parent=b)  # "회의록" 안에 들어가는 짧은 제목은 빠짐
    found = note_focus.mentioned_pages("다라 프로젝트의 회의록 확인해줘")
    assert [p.id for p in found] == [note_b.id]  # 프로젝트 자체는 경로로만 쓰였으므로 빠짐


def test_mentioning_only_a_project_keeps_it(ctx):
    project = _page("여행 계획")
    assert [p.id for p in note_focus.mentioned_pages("여행 계획 보여줘")] == [project.id]
    assert note_focus.mentioned_pages("여행 계획 보여줘", exclude_id=project.id) == []


def test_find_page_by_path(ctx):
    a, b = _page("가나"), _page("다라")
    _page("설계 문서", parent=a)
    target = _page("설계 문서", parent=b)
    res = note_focus.find_page("다라 / 설계")
    assert [r["id"] for r in res["results"]] == [target.id]
    assert res["results"][0]["path"] == "다라 / 설계 문서"


def test_merge_keeps_newest_first_and_caps():
    assert note_focus.merge([1, 2, 3], [4, 2]) == [4, 2, 1]


def test_chat_keeps_named_note_for_following_messages(ai_client):
    project = ai_client.post("/api/pages", json={"title": "마케팅", "kind": "folder"}).get_json()["page"]
    note = ai_client.post("/api/pages", json={"title": "주간 보고", "parent_id": project["id"]}).get_json()["page"]
    ai_client.put(f"/api/pages/{note['id']}/source", json={"markdown": "이번 주 매출 정리 {{비밀 메모}}"})

    first = _events(_chat(ai_client, message="마케팅 프로젝트의 주간 보고 확인해줘"))[-1]
    assert f'<working_note id="{note["id"]}" path="마케팅 / 주간 보고" file="마케팅/주간 보고.md">' in first["text"]
    assert "이번 주 매출 정리" in first["text"] and "비밀 메모" not in first["text"]  # 비공개는 그대로 가림
    assert [f["id"] for f in first["focus"]] == [note["id"]]

    # 이름을 다시 말하지 않아도 그 노트의 최신 내용이 계속 들어감 ("거기에 추가해줘")
    ai_client.put(f"/api/pages/{note['id']}/source", json={"markdown": "다음 주 계획 추가됨"})
    second = _events(_chat(ai_client, message="거기에 한 줄 추가해줘"))[-1]
    assert "다음 주 계획 추가됨" in second["text"]
    assert [f["id"] for f in ai_client.get("/api/ai/conversation").get_json()["focus"]] == [note["id"]]

    # [x] 로 빼면 다음 질문부터 안 보냄
    conv_id = ai_client.get("/api/ai/conversation").get_json()["id"]
    res = ai_client.post(f"/api/ai/conversations/{conv_id}/focus/remove", json={"page_id": note["id"]})
    assert res.get_json()["focus"] == []
    third = _events(_chat(ai_client, message="고마워"))[-1]
    assert "working_note" not in third["text"]


def test_no_focus_when_note_reading_is_off(ai_client):
    ai_client.post("/api/pages", json={"title": "가계부", "kind": "folder"})
    assert ai_client.post("/settings", data={"ai_initial_prompt": ""}).status_code == 302  # 노트 읽기·수정 끔

    text = _events(_chat(ai_client, message="가계부 보여줘"))[-1]["text"]
    assert "working_note" not in text
