"""Claude 가 MarkNote 노트를 읽는 도구 + 공통 도우미. MCP 서버(app/mcp_server.py)가 감싸서 Claude Code 에 제공한다.

노트는 instance/notes/ 의 .md 파일 (docs/storage.md). AI 는 파일을 직접 열지 않고 이 도구로만 읽는다 —
그래야 비공개 표시({{ }}, :::비공개)를 항상 가린 채로 보낼 수 있다.
바로 고치는 도구(edit_page / create_page)는 note_edits.py.
"""

import difflib

from . import page_tree as tree
from . import secret_text, store
from .page_tree import Page

MAX_READ_CHARS = 50_000
MAX_SEARCH_RESULTS = 20
MAX_LIST_PAGES = 500
MAX_DIFF_LINES = 400


class ToolError(ValueError):
    pass


class ProposalConflict(ToolError):
    """되돌리려는 부분을 그 뒤 사용자가 고침."""


def _visible_page(page_id: int) -> Page:
    page = store.get(page_id)
    if page is None or page.status == "trashed":
        raise ToolError(f"페이지 {page_id} 를 찾을 수 없습니다 (없거나 휴지통에 있음).")
    return page


def _path(page: Page) -> str:
    return " / ".join(p.display_title for p in [*tree.ancestors(page), page])


def _snippet(text: str, query: str, width: int = 60) -> str:
    i = text.lower().find(query.lower())
    if i < 0:
        return text[: width * 2].replace("\n", " ")
    start = max(0, i - width)
    return ("…" if start else "") + text[start : i + len(query) + width].replace("\n", " ") + "…"


def _row(page: Page, **extra) -> dict:
    return {"id": page.id, "title": page.display_title, "kind": page.kind, "path": _path(page),
            "file": tree.file_path(page), "status": page.status, **extra}


# ---------- 읽기 ----------

def search_pages(query: str, limit: int = 10) -> dict:
    query = (query or "").strip()
    if not query:
        raise ToolError("검색어를 입력하세요.")
    limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
    needle = query.lower()
    pages = [p for p in store.all_pages() if p.status != "trashed"]
    by_title = [p for p in pages if needle in p.display_title.lower()]
    results = []
    for page in [*by_title, *[p for p in pages if p not in by_title]]:
        if len(results) >= limit:
            break
        # 비공개 부분으로만 걸린 페이지는 빼야 "어디에 있는지"도 새지 않음 → 가린 글에서 찾는다
        source = secret_text.redact(tree.page_source(page))
        if page not in by_title and needle not in source.lower():
            continue
        results.append(_row(page, snippet=_snippet(source, query)))
    return {"query": query, "results": results}


def list_pages() -> dict:
    rows: list[dict] = []

    def walk(parent_id, depth):
        for p in store.children(parent_id, readme=True):
            if p.status == "trashed" or len(rows) >= MAX_LIST_PAGES:
                continue
            rows.append({"id": p.id, "title": p.display_title, "kind": p.kind, "file": tree.file_path(p),
                         "depth": depth, "status": p.status})
            walk(p.id, depth + 1)

    walk(None, 0)
    return {"pages": rows, "truncated": len(rows) >= MAX_LIST_PAGES}


def read_page(page_id: int) -> dict:
    """노트는 내용. 폴더는 안의 노트 전부(하위 폴더까지, 길면 앞에서부터 — 넘친 노트는 more_notes 목록)."""
    page = _visible_page(page_id)
    info = {
        "id": page.id,
        "title": page.title,
        "kind": page.kind,
        "status": page.status,
        "path": _path(page),
        "file": tree.file_path(page),
        "parent_id": page.parent_id,
    }
    if not tree.is_folder(page):
        source = secret_text.redact(tree.page_source(page))
        return {**info, "markdown": source[:MAX_READ_CHARS], "truncated": len(source) > MAX_READ_CHARS}
    kids = [c for c in store.children(page.id) if c.status != "trashed"]
    readme = tree.readme(page)
    notes, more, used = [], [], 0
    for note in tree.folder_notes(page):
        row = {"id": note.id, "title": note.display_title, "file": tree.file_path(note)}
        source = secret_text.redact(tree.page_source(note))
        if more or used + len(source) > MAX_READ_CHARS:
            more.append(row)
            continue
        used += len(source)
        notes.append({**row, "markdown": source})
    return {
        **info,
        "readme_id": readme.id if readme else None,
        "sub_pages": [{"id": c.id, "title": c.display_title, "kind": c.kind, "file": tree.file_path(c)} for c in kids],
        "notes": notes,
        "more_notes": more,
        "hint": "폴더 안 노트 전부입니다 (README.md = 폴더 설명)." + (
            " 길어서 more_notes 는 내용을 빼고 목록만 — 필요한 것을 read_page 로 하나씩 읽으세요." if more else ""),
    }


# ---------- 수정 도구 공통 ----------

def _check_name(parent_id, title, exclude_id=None, kind="note") -> None:
    try:
        tree.check_name(parent_id, title, exclude_id, kind)
    except tree.NameConflict as err:
        raise ToolError(f"{err} 다른 이름을 쓰세요.") from None


def _restore_secrets(markdown: str, base: str) -> str:
    """AI 는 비공개 부분을 ⟦🔒…⟧ 로만 본다 → 원래 내용으로 되돌리고, 하나라도 빠지면 거절."""
    try:
        restored = secret_text.restore(markdown, secret_text.tag_map(base))
        secret_text.check_kept(secret_text.secrets_in(base), secret_text.secrets_in(restored))
    except secret_text.SecretError as err:
        raise ToolError(str(err)) from None
    return restored


def _diff(before: str, after: str) -> list[dict]:
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=2))[2:]
    result = []
    for line in lines[:MAX_DIFF_LINES]:
        kind = {"+": "add", "-": "del", "@": "hunk"}.get(line[:1], "same")
        result.append({"t": kind, "s": line if kind == "hunk" else line[1:]})
    if len(lines) > MAX_DIFF_LINES:
        result.append({"t": "hunk", "s": f"… {len(lines) - MAX_DIFF_LINES}줄 더 있음"})
    return result
