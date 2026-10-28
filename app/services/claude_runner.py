"""서버에 설치·로그인된 Claude Code CLI 를 `claude -p` 로 실행한다.

앱은 Claude 로그인을 처리하지 않는다. 인증은 둘 중 하나:
  1) 서버를 실행하는 OS 사용자로 터미널에서 `claude` 를 실행해 직접 로그인 (권장, 자동 갱신)
  2) `claude setup-token` 으로 만든 토큰을 .env 의 CLAUDE_CODE_OAUTH_TOKEN 에 저장
둘 다 구독 사용량으로 동작하며, API 요금으로 넘어가지 않도록 API 키 변수는 자식 프로세스에서 지운다.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator

SYSTEM_PROMPT = (
    "You are the AI assistant inside MarkNote, the user's private Markdown note and presentation app. "
    "With every message, stdin tells you what the user is looking at right now: "
    "<location> says which note page or presentation slide is open; "
    "<selection> is the text the user has highlighted — when they say 'this part', 'here' or 'the selected text', "
    "they mean <selection>; <page> / <presentation> hold the LATEST content of what they are viewing, "
    "including edits they were typing a moment ago, so trust it over older copies in the conversation. "
    "If a tag says '(지난 메시지 이후 바뀐 내용 없음)', the earlier copy is still current. "
    "Parts the user marked private appear only as placeholders like ⟦🔒a1b2c3⟧: you cannot see their content, "
    "so never guess it or ask for it, and when you edit a note or slide, keep every placeholder exactly as it is "
    "(edits that drop or change one are rejected). "
    "Answer in the user's language (Korean by default). "
    "Write answers in Markdown so they can be inserted into a page as-is."
)
NO_TOOLS_PROMPT = (
    "You have no tools: never claim to have read other pages or edited pages yourself. "
    "When the user asks for a rewrite, return only the revised Markdown."
)
FOCUS_PROMPT = (
    "Notes are grouped by project (a top-level page); users refer to a note like \"<project>의 <note>\". "
    "Use find_page with \"project / note\" to locate it. <working_note> blocks are notes this conversation is "
    "working on, always with their latest content: when the user says \"there\", \"that note\" (거기, 그 노트) "
    "without naming one, they mean the first <working_note> unless the context says otherwise. "
)
READ_TOOLS_PROMPT = FOCUS_PROMPT + (
    "You can read the user's other notes with the marknote tools (search_pages, find_page, list_pages, read_page). "
    "You cannot change notes: when asked to edit, return the revised Markdown in your answer instead."
)
EDIT_TOOLS_PROMPT = FOCUS_PROMPT + (
    "You can read the user's notes with the marknote tools (search_pages, find_page, list_pages, read_page) and "
    "CHANGE them directly with edit_page / create_page. When the user asks to fix, rewrite, add to, organize "
    "or create a note, do it with these tools instead of only suggesting text. Read a page before editing it. "
    "Edits apply immediately and are highlighted on the page; afterwards say briefly what you changed "
    "(the user can undo it or confirm it)."
)


DECK_EDIT_PROMPT = (
    "You CAN change presentations directly: use read_deck to get slide/element ids, then edit_deck. "
    "When the user asks to change, fix, restyle, add or remove anything in a presentation, do it with edit_deck "
    "instead of only suggesting text. edit_deck applies immediately and the user sees it in the editor; "
    "afterwards say briefly what you changed (they can undo it from the chat)."
)
DELETE_PROMPT = (
    "You CAN delete notes and folders with trash_page: it only moves them to the trash (nothing is permanently "
    "deleted, the user can undo or restore). When the user asks to delete or remove a note/folder, find it, "
    "then call trash_page instead of telling them to do it themselves. Say what you moved to the trash."
)
NO_DELETE_PROMPT = "You cannot delete notes (turned off in settings): tell the user to move it to the trash themselves."
NO_DECK_EDIT_PROMPT = (
    "You cannot change presentation slides (disabled in settings): suggest the new text instead."
)


def system_prompt_for(notes_read: bool, notes_edit: bool, custom: str = "", decks_edit: bool = False,
                      notes_delete: bool = False) -> str:
    """기본 지침 + 권한별 도구 안내 + 설정 화면의 사용자 초기 프롬프트."""
    tools = EDIT_TOOLS_PROMPT if notes_edit else READ_TOOLS_PROMPT if notes_read else NO_TOOLS_PROMPT
    if notes_read:
        tools += " " + (DELETE_PROMPT if notes_delete else NO_DELETE_PROMPT)
    prompt = f"{SYSTEM_PROMPT} {tools} {DECK_EDIT_PROMPT if decks_edit else NO_DECK_EDIT_PROMPT}"
    if custom.strip():
        prompt += "\n\nUser's custom instructions (follow them):\n" + custom.strip()
    return prompt

# 이 변수들이 있으면 Claude Code 가 구독 대신 API 키(종량 과금)를 쓰므로 자식 프로세스에서 제거
_BILLING_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_API_AUTH_METHODS = {"api_key", "api_key_helper", "third_party"}
_STATUS_TTL = 60

_lock = threading.Lock()
_current: subprocess.Popen | None = None
_status_cache: tuple[float, dict] | None = None
_stop_reason: dict[str, str | None] = {"value": None}  # 취소·시간초과 시 사용자에게 보일 이유


def find_command(configured: list[str] | None = None) -> list[str] | None:
    """실행할 claude 명령. 설정 → CLAUDE_BIN → PATH → 기본 설치 위치 순."""
    if configured:
        return list(configured)
    candidates = [os.environ.get("CLAUDE_BIN"), shutil.which("claude")]
    home_bin = os.path.join(os.path.expanduser("~"), ".local", "bin")
    candidates += [os.path.join(home_bin, "claude.exe"), os.path.join(home_bin, "claude")]
    for path in candidates:
        if path and os.path.isfile(path):
            return [path]
    return None


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _BILLING_ENV}


def _popen_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _run_quick(command: list[str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*command, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_child_env(),
        creationflags=_popen_flags(),
    )


def usage_text(configured: list[str] | None, workdir: str) -> str:
    """`claude -p /usage` 의 출력 (구독 세션·주간 한도). 모델을 부르지 않는 로컬 명령이라 토큰이 들지 않는다.

    실패하면 RuntimeError (설치 안 됨·로그인 안 됨·형식이 다름 등).
    """
    command = find_command(configured)
    if command is None:
        raise RuntimeError("Claude Code 가 설치되어 있지 않습니다.")
    os.makedirs(workdir, exist_ok=True)
    try:
        proc = subprocess.run(
            [*command, "-p", "/usage", "--output-format", "json", "--tools", "", "--strict-mcp-config"],
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=_child_env(),
            creationflags=_popen_flags(),
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise RuntimeError(f"claude /usage 실행 실패: {err}") from None
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise RuntimeError((proc.stderr or proc.stdout or "응답 없음").strip()[:300]) from None
    if data.get("is_error") or data.get("local_command") not in (None, "usage"):
        raise RuntimeError(str(data.get("result") or "사용량을 읽지 못했습니다.")[:300])
    return str(data.get("result") or "")


def status(configured: list[str] | None = None, refresh: bool = False) -> dict:
    """설치·로그인 상태. state: ready / not_installed / logged_out / api_billing / error"""
    global _status_cache
    if not refresh and _status_cache and time.monotonic() - _status_cache[0] < _STATUS_TTL:
        return _status_cache[1]

    result = _check_status(configured)
    _status_cache = (time.monotonic(), result)
    return result


def invalidate_status() -> None:
    global _status_cache
    _status_cache = None


def _check_status(configured) -> dict:
    command = find_command(configured)
    if command is None:
        return {"state": "not_installed", "message": "서버에 Claude Code(claude)가 설치되어 있지 않습니다."}
    try:
        version = _run_quick(command, "--version").stdout.strip().splitlines()[0:1]
        auth = _run_quick(command, "auth", "status")
    except (OSError, subprocess.TimeoutExpired) as err:
        return {"state": "error", "message": f"claude 실행 실패: {err}"}

    try:
        info = json.loads(auth.stdout)
    except json.JSONDecodeError:
        info = {}
    method = info.get("authMethod")
    base = {"version": version[0] if version else None, "auth_method": method}

    if auth.returncode != 0 or method in (None, "none"):
        return {**base, "state": "logged_out", "message": "Claude Code에 로그인되어 있지 않습니다."}
    if method in _API_AUTH_METHODS:
        return {
            **base,
            "state": "api_billing",
            "message": "Claude Code가 API 키로 인증되어 있어 API 요금이 나갑니다. 구독 계정으로 다시 로그인하세요.",
        }
    return {**base, "state": "ready", "message": "연결됨"}


def is_busy() -> bool:
    return _lock.locked()


def cancel() -> bool:
    proc = _current
    if proc is None or proc.poll() is not None:
        return False
    _stop_reason["value"] = "중단했습니다."
    proc.terminate()
    return True


def _parse_line(line: str) -> list[dict]:
    """stream-json 한 줄을 화면용 이벤트(0개 이상)로 바꾼다."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return []
    kind = data.get("type")
    if kind == "system" and data.get("subtype") == "init":
        return [{"type": "session", "session_id": data.get("session_id"), "model": data.get("model")}]
    if kind == "rate_limit_event":
        return [{"type": "limit", "info": data.get("rate_limit_info") or {}}]
    if kind == "stream_event":
        delta = (data.get("event") or {}).get("delta") or {}
        if delta.get("type") == "text_delta":
            return [{"type": "delta", "text": delta.get("text", "")}]
        return []
    if kind == "assistant":
        parts = (data.get("message") or {}).get("content") or []
        events = [
            {"type": "tool", "name": p.get("name", ""), "input": p.get("input") or {}}
            for p in parts
            if p.get("type") == "tool_use"
        ]
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        if text:
            events.append({"type": "assistant", "text": text})
        usage = (data.get("message") or {}).get("usage")
        if usage:
            events.append({"type": "usage", "usage": usage})
        return events
    if kind == "system" and data.get("subtype") == "api_retry":
        return [{"type": "retry", "attempt": data.get("attempt")}]
    if kind == "result":
        return [
            {
                "type": "result",
                "ok": not data.get("is_error") and data.get("subtype") == "success",
                "text": data.get("result") or "",
                "session_id": data.get("session_id"),
                "model_usage": data.get("modelUsage") or {},
            }
        ]
    return []


def build_args(
    command: list[str],
    message: str,
    system_prompt: str,
    session_id: str | None = None,
    model: str | None = None,
    mcp_config_path: str | None = None,
    allowed_tools: list[str] | None = None,
    effort: str | None = None,
) -> list[str]:
    args = [
        *command, "-p", message,
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--tools", "",  # 파일·명령 등 Claude Code 기본 도구는 모두 끔
        "--strict-mcp-config",  # 사용자 PC 의 다른 MCP 설정은 무시
        "--permission-mode", "dontAsk",  # 허용 목록에 없는 도구 호출은 거부
        "--append-system-prompt", system_prompt,
    ]
    if mcp_config_path and allowed_tools:
        # MarkNote 노트 도구만 연결하고, 그 도구들만 허용
        args += ["--mcp-config", mcp_config_path, "--allowedTools", ",".join(allowed_tools), "--max-turns", "20"]
    else:
        args += ["--disallowedTools", "mcp__*", "--max-turns", "2"]
    if session_id:
        args += ["--resume", session_id]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    return args


def stream_chat(
    message: str,
    context: str,
    session_id: str | None,
    workdir: str,
    model: str | None = None,
    timeout: int = 300,
    configured: list[str] | None = None,
    system_prompt: str = f"{SYSTEM_PROMPT} {NO_TOOLS_PROMPT}",
    mcp_config: dict | None = None,
    allowed_tools: list[str] | None = None,
    effort: str | None = None,
) -> Iterator[dict]:
    """claude -p 를 실행하며 이벤트를 차례로 내보낸다. 동시에 하나만 실행한다.

    잠금은 제너레이터가 시작될 때 잡으므로, 바쁘거나 설치가 안 된 경우도 error 이벤트로 알린다.
    """
    global _current
    command = find_command(configured)
    if command is None:
        yield {"type": "error", "message": "서버에 Claude Code(claude)가 설치되어 있지 않습니다."}
        return
    if not _lock.acquire(blocking=False):
        yield {"type": "error", "message": "이미 다른 요청을 처리하고 있습니다. 잠시 후 다시 시도하세요."}
        return
    _stop_reason["value"] = None

    proc = None
    mcp_config_path = None
    try:
        os.makedirs(workdir, exist_ok=True)
        if mcp_config:
            mcp_config_path = os.path.join(workdir, "mcp-config.json")
            with open(mcp_config_path, "w", encoding="utf-8") as f:
                json.dump(mcp_config, f)
        args = build_args(command, message, system_prompt, session_id, model, mcp_config_path, allowed_tools, effort)
        proc = subprocess.Popen(
            args,
            cwd=workdir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
            creationflags=_popen_flags(),
        )
        _current = proc
        stderr_lines: list[str] = []
        threading.Thread(target=lambda: stderr_lines.extend(proc.stderr), daemon=True).start()
        def on_timeout():
            _stop_reason["value"] = f"{timeout}초 안에 끝나지 않아 중단했습니다."
            proc.kill()

        watchdog = threading.Timer(timeout, on_timeout)
        watchdog.start()

        try:
            proc.stdin.write(context)
            proc.stdin.close()
        except OSError:
            pass  # 프로세스가 바로 끝난 경우 — 아래에서 오류로 보고됨

        got_result = False
        for line in proc.stdout:
            for event in _parse_line(line):
                if event["type"] == "result":
                    got_result = True
                yield event
        proc.wait()
        watchdog.cancel()

        if not got_result:
            reason = _stop_reason["value"]
            if reason is None:
                detail = "".join(stderr_lines[-5:]).strip()
                reason = f"Claude Code 응답이 완료되지 않았습니다. {detail}".strip()
            yield {"type": "error", "message": reason}
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()  # 브라우저 연결이 끊긴 경우 등
        if mcp_config_path and os.path.exists(mcp_config_path):
            os.remove(mcp_config_path)
        _current = None
        _lock.release()
