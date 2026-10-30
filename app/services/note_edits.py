"""AI 가 노트(.md 파일)를 바로 고친다 — 적용 후 [되돌리기] / [확정] (docs/claude-integration.md "노트 바로 수정").

- 수정 기록은 instance/ai-edits/notes/<번호>.json: 적용 전·후 블록 내용, 바뀐 블록 위치
- 페이지 강조: 지금 파일 내용이 AI 가 쓴 그대로일 때만 (사용자가 고쳐 저장하면 강조는 저절로 사라짐)
- 되돌리기: 그 뒤 파일이 그대로면 통째로 원래대로. 사용자가 다른 곳을 고쳤으면 AI 가 바꾼 부분만 되돌리고,
  AI 가 바꾼 바로 그 부분을 고쳤으면 ProposalConflict (force 면 수정 전 전체로)
- 비공개 표시({{ }})·같은 이름 금지 규칙은 note_tools 와 같다. 폴더에는 내용을 쓸 수 없다
- 삭제(trash_page)는 실제로 지우지 않고 휴지통(notes/.trash/)으로 옮긴다. 되돌리기 = 원래 자리로 복원
"""

from difflib import SequenceMatcher

from . import page_tree as tree
from . import records, store
from .markdown_renderer import split_markdown
from .note_tools import ProposalConflict, ToolError, _check_name, _diff, _restore_secrets, _visible_page
from .page_tree import Page

KIND = "notes"


def _snapshot(page: Page) -> dict:
    return {"title": page.title, "blocks": [b.content for b in page.blocks]}


def apply_blocks(page: Page, contents: list[str]) -> list[int]:
    """페이지 본문을 contents 로 맞춘다 (같은 블록은 번호 유지). 바뀐(새로 쓴) 블록의 위치 목록을 돌려준다."""
    old = page.blocks
    blocks: list = list(contents)
    changed = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, [b.content for b in old], contents, autojunk=False).get_opcodes():
        if tag == "equal":
            blocks[j1:j2] = old[i1:i2]
        else:
            changed += range(j1, j2)
    store.save_blocks(page, blocks)
    return changed


# ---------- AI 도구 (MCP) ----------

def edit_page(run_id: str, page_id: int, markdown: str, summary: str, title: str | None = None) -> dict:
    page = _visible_page(page_id)
    if tree.is_folder(page) and (markdown or "").strip():
        raise ToolError("폴더에는 내용이 없습니다. 그 안의 노트를 고치거나 create_page 로 노트를 만드세요.")
    base = tree.page_source(page)
    markdown = _restore_secrets(markdown, base)
    new_title = title.strip()[:200] if title is not None else None
    if new_title is not None:
        _check_name(page.parent_id, new_title, page.id, page.kind)
    if markdown.strip() == base.strip() and (new_title is None or new_title == page.title):
        raise ToolError("기존 내용과 같아서 바꿀 것이 없습니다.")

    note = (summary or "").strip()
    before = _snapshot(page)
    changed = [] if tree.is_folder(page) else apply_blocks(page, split_markdown(markdown))
    if new_title is not None and new_title != page.title:
        tree.rename_page(store.get(page.id), new_title)
    page = store.get(page.id)
    edit = records.create(
        KIND, run_id=run_id, type="update", page_id=page.id, summary=note[:500],
        before=before, after=_snapshot(page), changed=changed, status="applied",
    )
    return {
        "applied": True,
        "edit_id": edit.id,
        "file": tree.file_path(page),
        "changed_blocks": len(changed),
        "message": "노트 파일에 바로 적용했습니다. 사용자는 채팅이나 페이지에서 [되돌리기](Ctrl+Z) 또는 [확정](Ctrl+S)을 고릅니다.",
    }


def create_page(run_id: str, title: str, markdown: str, parent_id: int | None = None, summary: str = "",
                folder: bool = False) -> dict:
    title = (title or "").strip()[:200]
    if not title:
        raise ToolError("새 페이지 제목을 입력하세요.")
    parent = _visible_page(parent_id) if parent_id is not None else None
    if folder and (markdown or "").strip():
        raise ToolError("폴더에는 내용을 쓸 수 없습니다. 폴더를 folder=true 로 먼저 만들고 그 안(parent_id)에 노트를 만드세요.")
    _check_name(parent.id if parent else None, title, kind="folder" if folder else "note")
    markdown = _restore_secrets(markdown, "")
    try:
        page = tree.create_page(parent, title, kind="folder" if folder else "note")
    except store.StoreError as err:
        raise ToolError(str(err)) from None
    blocks = [] if folder else store.save_blocks(page, split_markdown(markdown))
    page = store.get(page.id)
    edit = records.create(
        KIND, run_id=run_id, type="create", page_id=page.id, summary=(summary or "").strip()[:500],
        before={"title": "", "blocks": []}, after=_snapshot(page), changed=list(range(len(blocks))), status="applied",
    )
    return {
        "applied": True,
        "edit_id": edit.id,
        "page_id": page.id,
        "file": tree.file_path(page),
        "message": "새 페이지를 만들었습니다. 사용자는 [되돌리기](휴지통으로) 또는 [확정]을 고릅니다.",
    }


def trash_page(run_id: str, page_id: int, summary: str = "") -> dict:
    """노트·폴더를 휴지통으로 (폴더면 안의 것까지). 영구 삭제는 하지 않는다."""
    page = _visible_page(page_id)
    inside = [p for p in tree.descendants(page) if p.status != "trashed"]
    before = {"title": page.title, "blocks": [], "file": tree.file_path(page),
              "notes": len([p for p in inside if not tree.is_folder(p)])}
    tree.trash(page)
    edit = records.create(
        KIND, run_id=run_id, type="trash", page_id=page.id, summary=(summary or "").strip()[:500],
        before=before, after=before, changed=[], status="applied",
    )
    return {
        "applied": True,
        "edit_id": edit.id,
        "file": before["file"],
        "message": "휴지통으로 옮겼습니다 (영구 삭제 아님). 사용자는 [되돌리기](원래 자리로 복원) 또는 [확정]을 고릅니다.",
    }


# ---------- 되돌리기 / 확정 ----------

def _reverse(before: list[str], after: list[str], current: list[str]) -> list[str] | None:
    """AI 가 바꾼 부분만 current 에서 원래대로. 사용자가 그 부분을 고쳤으면 None."""
    where = {}  # after 블록 번호 → current 블록 번호 (그대로 남아 있는 것만)
    for tag, a1, a2, c1, _ in SequenceMatcher(None, after, current, autojunk=False).get_opcodes():
        if tag == "equal":
            where.update({a1 + k: c1 + k for k in range(a2 - a1)})
    result = list(current)
    hunks = [op for op in SequenceMatcher(None, before, after, autojunk=False).get_opcodes() if op[0] != "equal"]
    for _, i1, i2, j1, j2 in reversed(hunks):  # 뒤에서부터 바꿔야 앞쪽 번호가 안 흔들림
        if j2 > j1:
            spots = [where.get(j) for j in range(j1, j2)]
            if None in spots or spots != list(range(spots[0], spots[0] + len(spots))):
                return None
            k1, k2 = spots[0], spots[-1] + 1
        else:  # AI 가 지운 자리 → 바로 앞 블록 다음에 되살림
            if j1 == 0:
                k1 = 0
            elif where.get(j1 - 1) is None:
                return None
            else:
                k1 = where[j1 - 1] + 1
            k2 = k1
        result[k1:k2] = before[i1:i2]
    return result


def get(edit_id) -> records.Record | None:
    return records.get(KIND, edit_id)


def undo_edit(edit: records.Record, force: bool = False) -> Page | None:
    if edit.status == "undone":
        raise ToolError("이미 되돌린 수정입니다.")
    page = store.get(edit.page_id)
    if page is None:
        raise ToolError("페이지가 없습니다 (영구 삭제됨).")
    if edit.type == "create":
        if page.status != "trashed":
            tree.trash(page)
    elif edit.type == "trash":
        if page.status == "trashed":
            tree.restore(page)
    else:
        if page.status == "trashed":
            raise ToolError("페이지가 휴지통에 있습니다. 먼저 복원하세요.")
        current = [b.content for b in page.blocks]
        restored = _reverse(edit.before["blocks"], edit.after["blocks"], current)
        if restored is None:
            if not force:
                raise ProposalConflict("AI 가 고친 부분을 그 뒤에 직접 고쳤습니다. 되돌리면 그 부분의 내 수정도 사라집니다.")
            restored = edit.before["blocks"]
        if not tree.is_folder(page):
            apply_blocks(page, restored)
        page = store.get(page.id)
        if edit.before["title"] != edit.after["title"] and page.title == edit.after["title"]:
            tree.rename_page(page, edit.before["title"])
    edit.status = "undone"
    edit.save()
    return store.get(edit.page_id)


def confirm_edit(edit: records.Record) -> None:
    if edit.status == "applied":
        edit.status = "confirmed"
        edit.save()


def confirm_page(page_id: int) -> list[records.Record]:
    edits = pending(page_id)
    for edit in edits:
        confirm_edit(edit)
    return edits


def hide_page(page_id: int) -> list[records.Record]:
    """사용자가 직접 고쳐 저장했을 때 — 강조는 파일 내용이 바뀌면 저절로 사라지므로 따로 할 일이 없다."""
    return pending(page_id)


def pending(page_id: int) -> list[records.Record]:
    return records.query(KIND, page_id=page_id, status="applied")


def by_run(run_id: str) -> list[records.Record]:
    return records.query(KIND, run_id=run_id)


def _highlight(edit: records.Record, page: Page | None) -> list[int]:
    """강조할 블록 번호 — 파일이 AI 가 쓴 그대로일 때만."""
    if edit.status != "applied" or page is None or page.status == "trashed":
        return []
    blocks = page.blocks
    if [b.content for b in blocks] != edit.after["blocks"]:
        return []
    return [blocks[i].id for i in edit.changed if i < len(blocks)]


def edit_dict(edit: records.Record) -> dict:
    page = store.get(edit.page_id)
    before = "\n\n".join(edit.before.get("blocks", []))
    after = "\n\n".join(edit.after.get("blocks", []))
    return {
        "id": edit.id,
        "kind": edit.type,
        "status": edit.status,
        "page_id": edit.page_id,
        "page_title": page.display_title if page else "(삭제된 페이지)",
        "page_alive": page is not None and page.status != "trashed",
        "file": tree.file_path(page) if page and page.status == "active" else edit.before.get("file"),
        "inside_notes": edit.before.get("notes", 0) if edit.type == "trash" else None,
        "summary": edit.summary,
        "title_change": [edit.before.get("title"), edit.after.get("title")]
        if edit.type == "update" and edit.before.get("title") != edit.after.get("title") else None,
        "changed_block_ids": _highlight(edit, page),
        "diff": _diff(before, after),
    }
