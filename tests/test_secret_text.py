"""비공개 표시: {{ }} · :::비공개 부분은 AI 에게 가지 않고, AI 수정으로 지워지지 않는다."""
from conftest import api_note

import pytest

from app.services import secret_text as st


@pytest.fixture(autouse=True)
def _ctx(app):
    with app.app_context():
        yield


def test_tokens_round_trip_and_rules():
    text = (
        "은행 {{123-456}} 와 `{{코드}}` 는 다름\n"
        "\n"
        ":::비공개\n아이디: chae\n\n비번 힌트\n:::\n"
        "```\n{{코드 블록}}\n:::비공개\n```\n"
        "끝 {{닫지 않음\n다음 줄도\n\n여기는 보임"
    )
    toks = st.tokens(text)
    assert "".join(raw for _, raw in toks) == text
    assert st.secrets_in(text) == [
        "{{123-456}}",
        ":::비공개\n아이디: chae\n\n비번 힌트\n:::",
        "{{닫지 않음\n다음 줄도",
    ]


def test_unclosed_block_hides_to_end():
    assert st.redact("공개\n:::비공개\n비밀\n\n또 비밀").startswith("공개\n⟦🔒")
    assert "비밀" not in st.redact("공개\n:::비공개\n비밀\n\n또 비밀")


def test_redact_and_restore():
    base = "통장 {{111-222}} 메모 {{PIN 0000}}"
    masked = st.redact(base)
    assert "111" not in masked and "0000" not in masked and masked.count("⟦🔒") == 2
    edited = masked.replace("통장", "월급 통장")
    restored = st.restore(edited, st.tag_map(base))
    assert restored == "월급 " + base
    st.check_kept(st.secrets_in(base), st.secrets_in(restored))


def test_removed_or_unknown_placeholder_is_rejected():
    base = "a {{비밀1}} b {{비밀2}}"
    masked = st.redact(base)
    dropped = st.restore(masked.split(" b ")[0], st.tag_map(base))
    with pytest.raises(st.SecretError):
        st.check_kept(st.secrets_in(base), st.secrets_in(dropped))
    with pytest.raises(st.SecretError):
        st.restore("⟦🔒abcdef⟧", st.tag_map(base))


def test_scrub_selection():
    page = "계좌 {{123-456-7890}} 입니다"
    assert st.scrub_selection("계좌 123-456-7890 입니다", [page]) == "계좌 ⟦🔒⟧ 입니다"
    assert st.scrub_selection("그냥 글", [page]) == "그냥 글"


def test_redact_obj():
    assert st.redact_obj({"a": ["x {{y}}"], "n": 1})["a"][0].startswith("x ⟦🔒")


# ---------- AI 로 가는 길 ----------

from app.services import ai_context, deck_tools, note_edits, note_tools, store  # noqa: E402
from app.services import decks as deck_service  # noqa: E402
from app.services import page_tree as tree  # noqa: E402

SECRET_PAGE = "계좌 {{110-222-333}} 메모\n\n:::비공개\n아이디: chae\n\n비번 힌트: 강아지\n:::\n\n공개 문단"


@pytest.fixture
def secret_page(app, auth_client):
    page = api_note(auth_client, "개인 정보")
    auth_client.put(f"/api/pages/{page['id']}/source", json={"markdown": SECRET_PAGE})
    return store.get(page["id"])


def _leaks(text):
    return [s for s in ("110-222-333", "chae", "강아지") if s in text]


def test_secret_box_stays_one_block(secret_page):
    assert len(secret_page.blocks) == 3
    assert tree.page_source(secret_page) == SECRET_PAGE


def test_context_read_and_search_hide_secrets(secret_page):
    location = {"view": "page", "page_id": secret_page.id, "selection": "계좌 110-222-333 메모"}
    stdin, _ = ai_context.build(location, secret_page, False, None, None)
    assert not _leaks(stdin) and "⟦🔒" in stdin and "공개 문단" in stdin
    assert not _leaks(str(note_tools.read_page(secret_page.id)))
    assert note_tools.search_pages("강아지")["results"] == []  # 어디에 있는지도 안 알려 줌
    assert [r["id"] for r in note_tools.search_pages("공개 문단")["results"]] == [secret_page.id]


def test_ai_update_keeps_secrets(secret_page):
    masked = note_tools.read_page(secret_page.id)["markdown"]
    note_edits.edit_page("r", secret_page.id, masked.replace("공개 문단", "고친 문단"), "고침")
    assert tree.page_source(store.get(secret_page.id)) == SECRET_PAGE.replace("공개 문단", "고친 문단")

    dropped = masked.split("\n\n")[-1]  # 비공개 부분을 지운 글
    with pytest.raises(note_tools.ToolError, match="비공개"):
        note_edits.edit_page("r", secret_page.id, dropped, "지움")


def test_deck_secrets(app, auth_client):
    deck = auth_client.post("/api/decks", json={"title": "발표"}).get_json()["deck"]
    deck_tools.edit_deck("r", deck["id"], [{"op": "update_element", "slide": 1, "element": "제목 1", "set": {"text": "제목 {{비밀 숫자 42}}"}}])
    data = deck_tools.read_deck(deck["id"])
    title = data["slides"][0]["elements"][0]["text"]
    assert "비밀 숫자" not in str(data) and "⟦🔒" in title

    # 자리 표시를 그대로 두면 됨
    deck_tools.edit_deck("r", deck["id"], [{"op": "update_element", "slide": 1, "element": "제목 1", "set": {"text": title.replace("제목", "새 제목")}}])
    assert "새 제목 {{비밀 숫자 42}}" in deck_service.get(deck["id"]).slides[0].elements[0]["text"]
    # 비공개 글을 덮어쓰거나 지우면 거절
    for op in ({"op": "update_element", "slide": 1, "element": "제목 1", "set": {"text": "없앰"}},
               {"op": "delete_element", "slide": 1, "element": "제목 1"}):
        with pytest.raises(note_tools.ToolError, match="비공개"):
            deck_tools.edit_deck("r", deck["id"], [op])
    assert "42" in deck_service.get(deck["id"]).slides[0].elements[0]["text"]
