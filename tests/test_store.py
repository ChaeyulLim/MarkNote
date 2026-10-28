"""파일 저장 (docs/storage.md): instance/notes/<프로젝트>/<폴더…>/<노트>.md"""

import os

import pytest
from conftest import folder, make_page

from app.services import page_tree as tree
from app.services import store


def _files(app):
    root = app.config["NOTES_DIR"]
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".marknote"]
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        found += [f"{rel}/{n}".removeprefix("./") for n in filenames]
    return sorted(found)


def _read(page):
    with open(tree.abs_file_path(page), encoding="utf-8") as f:
        return f.read()


def test_layout_on_disk(ctx, app):
    project = make_page(None, "회사")
    note = make_page(project, "회의록", "안건")
    sub = folder(project, "2026-03")
    make_page(sub, "3월 회의", "내용")
    assert _files(app) == ["회사/2026-03/3월 회의.md", "회사/회의록.md"]
    assert tree.file_path(project) == "회사/" and tree.file_path(sub) == "회사/2026-03/"
    assert tree.file_path(note) == "회사/회의록.md"
    assert tree.abs_file_path(note) == os.path.join(app.config["NOTES_DIR"], "회사", "회의록.md")
    assert store.get(note.id).url == "/n/%ED%9A%8C%EC%82%AC/%ED%9A%8C%EC%9D%98%EB%A1%9D.md"
    assert store.get(sub.id).url.endswith("/")


def test_note_file_is_pure_body(ctx, app):
    note = make_page(make_page(None, "회사"), "회의록", "안건\n\n둘째")
    assert _read(note) == "안건\n\n둘째\n"


def test_notes_only_inside_folders(ctx):
    project = make_page(None, "회사")
    note = make_page(project, "회의록")
    with pytest.raises(store.StoreError):
        tree.create_page(note, "노트 아래")
    with pytest.raises(store.StoreError):
        store.save_blocks(project, ["내용"])


def test_unsafe_title_characters(ctx, app):
    note = make_page(make_page(None, "회사"), 'a/b:c?', "x")
    assert note.path == "회사/abc.md"


def test_folders_first_then_user_order(ctx):
    project = make_page(None, "회사")
    a, b = make_page(project, "가"), make_page(project, "나")
    sub = folder(project, "폴더")
    assert [p.id for p in store.children(project.id)] == [sub.id, a.id, b.id]
    store.set_positions([b.id, a.id])
    assert [p.id for p in store.children(project.id)] == [sub.id, b.id, a.id]


def test_readme_is_folder_description(ctx, app):
    project = make_page(None, "회사")
    readme = tree.create_readme(project)
    store.replace_source(readme, "회사 소개")
    make_page(project, "회의록")
    assert [p.title for p in store.children(project.id)] == ["회의록"]  # 목록에는 안 나옴
    assert tree.readme(store.get(project.id)).id == readme.id
    assert "회사/README.md" in _files(app)


def test_rename_and_move_keep_ids(ctx, app):
    a, b = make_page(None, "가"), make_page(None, "나")
    sub = folder(a, "폴더")
    note = make_page(sub, "노트", "본문")
    tree.rename_page(store.get(sub.id), "새 폴더")
    assert store.get(note.id).path == "가/새 폴더/노트.md"
    store.move(store.get(sub.id), store.get(b.id))
    assert store.get(note.id).path == "나/새 폴더/노트.md" and store.get(note.id).parent_id == sub.id


def test_rename_rewrites_links_everywhere(ctx, app):
    project = make_page(None, "회사")
    sub = folder(project, "2026-03")
    target = make_page(sub, "3월 회의", "안건")
    other = make_page(project, "목차", "[3월](<2026-03/3월 회의.md>) · [폴더](2026-03/) · [밖](https://x.y/a.md) · [없음](없는.md)")
    inside = make_page(sub, "메모", "[목차](../목차.md)")

    tree.rename_page(store.get(target.id), "삼월")
    assert tree.page_source(store.get(other.id)).startswith("[3월](<2026-03/삼월.md>)")

    tree.rename_page(store.get(sub.id), "3월")
    src = tree.page_source(store.get(other.id))
    assert src == "[3월](<3월/삼월.md>) · [폴더](3월/) · [밖](https://x.y/a.md) · [없음](없는.md)"
    assert tree.page_source(store.get(inside.id)) == "[목차](../목차.md)"  # 같이 옮겨져 상대 위치 그대로


def test_move_rewrites_links_inside_moved_note(ctx, app):
    project = make_page(None, "회사")
    sub = folder(project, "보관")
    target = make_page(project, "회의록", "x")
    moved = make_page(project, "메모", "[회의록](회의록.md) 그리고 [이름 있는](%EB%A9%94%EB%AA%A8.md)")
    store.move(store.get(moved.id), store.get(sub.id))
    assert tree.page_source(store.get(moved.id)) == "[회의록](../회의록.md) 그리고 [이름 있는](%EB%A9%94%EB%AA%A8.md)"  # 자기 자신은 상대 위치 그대로
    store.move(store.get(target.id), store.get(sub.id))
    assert tree.page_source(store.get(moved.id)).startswith("[회의록](회의록.md)")


def test_trash_and_restore_to_original_place(ctx, app):
    project = make_page(None, "회사")
    sub = folder(project, "2026-03")
    note = make_page(sub, "3월", "내용")
    tree.trash(store.get(sub.id))
    trashed = store.get(sub.id)
    assert trashed.status == "trashed" and trashed.path.startswith(".trash/")
    assert store.get(note.id).status == "trashed"
    assert [p.id for p in tree.top_level_with_status("trashed")] == [sub.id]
    restored = tree.restore(trashed)
    assert restored.path == "회사/2026-03" and store.get(note.id).path == "회사/2026-03/3월.md"
    assert tree.page_source(store.get(note.id)) == "내용"


def test_restored_note_without_folder_goes_to_inbox(ctx):
    project = make_page(None, "회사")
    note = make_page(project, "회의록", "x")
    tree.trash(store.get(note.id))
    tree.trash(store.get(project.id))
    restored = tree.restore(store.get(note.id))
    assert restored.path == f"{tree.INBOX_TITLE}/회의록.md"


def test_files_added_outside_get_ids(ctx, app):
    root = app.config["NOTES_DIR"]
    os.makedirs(os.path.join(root, "밖", "폴더"))
    with open(os.path.join(root, "밖", "메모.md"), "w", encoding="utf-8") as f:
        f.write("# 손으로 만든 노트")
    with open(os.path.join(root, "맨위.md"), "w", encoding="utf-8") as f:
        f.write("무시됨")
    store.invalidate()
    project = tree.name_conflict(None, "밖")
    assert project is not None and project.kind == "folder"
    assert [(p.title, p.kind) for p in store.children(project.id)] == [("폴더", "folder"), ("메모", "note")]
    assert [p.title for p in store.children(None)] == ["밖"]  # 맨 위 .md 는 무시
    memo = tree.name_conflict(project.id, "메모")
    assert tree.page_source(memo) == "# 손으로 만든 노트"
    store.invalidate()
    assert tree.name_conflict(project.id, "메모").id == memo.id  # 번호는 index.json 에 남음
    assert tree.by_path("밖/메모.md").id == memo.id and tree.by_path("밖/폴더/").kind == "folder"


def test_file_deleted_outside_disappears(ctx, app):
    note = make_page(make_page(None, "회사"), "회의록", "x")
    os.remove(tree.abs_file_path(note))
    store.invalidate()
    assert store.get(note.id) is None


def test_folder_and_note_may_share_a_name(ctx, app):
    """폴더 "회의" 와 노트 "회의.md" 는 디스크에서 다른 이름 → 함께 둘 수 있다. 같은 종류끼리만 겹침 금지."""
    project = make_page(None, "회사")
    sub = folder(project, "회의")
    note = tree.create_page(project, "회의")  # 노트
    assert note.path == "회사/회의.md" and sub.path == "회사/회의"
    with pytest.raises(tree.NameConflict):
        tree.create_page(project, "회의")  # 노트끼리는 안 됨
    with pytest.raises(tree.NameConflict):
        tree.create_page(project, "회의", kind="folder")  # 폴더끼리도 안 됨
    other = tree.create_page(project, "메모")
    tree.rename_page(store.get(other.id), "회의 (2)")
    assert tree.unique_title(project.id, "회의", kind="note") == "회의 (3)"
    assert tree.unique_title(project.id, "회의", kind="folder") == "회의 (2)"
    store.move(store.get(note.id), store.get(sub.id))  # 같은 이름 폴더 안으로 옮겨도 됨
    assert store.get(note.id).path == "회사/회의/회의.md"
