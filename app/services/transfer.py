"""마크다운 내보내기(.md / .zip)와 가져오기 — 폴더 구조 그대로 (docs/storage.md "가져오기 · 내보내기").

zip 구조 (notes/ 와 같은 모양):
    회사/                     ← 폴더 (맨 위 = 프로젝트)
    회사/README.md            ← 폴더 설명
    회사/회의록.md             ← 노트 (순수 본문)
    회사/2026-03/3월 회의.md
    images/<uuid>.png         ← 각 .md 에서 상대 경로로 참조

노트끼리 링크는 상대 경로라 그대로 동작한다. 가져올 때 노션 zip 의 "X.md" + "X/" 짝은 폴더 X + 그 안 README.md 로.
"""

import io
import os
import posixpath
import re
import unicodedata
import zipfile

from . import note_links, store
from . import page_tree as tree
from .attachments import ImageError, find_upload, save_image
from .page_tree import Page

TEXT_EXTS = {".md", ".markdown", ".txt"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
IMAGES_DIR = "images"
MAX_ENTRIES = 5000
MAX_TOTAL_BYTES = 300 * 1024 * 1024  # 압축 해제 후 합계 (zip 폭탄 방지)

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
_H1 = re.compile(r"\A#[ \t]+(.+?)[ \t]*(?:\n|\Z)")
_IMAGE = re.compile(r"(!\[[^\]\n]*\]\()(<[^>\n]+>|[^)\s]+)((?:\s+\"[^\"\n]*\")?\))")
_FILES_URL = re.compile(r"^/files/([0-9a-f]{32})$")
_NOTION_ID = re.compile(r"\s+[0-9a-f]{32}$")


class TransferError(ValueError):
    pass


class ImportConflict(TransferError):
    """가져올 맨 위 폴더·노트의 이름이 대상 폴더에 이미 있음 → 사용자가 덮어쓰기/이름 바꾸기를 골라 다시 요청."""

    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        names = ", ".join(f'"{c["title"]}"' for c in conflicts[:3]) + (" 등" if len(conflicts) > 3 else "")
        super().__init__(f"같은 이름이 이미 있습니다: {names}")


ON_CONFLICT = ("overwrite", "rename")

safe_name = tree.safe_name


# ---------- 내보내기 ----------

def export_page_markdown(page: Page) -> str:
    """단일 .md: 파일 내용 그대로 (그림은 이 앱 주소 /files/...)."""
    return page.source + "\n"


def export_zip(roots: list[Page], upload_folder: str) -> bytes:
    """폴더·노트들을 notes/ 와 같은 구조로. roots 의 부모 폴더 기준 경로로 넣는다."""
    images: set[str] = set()  # uploads 안 파일 이름

    def image_for(target: str, md_dir: str) -> str:
        m = _FILES_URL.match(target)
        name = find_upload(upload_folder, m.group(1)) if m else None
        if name is None:
            return target
        images.add(name)
        return note_links.format_target(posixpath.relpath(f"{IMAGES_DIR}/{name}", md_dir or "."), "", False)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root in roots:
            base = posixpath.dirname(root.path)
            for p in [root, *tree.descendants(root)]:
                if p.status != root.status:
                    continue
                rel = posixpath.relpath(p.path, base) if base else p.path
                if tree.is_folder(p):
                    zf.writestr(rel + "/", b"")
                    continue
                md_dir = posixpath.dirname(rel)
                text = _IMAGE.sub(lambda mm: f"{mm.group(1)}{image_for(mm.group(2).strip('<>'), md_dir)}{mm.group(3)}", p.source)
                zf.writestr(rel, text + "\n")
        for name in sorted(images):
            zf.write(os.path.join(upload_folder, name), f"{IMAGES_DIR}/{name}")
    return buf.getvalue()


# ---------- 가져오기 ----------

def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949"):  # 윈도우 메모장 한글 파일 대비
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _normalize_path(path: str) -> str | None:
    p = posixpath.normpath(path.replace("\\", "/")).lstrip("/")
    if p in ("", ".") or p.startswith(".."):
        return None
    return unicodedata.normalize("NFC", p)  # macOS zip 의 한글(NFD) 대비


def _zip_name(info: zipfile.ZipInfo) -> str:
    # UTF-8 표시가 없는 zip(윈도우 탐색기 등)은 cp437 로 잘못 읽히므로 다시 해석
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


def _ext(path: str) -> str:
    return posixpath.splitext(path)[1].lower()


def _collect(uploads: list[tuple[str, bytes]]) -> tuple[dict[str, bytes], set[str]]:
    """(파일 경로 → 내용, 빈 폴더 포함 폴더 경로들)."""
    entries: dict[str, bytes] = {}
    dirs: set[str] = set()
    total = 0

    def add(path, data):
        nonlocal total
        total += len(data)
        if total > MAX_TOTAL_BYTES or len(entries) >= MAX_ENTRIES:
            raise TransferError("가져올 파일이 너무 많거나 큽니다.")
        entries[path] = data

    def add_zip(data, prefix, nested):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise TransferError("zip 파일을 읽을 수 없습니다.") from None
        with zf:
            for info in zf.infolist():
                name = _normalize_path(_zip_name(info))
                if name is None or name.startswith("__MACOSX/") or any(part.startswith(".") for part in name.split("/")):
                    continue
                name = posixpath.join(prefix, name) if prefix else name
                if info.is_dir():
                    dirs.add(name)
                    continue
                if total + info.file_size > MAX_TOTAL_BYTES:
                    raise TransferError("가져올 파일이 너무 많거나 큽니다.")
                ext = _ext(name)
                if ext == ".zip" and not nested:
                    # 노션 내보내기처럼 zip 안에 zip 이 한 겹 더 있는 경우
                    add_zip(zf.read(info), posixpath.dirname(name), True)
                elif ext in TEXT_EXTS or ext in IMAGE_EXTS:
                    add(name, zf.read(info))

    for filename, data in uploads:
        name = _normalize_path(posixpath.basename((filename or "").replace("\\", "/")))
        ext = _ext(name or "")
        if ext == ".zip":
            add_zip(data, "", False)
        elif name and (ext in TEXT_EXTS or ext in IMAGE_EXTS):
            add(name, data)
        else:
            raise TransferError(f"지원하지 않는 형식입니다: {filename} (.md, .txt, .zip 가능)")
    return entries, dirs


def _clean_name(name: str) -> str:
    """노션 내보내기 이름 끝의 번호(" 1a2b…") 빼기."""
    return _NOTION_ID.sub("", name).strip()


def _body(text: str, title: str) -> str:
    """순수 본문만: 앞머리(--- ---)와 파일 이름과 같은 첫 줄 # 제목은 뺀다."""
    text = text.replace("\r\n", "\n")
    m = _FRONT_MATTER.match(text)
    if m and all(line.partition(":")[0].strip().lower() in ("icon", "title", "") for line in m.group(1).split("\n")):
        text = text[m.end():]
    text = text.lstrip("\n")
    h = _H1.match(text)
    if h and _clean_name(h.group(1)) == title:
        text = text[h.end():]
    return text.strip("\n")


class _Node:
    """가져올 폴더 또는 노트 하나 (zip 안 경로 기준)."""

    def __init__(self, src: str, kind: str, title: str):
        self.src = src  # zip 안 경로 (노트는 .md 파일, 폴더는 디렉토리)
        self.kind = kind
        self.title = title
        self.children: list["_Node"] = []
        self.readme: str | None = None  # 폴더 설명으로 쓸 zip 안 .md
        self.page: Page | None = None
        self.readme_page: Page | None = None


def _build(entries: dict[str, bytes], dirs: set[str]) -> tuple[list[_Node], dict[str, _Node]]:
    """zip 안 경로들 → 폴더·노트 나무. 노션 "X.md" + "X/" 짝은 폴더 X + README."""
    md_paths = [p for p in entries if _ext(p) in TEXT_EXTS]
    folders = set(dirs)
    for p in [*md_paths, *[p for p in entries if _ext(p) in IMAGE_EXTS]]:
        d = posixpath.dirname(p)
        while d:
            folders.add(d)
            d = posixpath.dirname(d)
    folders = {d for d in folders if posixpath.basename(d).lower() != IMAGES_DIR and not any(
        part.lower() == IMAGES_DIR for part in d.split("/"))}
    # 그림만 든 폴더는 만들지 않는다
    has_md = {posixpath.dirname(p) for p in md_paths}
    for d in list(has_md):
        while d:
            has_md.add(d)
            d = posixpath.dirname(d)
    folders = {d for d in folders if d in has_md or d in dirs}

    by_src: dict[str, _Node] = {}
    for d in sorted(folders, key=lambda d: (d.count("/"), d)):
        by_src[d] = _Node(d, "folder", _clean_name(posixpath.basename(d)) or store.UNTITLED)
    for p in sorted(md_paths):
        stem = posixpath.splitext(p)[0]
        if stem in by_src and by_src[stem].readme is None and not store.is_readme(p):
            by_src[stem].readme = p  # 노션 짝: X.md → X/README.md
            by_src[p] = by_src[stem]
            continue
        parent = by_src.get(posixpath.dirname(p))
        if parent is not None and store.is_readme(p) and parent.readme is None:
            parent.readme = p
            by_src[p] = parent
            continue
        by_src[p] = _Node(p, "note", _clean_name(posixpath.splitext(posixpath.basename(p))[0]) or store.UNTITLED)

    roots: list[_Node] = []
    for src, node in by_src.items():
        if node.src != src:
            continue  # README 로 쓰인 .md
        parent = by_src.get(posixpath.dirname(src))
        (parent.children if parent is not None else roots).append(node)
    return roots, by_src


def import_markdown(
    uploads: list[tuple[str, bytes]], parent: Page | None, upload_folder: str, on_conflict: str | None = None
) -> list[Page]:
    """파일들을 폴더·노트로 만든다. 만들어진 맨 위 폴더·노트 목록을 돌려준다.

    parent 가 없으면 맨 위 폴더는 프로젝트, 맨 위 낱개 노트는 "분류 안 됨" 프로젝트로.
    같은 이름이 있으면: on_conflict 가 없으면 ImportConflict (아무것도 만들지 않음),
    "rename" 이면 "제목 (2)" 로, "overwrite" 면 기존 것을 휴지통으로 보내고 그 자리에 넣는다.
    """
    entries, dirs = _collect(uploads)
    if not any(_ext(p) in TEXT_EXTS for p in entries):
        raise TransferError("가져올 마크다운 파일이 없습니다.")
    if parent is not None and not tree.is_folder(parent):
        parent = parent.parent
    roots, by_src = _build(entries, dirs)

    inbox = tree.name_conflict(None, tree.INBOX_TITLE, kind="folder") if parent is None else None

    def owner_of(node: _Node) -> Page | None:
        if parent is not None or node.kind == "folder":
            return parent
        return inbox

    # 0) 대상 폴더에 이미 있는 이름인지 먼저 확인
    existing: dict[int, Page] = {}
    for node in roots:
        owner = owner_of(node)
        if parent is None and node.kind == "note" and owner is None:
            continue  # "분류 안 됨" 이 아직 없음
        found = tree.name_conflict(owner.id if owner else None, node.title, kind=node.kind)
        if found is not None:
            existing[id(node)] = found
    if existing and on_conflict not in ON_CONFLICT:
        raise ImportConflict([
            {"title": n.title, "existing_id": existing[id(n)].id, "existing_title": existing[id(n)].display_title}
            for n in roots if id(n) in existing
        ])
    if parent is None and inbox is None and any(n.kind == "note" for n in roots):
        inbox = tree.inbox()

    # 1) 폴더·노트 만들기 (가져오는 것끼리 이름이 겹치면 "(2)")
    def create(node: _Node, owner: Page | None, position=None):
        node.page = tree.create_page(owner, node.title, unique=True, position=position, kind=node.kind)
        for child in node.children:
            create(child, node.page)
        if node.readme:
            node.readme_page = store.create(node.page, "README")

    for node in roots:
        old = existing.get(id(node)) if on_conflict == "overwrite" else None
        position = None
        if old is not None and old.status != "trashed":
            position = old.position
            tree.trash(old)
        create(node, owner_of(node), position)

    # 2) 본문: 상대 링크는 새 위치 기준으로, 그림은 업로드로
    store.invalidate()
    new_path: dict[str, str] = {}  # zip 안 경로 → notes/ 기준 새 경로
    notes: list[tuple[str, Page]] = []  # (zip 안 .md, 페이지)
    for src, node in by_src.items():
        if node.src == src:
            page = store.get(node.page.id)
            new_path[src] = page.path + ("/" if node.kind == "folder" else "")
            if node.kind == "note":
                notes.append((src, page))
        else:  # README 로 쓰인 .md (노션 짝이면 링크는 그 폴더로)
            page = store.get(node.readme_page.id)
            new_path[src] = page.path if store.is_readme(src) else store.get(node.page.id).path + "/"
            notes.append((src, page))

    def mapping(target: str) -> str | None:
        found = new_path.get(target) or new_path.get(target.rstrip("/"))
        if found is None:
            return None
        return found if found.endswith("/") or not target.endswith("/") else found + "/"

    image_paths = {p for p in entries if _ext(p) in IMAGE_EXTS}
    saved_images: dict[str, str] = {}

    def image_url(md_path: str, raw: str) -> str:
        target, _, _ = note_links.split_target(raw)
        path = note_links.resolve(md_path, target) if note_links.is_relative(target) else None
        if path not in image_paths:
            return raw
        if path not in saved_images:
            try:
                saved_images[path] = f"/files/{save_image(entries[path], posixpath.basename(path), upload_folder)}"
            except ImageError:
                saved_images[path] = raw
        return saved_images[path]

    for src, page in notes:
        title = posixpath.splitext(posixpath.basename(src))[0]
        body = _body(_decode_text(entries[src]), _clean_name(title))
        body = _IMAGE.sub(lambda mm: f"{mm.group(1)}{image_url(src, mm.group(2))}{mm.group(3)}", body)
        body = note_links.rewrite(body, src, page.path, mapping)
        if body.strip():
            store.replace_source(page, body)

    return [store.get(n.page.id) for n in roots]
