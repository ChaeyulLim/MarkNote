"""프레젠테이션 편집 작업(operation) 적용.

편집기·(나중에) 실행 취소·버전 기록·AI 제안이 모두 같은 작업 목록 형식을 쓴다.

  {"op": "add_slide", "id"?, "after"? | "index"?, "layout"?, "elements"?, "texts"?: {역할: 글}}
  {"op": "delete_slide", "slide"}
  {"op": "move_slide", "slide", "to"}                 to = 0부터 시작하는 새 위치
  {"op": "duplicate_slide", "slide", "id"?}
  {"op": "update_slide", "slide", "set": {layout?, notes?, hidden?, background?}}
  {"op": "set_elements", "slide", "elements": [...]}  슬라이드의 개체 전체 교체 (자동 저장용)
  {"op": "add_element", "slide", "element", "index"?}
  {"op": "update_element", "slide", "element": id, "set": {...}}
  {"op": "delete_element", "slide", "element": id}
"""

import copy
from datetime import datetime

from .decks import (
    COLOR_RE,
    ID_RE,
    LAYOUTS,
    Deck,
    ElementError,
    Slide,
    apply_layout,
    make_slide,
    new_id,
    sanitize_element,
    sanitize_elements,
    sanitize_text,
)

MAX_OPS = 500
MAX_SLIDES = 500


class OpError(ValueError):
    pass


def _find(ordered: list[Slide], slide_id) -> Slide:
    for slide in ordered:
        if slide.id == slide_id:
            return slide
    raise OpError(f"슬라이드를 찾을 수 없습니다: {slide_id}")


def _index_of(ordered: list[Slide], slide_id) -> int:
    return ordered.index(_find(ordered, slide_id))


def _new_slide_id(ordered: list[Slide], requested) -> str:
    if requested is None:
        return new_id("s")
    if not isinstance(requested, str) or not ID_RE.match(requested):
        raise OpError("슬라이드 ID 가 올바르지 않습니다.")
    if any(s.id == requested for s in ordered):
        raise OpError(f"이미 있는 슬라이드 ID 입니다: {requested}")
    return requested


def _element_index(elements: list[dict], element_id) -> int:
    for i, e in enumerate(elements):
        if e["id"] == element_id:
            return i
    raise OpError(f"개체를 찾을 수 없습니다: {element_id}")


def apply_ops(deck: Deck, ops) -> None:
    """작업 목록을 순서대로 검증·적용한다. 하나라도 잘못되면 OpError (호출한 쪽은 저장하지 않음 → 파일은 그대로)."""
    if not isinstance(ops, list) or not ops:
        raise OpError("작업 목록이 비어 있습니다.")
    if len(ops) > MAX_OPS:
        raise OpError(f"한 번에 {MAX_OPS}개 작업까지 보낼 수 있습니다.")

    ordered = list(deck.slides)
    try:
        for op in ops:
            if not isinstance(op, dict):
                raise OpError("작업 형식이 올바르지 않습니다.")
            _apply_one(deck, ordered, op)
    except ElementError as err:
        raise OpError(str(err)) from None

    deck.slides[:] = ordered
    deck.version += 1
    deck.updated_at = datetime.now()


def _apply_one(deck: Deck, ordered: list[Slide], op: dict) -> None:
    kind = op.get("op")

    if kind == "add_slide":
        if len(ordered) >= MAX_SLIDES:
            raise OpError(f"슬라이드는 {MAX_SLIDES}장까지입니다.")
        layout = op.get("layout") or "title_content"
        if layout not in LAYOUTS:
            raise OpError(f"알 수 없는 레이아웃입니다: {layout}")
        slide = make_slide(layout, _new_slide_id(ordered, op.get("id")))
        if op.get("elements") is not None:
            slide.elements = sanitize_elements(op["elements"])
        if isinstance(op.get("texts"), dict):  # 개체 틀 역할별 글 (예: {"title": "...", "body": "..."})
            slide.elements = [
                {**e, "text": sanitize_text(op["texts"][e["placeholder"]])} if e.get("placeholder") in op["texts"] else e
                for e in slide.elements
            ]
        if op.get("after") is not None:
            index = _index_of(ordered, op["after"]) + 1
        else:
            index = int(op.get("index", len(ordered)))
        ordered.insert(max(0, min(index, len(ordered))), slide)

    elif kind == "delete_slide":
        ordered.remove(_find(ordered, op.get("slide")))

    elif kind == "move_slide":
        slide = _find(ordered, op.get("slide"))
        ordered.remove(slide)
        to = int(op.get("to", len(ordered)))
        ordered.insert(max(0, min(to, len(ordered))), slide)

    elif kind == "duplicate_slide":
        source = _find(ordered, op.get("slide"))
        copy_slide = Slide(
            id=_new_slide_id(ordered, op.get("id")),
            layout=source.layout,
            hidden=source.hidden,
            notes=source.notes,
            background=source.background,
            elements=copy.deepcopy(source.elements),
        )
        ordered.insert(ordered.index(source) + 1, copy_slide)

    elif kind == "update_slide":
        slide = _find(ordered, op.get("slide"))
        changes = op.get("set") or {}
        if "layout" in changes:
            if changes["layout"] not in LAYOUTS:
                raise OpError(f"알 수 없는 레이아웃입니다: {changes['layout']}")
            slide.layout = changes["layout"]
            slide.elements = sanitize_elements(apply_layout(slide.elements or [], slide.layout))
        if "notes" in changes:
            slide.notes = str(changes["notes"] or "")[:20_000]
        if "hidden" in changes:
            slide.hidden = bool(changes["hidden"])
        if "background" in changes:
            bg = changes["background"]
            slide.background = bg if isinstance(bg, str) and COLOR_RE.match(bg) else None

    elif kind == "set_elements":
        slide = _find(ordered, op.get("slide"))
        slide.elements = sanitize_elements(op.get("elements"))

    elif kind == "add_element":
        slide = _find(ordered, op.get("slide"))
        element = sanitize_element(op.get("element"))
        elements = list(slide.elements or [])
        if any(e["id"] == element["id"] for e in elements):
            raise OpError(f"이미 있는 개체 ID 입니다: {element['id']}")
        index = int(op.get("index", len(elements)))
        elements.insert(max(0, min(index, len(elements))), element)
        slide.elements = sanitize_elements(elements)

    elif kind == "update_element":
        slide = _find(ordered, op.get("slide"))
        elements = copy.deepcopy(slide.elements or [])
        i = _element_index(elements, op.get("element"))
        changes = op.get("set") or {}
        if not isinstance(changes, dict) or "id" in changes:
            raise OpError("바꿀 내용이 올바르지 않습니다.")
        merged = {**elements[i], **{k: v for k, v in changes.items() if k != "style"}}
        if isinstance(changes.get("style"), dict):
            merged["style"] = {**elements[i].get("style", {}), **changes["style"]}
        elements[i] = merged
        slide.elements = sanitize_elements(elements)

    elif kind == "delete_element":
        slide = _find(ordered, op.get("slide"))
        elements = list(slide.elements or [])
        elements.pop(_element_index(elements, op.get("element")))
        slide.elements = elements

    else:
        raise OpError(f"알 수 없는 작업입니다: {kind}")
