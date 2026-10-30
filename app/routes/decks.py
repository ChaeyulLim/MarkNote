from contextlib import contextmanager
from datetime import datetime
from io import BytesIO

from flask import Blueprint, abort, jsonify, render_template, request, url_for
from PIL import Image

from ..services import decks as deck_service
from ..services.attachments import ImageError, save_image
from ..services.deck_ops import OpError, apply_ops
from ..services import userdata

bp = Blueprint("decks", __name__)

VIEWS = {"active": "프레젠테이션", "archived": "보관함", "trashed": "휴지통"}


def _json() -> dict:
    return request.get_json(silent=True) or {}


# ---------- 화면 ----------

@bp.get("/decks")
def list_decks():
    view = request.args.get("view", "active")
    if view not in VIEWS:
        view = "active"
    decks = deck_service.decks_with_status(view)
    cards = [{**deck_service.deck_dict(d, with_slides=False), "first": deck_service.slide_dict(d.slides[0]) if d.slides else None} for d in decks]
    return render_template("deck_list.html", view=view, views=VIEWS, cards=cards, sidebar_mode="decks")


@bp.get("/d/<int:deck_id>")
def editor(deck_id):
    deck = deck_service.get_or_404(deck_id)
    data = {
        "deck": deck_service.deck_dict(deck),
        "layouts": deck_service.layout_list(),
        "fonts": deck_service.FONTS,
    }
    return render_template("deck_editor.html", deck=deck, data=data)


@bp.get("/d/<int:deck_id>/show")
def show(deck_id):
    deck = deck_service.get_or_404(deck_id)
    return render_template("deck_show.html", deck=deck, data={"deck": deck_service.deck_dict(deck)})


# ---------- API ----------

@bp.post("/api/decks")
def create_deck():
    deck = deck_service.create_deck(str(_json().get("title") or ""))
    return jsonify(deck=deck_service.deck_dict(deck), url=url_for("decks.editor", deck_id=deck.id)), 201


@bp.get("/api/decks/<int:deck_id>")
def get_deck(deck_id):
    return jsonify(deck=deck_service.deck_dict(deck_service.get_or_404(deck_id)))


@contextmanager
def _editing(deck_id):
    with deck_service.editing(deck_id) as deck:
        if deck is None:
            abort(404, description="프레젠테이션을 찾을 수 없습니다.")
        yield deck


@bp.patch("/api/decks/<int:deck_id>")
def update_deck(deck_id):
    data = _json()
    with _editing(deck_id) as deck:
        if "title" in data:
            deck.title = str(data["title"] or "").strip()[:200]
            deck.updated_at = datetime.now()
    return jsonify(deck=deck_service.deck_dict(deck, with_slides=False))


@bp.post("/api/decks/<int:deck_id>/ops")
def deck_ops(deck_id):
    with _editing(deck_id) as deck:
        if deck.status == "trashed":
            abort(400, description="휴지통에 있는 프레젠테이션은 편집할 수 없습니다.")
        try:
            apply_ops(deck, _json().get("ops"))
        except (OpError, TypeError, ValueError) as err:
            abort(400, description=str(err))  # 저장하지 않으므로 파일은 그대로
    return jsonify(version=deck.version)


@bp.post("/api/decks/<int:deck_id>/<any(archive, trash, restore):action>")
def change_status(deck_id, action):
    with _editing(deck_id) as deck:
        deck_service.set_status(deck, {"archive": "archived", "trash": "trashed", "restore": "active"}[action])
    return jsonify(deck=deck_service.deck_dict(deck, with_slides=False), redirect=url_for("decks.list_decks"))


@bp.delete("/api/decks/<int:deck_id>")
def delete_deck(deck_id):
    deck = deck_service.get_or_404(deck_id)
    if deck.status != "trashed":
        abort(400, description="휴지통에 있는 프레젠테이션만 영구 삭제할 수 있습니다.")
    deck_service.delete_permanently(deck)
    return jsonify(ok=True, redirect=url_for("decks.list_decks", view="trashed"))


@bp.post("/api/decks/<int:deck_id>/images")
def upload_image(deck_id):
    deck_service.get_or_404(deck_id)
    file = request.files.get("file")
    if file is None:
        abort(400, description="파일이 없습니다.")
    data = file.read()
    try:
        file_id = save_image(data, file.filename, userdata.path("UPLOAD_FOLDER"))
    except ImageError as err:
        abort(400, description=str(err))
    with Image.open(BytesIO(data)) as img:
        width, height = img.size
    return jsonify(url=url_for("files.get_file", file_id=file_id), width=width, height=height), 201
