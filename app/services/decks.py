"""프레젠테이션(덱): 레이아웃 정의, 개체 검증·정화, 생성·복제·삭제.

덱 하나 = instance/decks/<번호>.json (슬라이드·개체 전부). 웹 서버와 AI 도구(MCP)가 함께 고치므로
고칠 때는 with editing(deck_id) as deck: … 로 잠금 안에서 읽고-고치고-쓴다 (docs/storage.md).

좌표는 1280×720 논리 단위 (PowerPoint 16:9 기본 크기 33.867×19.05cm, 1단위 = 9525 EMU).
글자 크기는 pt (화면에서는 pt × 4/3 px).
"""

import copy
import os
import re
import secrets
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime

import bleach
from flask import abort, current_app

from . import filestore
from .attachments import find_upload

W, H = 1280, 720
ID_RE = re.compile(r"^[a-z][a-z0-9]{3,23}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
FILE_SRC_RE = re.compile(r"^/files/[0-9a-f]{32}$")

FONTS = ["맑은 고딕", "나눔고딕", "굴림", "돋움", "바탕", "궁서", "Arial", "Calibri", "Georgia", "Times New Roman", "Consolas"]
ELEMENT_TYPES = {"text", "shape", "image"}
SHAPES = {"rect", "round_rect", "ellipse", "triangle", "pentagon", "right_arrow", "line", "arrow_line"}
ROLES = {"title", "subtitle", "body", "body2", "header", "header2", "caption", "picture"}
ALIGNS = {"left", "center", "right", "justify"}
VALIGNS = {"top", "middle", "bottom"}
TEXT_TAGS = ["b", "strong", "i", "em", "u", "s", "strike", "br", "div", "p", "ul", "ol", "li", "span"]
MAX_ELEMENTS = 300
MAX_TEXT = 20_000


def new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(5)


# ---------- 레이아웃 (PowerPoint 기본 Office 테마 9종) ----------

def _ph(role, x, y, w, h, **style):
    return {"role": role, "x": x, "y": y, "w": w, "h": h, "style": style}


_TITLE = _ph("title", 88, 38, 1104, 140, fontSize=44, valign="middle")
LAYOUTS: dict[str, dict] = {
    "title_slide": {"name": "제목 슬라이드", "placeholders": [
        _ph("title", 160, 118, 960, 250, fontSize=60, align="center", valign="bottom"),
        _ph("subtitle", 160, 378, 960, 174, fontSize=24, align="center", valign="top"),
    ]},
    "title_content": {"name": "제목 및 내용", "placeholders": [
        _TITLE, _ph("body", 88, 192, 1104, 457, fontSize=28, bullets=True),
    ]},
    "section_header": {"name": "구역 머리글", "placeholders": [
        _ph("title", 87, 180, 1104, 300, fontSize=60, valign="bottom"),
        _ph("subtitle", 87, 483, 1104, 158, fontSize=24, color="#595959"),
    ]},
    "two_content": {"name": "콘텐츠 2개", "placeholders": [
        _TITLE,
        _ph("body", 88, 192, 544, 457, fontSize=28, bullets=True),
        _ph("body2", 648, 192, 544, 457, fontSize=28, bullets=True),
    ]},
    "comparison": {"name": "비교", "placeholders": [
        _TITLE,
        _ph("header", 88, 176, 541, 86, fontSize=24, bold=True, valign="bottom"),
        _ph("body", 88, 262, 541, 384, fontSize=24, bullets=True),
        _ph("header2", 648, 176, 544, 86, fontSize=24, bold=True, valign="bottom"),
        _ph("body2", 648, 262, 544, 384, fontSize=24, bullets=True),
    ]},
    "title_only": {"name": "제목만", "placeholders": [_TITLE]},
    "blank": {"name": "빈 화면", "placeholders": []},
    "content_caption": {"name": "캡션 있는 콘텐츠", "placeholders": [
        _ph("title", 88, 48, 413, 168, fontSize=32, valign="bottom"),
        _ph("body", 544, 104, 648, 512, fontSize=32, bullets=True),
        _ph("caption", 88, 216, 413, 402, fontSize=16),
    ]},
    "picture_caption": {"name": "캡션 있는 그림", "placeholders": [
        _ph("title", 88, 48, 413, 168, fontSize=32, valign="bottom"),
        _ph("picture", 544, 104, 648, 512),
        _ph("caption", 88, 216, 413, 402, fontSize=16),
    ]},
}

# PowerPoint 한국어판의 개체 틀 이름
_ROLE_NAMES = {
    "title": "제목", "subtitle": "부제목", "body": "내용 개체 틀", "body2": "내용 개체 틀",
    "header": "텍스트 개체 틀", "header2": "텍스트 개체 틀", "caption": "텍스트 개체 틀", "picture": "그림 개체 틀",
}


def layout_list() -> list[dict]:
    """편집기용 레이아웃 정의 (서버와 똑같이 개체 틀을 만들 수 있도록 위치·기본 서식·이름 포함)."""
    return [
        {
            "id": key,
            "name": value["name"],
            "placeholders": [{**spec, "name": _ROLE_NAMES[spec["role"]]} for spec in value["placeholders"]],
        }
        for key, value in LAYOUTS.items()
    ]


def placeholder_element(spec: dict, number: int) -> dict:
    role = spec["role"]
    return {
        "id": new_id("e"),
        "type": "image" if role == "picture" else "text",
        "name": f"{_ROLE_NAMES[role]} {number}",
        "placeholder": role,
        "x": spec["x"], "y": spec["y"], "w": spec["w"], "h": spec["h"], "rotate": 0,
        "text": "", "src": None,
        "style": {"fontFamily": FONTS[0], "fontSize": 18, "color": "#000000", "align": "left", "valign": "top",
                  **spec["style"]},
    }


def layout_elements(layout: str) -> list[dict]:
    return [placeholder_element(spec, i + 1) for i, spec in enumerate(LAYOUTS[layout]["placeholders"])]


def apply_layout(elements: list[dict], layout: str) -> list[dict]:
    """레이아웃 변경: 같은 역할의 개체 틀은 내용을 유지한 채 위치·크기만, 없는 개체 틀은 새로 만든다."""
    result = copy.deepcopy(elements)
    by_role = {e.get("placeholder"): e for e in result if e.get("placeholder")}
    for i, spec in enumerate(LAYOUTS[layout]["placeholders"]):
        existing = by_role.get(spec["role"])
        if existing:
            existing.update(x=spec["x"], y=spec["y"], w=spec["w"], h=spec["h"], rotate=0)
        else:
            result.append(placeholder_element(spec, len(result) + i + 1))
    return result


# ---------- 개체 검증·정화 ----------

class ElementError(ValueError):
    pass


def _num(value, lo, hi, default=0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return round(max(lo, min(hi, number)), 2)


def _color(value, allow_none=True):
    if value in (None, "none") and allow_none:
        return None
    return value if isinstance(value, str) and COLOR_RE.match(value) else None


def sanitize_style(style: dict) -> dict:
    style = style if isinstance(style, dict) else {}
    out = {
        "fontFamily": style.get("fontFamily") if style.get("fontFamily") in FONTS else FONTS[0],
        "fontSize": _num(style.get("fontSize"), 1, 400, 18),
        "color": _color(style.get("color"), allow_none=False) or "#000000",
        "align": style.get("align") if style.get("align") in ALIGNS else "left",
        "valign": style.get("valign") if style.get("valign") in VALIGNS else "top",
    }
    for flag in ("bold", "italic", "underline", "strike", "bullets", "flipH", "flipV"):
        if style.get(flag):
            out[flag] = True
    if "fill" in style:
        out["fill"] = _color(style.get("fill"))
    if "line" in style:
        out["line"] = _color(style.get("line"))
    if "lineWidth" in style:
        out["lineWidth"] = _num(style.get("lineWidth"), 0, 40, 1)
    return out


def sanitize_text(html) -> str:
    html = html if isinstance(html, str) else ""
    return bleach.clean(html[:MAX_TEXT], tags=TEXT_TAGS, attributes={}, strip=True)


def sanitize_element(raw) -> dict:
    if not isinstance(raw, dict):
        raise ElementError("개체 형식이 올바르지 않습니다.")
    eid = raw.get("id")
    if not isinstance(eid, str) or not ID_RE.match(eid):
        raise ElementError("개체 ID 가 올바르지 않습니다.")
    kind = raw.get("type")
    if kind not in ELEMENT_TYPES:
        raise ElementError(f"지원하지 않는 개체 종류입니다: {kind}")
    role = raw.get("placeholder")
    element = {
        "id": eid,
        "type": kind,
        "name": str(raw.get("name") or "")[:80],
        "placeholder": role if role in ROLES else None,
        "x": _num(raw.get("x"), -W * 2, W * 3),
        "y": _num(raw.get("y"), -H * 2, H * 3),
        "w": _num(raw.get("w"), 0, W * 4, 100),
        "h": _num(raw.get("h"), 0, H * 4, 100),
        "rotate": _num(raw.get("rotate"), -360, 360),
        "text": sanitize_text(raw.get("text")),
        "src": None,
        "style": sanitize_style(raw.get("style")),
    }
    if kind == "shape":
        element["shape"] = raw.get("shape") if raw.get("shape") in SHAPES else "rect"
    if kind == "image":
        src = raw.get("src")
        if src is not None and not (isinstance(src, str) and FILE_SRC_RE.match(src)):
            raise ElementError("그림 주소가 올바르지 않습니다.")
        element["src"] = src
        if src is None and element["placeholder"] != "picture":
            raise ElementError("그림이 없는 그림 개체입니다.")
    return element


def sanitize_elements(raw_list) -> list[dict]:
    if not isinstance(raw_list, list):
        raise ElementError("개체 목록 형식이 올바르지 않습니다.")
    if len(raw_list) > MAX_ELEMENTS:
        raise ElementError(f"슬라이드 하나에 개체는 {MAX_ELEMENTS}개까지입니다.")
    elements = [sanitize_element(e) for e in raw_list]
    ids = [e["id"] for e in elements]
    if len(ids) != len(set(ids)):
        raise ElementError("같은 ID 의 개체가 있습니다.")
    return elements


# ---------- 덱 (JSON 파일) ----------

@dataclass
class Slide:
    id: str
    layout: str = "title_content"
    hidden: bool = False
    notes: str = ""
    background: str | None = None
    elements: list = field(default_factory=list)


@dataclass
class Deck:
    id: int
    title: str = ""
    status: str = "active"  # active / archived / trashed
    position: int = 0
    version: int = 0  # 작업(op)이 적용될 때마다 1 증가
    settings: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    trashed_at: datetime | None = None
    slides: list[Slide] = field(default_factory=list)

    @property
    def display_title(self) -> str:
        return self.title or "제목 없는 프레젠테이션"


def _dir() -> str:
    return current_app.config["DECKS_DIR"]


def _path(deck_id: int) -> str:
    return os.path.join(_dir(), f"{int(deck_id)}.json")


def _time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _load(path: str) -> Deck | None:
    data = filestore.read_json(path, None)
    if not isinstance(data, dict) or "id" not in data:
        return None
    slides = [Slide(**{k: v for k, v in s.items() if k in Slide.__dataclass_fields__}) for s in data.get("slides", [])]
    return Deck(
        id=int(data["id"]), title=data.get("title", ""), status=data.get("status", "active"),
        position=int(data.get("position", 0)), version=int(data.get("version", 0)), settings=data.get("settings") or {},
        created_at=_time(data.get("created_at")) or datetime.now(), updated_at=_time(data.get("updated_at")) or datetime.now(),
        trashed_at=_time(data.get("trashed_at")), slides=slides,
    )


def get(deck_id) -> Deck | None:
    try:
        return _load(_path(int(deck_id)))
    except (TypeError, ValueError):
        return None


def get_or_404(deck_id) -> Deck:
    deck = get(deck_id)
    if deck is None:
        abort(404, description="프레젠테이션을 찾을 수 없습니다.")
    return deck


def save(deck: Deck) -> Deck:
    data = asdict(deck)
    for key in ("created_at", "updated_at", "trashed_at"):
        data[key] = data[key].isoformat(timespec="seconds") if data[key] else None
    filestore.write_json(_path(deck.id), data)
    return deck


@contextmanager
def editing(deck_id):
    """잠금 안에서 덱을 읽어 넘기고, 오류 없이 끝나면 저장한다 (오류가 나면 파일은 그대로)."""
    with filestore.locked(_path(deck_id)):
        deck = get(deck_id)
        yield deck
        if deck is not None:
            save(deck)


def all_decks() -> list[Deck]:
    decks = []
    for name in os.listdir(_dir()):
        if name.endswith(".json") and name[:-5].isdigit():
            deck = _load(os.path.join(_dir(), name))
            if deck is not None:
                decks.append(deck)
    return decks


def slide_dict(slide: Slide) -> dict:
    return {
        "id": slide.id,
        "layout": slide.layout,
        "hidden": slide.hidden,
        "notes": slide.notes,
        "background": slide.background,
        "elements": slide.elements or [],
    }


def deck_dict(deck: Deck, with_slides: bool = True) -> dict:
    data = {
        "id": deck.id,
        "title": deck.title,
        "display_title": deck.display_title,
        "status": deck.status,
        "version": deck.version,
        "slide_count": len(deck.slides),
        "updated_at": deck.updated_at.strftime("%Y-%m-%d %H:%M"),
    }
    if with_slides:
        data["slides"] = [slide_dict(s) for s in deck.slides]
    return data


def make_slide(layout: str = "title_content", slide_id: str | None = None) -> Slide:
    return Slide(id=slide_id or new_id("s"), layout=layout, elements=layout_elements(layout))


def create_deck(title: str = "") -> Deck:
    deck_id = filestore.Counter(os.path.join(_dir(), "_next.json")).next()
    while os.path.exists(_path(deck_id)):  # 가져온 파일과 번호가 겹치면 건너뜀
        deck_id = filestore.Counter(os.path.join(_dir(), "_next.json")).next()
    position = max((d.position for d in all_decks()), default=-1) + 1
    return save(Deck(id=deck_id, title=title.strip()[:200], position=position, slides=[make_slide("title_slide")]))


def set_status(deck: Deck, status: str) -> None:
    deck.status = status
    deck.trashed_at = datetime.now() if status == "trashed" else None


def image_ids(deck: Deck) -> set[str]:
    return {e["src"].rsplit("/", 1)[-1] for s in deck.slides for e in s.elements or [] if e.get("src")}


def delete_permanently(deck: Deck) -> None:
    """덱 파일과 그 슬라이드의 그림 파일을 지운다 (다른 덱이 함께 쓰는 그림은 남김)."""
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    shared = set().union(*(image_ids(d) for d in all_decks() if d.id != deck.id))
    for file_id in image_ids(deck) - shared:
        name = find_upload(upload_folder, file_id)
        if name is None:
            continue
        try:
            os.remove(os.path.join(upload_folder, name))
        except OSError:
            current_app.logger.warning("첨부 파일 삭제 실패: %s", name)
    try:
        os.remove(_path(deck.id))
    except FileNotFoundError:
        pass


def decks_with_status(status: str) -> list[Deck]:
    return sorted((d for d in all_decks() if d.status == status), key=lambda d: d.updated_at, reverse=True)
