"""페이지 트리 — 폴더·노트(.md) 다루기 (VS Code 탐색기처럼 맨 위에도 둘 다 놓인다). 실제 파일 읽기·쓰기는 store.py (docs/storage.md)."""

import os
import re

from flask import abort, current_app

from . import note_links, store
from .markdown_renderer import join_markdown, render_block
from .store import Block, Page, StoreError, safe_name

FILES_URL_RE = re.compile(r"/files/([0-9a-f]{32})")

__all__ = ["Block", "Page", "StoreError", "safe_name"]


def get_or_404(page_id) -> Page:
    page = store.get(page_id)
    if page is None:
        abort(404, description="페이지를 찾을 수 없습니다.")
    return page


def by_path(path: str) -> Page | None:
    """notes/ 기준 경로("회사/회의록.md", "회사/2026-03" 또는 "회사/2026-03/")의 활성 폴더·노트."""
    return store.by_path(path.strip("/"))


def link_resolver(page: Page | None):
    """그 노트 본문의 상대 링크 → (화면 주소, 있는지). 다른 링크는 None (그대로)."""
    def resolve(href: str):
        path, tail, _ = note_links.split_target(href)
        if page is None or not note_links.is_relative(path):
            return None
        target = note_links.resolve(page.path, path)
        found = by_path(target) if target else None
        if found is None:
            return href, False
        return found.url + tail, True
    return resolve


def block_dict(block: Block, page: Page | None = None) -> dict:
    return {"id": block.id, "content": block.content, "html": render_block(block.content, link_resolver(page))}


def active_tree() -> list[dict]:
    """사이드바용 활성 페이지 트리: [{"page": Page, "children": [...]}, ...]"""

    def build(parent_id):
        return [{"page": p, "children": build(p.id)} for p in store.children(parent_id, "active")]

    return build(None)


def tree_json() -> list[dict]:
    """탐색기용 활성 트리 (JSON): 폴더 먼저, 그다음 정한 순서."""

    def row(node):
        p = node["page"]
        item = {"id": p.id, "kind": p.kind, "name": p.display_title, "path": file_path(p), "abs": abs_file_path(p), "url": p.url}
        if is_folder(p):
            item["children"] = [row(c) for c in node["children"]]
        return item

    return [row(n) for n in active_tree()]


def descendants(page: Page) -> list[Page]:
    return store.descendants(page)


def ancestors(page: Page) -> list[Page]:
    """루트부터 부모까지 순서대로."""
    chain: list[Page] = []
    seen = {page.id}
    current = page.parent
    while current is not None and current.id not in seen:
        chain.append(current)
        seen.add(current.id)
        current = current.parent
    return list(reversed(chain))


def siblings_around(page: Page) -> tuple[Page | None, Page | None]:
    siblings = [p for p in store.children(page.parent_id, "active") if not is_folder(p)]
    ids = [p.id for p in siblings]
    if page.id not in ids:
        return None, None
    i = ids.index(page.id)
    return (siblings[i - 1] if i > 0 else None), (siblings[i + 1] if i + 1 < len(siblings) else None)


def recent(limit: int = 10) -> list[Page]:
    pages = [p for p in store.all_pages() if p.status == "active"]
    return sorted(pages, key=lambda p: p.updated_at, reverse=True)[:limit]


# ---------- 폴더 · 노트 ----------

def is_folder(page: Page) -> bool:
    return page.kind == "folder"


def file_path(page: Page) -> str:
    """화면에 보여 주는 파일 위치 (notes 폴더 기준): 폴더 "회사/", 노트 "회사/회의록.md"."""
    return page.file


def abs_file_path(page: Page) -> str:
    """서버 컴퓨터의 실제 위치."""
    return os.path.normpath(os.path.join(os.path.abspath(store.root()), *page.path.split("/")))


def create_page(parent: Page | None, title: str = "", markdown: str = "", unique: bool = False,
                position: int | None = None, kind: str = "note") -> Page:
    """새 폴더 또는 노트 (parent 가 없으면 맨 위). 이름이 겹치면 NameConflict (unique=True 면 "(2)")."""
    title = (title or "").strip()[:200]
    parent_id = parent.id if parent else None
    if not title or unique:
        title = unique_title(parent_id, title or store.UNTITLED, kind=kind)
    else:
        check_name(parent_id, title, kind=kind)
    page = store.create(parent, title, markdown, position, kind)
    return store.get(page.id)


def folder_notes(folder: Page) -> list[Page]:
    """폴더 안 노트 전부 (하위 폴더까지): 폴더마다 노트 → 하위 폴더 순. "OO 폴더 확인해줘" 용."""
    result: list[Page] = []

    def walk(f: Page):
        kids = store.children(f.id, f.status)
        result.extend(k for k in kids if not is_folder(k))
        for k in kids:
            if is_folder(k):
                walk(k)

    if is_folder(folder):
        walk(folder)
    return result


def rename_page(page: Page, title: str) -> Page:
    title = (title or "").strip()[:200]
    if not title:
        if store.UNTITLED_RE.match(page.name):
            return page
        title = unique_title(page.parent_id, store.UNTITLED, page.id, kind=page.kind)
    else:
        check_name(page.parent_id, title, page.id, kind=page.kind)
    return store.rename(page, title)


# ---------- 같은 폴더 안 같은 이름 금지 ----------
# 파일 이름이 곧 제목이므로 같은 폴더(부모) 안에서 겹칠 수 없다. 비교는 공백·대소문자 무시, 파일 이름에 못 쓰는 글자 뺀 뒤.
# 폴더 "회의" 와 노트 "회의.md" 는 디스크에서 다른 이름이라 함께 둘 수 있다 → 같은 종류끼리만 비교 (kind)

class NameConflict(ValueError):
    def __init__(self, existing: Page):
        self.existing = existing
        what = "폴더" if is_folder(existing) else "노트"
        where = f'"{existing.parent.display_title}" 안에' if existing.parent_id else "맨 위에"
        super().__init__(f'{where} 같은 이름의 {what} "{existing.display_title}"가 이미 있습니다.')


def _name_key(title: str | None) -> str:
    return store.name_key(safe_name(title)) if (title or "").strip() else ""


def name_conflict(parent_id: int | None, title: str | None, exclude_id: int | None = None,
                  kind: str | None = None) -> Page | None:
    """같은 폴더 안 같은 이름 (kind 를 주면 그 종류끼리만 — 폴더 "회의" 와 노트 "회의.md" 는 겹치지 않음)."""
    key = _name_key(title)
    if not key:
        return None
    for p in store.children(parent_id, "active"):
        if p.id != exclude_id and (kind is None or p.kind == kind) and store.name_key(p.name) == key:
            return p
    return None


def check_name(parent_id: int | None, title: str | None, exclude_id: int | None = None, kind: str = "note") -> None:
    existing = name_conflict(parent_id, title, exclude_id, kind=kind)
    if existing is not None:
        raise NameConflict(existing)


def unique_title(parent_id: int | None, title: str, exclude_id: int | None = None, kind: str = "note") -> str:
    """겹치면 "제목 (2)", "제목 (3)" …"""
    title = safe_name(title)
    if name_conflict(parent_id, title, exclude_id, kind) is None:
        return title
    n = 2
    while name_conflict(parent_id, f"{title} ({n})", exclude_id, kind) is not None:
        n += 1
    return f"{title} ({n})"


def is_self_or_descendant(candidate: Page, page: Page) -> bool:
    return candidate.id == page.id or candidate.id in {d.id for d in descendants(page)}


# ---------- 보관함 · 휴지통 ----------

def top_level_with_status(status: str) -> list[Page]:
    """보관함/휴지통 목록: 칸의 맨 위 페이지만."""
    pages = [p for p in store.all_pages() if p.status == status and p.bucket]
    return sorted(pages, key=lambda p: p.trashed_at or p.updated_at, reverse=True)


def archive(page: Page) -> None:
    if page.status == "active":
        store.to_bucket(page, "archived")


def trash(page: Page) -> None:
    if page.status != "trashed":
        store.to_bucket(page, "trashed")


def restore(page: Page) -> Page:
    """꺼내기 — 원래 자리로. 원래 폴더가 없어졌으면 그 경로의 폴더를 다시 만든다 (VS Code 휴지통 복원처럼).
    휴지통에 있는 사이 같은 이름이 생겼으면 "(2)" 를 붙인다."""
    if page.status == "active":
        return page
    parent = page.parent
    if parent is None or parent.status != "active":
        parent = ensure_folders(store.original_path(page).split("/")[:-1])
    title = unique_title(parent.id if parent else None, page.name, kind=page.kind)
    return store.restore(page, parent, title)


def ensure_folders(names: list[str]) -> Page | None:
    """맨 위부터 names 경로의 폴더 (없는 것은 만듦). 빈 목록이면 None (맨 위)."""
    parent = None
    for name in names:
        found = name_conflict(parent.id if parent else None, name, kind="folder")
        parent = found if found is not None else store.create(parent, name, kind="folder")
    return parent


def delete_permanently(page: Page, upload_folder: str) -> None:
    """영구 삭제 — 노트 본문에 들어 있던 그림 파일도 지운다. 다른 노트(복사본 등)가 아직 쓰는 그림은 남긴다."""
    gone = [page, *descendants(page)]
    gone_ids = {p.id for p in gone}
    still_used = {f for p in store.all_pages() if p.id not in gone_ids for f in FILES_URL_RE.findall(p.source)}
    for p in gone:
        for file_id in set(FILES_URL_RE.findall(p.source)) - still_used:
            for name in _upload_names(upload_folder, file_id):
                try:
                    os.remove(os.path.join(upload_folder, name))
                except OSError:
                    current_app.logger.warning("첨부 파일 삭제 실패: %s", name)
    store.delete(page)


def copy_page(page: Page, parent: Page | None) -> Page:
    """복사본 만들기 (폴더면 안의 것까지) — parent 폴더 안에 (None 이면 맨 위). 이름이 겹치면 "(2)".
    복사본 안의 링크: 함께 복사된 노트끼리는 복사본을, 그 밖은 원래 대상을 가리키도록 상대 경로를 다시 쓴다.
    그림(/files/…)은 원본과 같은 파일을 함께 쓴다."""
    if parent is not None and is_self_or_descendant(parent, page):
        raise StoreError("자기 자신이나 그 안의 폴더에는 붙여 넣을 수 없습니다.")
    pairs: list[tuple[Page, Page]] = []

    def copy(src: Page, dest: Page | None, title: str) -> Page:
        made = store.create(dest, title, kind=src.kind)
        pairs.append((src, made))
        for child in store.children(src.id, status="active"):
            copy(child, made, child.name)
        return made

    top = copy(page, parent, unique_title(parent.id if parent else None, page.name, kind=page.kind))
    moved = {src.path: made.path for src, made in pairs}
    for src, made in pairs:
        if src.kind == "note" and src.source:
            body = note_links.rewrite(src.source, src.path, made.path, lambda target: moved.get(target.rstrip("/"), target.rstrip("/")) + target[len(target.rstrip("/")):])
            store.replace_source(store.get(made.id), body)
    return store.get(top.id)


def _upload_names(upload_folder: str, file_id: str) -> list[str]:
    try:
        return [n for n in os.listdir(upload_folder) if n.startswith(file_id + ".")]
    except FileNotFoundError:
        return []


# ---------- 블록 ----------

def insert_blocks(page: Page, index: int, contents: list[str]) -> list[Block]:
    blocks: list = list(page.blocks)
    blocks[index:index] = contents
    saved = store.save_blocks(page, blocks)
    return saved[index:index + len(contents)]


def replace_markdown(page: Page, markdown: str) -> list[Block]:
    """페이지 본문 전체를 마크다운으로 교체 (빈 줄 기준으로 블록 분할, 같은 블록은 번호 유지)."""
    return store.replace_source(page, markdown)


def page_source(page: Page) -> str:
    return join_markdown([b.content for b in page.blocks])
