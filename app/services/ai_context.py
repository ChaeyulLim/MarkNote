"""AI 도우미에 질문할 때 함께 보내는 "지금 상황".

  <location>   사용자가 지금 보고 있는 곳 (노트 페이지 / 프레젠테이션·슬라이드 / 그 밖의 화면) — 몇십 토큰
  <selection>  사용자가 드래그로 선택한 글자 ("이 부분") — 글자 그대로, 이미지 아님
  <page> / <presentation>  보고 있는 페이지·프레젠테이션의 최신 내용
                           (편집기가 질문 직전에 저장하므로 입력 중이던 내용도 포함)

같은 대화에서 내용이 바뀌지 않았으면 전체 대신 "바뀐 내용 없음" 한 줄만 보내 토큰을 아낀다.
"""

import hashlib
import re
from collections import OrderedDict
from html import unescape

from . import decks as deck_service
from . import page_tree as tree
from . import secret_text, store
from .decks import LAYOUTS, Deck
from .page_tree import Page

MAX_CONTEXT_CHARS = 150_000
MAX_SELECTION_CHARS = 2000
_REMEMBERED_SESSIONS = 50

VIEW_NAMES = {
    "home": "노트 홈",
    "archive": "노트 보관함",
    "trash": "노트 휴지통",
    "settings": "설정",
    "decks": "프레젠테이션 목록",
}

# 대화(session)별로 마지막에 보낸 내용의 해시 {session_id: {key: hash}}
_sent: "OrderedDict[str, dict[str, str]]" = OrderedDict()


def _quote(text: str) -> str:
    return str(text).replace('"', "'").replace("\n", " ")[:200]


def _clip(text: str) -> str:
    if len(text) > MAX_CONTEXT_CHARS:
        return text[:MAX_CONTEXT_CHARS] + "\n…(내용이 길어 뒷부분은 생략됨)\n"
    return text


# ---------- 노트 페이지 ----------

def _body(page: Page) -> str:
    return secret_text.redact(tree.page_source(page)) + "\n"


def _folder_text(folder: Page, tag: str) -> str:
    """폴더 = 안의 노트 전부 (하위 폴더까지)."""
    path = " / ".join(p.display_title for p in [*tree.ancestors(folder), folder])
    notes = "".join(
        f'<note id="{n.id}" file="{_quote(tree.file_path(n))}">\n{_body(n)}</note>\n' for n in tree.folder_notes(folder)
    ) or "(빈 폴더)\n"
    return f'<{tag} id="{folder.id}" kind="folder" path="{_quote(path)}" file="{_quote(tree.file_path(folder))}">\n{notes}</{tag}>\n'


def _page_text(page: Page) -> str:
    if tree.is_folder(page):
        return _folder_text(page, "page")
    return f'<page id="{page.id}" title="{_quote(page.display_title)}" file="{_quote(tree.file_path(page))}">\n{_body(page)}</page>\n'


def page_context(page: Page, include_children: bool = False) -> str:
    """보고 있는 노트. 폴더면 안의 노트 전부 (include_children 은 예전 하위 페이지용 — 지금은 쓰지 않음)."""
    return _clip(_page_text(page))


# ---------- 프레젠테이션 ----------

def plain_text(html: str) -> str:
    """슬라이드 글상자 HTML → 줄 단위 글자 (목록은 "• ")."""
    text = re.sub(r"<li[^>]*>", "\n• ", html or "")
    text = re.sub(r"<(br|/div|/p|/li|div|p)[^>]*>", "\n", text)
    text = unescape(re.sub(r"<[^>]+>", "", text))
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def _slide_title(slide) -> str:
    for role in ("title", "header"):
        for el in slide.elements or []:
            if el.get("placeholder") == role and plain_text(el.get("text")):
                return secret_text.redact(plain_text(el["text"])).splitlines()[0]
    for el in slide.elements or []:
        if plain_text(el.get("text")):
            return secret_text.redact(plain_text(el["text"])).splitlines()[0]
    return "(제목 없음)"


def deck_context(deck: Deck, current_index: int | None = None) -> str:
    """모든 슬라이드의 글 + 지금 보는 슬라이드는 개체 위치·크기까지."""
    lines = [f'<presentation id="{deck.id}" title="{_quote(deck.display_title)}" slides="{len(deck.slides)}">']
    for i, slide in enumerate(deck.slides, 1):
        layout = LAYOUTS.get(slide.layout, {}).get("name", slide.layout)
        flags = (" (숨김)" if slide.hidden else "") + (" ← 사용자가 지금 보는 슬라이드" if i == current_index else "")
        lines.append(f"\n[슬라이드 {i}] 레이아웃: {layout}{flags}")
        for el in slide.elements or []:
            text = secret_text.redact(plain_text(el.get("text")))
            kind = el["type"] + (f":{el['shape']}" if el.get("shape") else "")
            if i == current_index:
                pos = f" 위치 {round(el['x'])},{round(el['y'])} 크기 {round(el['w'])}×{round(el['h'])}"
                size = f" {el.get('style', {}).get('fontSize')}pt" if text else ""
                lines.append(f"- {el.get('name') or el['id']} ({kind}{size}){pos}" + (f": {text}" if text else ""))
            elif text:
                lines.append(f"- {el.get('name') or el['id']}: {text}")
        if slide.notes:
            lines.append(f"  노트: {secret_text.redact(slide.notes)}")
    lines.append("</presentation>\n")
    return _clip("\n".join(lines))


def deck_texts(deck: Deck) -> list[str]:
    """프레젠테이션의 모든 글 (비공개 표시 확인용): 글상자 HTML, 슬라이드 노트."""
    texts = []
    for slide in deck.slides:
        texts += [el.get("text") or "" for el in slide.elements or []]
        texts.append(slide.notes or "")
    return texts


# ---------- 위치 ----------

def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def describe_location(location: dict) -> str:
    view = location.get("view")
    if view == "page" and (page := store.get(_int(location.get("page_id")) or 0)):
        path = " / ".join(p.display_title for p in [*tree.ancestors(page), page])
        where = f'노트 페이지 "{_quote(page.display_title)}" (경로: {_quote(path)}, 파일: {_quote(tree.file_path(page))}, id {page.id})'
        block = _int(location.get("editing_block"))
        if block:
            where += f", {block}번째 블록을 편집 중 (전체 {_int(location.get('block_count')) or '?'}개)"
        return where
    if view == "deck" and (deck := deck_service.get(_int(location.get("deck_id")) or 0)):
        where = f'프레젠테이션 "{_quote(deck.display_title)}" (id {deck.id})'
        index = _int(location.get("slide_index"))
        if index:
            where += f", 슬라이드 {index}/{len(deck.slides)} 보는 중"
        selected = [_quote(n)[:60] for n in (location.get("selected") or []) if isinstance(n, str)][:20]
        if location.get("editing"):
            where += f', "{_quote(location["editing"])[:60]}" 글자 편집 중'
        elif selected:
            where += f", 선택한 개체: {', '.join(selected)}"
        return where
    return VIEW_NAMES.get(view, "MarkNote")


# ---------- 조립 ----------

def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def working_note(page: Page) -> str:
    """대화가 다루는 노트 (채팅에서 이름으로 부르거나 AI 가 읽고 고친 노트) — 매번 최신 내용.
    폴더면 안의 노트 전부 — "OO 폴더 확인해줘"."""
    if tree.is_folder(page):
        return _clip(_folder_text(page, "working_note"))
    path = " / ".join(p.display_title for p in [*tree.ancestors(page), page])
    return _clip(f'<working_note id="{page.id}" path="{_quote(path)}" file="{_quote(tree.file_path(page))}">\n{_body(page)}</working_note>\n')


def build(location: dict, page: Page | None, include_children: bool, deck: Deck | None, session_id: str | None,
          focus: list[Page] | None = None):
    """(stdin 으로 보낼 글, 이번에 보낸 내용의 해시들) — 해시는 응답 후 remember() 로 기록.

    focus: 이 대화가 다루는 노트 (지금 보는 페이지 말고). "거기에 추가해줘" 같은 말이 가리키는 곳."""
    location = location if isinstance(location, dict) else {}
    parts = [f"<location>사용자가 지금 보고 있는 곳: {describe_location(location)}</location>\n"]
    selection = str(location.get("selection") or "").strip()[:MAX_SELECTION_CHARS]
    if selection:
        # 화면에서 비공개 부분을 빼고 보내지만, 서버에서도 지금 페이지·프레젠테이션의 비공개 내용을 지움
        sources = [tree.page_source(page)] if page is not None else []
        if deck is not None:
            sources += deck_texts(deck)
        selection = secret_text.scrub_selection(selection, sources)
        parts.append(f"<selection>{selection}</selection>\n")

    previous = _sent.get(session_id, {}) if session_id else {}
    hashes: dict[str, str] = {}
    documents = []
    if page is not None:
        documents.append((f"page:{page.id}:{int(include_children)}", page_context(page, include_children),
                          f'<page id="{page.id}" title="{_quote(page.display_title)}">(지난 메시지 이후 바뀐 내용 없음)</page>\n'))
    if deck is not None:
        current = location.get("slide_index") if location.get("view") == "deck" else None
        # 지금 보는 슬라이드 번호가 바뀌면 상세 부분이 달라지므로 해시에도 반영된다
        documents.append((f"deck:{deck.id}", deck_context(deck, _int(current)),
                          f'<presentation id="{deck.id}">(지난 메시지 이후 바뀐 내용 없음)</presentation>\n'))
    for note in focus or []:
        if page is not None and note.id == page.id:
            continue
        documents.append((f"note:{note.id}", working_note(note),
                          f'<working_note id="{note.id}">(지난 메시지 이후 바뀐 내용 없음)</working_note>\n'))
    for key, full, unchanged in documents:
        hashes[key] = _digest(full)
        parts.append(unchanged if previous.get(key) == hashes[key] else full)
    return "".join(parts), hashes


def remember(session_id: str | None, hashes: dict[str, str]) -> None:
    if not session_id or not hashes:
        return
    _sent.setdefault(session_id, {}).update(hashes)
    _sent.move_to_end(session_id)
    while len(_sent) > _REMEMBERED_SESSIONS:
        _sent.popitem(last=False)
