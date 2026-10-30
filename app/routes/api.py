from flask import Blueprint, abort, jsonify, request, url_for

from ..services import page_tree as tree
from ..services import store
from ..services.attachments import ImageError, alt_text, save_image
from ..services.markdown_renderer import split_markdown
from ..services.page_tree import Page
from ..services import userdata

bp = Blueprint("api", __name__, url_prefix="/api")


def _json() -> dict:
    return request.get_json(silent=True) or {}


def _page_url(page: Page) -> str:
    return page.url


def _page_view(page: Page) -> dict:
    return {**page.to_dict(), "path": tree.file_path(page), "abs_path": tree.abs_file_path(page)}


# ---------- 페이지 (폴더 = 디렉토리, 노트 = .md 파일 — 맨 위에도 둘 다) ----------

def _check_name(parent_id, title, exclude_id=None, kind="note"):
    try:
        tree.check_name(parent_id, title, exclude_id, kind)
    except tree.NameConflict as err:
        abort(409, description=str(err))


@bp.get("/tree")
def page_tree():
    """탐색기 트리 (폴더·노트 전부)."""
    return jsonify(nodes=tree.tree_json())


@bp.post("/pages")
def create_page():
    data = _json()
    parent = tree.get_or_404(data["parent_id"]) if data.get("parent_id") is not None else None
    title = str(data.get("title") or "").strip()[:200]
    kind = "folder" if data.get("kind") == "folder" else "note"
    _check_name(parent.id if parent else None, title, kind=kind)
    try:
        page = tree.create_page(parent, title, kind=kind)
    except store.StoreError as err:
        abort(400, description=str(err))
    return jsonify(page=_page_view(page), url=_page_url(page)), 201


@bp.patch("/pages/<int:page_id>")
def update_page(page_id):
    page = tree.get_or_404(page_id)
    data = _json()
    try:
        if "title" in data:
            title = str(data["title"] or "").strip()[:200]
            _check_name(page.parent_id, title, page.id, page.kind)
            page = tree.rename_page(page, title)
        if "parent_id" in data:
            page = _move(page, data["parent_id"])
        if "order" in data:  # 끌어서 바꾼 순서: 같은 폴더 안 형제 번호들
            store.set_positions([int(i) for i in data["order"]])
            page = store.get(page.id)
    except tree.NameConflict as err:
        abort(409, description=str(err))
    except store.StoreError as err:
        abort(400, description=str(err))
    return jsonify(page=_page_view(page), url=_page_url(page), path=tree.file_path(page), abs_path=tree.abs_file_path(page))


def _move(page: Page, parent_id) -> Page:
    new_parent = None
    if parent_id is not None:
        new_parent = tree.get_or_404(parent_id)
        if tree.is_self_or_descendant(new_parent, page):
            abort(400, description="자기 자신이나 그 안의 폴더로는 옮길 수 없습니다.")
    if (new_parent.id if new_parent else None) == page.parent_id:
        return page
    _check_name(new_parent.id if new_parent else None, page.title, page.id, page.kind)
    return store.move(page, new_parent)


@bp.post("/pages/<int:page_id>/copy")
def copy_page(page_id):
    """사이드바 복사·붙여넣기 (Ctrl+C → Ctrl+V): parent_id 폴더 안에 복사본 (없으면 맨 위)."""
    page = tree.get_or_404(page_id)
    if page.status != "active":
        abort(400, description="보관함·휴지통에 있는 것은 복사할 수 없습니다.")
    data = _json()
    parent = tree.get_or_404(data["parent_id"]) if data.get("parent_id") is not None else None
    try:
        copied = tree.copy_page(page, parent)
    except store.StoreError as err:
        abort(400, description=str(err))
    return jsonify(page=_page_view(copied), url=_page_url(copied)), 201


def _redirect_after_leaving(parent_id) -> str:
    parent = store.get(parent_id) if parent_id is not None else None
    if parent is not None and parent.status == "active":
        return _page_url(parent)
    return url_for("pages.index")


@bp.post("/pages/<int:page_id>/archive")
def archive_page(page_id):
    page = tree.get_or_404(page_id)
    tree.archive(page)
    return jsonify(page=store.get(page_id).to_dict(), redirect=_redirect_after_leaving(page.parent_id))


@bp.post("/pages/<int:page_id>/trash")
def trash_page(page_id):
    page = tree.get_or_404(page_id)
    tree.trash(page)
    return jsonify(page=store.get(page_id).to_dict(), redirect=_redirect_after_leaving(page.parent_id))


@bp.post("/pages/<int:page_id>/restore")
def restore_page(page_id):
    page = tree.restore(tree.get_or_404(page_id))
    return jsonify(page=page.to_dict(), redirect=_page_url(page))


@bp.delete("/pages/<int:page_id>")
def delete_page(page_id):
    page = tree.get_or_404(page_id)
    if page.status != "trashed":
        abort(400, description="휴지통에 있는 페이지만 영구 삭제할 수 있습니다.")
    tree.delete_permanently(page, userdata.path("UPLOAD_FOLDER"))
    return jsonify(ok=True, redirect=url_for("pages.trash"))


@bp.post("/trash/empty")
def empty_trash():
    for page in tree.top_level_with_status("trashed"):
        tree.delete_permanently(page, userdata.path("UPLOAD_FOLDER"))
    return jsonify(ok=True, redirect=url_for("pages.trash"))


# ---------- 블록 (본문을 빈 줄로 나눈 조각 — 번호는 서버 메모리에만) ----------

def _editable(page: Page) -> Page:
    if tree.is_folder(page):
        abort(400, description="폴더에는 내용을 쓸 수 없습니다. 안에 노트를 만드세요.")
    return page


def _block(block_id: int):
    """(페이지, 블록 목록, 위치). 파일이 앱 밖에서 바뀌어 블록이 없어졌으면 409."""
    page_id = store.block_owner(block_id)
    page = store.get(page_id) if page_id is not None else None
    if page is None:
        abort(409, description="페이지가 바뀌었습니다. 새로고침하세요.")
    blocks = page.blocks
    for i, b in enumerate(blocks):
        if b.id == block_id:
            return page, blocks, i
    abort(409, description="페이지가 바뀌었습니다. 새로고침하세요.")


def _index_of(blocks, block_id: int) -> int:
    for i, b in enumerate(blocks):
        if b.id == block_id:
            return i
    abort(409, description="페이지가 바뀌었습니다. 새로고침하세요.")


@bp.post("/pages/<int:page_id>/blocks")
def create_block(page_id):
    page = _editable(tree.get_or_404(page_id))
    data = _json()
    blocks = page.blocks
    index = len(blocks)
    if data.get("after_id") is not None:
        index = _index_of(blocks, int(data["after_id"])) + 1
    elif data.get("before_id") is not None:
        index = _index_of(blocks, int(data["before_id"]))
    (block,) = tree.insert_blocks(page, index, [str(data.get("content") or "")])
    return jsonify(block=tree.block_dict(block, page)), 201


@bp.patch("/blocks/<int:block_id>")
def update_block(block_id):
    page, blocks, i = _block(block_id)
    data = _json()
    content = str(data.get("content") or "")
    if data.get("split") is False:
        # 입력 중 자동 저장: 편집 중인 화면이 바뀌지 않도록 나누지 않고 그대로 저장 (편집을 마칠 때 나눔)
        blocks[i] = store.Block(block_id, content)
        saved = store.save_blocks(page, blocks)
        return jsonify(blocks=[tree.block_dict(saved[i], page)])
    # 붙여넣기 등으로 빈 줄이 생기면 여러 블록으로 나눈다
    chunks = split_markdown(content) or [""]
    new: list = [store.Block(block_id, chunks[0]), *chunks[1:]]
    saved = store.save_blocks(page, blocks[:i] + new + blocks[i + 1:])
    return jsonify(blocks=[tree.block_dict(b, page) for b in saved[i:i + len(new)]])


@bp.delete("/blocks/<int:block_id>")
def delete_block(block_id):
    page, blocks, i = _block(block_id)
    store.save_blocks(page, blocks[:i] + blocks[i + 1:])
    return jsonify(ok=True)


@bp.post("/pages/<int:page_id>/blocks/reorder")
def reorder_blocks(page_id):
    page = _editable(tree.get_or_404(page_id))
    order = [int(i) for i in _json().get("order", [])]
    blocks = page.blocks
    by_id = {b.id: b for b in blocks}
    ordered = [by_id[i] for i in order if i in by_id]
    ordered += [b for b in blocks if b not in ordered]
    store.save_blocks(page, ordered)
    return jsonify(ok=True)


@bp.get("/pages/<int:page_id>/source")
def get_source(page_id):
    page = tree.get_or_404(page_id)
    return jsonify(markdown=tree.page_source(page))


@bp.put("/pages/<int:page_id>/source")
def put_source(page_id):
    page = _editable(tree.get_or_404(page_id))
    blocks = tree.replace_markdown(page, str(_json().get("markdown") or ""))
    return jsonify(blocks=[tree.block_dict(b, page) for b in blocks])


@bp.get("/pages/<int:page_id>/blocks")
def get_blocks(page_id):
    page = tree.get_or_404(page_id)
    return jsonify(page=page.to_dict(), blocks=[tree.block_dict(b, page) for b in page.blocks])


# ---------- 이미지 업로드 ----------

@bp.post("/uploads")
def upload():
    file = request.files.get("file")
    if file is None:
        abort(400, description="파일이 없습니다.")
    try:
        file_id = save_image(file.read(), file.filename, userdata.path("UPLOAD_FOLDER"))
    except ImageError as err:
        abort(400, description=str(err))
    return jsonify(id=file_id, url=url_for("files.get_file", file_id=file_id), name=alt_text(file.filename)), 201
