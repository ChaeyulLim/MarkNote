"""채팅에서 이름으로 노트를 가리키기 + 대화가 "다루는 노트" 유지 (docs/claude-integration.md "노트 지목").

- "OO폴더의 OO노트 확인해줘" → 메시지에 나온 노트 제목을 서버가 찾아 그 노트 내용을 함께 보낸다.
  같은 이름이 여러 곳에 있으면 메시지에 함께 나온 상위 폴더 이름으로 고른다.
- AI 가 읽거나 고치거나 만든 노트, 사용자가 이름으로 부른 노트는 대화에 "다루는 노트"로 남고,
  다음 질문부터 매번 최신 내용을 다시 보낸다 → "거기에 추가해줘" 가 그 노트를 가리킨다.
- 노트 읽기 권한(ai_notes_read)이 꺼져 있으면 아무것도 하지 않는다 (호출하는 쪽에서 확인).
"""

import re

from . import page_tree as tree
from . import store
from .page_tree import Page

MAX_FOCUS = 3  # 한 대화에서 함께 다루는 노트 수
MIN_TITLE_CHARS = 2  # "a" 같은 한 글자 제목은 아무 문장에나 걸리므로 제외
PATH_SPLIT_RE = re.compile(r"\s*[/>›»]\s*")

key = tree._name_key


def _active_pages() -> list[Page]:
    return sorted((p for p in store.all_pages() if p.status != "trashed"), key=lambda p: p.id)


def _ancestor_keys(page: Page) -> list[str]:
    return [key(a.title) for a in tree.ancestors(page)]


def mentioned_pages(message: str, exclude_id: int | None = None) -> list[Page]:
    """메시지에 제목이 나온 노트. 상위 페이지 이름은 경로로만 쓰였으면(그 아래 노트도 나왔으면) 뺀다."""
    text = key(message)
    if not text:
        return []
    hits = [p for p in _active_pages() if len(key(p.title)) >= MIN_TITLE_CHARS and key(p.title) in text]
    # "회의" 와 "회의록" 이 둘 다 걸리면 긴 것만
    titles = {key(p.title) for p in hits}
    hits = [p for p in hits if not any(t != key(p.title) and key(p.title) in t for t in titles)]
    # 폴더 이름이 그 아래 노트를 가리키는 경로로만 쓰였으면 폴더 자체는 빼기
    parents = {a.id for p in hits for a in tree.ancestors(p)}
    hits = [p for p in hits if p.id not in parents]
    # 같은 제목이 여러 곳에 있으면 상위 이름이 메시지에 나온 쪽
    by_title: dict[str, list[Page]] = {}
    for p in hits:
        by_title.setdefault(key(p.title), []).append(p)
    result = []
    for same in by_title.values():
        if len(same) > 1:
            same = [p for p in same if any(a and a in text for a in _ancestor_keys(p))] or same
        result += same
    return [p for p in result if p.id != exclude_id][:MAX_FOCUS]


def find_page(path: str) -> dict:
    """AI 도구: "폴더 / 노트" 같은 경로(또는 제목만)로 노트 찾기."""
    from .note_tools import ToolError, _path

    parts = [key(p) for p in PATH_SPLIT_RE.split((path or "").strip()) if p.strip()]
    if not parts:
        raise ToolError("찾을 노트 이름을 입력하세요. 예: \"폴더 / 노트\"")
    pages = _active_pages()
    name = parts[-1]
    found = [p for p in pages if key(p.title) == name] or [p for p in pages if name in key(p.title)]
    if len(parts) > 1:
        def under(p):
            names = _ancestor_keys(p)
            return all(any(w == n or w in n for n in names) for w in parts[:-1])
        found = [p for p in found if under(p)] or found
    return {
        "query": path,
        "results": [{"id": p.id, "title": p.display_title, "kind": p.kind, "path": _path(p), "file": tree.file_path(p)}
                    for p in found[:10]],
        "hint": "read_page 로 내용을 읽으세요." if found else "없습니다. search_pages 로 내용 검색을 해 보세요.",
    }


def merge(previous: list[int], new: list[int]) -> list[int]:
    """새로 다룬 노트를 앞에, 중복 없이 MAX_FOCUS 개까지."""
    merged: list[int] = []
    for page_id in [*new, *previous]:
        if page_id not in merged:
            merged.append(page_id)
    return merged[:MAX_FOCUS]


def alive(page_ids: list[int]) -> list[Page]:
    pages = [store.get(i) for i in page_ids or []]
    return [p for p in pages if p is not None and p.status != "trashed"]
