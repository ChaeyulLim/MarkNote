import io
import os

import pytest
from PIL import Image

from app.services import decks as deck_service
from app.services.deck_ops import OpError, apply_ops


def _create(client, title="발표"):
    res = client.post("/api/decks", json={"title": title})
    assert res.status_code == 201
    return res.get_json()["deck"]


def _ops(client, deck_id, ops):
    return client.post(f"/api/decks/{deck_id}/ops", json={"ops": ops})


def _deck(client, deck_id):
    return client.get(f"/api/decks/{deck_id}").get_json()["deck"]


def _text_el(eid="eabc1234", **over):
    el = {"id": eid, "type": "text", "name": "TextBox 1", "x": 10, "y": 20, "w": 300, "h": 40, "text": "안녕",
          "style": {"fontSize": 24, "color": "#ff0000"}}
    el.update(over)
    return el


# ---------- 생성 · 목록 · 화면 ----------

def test_new_deck_starts_with_title_slide(auth_client):
    deck = _create(auth_client)
    assert deck["slide_count"] == 1
    slide = deck["slides"][0]
    assert slide["layout"] == "title_slide"
    assert [e["placeholder"] for e in slide["elements"]] == ["title", "subtitle"]
    assert slide["elements"][0]["name"] == "제목 1"


def test_pages_render(auth_client):
    deck = _create(auth_client, "화면 확인")
    assert "화면 확인" in auth_client.get("/decks").get_data(as_text=True)
    editor = auth_client.get(f"/d/{deck['id']}").get_data(as_text=True)
    assert 'id="deck-data"' in editor and "새<br>슬라이드" in editor
    assert auth_client.get(f"/d/{deck['id']}/show").status_code == 200


def test_sidebar_tabs(auth_client):
    _create(auth_client, "사이드바에 보임")
    decks_html = auth_client.get("/decks").get_data(as_text=True)
    assert "사이드바에 보임" in decks_html and "새 프레젠테이션" in decks_html
    notes_html = auth_client.get("/").get_data(as_text=True)
    assert "새 프로젝트" in notes_html and 'class="side-tab active"' in notes_html


def test_rename(auth_client):
    deck = _create(auth_client)
    auth_client.patch(f"/api/decks/{deck['id']}", json={"title": "새 이름"})
    assert _deck(auth_client, deck["id"])["title"] == "새 이름"


# ---------- 작업(op) ----------

def test_add_move_duplicate_delete_slides(auth_client):
    deck = _create(auth_client)
    first = deck["slides"][0]["id"]
    res = _ops(auth_client, deck["id"], [
        {"op": "add_slide", "id": "sslide0001", "after": first, "layout": "two_content"},
        {"op": "duplicate_slide", "slide": "sslide0001", "id": "sslide0002"},
        {"op": "move_slide", "slide": "sslide0002", "to": 0},
    ])
    assert res.status_code == 200 and res.get_json()["version"] == 1
    ids = [s["id"] for s in _deck(auth_client, deck["id"])["slides"]]
    assert ids == ["sslide0002", first, "sslide0001"]

    _ops(auth_client, deck["id"], [{"op": "delete_slide", "slide": first}])
    assert [s["id"] for s in _deck(auth_client, deck["id"])["slides"]] == ["sslide0002", "sslide0001"]


def test_set_elements_and_element_ops(auth_client):
    deck = _create(auth_client)
    sid = deck["slides"][0]["id"]
    _ops(auth_client, deck["id"], [{"op": "set_elements", "slide": sid, "elements": [_text_el()]}])
    _ops(auth_client, deck["id"], [
        {"op": "update_element", "slide": sid, "element": "eabc1234", "set": {"x": 99, "style": {"bold": True}}},
        {"op": "add_element", "slide": sid, "element": {"id": "eshape01", "type": "shape", "shape": "ellipse", "x": 0, "y": 0, "w": 50, "h": 50, "style": {"fill": "#4472c4", "line": None}}},
    ])
    elements = _deck(auth_client, deck["id"])["slides"][0]["elements"]
    assert elements[0]["x"] == 99
    assert elements[0]["style"]["bold"] is True and elements[0]["style"]["fontSize"] == 24  # 기존 서식 유지
    assert elements[1]["shape"] == "ellipse" and elements[1]["style"]["line"] is None

    _ops(auth_client, deck["id"], [{"op": "delete_element", "slide": sid, "element": "eabc1234"}])
    assert [e["id"] for e in _deck(auth_client, deck["id"])["slides"][0]["elements"]] == ["eshape01"]


def test_layout_change_keeps_text(auth_client):
    deck = _create(auth_client)
    slide = deck["slides"][0]
    elements = slide["elements"]
    elements[0]["text"] = "발표 제목"
    _ops(auth_client, deck["id"], [
        {"op": "set_elements", "slide": slide["id"], "elements": elements},
        {"op": "update_slide", "slide": slide["id"], "set": {"layout": "title_content"}},
    ])
    after = _deck(auth_client, deck["id"])["slides"][0]
    title = next(e for e in after["elements"] if e["placeholder"] == "title")
    assert after["layout"] == "title_content"
    assert title["text"] == "발표 제목" and (title["x"], title["y"]) == (88, 38)  # 새 레이아웃 위치로
    assert any(e["placeholder"] == "body" for e in after["elements"])  # 없던 개체 틀 추가


def test_text_is_sanitized_and_numbers_clamped(auth_client):
    deck = _create(auth_client)
    sid = deck["slides"][0]["id"]
    evil = _text_el(text='<b>굵게</b><script>alert(1)</script><img src=x onerror=alert(1)><a href="javascript:x">링크</a>',
                    x=1e9, style={"fontSize": 9999, "color": "red", "fontFamily": "Comic<script>"})
    _ops(auth_client, deck["id"], [{"op": "set_elements", "slide": sid, "elements": [evil]}])
    el = _deck(auth_client, deck["id"])["slides"][0]["elements"][0]
    assert el["text"] == "<b>굵게</b>alert(1)링크"
    assert el["x"] == 1280 * 3
    assert el["style"]["fontSize"] == 400 and el["style"]["color"] == "#000000" and el["style"]["fontFamily"] == "맑은 고딕"


@pytest.mark.parametrize("bad_ops, message", [
    ([{"op": "delete_slide", "slide": "snothere0"}], "찾을 수 없습니다"),
    ([{"op": "nope"}], "알 수 없는 작업"),
    ([{"op": "set_elements", "slide": "__SID__", "elements": [{"id": "e1", "type": "text"}]}], "개체 ID"),
    ([{"op": "set_elements", "slide": "__SID__", "elements": [{"id": "eabcd", "type": "video"}]}], "지원하지 않는"),
    ([{"op": "set_elements", "slide": "__SID__", "elements": [{"id": "eabcd", "type": "image", "src": "http://evil/x.png"}]}], "그림 주소"),
    ([{"op": "update_slide", "slide": "__SID__", "set": {"layout": "weird"}}], "레이아웃"),
])
def test_bad_ops_are_rejected_atomically(auth_client, bad_ops, message):
    deck = _create(auth_client)
    sid = deck["slides"][0]["id"]
    ops = [{"op": "add_slide", "id": "sgood00001"}] + [{**o, "slide": sid} if o.get("slide") == "__SID__" else o for o in bad_ops]
    res = _ops(auth_client, deck["id"], ops)
    assert res.status_code == 400 and message in res.get_json()["error"]
    assert _deck(auth_client, deck["id"])["slide_count"] == 1  # 앞의 add_slide 도 반영되지 않음


def test_duplicate_slide_id_rejected(auth_client):
    deck = _create(auth_client)
    sid = deck["slides"][0]["id"]
    assert _ops(auth_client, deck["id"], [{"op": "add_slide", "id": sid}]).status_code == 400


def test_apply_ops_requires_list(app):
    with app.app_context():
        deck = deck_service.create_deck("x")
        with pytest.raises(OpError):
            apply_ops(deck, [])


# ---------- 보관 · 휴지통 · 그림 ----------

def _png():
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(buf, format="PNG")
    return buf.getvalue()


def test_image_upload_and_permanent_delete(auth_client, app):
    deck = _create(auth_client)
    res = auth_client.post(f"/api/decks/{deck['id']}/images", data={"file": (io.BytesIO(_png()), "a.png")},
                           content_type="multipart/form-data")
    assert res.status_code == 201
    body = res.get_json()
    assert (body["width"], body["height"]) == (40, 20)
    served = auth_client.get(body["url"])
    assert served.status_code == 200
    served.close()

    sid = deck["slides"][0]["id"]
    image = {"id": "eimg00001", "type": "image", "x": 0, "y": 0, "w": 40, "h": 20, "src": body["url"]}
    assert _ops(auth_client, deck["id"], [{"op": "set_elements", "slide": sid, "elements": [image]}]).status_code == 200

    assert auth_client.delete(f"/api/decks/{deck['id']}").status_code == 400  # 휴지통 먼저
    auth_client.post(f"/api/decks/{deck['id']}/trash")
    assert _ops(auth_client, deck["id"], [{"op": "add_slide"}]).status_code == 400  # 휴지통에서는 편집 불가
    assert auth_client.delete(f"/api/decks/{deck['id']}").status_code == 200
    assert auth_client.get(body["url"]).status_code == 404
    with app.app_context():
        assert deck_service.get(deck["id"]) is None
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []


def test_archive_and_restore_lists(auth_client):
    deck = _create(auth_client, "보관할 발표")
    auth_client.post(f"/api/decks/{deck['id']}/archive")
    assert "보관할 발표" in auth_client.get("/decks?view=archived").get_data(as_text=True)
    assert "보관할 발표" not in auth_client.get("/decks").get_data(as_text=True).split('class="deck-grid"')[-1]
    auth_client.post(f"/api/decks/{deck['id']}/restore")
    assert _deck(auth_client, deck["id"])["status"] == "active"


def test_deck_api_requires_login(client):
    assert client.post("/api/decks", json={}).status_code == 401


def test_deck_is_a_json_file(auth_client, app):
    deck = _create(auth_client, "파일")
    path = os.path.join(app.config["DECKS_DIR"], f"{deck['id']}.json")
    with open(path, encoding="utf-8") as f:
        assert '"title": "파일"' in f.read()
