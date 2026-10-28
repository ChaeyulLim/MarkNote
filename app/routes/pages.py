from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..services import page_tree as tree
from ..services import store
from ..services.page_tree import Page
from ..services.settings import MAX_PROMPT_CHARS, get_settings, save_settings

bp = Blueprint("pages", __name__)


@bp.route("/")
def index():
    return render_template("index.html", recent=[p for p in tree.recent(15) if p.kind == "note"][:10])


@bp.route("/n/<path:path>")
def note(path):
    """화면 주소 = 파일 경로: /n/회사/회의록.md, 폴더는 /n/회사/2026-03/ (docs/storage.md "주소")."""
    page = tree.by_path(path)
    if page is None:
        abort(404, description="노트를 찾을 수 없습니다. 이름이 바뀌었거나 옮겨졌을 수 있습니다.")
    if tree.is_folder(page) != path.endswith("/"):
        return redirect(page.url)
    return _show(page)


@bp.route("/p/<int:page_id>")
def view(page_id):
    """앱 안쪽 번호 주소 — 활성 페이지는 경로 주소로 보낸다 (보관함·휴지통 안은 여기서 보여 줌)."""
    page = tree.get_or_404(page_id)
    if page.status == "active":
        return redirect(page.url)
    return _show(page)


def _show(page: Page):
    ancestors = tree.ancestors(page)
    if tree.is_folder(page):
        return _folder(page)
    prev_page, next_page = tree.siblings_around(page)
    data = {"page": page.to_dict(), "blocks": [tree.block_dict(b, page) for b in page.blocks]}
    return render_template(
        "page.html",
        page=page,
        ancestors=ancestors,
        open_ids=[a.id for a in ancestors],
        current_id=page.id,
        prev_page=prev_page,
        next_page=next_page,
        data=data,
    )


def _folder(folder: Page):
    """폴더: 편집기 대신 설명(README.md)과 그 안의 폴더·노트 목록."""
    items = [n for n in store.children(folder.id) if n.status == folder.status and n.path.startswith(folder.path + "/")]
    counts = {n.id: len(store.children(n.id, n.status)) for n in items if tree.is_folder(n)}
    readme = tree.readme(folder)
    ancestors = tree.ancestors(folder)
    return render_template(
        "folder.html",
        page=folder,
        ancestors=ancestors,
        items=items,
        sub_counts=counts,
        readme=readme,
        readme_html=[tree.block_dict(b, readme)["html"] for b in readme.blocks] if readme else [],
        open_ids=[a.id for a in ancestors] + [folder.id],
        current_id=folder.id,
        data={"page": folder.to_dict()},
    )


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
