import pytest
from conftest import folder, make_page, note

from app.services import note_tools, store
from app.services import page_tree as tree


def test_search_by_title_and_body_excludes_trash(ctx):
    a = note("회의록", "안건 정리")
    b = note("일기", "오늘 회의가 길었다")
    tree.trash(note("버린 회의", "내용"))
    results = note_tools.search_pages("회의")["results"]
    assert [r["id"] for r in results] == [a.id, b.id]
    assert "회의" in results[1]["snippet"]
    assert results[0]["file"] == "프로젝트/회의록.md"


def test_search_requires_query(ctx):
    with pytest.raises(note_tools.ToolError):
        note_tools.search_pages("  ")


def test_search_hides_pages_matched_only_by_secret(ctx):
    note("메모", "비밀번호 {{hunter2}}")
    assert note_tools.search_pages("hunter2")["results"] == []


def test_list_pages_as_tree(ctx):
    root = make_page(None, "프로젝트")
    make_page(root, "설계")
    tree.trash(make_page(None, "휴지통"))
    rows = note_tools.list_pages()["pages"]
    assert [(r["title"], r["kind"], r["file"], r["depth"]) for r in rows] == [
        ("프로젝트", "folder", "프로젝트/", 0), ("설계", "note", "프로젝트/설계.md", 1)]


def test_read_note(ctx):
    page = note("설계", "소개\n\n[상세](상세.md)")
    info = note_tools.read_page(page.id)
    assert info["path"] == "프로젝트 / 설계" and info["file"] == "프로젝트/설계.md"
    assert info["markdown"] == "소개\n\n[상세](상세.md)" and info["kind"] == "note"


def test_read_folder_returns_every_note_inside(ctx):
    """"회사 폴더 확인해줘" → 폴더를 읽으면 안의 노트 전부 (하위 폴더까지). README.md 도 그냥 노트."""
    project = make_page(None, "회사")
    make_page(project, "README", "회사 소개")
    make_page(project, "회의록", "안건 {{비밀}}")
    sub = folder(project, "2026-03")
    make_page(sub, "3월", "3월 내용")
    info = note_tools.read_page(project.id)
    assert info["kind"] == "folder" and [s["title"] for s in info["sub_pages"]] == ["2026-03", "README", "회의록"]
    assert [n["file"] for n in info["notes"]] == ["회사/README.md", "회사/회의록.md", "회사/2026-03/3월.md"]
    assert info["notes"][0]["markdown"] == "회사 소개" and info["notes"][2]["markdown"] == "3월 내용"
    assert "비밀" not in info["notes"][1]["markdown"]  # 비공개는 가림
    assert info["more_notes"] == []


def test_read_big_folder_lists_the_rest(ctx, monkeypatch):
    monkeypatch.setattr(note_tools, "MAX_READ_CHARS", 10)
    project = make_page(None, "회사")
    make_page(project, "가", "12345678")
    make_page(project, "나", "12345678")
    info = note_tools.read_page(project.id)
    assert [n["title"] for n in info["notes"]] == ["가"] and [n["title"] for n in info["more_notes"]] == ["나"]


def test_read_trashed_page_is_refused(ctx):
    page = note("x")
    tree.trash(page)
    with pytest.raises(note_tools.ToolError):
        note_tools.read_page(store.get(page.id).id)
