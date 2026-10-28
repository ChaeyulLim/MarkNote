import json
import os
import sys
import uuid
from urllib.parse import urlsplit

from flask import Blueprint, Response, abort, current_app, jsonify, request, url_for

from ..services import ai_context, ai_usage, chat_runs, chat_store, claude_auth, claude_runner, deck_tools, note_edits, note_focus, note_tools
from ..services import decks as deck_service
from ..services import store
from ..services import page_tree as tree
from ..services.markdown_renderer import render_block
from ..services.settings import get_settings, save_ai_prefs

bp = Blueprint("ai", __name__, url_prefix="/api/ai")

MAX_MESSAGE_CHARS = 8000


def _command():
    return current_app.config.get("CLAUDE_COMMAND")


READ_TOOLS = ["search_pages", "find_page", "list_pages", "read_page"]
EDIT_TOOLS = ["edit_page", "create_page"]
DELETE_TOOLS = ["trash_page"]  # 휴지통으로 옮기기만
DECK_TOOLS = ["list_decks", "read_deck", "edit_deck"]
_TOOL_LABELS = {  # 도구 이름 → (아이콘, 화면 표시)
    "search_pages": ("search", "노트 검색"),
    "find_page": ("search", "노트 찾기"),
    "list_pages": ("layers", "페이지 목록 확인"),
    "read_page": ("file", "페이지 읽기"),
    "edit_page": ("pencil", "노트 수정"),
    "create_page": ("file-plus", "새 노트 만들기"),
    "trash_page": ("trash", "휴지통으로 이동"),
    "propose_page_update": ("pencil", "수정안 작성"),  # 예전 대화 기록용
    "propose_new_page": ("file-plus", "새 페이지안 작성"),
    "list_decks": ("layers", "프레젠테이션 목록 확인"),
    "read_deck": ("presentation", "프레젠테이션 읽기"),
    "edit_deck": ("pencil", "슬라이드 수정"),
    "resume_fallback": ("refresh", "지난 대화를 다시 불러와 이어 가기"),
}


MCP_PATHS = ("NOTES_DIR", "UPLOAD_FOLDER", "DECKS_DIR", "AI_EDITS_DIR", "SETTINGS_FILE")


def _tools_config(run_id: str, prefs: dict) -> tuple[dict | None, list[str] | None]:
    """claude 에 넘길 MarkNote MCP 서버 설정과 허용 도구 목록 (권한이 하나도 없으면 도구 없이 실행)."""
    tools = (
        (READ_TOOLS if prefs["ai_notes_read"] else [])
        + (EDIT_TOOLS if prefs["ai_notes_edit"] else [])
        + (DELETE_TOOLS if prefs["ai_notes_delete"] else [])
        + (DECK_TOOLS if prefs["ai_decks_edit"] else [])
    )
    if not tools:
        return None, None
    project_root = os.path.dirname(current_app.root_path)
    server = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "app.mcp_server"],
        "env": {
            "PYTHONPATH": project_root,
            "PYTHONIOENCODING": "utf-8",
            # 저장 위치 (파일) — MCP 서버도 같은 파일을 읽고 쓴다
            **{key: current_app.config[key] for key in MCP_PATHS},
            "MARKNOTE_RUN_ID": run_id,
            "MARKNOTE_ALLOW_EDIT": "1" if prefs["ai_notes_edit"] else "0",
            "MARKNOTE_ALLOW_DECK_EDIT": "1" if prefs["ai_decks_edit"] else "0",
            "MARKNOTE_ALLOW_DELETE": "1" if prefs["ai_notes_delete"] else "0",
        },
    }
    return {"mcpServers": {"marknote": server}}, [f"mcp__marknote__{t}" for t in tools]


def _tool_event(name: str, args: dict) -> dict:
    short = name.removeprefix("mcp__marknote__")
    icon, label = _TOOL_LABELS.get(short, ("info", short))
    if short == "search_pages" and args.get("query"):
        label = f"{label}: {str(args['query'])[:40]}"
    elif short == "find_page" and args.get("path"):
        label = f"{label}: {str(args['path'])[:40]}"
    elif short in ("read_page", "edit_page", "trash_page", "propose_page_update") and args.get("page_id") is not None:
        page = store.get(int(args["page_id"])) if str(args["page_id"]).isdigit() else None
        label = f"{label}: {page.display_title if page else '#' + str(args['page_id'])}"
    elif short in ("create_page", "propose_new_page") and args.get("title"):
        label = f"{label}: {str(args['title'])[:40]}"
    elif short in ("read_deck", "edit_deck") and str(args.get("deck_id", "")).isdigit():
        deck = deck_service.get(args["deck_id"])
        label = f"{label}: {deck.display_title if deck else '#' + str(args['deck_id'])}"
    return {"type": "tool", "icon": icon, "label": label}


@bp.get("/status")
def status():
    return jsonify(claude_runner.status(_command(), refresh=request.args.get("refresh") == "1"))


@bp.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()
    if not message:
        abort(400, description="메시지를 입력하세요.")
    if len(message) > MAX_MESSAGE_CHARS:
        abort(400, description=f"메시지는 {MAX_MESSAGE_CHARS}자 이하로 입력하세요.")
    if claude_runner.is_busy() or chat_runs.active():
        abort(409, description="이미 다른 요청을 처리하고 있습니다. 잠시 후 다시 시도하세요.")

    # 대화는 서버 파일에 기록 → 다른 페이지로 옮겨도 이어진다
    conv_id = data.get("conversation_id")
    if not conv_id or not chat_store.exists(conv_id):
        conv_id = chat_store.current_id()
    conv = chat_store.load(conv_id)
    session_id = conv["session_id"]
    prefs = get_settings()

    # 질문할 때마다: 지금 위치 + 선택한 글자 + 보고 있는 페이지/프레젠테이션의 최신 내용
    # (편집기가 보내기 직전에 저장하므로 입력 중이던 내용도 들어 있음. 바뀐 게 없으면 한 줄로 대체)
    page = tree.get_or_404(data["page_id"]) if data.get("page_id") is not None else None
    deck = deck_service.get_or_404(data["deck_id"]) if data.get("deck_id") is not None else None
    location = data.get("location") or {}
    include_children = bool(data.get("include_children"))
    # 이 대화가 다루는 노트 + 이번 메시지에서 이름으로 부른 노트 ("OO프로젝트의 OO노트") — 노트 읽기 권한이 있을 때만
    focus = []
    if prefs["ai_notes_read"]:
        mentioned = [p.id for p in note_focus.mentioned_pages(message, page.id if page else None)]
        focus_ids = note_focus.merge(conv["focus"], mentioned)
        if focus_ids != conv["focus"]:
            chat_store.append(conv_id, {"t": "focus", "page_ids": focus_ids})
        focus = note_focus.alive(focus_ids)
    context, sent_hashes = ai_context.build(location, page, include_children, deck, session_id, focus)
    # /resume 으로 오래된 대화를 이어 갈 때 Claude 쪽 기록이 지워졌을 수 있다 → 그때 쓸 "새로 시작" 입력
    fallback = None
    if session_id:
        fresh_context, fresh_hashes = ai_context.build(location, page, include_children, deck, None, focus)
        fallback = (chat_store.recap(conv_id) + fresh_context, fresh_hashes)

    chat_store.append(
        conv_id,
        {
            "t": "user",
            "id": chat_store.message_id(),
            "text": message,
            "selection": str(location.get("selection") or "")[:2000] or None,
            "location": {k: location.get(k) for k in ("view", "page_id", "deck_id", "slide_index") if location.get(k) is not None},
        },
    )

    config = current_app.config
    run = chat_runs.Run(uuid.uuid4().hex, conv_id)
    mcp_config, allowed_tools = _tools_config(run.id, prefs)

    def start(stdin_context: str, resume: str | None):
        return claude_runner.stream_chat(
            message,
            stdin_context,
            resume,
            workdir=os.path.join(current_app.instance_path, "ai-workdir"),
            model=prefs["ai_model"] or config.get("CLAUDE_MODEL"),
            effort=prefs["ai_effort"] or None,
            timeout=config.get("CLAUDE_TIMEOUT", 300),
            configured=_command(),
            system_prompt=claude_runner.system_prompt_for(
                prefs["ai_notes_read"], prefs["ai_notes_edit"], prefs["ai_initial_prompt"], prefs["ai_decks_edit"],
                prefs["ai_notes_delete"],
            ),
            mcp_config=mcp_config,
            allowed_tools=allowed_tools,
        )

    retry = (lambda: start(fallback[0], None), fallback[1]) if fallback else None
    run.add({"type": "run", "run_id": run.id, "conversation_id": conv_id})
    chat_runs.start(run, _process, current_app._get_current_object(), start(context, session_id), prefs, sent_hashes, retry)
    return _stream(run)


MISSING_SESSION = "No conversation found"  # claude --resume 할 대화 기록이 없을 때 (보관 기간이 지나 지워짐 등)


def _sse(event: dict | None) -> str:
    if event is None:
        return ": keep-alive\n\n"  # 오래 걸리는 도구 실행 중 연결 유지
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _stream(run: chat_runs.Run, after: int = 0) -> Response:
    """Run 의 이벤트를 SSE 로. 브라우저가 끊어도 Run 은 서버에서 계속된다."""

    def generate():
        for event in run.follow(after):
            yield _sse(event)

    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _with_fallback(run: chat_runs.Run, events, retry, hashes: list):
    """--resume 할 Claude 대화 기록이 없으면, 지난 대화를 글로 붙여 새 Claude 대화로 다시 실행."""
    for event in events:
        if retry and event["type"] == "error" and MISSING_SESSION in event.get("message", ""):
            events.close()  # 첫 실행의 잠금을 풀어야 다시 실행할 수 있음
            chat_store.append(run.conversation_id, {"t": "session", "session_id": None})
            hashes[0] = retry[1]
            yield {"type": "tool", "name": "resume_fallback", "input": {}}
            yield from retry[0]()
            return
        yield event


def _process(run: chat_runs.Run, app, events, prefs: dict, sent_hashes: dict, retry=None) -> None:
    """(백그라운드 스레드) Claude Code 이벤트를 화면용으로 바꿔 Run 에 쌓고, 끝나면 대화 파일에 기록."""
    with app.app_context():
        text = ""
        model, last_context = None, 0
        tools: list[dict] = []
        touched: list[int] = []  # AI 가 읽거나 고친 노트 → 대화의 "다루는 노트"
        record = {"t": "assistant", "id": chat_store.message_id(), "run_id": run.id, "ok": False}
        hashes = [sent_hashes]
        for event in _with_fallback(run, events, retry, hashes):
            if event["type"] == "usage":  # 화면에는 결과에 합쳐서 보냄
                last_context = ai_usage.context_tokens(event["usage"]) or last_context
                continue
            if event["type"] == "limit":
                ai_usage.set_limit(event["info"])
                continue
            if event["type"] == "session":
                model = event.get("model") or model
                event = {**event, "model_label": ai_usage.model_label(model)}
                if event.get("session_id"):
                    chat_store.append(run.conversation_id, {"t": "session", "session_id": event["session_id"]})
            elif event["type"] == "delta":
                text += event["text"]
            elif event["type"] == "assistant" and not text:
                text = event["text"]
            elif event["type"] == "tool":
                short = event["name"].removeprefix("mcp__marknote__")
                if short in ("read_page", "edit_page") and str(event["input"].get("page_id", "")).isdigit():
                    touched.append(int(event["input"]["page_id"]))
                event = _tool_event(event["name"], event["input"])
                tools.append({"icon": event["icon"], "label": event["label"]})
            elif event["type"] == "result":
                ai_context.remember(event.get("session_id"), hashes[0])
                final = event["text"] or text
                store.invalidate()  # MCP 프로세스가 방금 고친 노트 파일을 다시 읽기 위해
                deck_edits = deck_tools.by_run(run.id)
                note_edits_ = note_edits.by_run(run.id)
                touched += [e.page_id for e in note_edits_ if e.type == "create"]
                focus = _update_focus(run.conversation_id, touched[::-1], prefs)
                usage = ai_usage.summarize(model, last_context, event.pop("model_usage", None))
                ai_usage.record(usage)
                event = {
                    **event,
                    "usage": {**usage, "today": ai_usage.overview(prefs)["today"], "limits": ai_usage.limits()},
                    "text": final,
                    "html": render_block(final),
                    "deck_edits": [deck_tools.edit_dict(e) for e in deck_edits],
                    "note_edits": [note_edits.edit_dict(e) for e in note_edits_],
                    "focus": focus,
                }
                record.update(
                    ok=event["ok"],
                    text=final,
                    html=event["html"],
                    deck_edit_ids=[e.id for e in deck_edits],
                    note_edit_ids=[e.id for e in note_edits_],
                )
            elif event["type"] == "error":
                record["error"] = event["message"]
            run.add(event)
        if not record.get("ok") and not record.get("error") and not record.get("text"):
            record["error"] = "응답이 완료되지 않았습니다."
        chat_store.append(run.conversation_id, {**record, "tools": tools})


# ---------- 대화 기록 (페이지를 옮겨도 유지) ----------

def _focus_view(page_ids: list[int]) -> list[dict]:
    return [{"id": p.id, "title": p.display_title, "path": note_tools._path(p), "file": tree.file_path(p)}
            for p in note_focus.alive(page_ids)]


def _update_focus(conv_id: str, touched: list[int], prefs: dict) -> list[dict]:
    """AI 가 이번에 읽고 고친 노트를 대화의 "다루는 노트" 앞에 넣는다. 화면 표시용 목록을 돌려준다."""
    current = chat_store.load(conv_id)["focus"]
    if prefs["ai_notes_read"] and touched:
        merged = note_focus.merge(current, touched)
        if merged != current:
            chat_store.append(conv_id, {"t": "focus", "page_ids": merged})
            current = merged
    return _focus_view(current)


def _message_view(rec: dict) -> dict:
    if rec["t"] == "user":
        return {"role": "user", "id": rec.get("id"), "text": rec.get("text", ""), "selection": rec.get("selection")}
    edits = [deck_tools.get(i) for i in rec.get("deck_edit_ids") or []]
    page_edits = [note_edits.get(i) for i in rec.get("note_edit_ids") or []]
    return {
        "role": "assistant",
        "id": rec.get("id"),
        "ok": bool(rec.get("ok")),
        "text": rec.get("text") or "",
        "html": rec.get("html") or "",
        "error": rec.get("error"),
        "tools": rec.get("tools") or [],
        "deck_edits": [deck_tools.edit_dict(e) for e in edits if e],
        "note_edits": [note_edits.edit_dict(e) for e in page_edits if e],
    }


def _conversation_view(conv_id: str) -> dict:
    conv = chat_store.load(conv_id)
    run = chat_runs.active(conv["id"])
    return dict(
        id=conv["id"],
        title=chat_store.summary(conv["id"])["title"],
        session_id=conv["session_id"],
        messages=[_message_view(m) for m in conv["messages"]],
        focus=_focus_view(conv["focus"]),
        # 아직 답변 중이면 화면이 이 실행을 처음부터 이어 받는다 (답은 끝나야 파일에 기록됨)
        running={"run_id": run.id} if run else None,
    )


@bp.get("/conversation")
def conversation():
    return jsonify(_conversation_view(chat_store.current_id()))


# ---------- /resume: 저장된 대화 목록에서 골라 이어 가기 ----------

def _location_label(location: dict | None) -> str | None:
    if not location:
        return None
    if location.get("page_id") and (page := store.get(int(location["page_id"]))):
        return page.display_title
    if location.get("deck_id") and (deck := deck_service.get(location["deck_id"])):
        return deck.display_title
    return None


@bp.get("/conversations")
def conversations():
    current = chat_store.current_id()
    rows = chat_store.list_conversations(request.args.get("q", ""))
    return jsonify(
        current=current,
        conversations=[{**r, "current": r["id"] == current, "where": _location_label(r.pop("location"))} for r in rows],
    )


@bp.post("/conversations/<conv_id>/resume")
def resume_conversation(conv_id):
    if chat_runs.active():
        abort(409, description="답변이 끝난 뒤에 다른 대화로 바꾸세요.")
    try:
        chat_store.set_current(conv_id)
    except ValueError as err:
        abort(404, description=str(err))
    return jsonify(_conversation_view(conv_id))


@bp.post("/conversations/<conv_id>/rename")
def rename_conversation(conv_id):
    try:
        title = chat_store.rename(conv_id, str((request.get_json(silent=True) or {}).get("title") or ""))
    except ValueError as err:
        abort(400, description=str(err))
    return jsonify(id=conv_id, title=title)


@bp.post("/conversations/<conv_id>/focus/remove")
def remove_focus(conv_id):
    """채팅의 "다루는 노트" 표시에서 [x] — 다음 질문부터 그 노트 내용을 보내지 않음."""
    if not chat_store.exists(conv_id):
        abort(404, description="대화를 찾을 수 없습니다.")
    page_id = (request.get_json(silent=True) or {}).get("page_id")
    current = chat_store.load(conv_id)["focus"]
    remaining = [i for i in current if i != page_id]
    if remaining != current:
        chat_store.append(conv_id, {"t": "focus", "page_ids": remaining})
    return jsonify(focus=_focus_view(remaining))


@bp.delete("/conversations/<conv_id>")
def delete_conversation(conv_id):
    if chat_runs.active(conv_id):
        abort(409, description="답변 중인 대화는 지울 수 없습니다.")
    try:
        was_current = chat_store.current_id() == conv_id
        chat_store.delete(conv_id)
    except ValueError as err:
        abort(404, description=str(err))
    # 지금 대화를 지웠으면 새 대화로
    return jsonify(ok=True, current=chat_store.new_conversation() if was_current else chat_store.current_id())


@bp.post("/conversation/new")
def new_conversation():
    if chat_runs.active():
        abort(409, description="답변이 끝난 뒤에 새 대화를 시작하세요.")
    return jsonify(id=chat_store.new_conversation(), session_id=None, messages=[], running=None)


@bp.get("/runs/<run_id>/events")
def run_events(run_id):
    run = chat_runs.get(run_id)
    if run is None:
        abort(404, description="이미 끝났거나 없는 답변입니다.")
    return _stream(run, request.args.get("after", 0, type=int))


@bp.get("/usage")
def usage():
    # 구독 세션·주간 한도는 claude -p /usage 로 확인 (1분 캐시, ?refresh=1 이면 새로)
    ai_usage.plan_usage(_command(), os.path.join(current_app.instance_path, "ai-workdir"), request.args.get("refresh") == "1")
    return jsonify(ai_usage.overview(get_settings()))


@bp.post("/prefs")
def prefs():
    data = request.get_json(silent=True) or {}
    save_ai_prefs(data.get("model"), data.get("effort"))
    return jsonify(ai_usage.overview(get_settings()))


@bp.post("/cancel")
def cancel():
    return jsonify(cancelled=claude_runner.cancel())


# ---------- 노트 AI 수정: 되돌리기 / 확정 ----------

@bp.post("/note-edits/<int:edit_id>/undo")
def undo_note_edit(edit_id):
    edit = note_edits.get(edit_id) or abort(404)
    force = bool((request.get_json(silent=True) or {}).get("force"))
    try:
        note_edits.undo_edit(edit, force=force)
    except note_tools.ProposalConflict as err:
        return jsonify(error=str(err), conflict=True), 409
    except note_tools.ToolError as err:
        abort(400, description=str(err))
    return jsonify(edit=note_edits.edit_dict(edit))


@bp.post("/note-edits/<int:edit_id>/confirm")
def confirm_note_edit(edit_id):
    edit = note_edits.get(edit_id) or abort(404)
    note_edits.confirm_edit(edit)
    return jsonify(edit=note_edits.edit_dict(edit))


@bp.get("/pages/<int:page_id>/note-edits")
def page_note_edits(page_id):
    """페이지에서 강조할 (아직 확정·되돌리기 안 한) AI 수정."""
    return jsonify(edits=[note_edits.edit_dict(e) for e in note_edits.pending(page_id)])


@bp.post("/pages/<int:page_id>/note-edits/confirm")
def confirm_page_note_edits(page_id):
    """Ctrl+S 나 직접 고쳐 저장했을 때: 이 페이지의 AI 수정을 모두 확정."""
    return jsonify(edits=[note_edits.edit_dict(e) for e in note_edits.confirm_page(page_id)])


@bp.post("/pages/<int:page_id>/note-edits/seen")
def hide_page_note_edits(page_id):
    """직접 고쳐 저장했을 때: 강조 표시만 지움 (확정은 아님 — 채팅에서 되돌리기 가능)."""
    return jsonify(edits=[note_edits.edit_dict(e) for e in note_edits.hide_page(page_id)])


# ---------- 프레젠테이션 AI 수정 되돌리기 ----------

@bp.post("/deck-edits/<int:edit_id>/undo")
def undo_deck_edit(edit_id):
    edit = deck_tools.get(edit_id) or abort(404)
    force = bool((request.get_json(silent=True) or {}).get("force"))
    try:
        deck_tools.undo_edit(edit, force=force)
    except note_tools.ProposalConflict as err:
        return jsonify(error=str(err), conflict=True), 409
    except note_tools.ToolError as err:
        abort(400, description=str(err))
    return jsonify(edit=deck_tools.edit_dict(edit))


# ---------- /login, /logout ----------

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _is_same_computer() -> bool:
    """브라우저가 서버와 같은 컴퓨터인지. 리버스 프록시 뒤라면 Host 헤더가 외부 주소라 원격으로 판별된다."""
    host = (urlsplit("//" + request.host).hostname or "").lower()
    return request.remote_addr in ("127.0.0.1", "::1") and host in _LOCAL_HOSTS


@bp.post("/login")
def login():
    if not _is_same_computer():
        return jsonify(
            state="remote",
            url=None,
            message="원격 접속에서는 패널에서 로그인할 수 없습니다. 서버 컴퓨터의 터미널(SSH)에서 "
            "`claude` 를 실행해 로그인한 뒤 /status 로 확인하세요.",
        )
    workdir = os.path.join(current_app.instance_path, "ai-workdir")
    os.makedirs(workdir, exist_ok=True)
    return jsonify(claude_auth.start_login(_command(), workdir))


@bp.get("/login")
def login_state():
    return jsonify(claude_auth.login_state())


@bp.post("/login/cancel")
def login_cancel():
    return jsonify(claude_auth.cancel_login())


@bp.post("/logout")
def logout():
    # 다른 계정의 Claude 대화는 이어 갈 수 없으므로, 기록은 두고 다음 질문부터 새 Claude 대화로
    chat_store.append(chat_store.current_id(), {"t": "session", "session_id": None})
    return jsonify(claude_auth.logout(_command()))
