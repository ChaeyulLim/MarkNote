import mimetypes

from flask import Blueprint, abort, current_app, send_from_directory

from ..services.attachments import find_upload

bp = Blueprint("files", __name__)


@bp.get("/files/<file_id>")
def get_file(file_id):
    name = find_upload(current_app.config["UPLOAD_FOLDER"], file_id)
    if name is None:
        abort(404)
    response = send_from_directory(
        current_app.config["UPLOAD_FOLDER"], name, mimetype=mimetypes.guess_type(name)[0], max_age=86400
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.cache_control.private = True
    response.cache_control.public = False
    return response
