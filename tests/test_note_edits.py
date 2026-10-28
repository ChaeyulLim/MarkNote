import os

import pytest
from conftest import folder, make_page, note

from app.services import note_edits, note_tools, store
from app.services import page_tree as tree


def _contents(page):
    return [b.content for b in store.get(page.id).blocks]


def test_edit_keeps_unchanged_block_ids(ctx):
    page = note("메모", "첫째\n\n둘째\n\n셋째")
    ids = [b.id for b in page.blocks]
    res = note_edits.edit_page("r1", page.id, "첫째\n\n둘째 고침\n\n셋째\n\n넷째", "고침")
    assert res["applied"] and res["changed_blocks"] == 2 and res["file"] == "프로젝트/메모.md"
    blocks = store.get(page.id).blocks
    assert blocks[0].id == ids[0] and blocks[2].id == ids[2]
    edit = note_edits.get(res["edit_id"])
    assert set(note_edits.edit_dict(edit)["changed_block_ids"]) == {blocks[1].id, blocks[3].id}


def test_edit_writes_the_file_and_record(ctx, app):
    page = note("메모", "하나")
    res = note_edits.edit_page("r1", page.id, "하나 고침", "")
    with open(tree.abs_file_path(store.get(page.id)), encoding="utf-8") as f:
        assert f.read().strip() == "하나 고침"
    assert os.path.exists(os.path.join(app.config["AI_EDITS_DIR"], "notes", f"{res['edit_id']}.json"))


def test_undo_whole_when_untouched(ctx):
    page = note("메모", "하나\n\n둘")
    res = note_edits.edit_page("r1", page.id, "하나\n\n둘 바꿈", "", title="새 이름")
    assert store.get(page.id).path == "프로젝트/새 이름.md"
    note_edits.undo_edit(note_edits.get(res["edit_id"]))
    assert _contents(page) == ["하나", "둘"]
    assert store.get(page.id).title == "메모"


def test_undo_keeps_user_changes_elsewhere(ctx):
    page = note("메모", "하나\n\n둘\n\n셋")
    res = note_edits.edit_page("r1", page.id, "하나\n\n둘 AI\n\n셋", "")
    store.replace_source(store.get(page.id), "하나 (내가 고침)\n\n둘 AI\n\n셋")  # AI 가 안 건드린 곳
    note_edits.undo_edit(note_edits.get(res["edit_id"]))
    assert _contents(page) == ["하나 (내가 고침)", "둘", "셋"]


def test_undo_conflict_when_user_edited_same_part(ctx):
    page = note("메모", "하나\n\n둘")
    res = note_edits.edit_page("r1", page.id, "하나\n\n둘 AI", "")
    store.replace_source(store.get(page.id), "하나\n\n둘 AI 를 내가 또 고침")
    edit = note_edits.get(res["edit_id"])
    with pytest.raises(note_tools.ProposalConflict):
        note_edits.undo_edit(edit)
    note_edits.undo_edit(edit, force=True)
    assert _contents(page) == ["하나", "둘"]
    assert note_edits.get(edit.id).status == "undone"


def test_project_has_no_content(ctx):
    project = make_page(None, "회사")
    with pytest.raises(note_tools.ToolError):
        note_edits.edit_page("r1", project.id, "내용", "")
    with pytest.raises(note_tools.ToolError):
        note_edits.create_page("r1", "새 프로젝트", "내용")  # 맨 위(프로젝트)는 내용 없이만


def test_trash_moves_to_trash_and_undo_restores(ctx, app):
    project = make_page(None, "회사")
    sub = folder(project, "2026-03")
    inside = make_page(sub, "3월", "내용")
    res = note_edits.trash_page("r1", sub.id, "안 쓰는 폴더")
    assert res["applied"] is True and res["file"] == "회사/2026-03/"
    # 지우지 않고 notes/.trash/<칸>/원래 경로 로 옮김
    trashed = store.get(sub.id)
    assert trashed.status == "trashed" and trashed.path.startswith(".trash/") and trashed.path.endswith("/회사/2026-03")
    assert store.get(inside.id).status == "trashed" and tree.page_source(store.get(inside.id)) == "내용"
    edit = note_edits.get(res["edit_id"])
    info = note_edits.edit_dict(edit)
    assert info["kind"] == "trash" and info["file"] == "회사/2026-03/" and info["inside_notes"] == 1
    assert info["page_alive"] is False
    with pytest.raises(note_tools.ToolError):
        note_edits.trash_page("r1", sub.id)  # 이미 휴지통
    note_edits.undo_edit(edit)
    assert store.get(sub.id).path == "회사/2026-03" and store.get(inside.id).path == "회사/2026-03/3월.md"
    assert note_edits.get(res["edit_id"]).status == "undone"


def test_trash_confirm_keeps_it_in_trash(ctx):
    page = note("메모", "x")
    res = note_edits.trash_page("r1", page.id)
    note_edits.confirm_edit(note_edits.get(res["edit_id"]))
    assert store.get(page.id).status == "trashed"  # 확정해도 영구 삭제는 아님
    assert note_edits.get(res["edit_id"]).status == "confirmed"


def test_create_undo_moves_to_trash(ctx):
    parent = make_page(None, "프로젝트")
    res = note_edits.create_page("r1", "새 노트", "내용", parent_id=parent.id)
    page = store.get(res["page_id"])
    assert page.parent_id == parent.id and _contents(page) == ["내용"] and res["file"] == "프로젝트/새 노트.md"
    with pytest.raises(note_tools.ToolError):
        note_edits.create_page("r1", "새 노트", "또", parent_id=parent.id)  # 같은 이름 금지
    note_edits.undo_edit(note_edits.get(res["edit_id"]))
    assert store.get(page.id).status == "trashed"


def test_page_routes_highlight_and_confirm(app, auth_client):
    with app.app_context():
        page = note("메모", "하나\n\n둘")
        page_id = page.id
        first = note_edits.edit_page("r1", page_id, "하나 고침\n\n둘", "")["edit_id"]
    url = f"/api/ai/pages/{page_id}/note-edits"
    edits = auth_client.get(url).get_json()["edits"]
    assert [e["id"] for e in edits] == [first] and len(edits[0]["changed_block_ids"]) == 1

    # 직접 고쳐 저장하면 강조는 저절로 사라짐 (아직 되돌릴 수 있음)
    auth_client.put(f"/api/pages/{page_id}/source", json={"markdown": "하나 고침\n\n둘 내가"})
    edits = auth_client.get(url).get_json()["edits"]
    assert edits[0]["status"] == "applied" and edits[0]["changed_block_ids"] == []

    # Ctrl+S → 확정
    auth_client.post(f"{url}/confirm", json={})
    assert auth_client.get(url).get_json()["edits"] == []
    res = auth_client.post(f"/api/ai/note-edits/{first}/undo", json={})
    assert res.status_code == 200  # 확정 뒤에도 채팅 카드에서 되돌릴 수 있음
    assert auth_client.get(f"/api/pages/{page_id}/source").get_json()["markdown"] == "하나\n\n둘 내가"
