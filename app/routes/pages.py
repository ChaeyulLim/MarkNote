from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..services import page_tree as tree
from ..services.page_tree import Page
from ..services.settings import MAX_PROMPT_CHARS, get_settings, save_settings

bp = Blueprint("pages", __name__)


@bp.route("/")
def index():
    return render_template("index.html", recent=[p for p in tree.recent(15) if p.kind == "note"][:10])


@bp.route("/n/<path:path>")
def note(path):
    """화면 주소 = 파일 경로: /n/회사/회의록.md. 폴더 주소(/n/회사/)는 화면이 따로 없다 → 탐색기에서 그 폴더를 펼쳐 보여 준다."""
    page = tree.by_path(path)
    if page is None:
        # 지웠거나 옮긴 노트·폴더의 옛 주소 → 남아 있는 가장 가까운 상위 폴더를 탐색기에서 보여 준다
        parts = path.strip("/").split("/")[:-1]
        while parts and tree.by_path("/".join(parts)) is None:
            parts.pop()
        flash("찾는 노트가 없습니다. 이름이 바뀌었거나 옮겨졌을 수 있습니다.", "info")
        return redirect(_reveal(tree.by_path("/".join(parts))) if parts else url_for("pages.index"))
    if tree.is_folder(page):
        return redirect(_reveal(page))
    if path.endswith("/"):
        return redirect(page.url)
    return _show(page)


@bp.route("/p/<int:page_id>")
def view(page_id):
    """앱 안쪽 번호 주소 — 활성 페이지는 경로 주소로 보낸다 (보관함·휴지통 안은 여기서 보여 줌)."""
    page = tree.get_or_404(page_id)
    if page.status == "active":
        return redirect(_reveal(page) if tree.is_folder(page) else page.url)
    if tree.is_folder(page):
        return redirect(url_for("pages.trash" if page.status == "trashed" else "pages.archive"))
    return _show(page)


def _reveal(folder: Page) -> str:
    return url_for("pages.index", reveal=folder.id)


def _show(page: Page):
    ancestors = tree.ancestors(page)
    prev_page, next_page = tree.siblings_around(page)
    data = {"page": page.to_dict(), "blocks": [tree.block_dict(b, page) for b in page.blocks]}
    return render_template(
        "page.html",
        page=page,
        ancestors=ancestors,
        prev_page=prev_page,
        next_page=next_page,
        data=data,
    )


@bp.route("/local")
def local():
    """내 PC 폴더 모드 — 화면 틀만 보내고, 노트는 브라우저가 PC 폴더에서 직접 읽고 쓴다 (서버에 저장하지 않음).
    주소의 # 뒤(노트 경로)는 서버로 오지 않는다."""
    return render_template("local.html", sidebar_mode="local")


@bp.route("/archive")
def archive():
    return render_template("list.html", kind="archive", pages=tree.top_level_with_status("archived"))


@bp.route("/trash")
def trash():
    return render_template("list.html", kind="trash", pages=tree.top_level_with_status("trashed"))


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        save_settings(
            request.form.get("ai_initial_prompt", ""),
            notes_read=request.form.get("ai_notes_read") == "1",
            notes_edit=request.form.get("ai_notes_edit") == "1",
            decks_edit=request.form.get("ai_decks_edit") == "1",
            notes_delete=request.form.get("ai_notes_delete") == "1",
        )
        flash("설정을 저장했습니다. 다음 대화부터 적용됩니다.", "ok")
        return redirect(url_for("pages.settings"))
    return render_template("settings.html", settings=get_settings(), max_prompt=MAX_PROMPT_CHARS)
