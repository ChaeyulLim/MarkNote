"""Claude 가 프레젠테이션을 다루는 도구. MCP 서버(app/mcp_server.py)가 감싸서 Claude Code 에 제공한다.

수정은 바로 적용한다(edit_deck). 바꾸기 전 슬라이드를 instance/ai-edits/decks/<번호>.json 에 보관해서
사용자가 채팅의 [되돌리기]로 원래대로 돌릴 수 있다.
"""

import copy

from . import decks as deck_service
from . import records, secret_text
from .ai_context import deck_texts
from .deck_ops import OpError, apply_ops
from .decks import Deck, Slide
from .note_tools import ProposalConflict, ToolError

MAX_LIST_DECKS = 200
KIND = "decks"

EDIT_DECK_HELP = """Edit a presentation. The ops are applied IMMEDIATELY, all or nothing, and the user sees the result
right away (they can undo it from the chat). Call read_deck first to get slide and element ids.

Canvas is 1280x720. fontSize is in pt. Colors are #rrggbb. Element text is limited HTML:
<b> <i> <u> <s> <br>, one <div> per line/paragraph (for a bulleted body, one <div> per bullet), <ul><li>.
Refer to a slide by its id or by its number (1-based, numbers as they were before this edit).
Refer to an element by its id or its name (e.g. "제목 1").

ops (a list, applied in order):
  {"op":"update_element","slide":S,"element":E,"set":{"text":"...","x":..,"y":..,"w":..,"h":..,"rotate":..,
        "style":{"fontSize":32,"color":"#1f4e79","bold":true,"align":"center","valign":"middle",
                 "fontFamily":"맑은 고딕","fill":"#ddeeff","line":"#000000","lineWidth":2,"bullets":true}}}
        (style keys are merged; others: italic, underline, strike; align left|center|right|justify; valign top|middle|bottom)
  {"op":"add_element","slide":S,"element":{"type":"text"|"shape","shape":"rect|round_rect|ellipse|triangle|pentagon|right_arrow|line|arrow_line",
        "name":"텍스트 상자 3","x":..,"y":..,"w":..,"h":..,"text":"...","style":{...}}}   (id is generated)
  {"op":"delete_element","slide":S,"element":E}
  {"op":"add_slide","after":S | "index":0-based,"layout":L,"texts":{"title":"...","subtitle":"...","body":"<div>a</div><div>b</div>","body2":"..."}}
        layouts: title_slide, title_content, section_header, two_content, comparison, title_only, blank,
                 content_caption, picture_caption. texts fills the layout's placeholders by role
                 (title, subtitle, body, body2, header, header2, caption).
  {"op":"delete_slide","slide":S}
  {"op":"move_slide","slide":S,"to":0-based index}
  {"op":"duplicate_slide","slide":S}
  {"op":"update_slide","slide":S,"set":{"layout":L,"notes":"speaker notes","hidden":false,"background":"#ffffff"}}

summary: one short Korean sentence describing the change (shown to the user with an undo button).
Images cannot be added by AI."""


def _visible_deck(deck_id) -> Deck:
    deck = deck_service.get(deck_id)
    if deck is None or deck.status == "trashed":
        raise ToolError(f"프레젠테이션 {deck_id} 를 찾을 수 없습니다 (없거나 휴지통에 있음).")
    return deck


# ---------- 읽기 ----------

def list_decks() -> dict:
    decks = sorted((d for d in deck_service.all_decks() if d.status != "trashed"),
                   key=lambda d: d.updated_at, reverse=True)[:MAX_LIST_DECKS]
    return {
        "decks": [
            {"id": d.id, "title": d.display_title, "status": d.status, "slides": len(d.slides)} for d in decks
        ]
    }


def read_deck(deck_id) -> dict:
    deck = _visible_deck(deck_id)
    return {
        "id": deck.id,
        "title": deck.display_title,
        "version": deck.version,
        "size": {"w": deck_service.W, "h": deck_service.H},
        "layouts": {key: value["name"] for key, value in deck_service.LAYOUTS.items()},
        "fonts": deck_service.FONTS,
        # 비공개 표시 부분은 ⟦🔒…⟧ 로만
        "slides": secret_text.redact_obj([{"number": i + 1, **deck_service.slide_dict(s)} for i, s in enumerate(deck.slides)]),
    }


# ---------- 수정 (바로 적용) ----------

def _resolve(deck: Deck, ops: list) -> list:
    """AI 가 쓰기 쉽게: 슬라이드 번호(1부터)·개체 이름도 받고, 새 개체 ID 는 대신 만든다."""
    slides = list(deck.slides)
    resolved = []
    for raw in ops:
        if not isinstance(raw, dict):
            raise ToolError("작업 형식이 올바르지 않습니다.")
        op = copy.deepcopy(raw)
        for key in ("slide", "after"):
            ref = op.get(key)
            if isinstance(ref, int) and not isinstance(ref, bool):
                if not 1 <= ref <= len(slides):
                    raise ToolError(f"슬라이드 {ref} 번이 없습니다 (전체 {len(slides)}장).")
                op[key] = slides[ref - 1].id
        element_ref = op.get("element")
        if isinstance(element_ref, str) and op.get("op") in ("update_element", "delete_element"):
            slide = next((s for s in slides if s.id == op.get("slide")), None)
            elements = slide.elements if slide else []
            if not any(e["id"] == element_ref for e in elements):
                match = next((e for e in elements if e.get("name") == element_ref), None)
                if match:
                    op["element"] = match["id"]
        if op.get("op") == "add_element" and isinstance(op.get("element"), dict):
            op["element"].setdefault("id", deck_service.new_id("e"))
        if op.get("op") == "add_slide" and isinstance(op.get("elements"), list):
            for e in op["elements"]:
                if isinstance(e, dict):
                    e.setdefault("id", deck_service.new_id("e"))
        resolved.append(op)
    return resolved


def edit_deck(run_id: str, deck_id, ops, summary: str = "") -> dict:
    _visible_deck(deck_id)
    if not isinstance(ops, list) or not ops:
        raise ToolError("작업(ops) 목록이 비어 있습니다.")
    try:
        with deck_service.editing(deck_id) as deck:  # 오류가 나면 저장하지 않음 → 아무것도 바뀌지 않음
            before = [deck_service.slide_dict(s) for s in deck.slides]
            texts = deck_texts(deck)
            ops = secret_text.restore_obj(ops, secret_text.tag_map(*texts))
            apply_ops(deck, _resolve(deck, ops))
            # 비공개 부분을 지우거나 바꾸는 수정(글 덮어쓰기·개체/슬라이드 삭제)은 통째로 거절
            secret_text.check_kept(
                [raw for t in texts for raw in secret_text.secrets_in(t)],
                [raw for t in deck_texts(deck) for raw in secret_text.secrets_in(t)],
            )
    except (OpError, TypeError, ValueError) as err:
        raise ToolError(f"적용하지 못했습니다 (아무것도 바뀌지 않음): {err}") from None
    edit = records.create(
        KIND, run_id=run_id, deck_id=deck.id, summary=(summary or "").strip()[:500],
        op_count=len(ops), before=before, version_after=deck.version, status="applied",
    )
    return {
        "applied": True,
        "edit_id": edit.id,
        "version": deck.version,
        "slides": len(deck.slides),
        "message": "프레젠테이션에 바로 적용했습니다. 사용자 화면에도 반영되며, 채팅의 [되돌리기]로 취소할 수 있습니다.",
    }


# ---------- 표시 / 되돌리기 ----------

def get(edit_id) -> records.Record | None:
    return records.get(KIND, edit_id)


def by_run(run_id: str) -> list[records.Record]:
    return records.query(KIND, run_id=run_id)


def edit_dict(edit: records.Record) -> dict:
    deck = deck_service.get(edit.deck_id)
    return {
        "id": edit.id,
        "deck_id": edit.deck_id,
        "deck_title": deck.display_title if deck else None,
        "summary": edit.summary,
        "op_count": edit.op_count,
        "status": edit.status,
    }


def undo_edit(edit: records.Record, force: bool = False) -> Deck:
    if edit.status != "applied":
        raise ToolError("이미 되돌린 수정입니다.")
    with deck_service.editing(edit.deck_id) as deck:
        if deck is None or deck.status == "trashed":
            raise ToolError("프레젠테이션이 없거나 휴지통에 있습니다.")
        if deck.version != edit.version_after and not force:
            raise ProposalConflict("AI 수정 뒤에 프레젠테이션이 또 바뀌었습니다. 되돌리면 그 뒤의 변경도 함께 사라집니다.")
        deck.slides = [
            Slide(id=d["id"], layout=d["layout"], hidden=d["hidden"], notes=d["notes"],
                  background=d["background"], elements=copy.deepcopy(d["elements"]))
            for d in edit.before
        ]
        deck.version += 1
    edit.status = "undone"
    edit.save()
    return deck
