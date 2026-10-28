"""페이지 트리 — 폴더(맨 위 폴더 = 프로젝트)·노트(.md) 다루기. 실제 파일 읽기·쓰기는 store.py (docs/storage.md)."""

import os
import re

from flask import abort, current_app

from . import note_links, store
from .markdown_renderer import join_markdown, render_block
from .store import Block, Page, StoreError, safe_name

INBOX_TITLE = "분류 안 됨"  # 프로젝트 밖에 놓일 노트(따로 가져온 .md, 부모가 없어진 채 복원한 노트)를 모으는 프로젝트
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


def is_project(page: Page) -> bool:
    """맨 위 폴더."""
    return is_folder(page) and page.parent_id is None


def file_path(page: Page) -> str:
    """화면에 보여 주는 파일 위치 (notes 폴더 기준): 폴더 "회사/", 노트 "회사/회의록.md"."""
    return page.file


def abs_file_path(page: Page) -> str:
    """서버 컴퓨터의 실제 위치."""
    return os.path.normpath(os.path.join(os.path.abspath(store.root()), *page.path.split("/")))


def inbox() -> Page:
    """"분류 안 됨" 프로젝트 (없으면 만듦)."""
    found = name_conflict(None, INBOX_TITLE, kind="folder")
    return found if found is not None else store.create(None, INBOX_TITLE)


def create_page(parent: Page | None, title: str = "", markdown: str = "", unique: bool = False,
                position: int | None = None, kind: str = "note") -> Page:
    """새 폴더 또는 노트 (parent 가 없으면 프로젝트 = 맨 위 폴더). 이름이 겹치면 NameConflict (unique=True 면 "(2)")."""
    title = (title or "").strip()[:200]
    parent_id = parent.id if parent else None
    kind = "folder" if parent is None else kind
    if not title or unique:
        title = unique_title(parent_id, title or store.UNTITLED, kind=kind)
    else:
        check_name(parent_id, title, kind=kind)
    page = store.create(parent, title, markdown, position, kind)
    return store.get(page.id)


def readme(folder: Page) -> Page | None:
    return store.readme_of(folder) if is_folder(folder) else None


def folder_notes(folder: Page) -> list[Page]:
    """폴더 안 노트 전부 (하위 폴더까지): 폴더마다 README → 노트 → 하위 폴더 순. "프로젝트 확인해줘" 용."""
    result: list[Page] = []

    def walk(f: Page):
        found = readme(f)
        if found is not None:
            result.append(found)
        kids = store.children(f.id, f.status)
        result.extend(k for k in kids if not is_folder(k))
        for k in kids:
            if is_folder(k):
                walk(k)

    if is_folder(folder):
        walk(folder)
    return result


def create_readme(folder: Page) -> Page:
    """폴더 설명 README.md (없으면 만듦)."""
    found = readme(folder)
    return found if found is not None else store.create(folder, "README")


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
        if existing.parent_id:
            what = "폴더" if is_folder(existing) else "노트"
            message = f'"{existing.parent.display_title}" 안에 같은 이름의 {what} "{existing.display_title}"가 이미 있습니다.'
        else:
            message = f'같은 이름의 프로젝트 "{existing.display_title}"가 이미 있습니다.'
        super().__init__(message)


def _name_key(title: str | None) -> str:
    return store.name_key(safe_name(title)) if (title or "").strip() else ""


def name_conflict(parent_id: int | None, title: str | None, exclude_id: int | None = None,
                  kind: str | None = None) -> Page | None:
    """같은 폴더 안 같은 이름 (kind 를 주면 그 종류끼리만 — 폴더 "회의" 와 노트 "회의.md" 는 겹치지 않음)."""
    key = _name_key(title)
    if not key:
        return None
    for p in store.children(parent_id, "active", readme=True):
        if p.id != exclude_id and (kind is None or p.kind == kind) and store.name_key(p.name) == key:
            return p
    return None


def check_name(parent_id: int | None, title: str | None, exclude_id: int | None = None, kind: str = "note") -> None:
    existing = name_conflict(parent_id, title, exclude_id, kind="folder" if parent_id is None else kind)
    if existing is not None:
        raise NameConflict(existing)


def unique_title(parent_id: int | None, title: str, exclude_id: int | None = None, kind: str = "note") -> str:
    """겹치면 "제목 (2)", "제목 (3)" …"""
    kind = "folder" if parent_id is None else kind
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
    """꺼내기. 원래 폴더가 살아 있으면 그 안으로. 없으면 폴더는 프로젝트(맨 위)로, 노트는 "분류 안 됨" 프로젝트로.
    휴지통에 있는 사이 같은 이름이 생겼으면 "(2)" 를 붙인다."""
    if page.status == "active":
        return page
    parent = page.parent
    if parent is None or parent.status != "active":
        parent = None if is_folder(page) else inbox()
    title = unique_title(parent.id if parent else None, page.name, kind=page.kind)
    return store.restore(page, parent, title)


def delete_permanently(page: Page, upload_folder: str) -> None:
    """영구 삭제 — 노트 본문에 들어 있던 그림 파일도 지운다."""
    for p in [page, *descendants(page)]:
        for file_id in FILES_URL_RE.findall(p.source):
            for name in _upload_names(upload_folder, file_id):
                try:
                    os.remove(os.path.join(upload_folder, name))
                except OSError:
                    current_app.logger.warning("첨부 파일 삭제 실패: %s", name)
    store.delete(page)


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
