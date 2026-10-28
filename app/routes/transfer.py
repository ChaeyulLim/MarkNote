from datetime import datetime
from io import BytesIO

from flask import Blueprint, abort, current_app, jsonify, redirect, request, send_file, url_for

from ..services import page_tree as tree
from ..services import transfer
from ..services.transfer import TransferError

bp = Blueprint("transfer", __name__)


def _download(data: bytes, filename: str, mimetype: str):
    return send_file(BytesIO(data), mimetype=mimetype, as_attachment=True, download_name=filename)


@bp.get("/export/page/<int:page_id>.md")
def export_page_md(page_id):
    page = tree.get_or_404(page_id)
    if tree.is_folder(page):  # 폴더 → .zip 으로
        return redirect(url_for("transfer.export_page_zip", page_id=page.id))
    data = transfer.export_page_markdown(page).encode("utf-8")
    return _download(data, f"{transfer.safe_name(page.display_title)}.md", "text/markdown; charset=utf-8")


@bp.get("/export/page/<int:page_id>.zip")
def export_page_zip(page_id):
    page = tree.get_or_404(page_id)
    data = transfer.export_zip([page], current_app.config["UPLOAD_FOLDER"])
    return _download(data, f"{transfer.safe_name(page.display_title)}.zip", "application/zip")


@bp.get("/export/all.zip")
def export_all():
    roots = [node["page"] for node in tree.active_tree()]
    data = transfer.export_zip(roots, current_app.config["UPLOAD_FOLDER"])
    return _download(data, f"MarkNote-{datetime.now():%Y%m%d-%H%M}.zip", "application/zip")


@bp.post("/api/import")
def import_files():
    uploads = [(f.filename or "", f.read()) for f in request.files.getlist("files")]
    if not uploads:
        abort(400, description="가져올 파일을 선택하세요.")
    parent_id = request.form.get("parent_id", type=int)
    parent = tree.get_or_404(parent_id) if parent_id is not None else None

    try:
        roots = transfer.import_markdown(
            uploads, parent, current_app.config["UPLOAD_FOLDER"], request.form.get("on_conflict")
        )
    except transfer.ImportConflict as err:
        # 화면이 경고를 띄우고 "덮어쓰기 / 이름 바꿔서 가져오기" 를 골라 on_conflict 와 함께 다시 보냄
        return jsonify(error=str(err), conflicts=err.conflicts), 409
    except TransferError as err:
        abort(400, description=str(err))

    if len(roots) == 1:
        redirect = roots[0].url
    elif parent is not None:
        redirect = parent.url
    else:
        redirect = url_for("pages.index")
    return jsonify(pages=[r.to_dict() for r in roots], redirect=redirect), 201
