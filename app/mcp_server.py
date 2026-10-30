"""MarkNote 노트 도구를 Claude Code 에 제공하는 MCP 서버 (stdio).

routes/ai.py 가 claude 를 실행할 때 --mcp-config 로 `python -m app.mcp_server` 를 띄운다.
노트 폴더 등 저장 위치는 그때 넘겨주는 환경변수(NOTES_DIR, DECKS_DIR, AI_EDITS_DIR …, MARKNOTE_*)로 정해진다.
AI 는 노트 파일을 직접 열지 않고 이 도구로만 읽고 쓴다 (비공개 표시를 가리기 위해).
stdout 은 MCP 통신에 쓰이므로 이 프로세스에서는 print 하지 않는다.
"""

import os

from mcp.server.mcpserver import MCPServer

from app import create_app
from app.services import deck_tools, note_edits, note_focus, note_tools

RUN_ID = os.environ.get("MARKNOTE_RUN_ID", "manual")
ALLOW_EDIT = os.environ.get("MARKNOTE_ALLOW_EDIT") == "1"
ALLOW_DECK_EDIT = os.environ.get("MARKNOTE_ALLOW_DECK_EDIT") == "1"
ALLOW_DELETE = os.environ.get("MARKNOTE_ALLOW_DELETE") == "1"  # 휴지통으로 옮기기만 (영구 삭제 없음)

flask_app = create_app(accounts=False)

server = MCPServer(
    name="marknote",
    instructions=(
        "Tools for the user's MarkNote notes. Notes are plain Markdown files in folders, like a file explorer: "
        "folders and notes can sit at the top level or inside folders, and README.md is just an ordinary note. Each result's `file` "
        "is the path under the notes folder (e.g. 회사/회의록.md, 회사/2026-03/). Links between notes are normal "
        "relative Markdown links ([회의록](../회의록.md)). When the user asks to check/review a folder, "
        "read_page on that folder returns every note inside it. Note and presentation edits "
        "are applied immediately; the user can undo them from the chat or the page."
    ),
)


def _call(fn, *args, **kwargs) -> dict:
    with flask_app.app_context():
        try:
            return fn(*args, **kwargs)
        except note_tools.ToolError as err:
            return {"error": str(err)}


@server.tool(description="Search MarkNote pages by title and content. Returns page ids, paths and snippets.")
def search_pages(query: str, limit: int = 10) -> dict:
    return _call(note_tools.search_pages, query, limit)


@server.tool(
    description="Find a note by name or path like \"Project / Note\" (what the user calls it in chat). "
    "Returns matching page ids and paths; then read_page."
)
def find_page(path: str) -> dict:
    return _call(note_focus.find_page, path)


@server.tool(description="List all folders and notes as a tree (id, title, kind, file, depth, status).")
def list_pages() -> dict:
    return _call(note_tools.list_pages)


@server.tool(description="Read a note (full Markdown), or a folder: every note inside it including sub-folders "
             "(very long folders list the rest in more_notes).")
def read_page(page_id: int) -> dict:
    return _call(note_tools.read_page, page_id)


if ALLOW_EDIT:

    @server.tool(
        description=(
            "Edit a page directly: replace its entire Markdown content (and optionally its title). "
            "Read the page first and send the complete new content, keeping every "
            "⟦🔒…⟧ placeholder exactly. The change is applied immediately; the user can undo it."
        )
    )
    def edit_page(page_id: int, markdown: str, summary: str, title: str | None = None) -> dict:
        return _call(note_edits.edit_page, RUN_ID, page_id, markdown, summary, title)

    @server.tool(
        description=(
            "Create a new note (a .md file) now inside the folder parent_id (omit it for the top level), "
            "or a folder with folder=true (empty markdown). Names must be unique within the same folder. "
            "The user can undo it (moves it to trash)."
        )
    )
    def create_page(title: str, markdown: str, parent_id: int | None = None, summary: str = "",
                    folder: bool = False) -> dict:
        return _call(note_edits.create_page, RUN_ID, title, markdown, parent_id, summary, folder)


if ALLOW_DELETE:

    @server.tool(
        description=(
            "Delete a note or folder by moving it to the trash (a folder goes with everything inside it). "
            "Nothing is permanently deleted: the user can undo it (restores it to where it was) or restore it "
            "from the trash later. Use only when the user asks to delete/remove a note or folder."
        )
    )
    def trash_page(page_id: int, summary: str = "") -> dict:
        return _call(note_edits.trash_page, RUN_ID, page_id, summary)


@server.tool(description="List the user's presentations (id, title, slide count).")
def list_decks() -> dict:
    return _call(deck_tools.list_decks)


@server.tool(
    description=(
        "Read a presentation as JSON: slides in order with ids, layout, notes, background and every element "
        "(id, name, type, placeholder role, x/y/w/h on a 1280x720 canvas, text as limited HTML, style)."
    )
)
def read_deck(deck_id: int) -> dict:
    return _call(deck_tools.read_deck, deck_id)


if ALLOW_DECK_EDIT:

    @server.tool(description=deck_tools.EDIT_DECK_HELP)
    def edit_deck(deck_id: int, ops: list[dict], summary: str) -> dict:
        return _call(deck_tools.edit_deck, RUN_ID, deck_id, ops, summary)


if __name__ == "__main__":
    server.run("stdio")
