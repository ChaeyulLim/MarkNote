"""노트 저장소 — DB 없이 instance/notes/ 의 폴더와 .md 파일 (docs/storage.md).

    notes/회사/                 프로젝트 = 맨 위 폴더
    notes/회사/README.md        폴더 설명 (있으면 폴더 화면 위에 보임, 노트 목록에는 안 나옴)
    notes/회사/회의록.md         노트
    notes/회사/2026-03/         폴더 안 폴더 (그냥 디렉토리)
    notes/회사/2026-03/3월.md    노트
    notes/.archive/<n>/…        보관함, notes/.trash/<n>/… 휴지통 (n = 옮길 때마다 새 칸, 원래 경로 그대로 아래에)
    notes/.marknote/index.json  페이지 번호·순서·보관/휴지통 칸 정보

페이지 번호(id)는 index.json 에만 있다 (파일은 순수 본문, 앞머리·아이콘 없음). 화면 주소는 경로(/n/...). 앱 밖에서 새 파일이 생기면 번호를 새로 매긴다.
블록 = 본문을 빈 줄로 나눈 조각. 블록 번호는 이 프로세스 메모리에만 있다 (파일이 바뀌면 바뀌지 않은 블록은 번호 유지).

한 요청 안에서는 폴더를 한 번만 훑고(snapshot) 쓰기가 일어나면 다시 훑는다.
"""

import hashlib
import itertools
import os
import posixpath
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote
from difflib import SequenceMatcher

from flask import current_app, g

from . import filestore, note_links
from .markdown_renderer import join_markdown, split_markdown

META_DIR = ".marknote"
STATUS_DIRS = {"archived": ".archive", "trashed": ".trash"}
UNTITLED = "제목 없음"
UNTITLED_RE = re.compile(rf"^{UNTITLED}(?: \(\d+\))?$")
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
RESERVED = {"images"}  # 프로젝트 안 그림 폴더 이름
README = "readme.md"  # 폴더 설명 파일 (대소문자 무시)


def is_readme(path: str) -> bool:
    return posixpath.basename(path).lower() == README


class StoreError(ValueError):
    pass


# ---------- 경로 ----------

def root() -> str:
    path = current_app.config["NOTES_DIR"]
    os.makedirs(path, exist_ok=True)
    return path


def _abs(rel: str) -> str:
    return os.path.join(root(), *rel.split("/"))


def _index_path() -> str:
    return os.path.join(root(), META_DIR, "index.json")


def safe_name(title: str) -> str:
    """파일·폴더 이름으로 쓸 수 있게."""
    name = UNSAFE_NAME.sub("", title or "").strip().strip(".")[:80].strip()
    return name or UNTITLED


def name_key(title: str | None) -> str:
    return " ".join((title or "").split()).casefold()


# ---------- 페이지 · 블록 ----------

@dataclass
class Block:
    id: int
    content: str


class Page:
    """폴더 또는 .md 파일(노트) 하나 (맨 위 폴더 = 프로젝트). 값은 훑은 시점 그대로 — 바꾸려면 store 함수를 쓴다."""

    def __init__(self, id, path, kind, status, parent_id, position, created_at, updated_at, trashed_at, bucket):
        self.id = id
        self.path = path  # notes/ 기준 상대 경로 ("회사", "회사/회의록.md", ".trash/3/회사/회의록.md")
        self.kind = kind  # folder / note
        self.status = status  # active / archived / trashed
        self.parent_id = parent_id
        self.position = position
        self.created_at = created_at
        self.updated_at = updated_at
        self.trashed_at = trashed_at
        self.bucket = bucket  # 보관함·휴지통 칸 번호 (이 페이지가 그 칸의 맨 위일 때만)
        self._doc = None

    def __repr__(self):
        return f"<Page {self.id} {self.path}>"

    def __eq__(self, other):
        return isinstance(other, Page) and other.id == self.id

    def __hash__(self):
        return hash(self.id)

    @property
    def name(self) -> str:
        base = posixpath.basename(self.path)
        return base[:-3] if self.kind == "note" else base

    @property
    def title(self) -> str:
        return "" if UNTITLED_RE.match(self.name) else self.name

    @property
    def display_title(self) -> str:
        return self.name

    @property
    def parent(self):
        return get(self.parent_id) if self.parent_id is not None else None

    @property
    def readme(self) -> bool:
        """폴더 설명 파일 (README.md)."""
        return self.kind == "note" and is_readme(self.path)

    @property
    def file(self) -> str:
        """notes/ 기준 파일 위치: 폴더 "회사/", 노트 "회사/회의록.md"."""
        return self.path + "/" if self.kind == "folder" else self.path

    @property
    def url(self) -> str:
        """화면 주소 (경로 그대로). 보관·휴지통 안은 번호 주소."""
        if self.status != "active":
            return f"/p/{self.id}"
        return "/n/" + quote(self.file, safe="/")

    @property
    def source(self) -> str:
        if self._doc is None:
            self._doc = _read_note(self.path) if self.kind == "note" else ""
        return self._doc

    @property
    def blocks(self) -> list[Block]:
        if self.kind == "folder":
            return []
        return _block_ids(self.id, self.source)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "title": self.title,
            "display_title": self.display_title,
            "status": self.status,
            "kind": self.kind,
            "url": self.url,
        }


def _read_note(rel: str) -> str:
    try:
        return filestore.read_text(_abs(rel)).strip("\n")
    except FileNotFoundError:
        return ""


def _write_note(rel: str, body: str) -> None:
    filestore.write_text(_abs(rel), body.strip("\n") + "\n")


# ---------- 블록 번호 (이 프로세스 메모리) ----------
# 다시 켜면 그전 번호와 겹치지 않게 지금 시각(ms)부터 센다 — 오래 열어 둔 편집기가 엉뚱한 블록을 고치지 않게
_next_block_id = itertools.count(int(time.time() * 1000))
# 편집기는 빈 줄이 든 블록을 잠시 나누지 않은 채 저장하기도 한다(입력 중 자동 저장) → 파일 본문이 기억한 것과 같으면
# 기억한 블록 나눔을 그대로 쓴다
_page_blocks: dict[int, tuple[str, list[int], list[str]]] = {}  # page_id -> (본문 해시, 블록 번호들, 내용)
_block_page: dict[int, int] = {}  # block_id -> page_id


def _digest(source: str) -> str:
    return hashlib.sha1(source.strip("\n").encode("utf-8")).hexdigest()


def _remember(page_id: int, contents: list[str], ids: list[int]) -> list[Block]:
    old = _page_blocks.get(page_id)
    if old:
        for i in old[1]:
            _block_page.pop(i, None)
    _page_blocks[page_id] = (_digest(join_markdown(contents)), ids, list(contents))
    for i in ids:
        _block_page[i] = page_id
    return [Block(i, c) for i, c in zip(ids, contents)]


def _block_ids(page_id: int, source: str) -> list[Block]:
    cached = _page_blocks.get(page_id)
    if cached and cached[0] == _digest(source):
        return [Block(i, c) for i, c in zip(cached[1], cached[2])]
    contents = split_markdown(source)
    ids = [next(_next_block_id) for _ in contents]
    if cached:
        previous = cached[2]
        # 파일이 바뀌었으면 그대로인 블록은 번호를 유지 (앱 밖 수정, AI 수정 뒤에도 편집기가 이어서 쓰게)
        for tag, i1, i2, j1, _ in SequenceMatcher(None, previous, contents, autojunk=False).get_opcodes():
            if tag == "equal":
                ids[j1:j1 + i2 - i1] = cached[1][i1:i2]
    return _remember(page_id, contents, ids)


def block_owner(block_id: int) -> int | None:
    return _block_page.get(block_id)


def save_blocks(page: Page, blocks: list) -> list[Block]:
    """본문을 blocks 로 다시 쓴다. Block 은 번호 유지, 문자열은 새 블록. 저장된 블록 목록을 돌려준다."""
    if page.kind == "folder":
        if any((b.content if isinstance(b, Block) else b).strip() for b in blocks):
            raise StoreError("폴더에는 내용을 쓸 수 없습니다. 안에 노트를 만들거나 README.md 에 설명을 쓰세요.")
        return []
    contents = [b.content if isinstance(b, Block) else b for b in blocks]
    ids = [b.id if isinstance(b, Block) else next(_next_block_id) for b in blocks]
    _write_note(page.path, join_markdown(contents))
    page._doc = join_markdown(contents)
    invalidate()
    return _remember(page.id, contents, ids)


def replace_source(page: Page, markdown: str) -> list[Block]:
    """본문 전체 교체 — 같은 내용의 블록은 번호를 유지."""
    old = page.blocks
    new = split_markdown(markdown)
    blocks: list = list(new)
    for tag, i1, i2, j1, _ in SequenceMatcher(None, [b.content for b in old], new, autojunk=False).get_opcodes():
        if tag == "equal":
            blocks[j1:j1 + i2 - i1] = old[i1:i2]
    return save_blocks(page, blocks)


# ---------- 훑기 (snapshot) ----------

class Snapshot:
    def __init__(self, pages: list[Page]):
        self.pages = {p.id: p for p in pages}
        self.paths = {p.path: p for p in pages if p.status == "active"}
        self.children: dict[int | None, list[Page]] = {}  # README.md 포함 (밖에서는 children() 로)
        # 탐색기처럼 폴더 먼저
        for p in sorted(pages, key=lambda p: (p.kind != "folder", p.position, name_key(p.name), p.id)):
            self.children.setdefault(p.parent_id, []).append(p)


def invalidate() -> None:
    g.pop("_notes_snapshot", None)


def snapshot() -> Snapshot:
    snap = g.get("_notes_snapshot")
    if snap is None:
        snap = g._notes_snapshot = _scan()
    return snap


def _mtime(path: str) -> datetime:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return datetime.now()


def _list(rel: str) -> list[str]:
    try:
        return sorted(os.listdir(_abs(rel) if rel else root()))
    except (FileNotFoundError, NotADirectoryError):
        return []


def _scan() -> Snapshot:
    """폴더를 훑어 페이지 목록을 만들고 index.json 과 맞춘다 (새 파일엔 번호, 없어진 파일은 지움)."""
    found: list[dict] = []  # {path, kind, parent (경로 또는 ("id", n)), status, bucket}

    def walk(dir_rel: str, status: str):
        for name in _list(dir_rel):
            if name.startswith("."):
                continue
            path = f"{dir_rel}/{name}"
            if os.path.isdir(_abs(path)):
                if name.lower() in RESERVED:
                    continue
                found.append({"path": path, "kind": "folder", "parent": dir_rel, "status": status, "bucket": None})
                walk(path, status)
            elif name.lower().endswith(".md"):
                found.append({"path": path, "kind": "note", "parent": dir_rel, "status": status, "bucket": None})

    for name in _list(""):  # 맨 위에는 폴더(프로젝트)만 — 맨 위의 .md 파일은 무시
        if name.startswith(".") or not os.path.isdir(_abs(name)):
            continue
        found.append({"path": name, "kind": "folder", "parent": None, "status": "active", "bucket": None})
        walk(name, "active")

    changed = False
    with filestore.locked(_index_path()):
        index = filestore.read_json(_index_path(), {"next_id": 1, "pages": {}, "buckets": {}})
        buckets = index.setdefault("buckets", {})
        for status, folder in STATUS_DIRS.items():
            for bucket in _list(folder):
                meta = buckets.get(bucket)
                base = f"{folder}/{bucket}"
                if meta is None or meta.get("status") != status:  # index 를 지웠으면 칸 안 맨 위 폴더를 프로젝트로
                    tops = [n for n in _list(base) if os.path.isdir(_abs(f"{base}/{n}"))]
                    if not tops:
                        continue
                    meta = buckets[bucket] = {"status": status, "root": tops[0], "kind": "folder", "parent": None,
                                              "at": _mtime(_abs(base)).isoformat()}
                    changed = True
                item = f"{base}/{meta['root']}"
                if not os.path.exists(_abs(item)):
                    continue
                kind = "folder" if os.path.isdir(_abs(item)) else "note"
                parent = meta.get("parent")
                found.append({"path": item, "kind": kind, "parent": ("orig", parent) if parent else None,
                              "status": status, "bucket": bucket, "at": meta.get("at")})
                if kind == "folder":
                    walk(item, status)
        for bucket in [b for b in buckets if not os.path.isdir(_abs(f"{STATUS_DIRS.get(buckets[b].get('status'), '.trash')}/{b}"))]:
            del buckets[bucket]
            changed = True

        entries = index.setdefault("pages", {})
        by_path = {e["path"]: int(i) for i, e in entries.items()}
        keep: dict[str, dict] = {}
        for item in found:
            page_id = by_path.get(item["path"])
            if page_id is None:
                page_id = int(index.get("next_id", 1))
                index["next_id"] = page_id + 1
                entries[str(page_id)] = {"path": item["path"], "created": datetime.now().isoformat(timespec="seconds")}
                changed = True
            item["id"] = page_id
            keep[str(page_id)] = entries[str(page_id)]
        if set(keep) != set(entries):
            changed = True
        index["pages"] = keep
        if changed:
            filestore.write_json(_index_path(), index)

    path_id = {item["path"]: item["id"] for item in found}
    pages = []
    for item in found:
        entry = keep[str(item["id"])]
        parent = item["parent"]
        if isinstance(parent, tuple):  # 보관·휴지통 칸 맨 위: 원래 부모 폴더가 아직 있으면 그 폴더
            parent_id = path_id.get(parent[1])
        else:
            parent_id = path_id.get(parent) if parent else None
        abs_path = _abs(item["path"])
        at = item.get("at")
        pages.append(Page(
            id=item["id"], path=item["path"], kind=item["kind"], status=item["status"], parent_id=parent_id,
            position=entry.get("position", 10**6),
            created_at=datetime.fromisoformat(entry["created"]) if entry.get("created") else _mtime(abs_path),
            updated_at=_mtime(abs_path), trashed_at=datetime.fromisoformat(at) if at and item["status"] == "trashed" else None,
            bucket=item["bucket"],
        ))
    return Snapshot(pages)


# ---------- 읽기 ----------

def get(page_id) -> Page | None:
    try:
        return snapshot().pages.get(int(page_id))
    except (TypeError, ValueError):
        return None


def by_path(path: str) -> Page | None:
    """활성 폴더·노트를 notes/ 기준 경로로."""
    return snapshot().paths.get(path)


def all_pages() -> list[Page]:
    return list(snapshot().pages.values())


def children(page_id: int | None, status: str | None = None, readme: bool = False) -> list[Page]:
    """폴더 안의 폴더·노트 (README.md 는 readme=True 일 때만)."""
    kids = snapshot().children.get(page_id, [])
    return [p for p in kids if (status is None or p.status == status) and (readme or not p.readme)]


def readme_of(folder: Page) -> Page | None:
    """폴더 설명 파일 README.md."""
    for p in snapshot().children.get(folder.id, []):
        if p.readme and p.path.startswith(folder.path + "/"):
            return p
    return None


def descendants(page: Page) -> list[Page]:
    """폴더 안에 실제로 들어 있는 것 전부 (README.md 포함). 노트는 하위가 없다."""
    if page.kind != "folder":
        return []
    result, frontier = [], [page.id]
    while frontier:
        kids = [c for pid in frontier for c in snapshot().children.get(pid, []) if c.path.startswith(page.path + "/")]
        result += kids
        frontier = [c.id for c in kids]
    return result


# ---------- 쓰기 ----------

def _update_index(fn) -> None:
    with filestore.update_json(_index_path(), {"next_id": 1, "pages": {}, "buckets": {}}) as index:
        fn(index)
    invalidate()


def _move_paths(index: dict, old: str, new: str) -> None:
    """index 안의 경로를 old(폴더면 그 안까지) → new 로."""
    for entry in index["pages"].values():
        p = entry["path"]
        if p == old:
            entry["path"] = new
        elif not old.endswith(".md") and p.startswith(old + "/"):
            entry["path"] = new + p[len(old):]


def _move_files(old: str, new: str) -> None:
    """노트 파일 또는 폴더(안의 것까지)를 옮긴다."""
    if os.path.exists(_abs(new)) and name_key(old) != name_key(new):
        raise StoreError(f"이미 있는 이름입니다: {new}")
    os.makedirs(os.path.dirname(_abs(new)), exist_ok=True)
    os.replace(_abs(old), _abs(new)) if old.endswith(".md") else shutil.move(_abs(old), _abs(new))


def _next_position(index: dict, path: str) -> int:
    folder = posixpath.dirname(path)
    taken = [e["position"] for e in index["pages"].values()
             if posixpath.dirname(e["path"]) == folder and e["path"] != path and "position" in e]
    return max(taken) + 1 if taken else 0


def _child_path(parent: Page | None, name: str, kind: str) -> str:
    if parent is None:
        if kind != "folder":
            raise StoreError("맨 위에는 폴더(프로젝트)만 만들 수 있습니다. 노트는 폴더 안에 만드세요.")
        return name
    if parent.kind != "folder":
        raise StoreError("노트 안에는 만들 수 없습니다. 폴더 안에 만드세요.")
    if kind == "folder" and name.lower() in RESERVED:
        raise StoreError(f'"{name}" 는 그림 폴더 이름이라 쓸 수 없습니다.')
    return f"{parent.path}/{name}" + (".md" if kind == "note" else "")


def create(parent: Page | None, title: str, markdown: str = "", position: int | None = None, kind: str = "note") -> Page:
    """폴더 또는 노트(.md) 만들기. parent 가 없으면 프로젝트(맨 위 폴더). 이름 겹침 확인은 호출한 쪽에서."""
    if parent is None:
        kind = "folder"
    if parent is not None and parent.status != "active":
        raise StoreError("보관함·휴지통의 폴더 안에는 만들 수 없습니다.")
    path = _child_path(parent, safe_name(title), kind)
    if os.path.exists(_abs(path)):
        raise StoreError(f"이미 있는 이름입니다: {path}")
    if kind == "folder":
        if markdown.strip():
            raise StoreError("폴더에는 내용을 쓸 수 없습니다.")
        os.makedirs(_abs(path))
    else:
        _write_note(path, markdown)

    def register(index):
        page_id = int(index.get("next_id", 1))
        index["next_id"] = page_id + 1
        entry = {"path": path, "created": datetime.now().isoformat(timespec="seconds"),
                 "position": position if position is not None else _next_position(index, path)}
        index["pages"][str(page_id)] = entry
        created.append(page_id)

    created: list[int] = []
    _update_index(register)
    return get(created[0])


def rename(page: Page, title: str) -> Page:
    name = safe_name(title)
    if name == page.name:
        return page
    new = posixpath.join(posixpath.dirname(page.path), name + (".md" if page.kind == "note" else ""))
    _move_files(page.path, new)
    _update_index(lambda index: _move_paths(index, page.path, new))
    if page.bucket:
        _update_index(lambda index: index["buckets"][page.bucket].update(root=new.split("/", 2)[2]))
    else:
        _relink(page.path, new, page.kind == "folder")
    return get(page.id)


def move(page: Page, parent: Page | None, position: int | None = None) -> Page:
    """다른 폴더 안으로 (parent 가 None 이면 맨 위 = 프로젝트, 폴더만)."""
    new = _child_path(parent, page.name, page.kind)
    if new != page.path:
        _move_files(page.path, new)

    def fn(index):
        _move_paths(index, page.path, new)
        entry = index["pages"][str(page.id)]
        entry["position"] = position if position is not None else _next_position(index, new)
    _update_index(fn)
    if new != page.path and page.status == "active":
        _relink(page.path, new, page.kind == "folder")
    return get(page.id)


def _relink(old: str, new: str, folder: bool) -> None:
    """old → new 로 옮긴 뒤, 그 경로(폴더면 안의 것까지)를 가리키던 상대 링크를 모든 노트에서 고친다.
    옮겨진 노트 안의 링크도 새 위치 기준으로 다시 쓴다 (docs/storage.md "노트끼리 링크")."""

    def shift(path: str, a: str, b: str) -> str:
        if path == a or (folder and path.startswith(a + "/")):
            return b + path[len(a):]
        return path

    active = [p for p in all_pages() if p.status == "active"]
    old_paths = {shift(p.path, new, old) for p in active}

    def mapping(target: str) -> str | None:
        bare = target.rstrip("/")
        if bare not in old_paths:
            return None  # 없는 대상 — 끊어진 링크는 그대로
        return shift(bare, old, new) + target[len(bare):]

    for page in active:
        if page.kind != "note":
            continue
        source = page.source
        fixed = note_links.rewrite(source, shift(page.path, new, old), page.path, mapping)
        if fixed != source:
            replace_source(page, fixed)


def set_positions(page_ids: list[int]) -> None:
    def fn(index):
        for pos, page_id in enumerate(page_ids):
            if str(page_id) in index["pages"]:
                index["pages"][str(page_id)]["position"] = pos
    _update_index(fn)


def to_bucket(page: Page, status: str) -> None:
    """보관함·휴지통으로: notes/.trash/<새 칸>/<원래 경로>."""
    folder = STATUS_DIRS[status]
    original = page.path if page.bucket is None and page.status == "active" else _original_path(page)
    parent = page.parent
    holder: list[str] = []

    def reserve(index):
        buckets = index.setdefault("buckets", {})
        n = max([int(b) for b in buckets if b.isdigit()] + [0]) + 1
        while os.path.exists(_abs(f"{folder}/{n}")):
            n += 1
        holder.append(str(n))
        buckets[str(n)] = {"status": status, "root": original, "kind": page.kind,
                           "parent": _original_path(parent) if parent is not None else None,
                           "at": datetime.now().isoformat(timespec="seconds")}
    _update_index(reserve)
    new = f"{folder}/{holder[0]}/{original}"
    old_bucket = page.bucket
    _move_files(page.path, new)
    _update_index(lambda index: _move_paths(index, page.path, new))
    if old_bucket:
        _drop_bucket(old_bucket)


def _original_path(page: Page) -> str:
    """보관·휴지통 안 페이지의 원래 경로 (활성 페이지면 그대로)."""
    if page.status == "active":
        return page.path
    parts = page.path.split("/", 2)
    return parts[2] if len(parts) == 3 else page.path


def _drop_bucket(bucket: str) -> None:
    def fn(index):
        meta = index.get("buckets", {}).pop(bucket, None)
        if meta:
            folder = _abs(f"{STATUS_DIRS[meta['status']]}/{bucket}")
            shutil.rmtree(folder, ignore_errors=True)
    _update_index(fn)


def restore(page: Page, new_parent: Page | None, title: str) -> Page:
    """보관·휴지통 칸에서 꺼내 new_parent 폴더 안으로 (None 이면 프로젝트로), 이름은 title."""
    new = _child_path(new_parent, safe_name(title), page.kind)
    bucket = page.bucket
    _move_files(page.path, new)
    _update_index(lambda index: _move_paths(index, page.path, new))
    if bucket:
        _drop_bucket(bucket)
    return get(page.id)


def delete(page: Page) -> None:
    """영구 삭제 (보관·휴지통 칸째)."""
    path = _abs(page.path)
    if page.kind == "note":
        os.remove(path)
    else:
        shutil.rmtree(path)
    if page.bucket:
        _drop_bucket(page.bucket)
    invalidate()
