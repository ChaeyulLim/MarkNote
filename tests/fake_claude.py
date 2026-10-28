"""테스트용 가짜 claude CLI. 실제 Claude Code 의 stream-json 출력 형식을 흉내 낸다.

환경변수 FAKE_CLAUDE_MODE: ready(기본) / logged_out / api_key / fail / slow / evil_url
환경변수 FAKE_CLAUDE_STATE: 지정하면 로그인 상태를 이 파일의 존재 여부로 관리
  (auth login 은 "<파일>.approve" 가 생길 때까지 기다림 = 브라우저 로그인 완료 흉내)
"""

import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")  # 실제 claude 처럼 UTF-8 로 입출력
sys.stdin.reconfigure(encoding="utf-8")

mode = os.environ.get("FAKE_CLAUDE_MODE", "ready")
state_file = os.environ.get("FAKE_CLAUDE_STATE")
args = sys.argv[1:]


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


if args[:1] == ["--version"]:
    print("9.9.9 (Claude Code)")
    sys.exit(0)

if args[:2] == ["auth", "status"]:
    if state_file:
        method = "claude.ai" if os.path.exists(state_file) else "none"
    else:
        method = {"ready": "claude.ai", "logged_out": "none", "api_key": "api_key"}.get(mode, "claude.ai")
    emit({"loggedIn": method != "none", "authMethod": method})
    sys.exit(0 if method != "none" else 1)

if args[:2] == ["auth", "login"]:
    url = "https://evil.example.com/steal" if mode == "evil_url" else "https://claude.ai/oauth/authorize?client_id=fake&state=xyz"
    print("Opening browser to sign in…", flush=True)
    print(f"\x1b[2mIf the browser didn't open, visit:\x1b[0m {url}", flush=True)
    print("Paste code here if prompted > ", end="", flush=True)
    for _ in range(200):  # 최대 20초 동안 브라우저 로그인 완료를 기다림
        if state_file and os.path.exists(state_file + ".approve"):
            open(state_file, "w").close()
            print("\nLogin successful", flush=True)
            sys.exit(0)
        time.sleep(0.1)
    sys.exit(1)

if args[:2] == ["auth", "logout"]:
    if state_file and os.path.exists(state_file):
        os.remove(state_file)
    print("Successfully logged out", flush=True)
    sys.exit(0)

if "-p" in args:
    message = args[args.index("-p") + 1]
    context = sys.stdin.read()
    resume = args[args.index("--resume") + 1] if "--resume" in args else None
    tools = args[args.index("--tools") + 1] if "--tools" in args else None

    if mode == "fail":
        print("boom", file=sys.stderr)
        sys.exit(1)
    if resume and resume.startswith("gone-"):  # Claude 쪽 대화 기록이 지워진 경우
        print(f"No conversation found with session ID: {resume}", file=sys.stderr)
        sys.exit(1)

    model = args[args.index("--model") + 1] if "--model" in args else None
    model_id = {"sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}.get(model, "claude-opus-5-5")
    emit({"type": "system", "subtype": "init", "session_id": resume or "sess-1", "model": model_id})
    emit({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour",
                                                          "utilization": 0.42, "resetsAt": 1793120000}})
    if mode == "slow":
        time.sleep(30)
    if mode == "slow_reply":  # 답이 조금 늦게 끝남 (답변 중 페이지 이동 확인용)
        time.sleep(1.5)
    if "천천히" in message:  # 화면 확인용: 메시지로도 느린 답 흉내
        time.sleep(4)

    def flag(name):
        return args[args.index(name) + 1] if name in args else None

    mcp_server = None
    if flag("--mcp-config"):
        with open(flag("--mcp-config"), encoding="utf-8") as f:
            server = json.load(f)["mcpServers"]["marknote"]
        mcp_server = " ".join(server["args"])
        # 노트 도구를 쓰는 것처럼 tool_use 메시지를 보냄
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__marknote__search_pages", "input": {"query": "회의"}},
        ]}})
        page_match = re.search(r'<page id="(\d+)"', context)
        if "고쳐" in message and page_match and server["env"]["MARKNOTE_ALLOW_EDIT"] == "1":
            # MCP 의 edit_page 처럼 실제 note_edits 로 지금 페이지 끝에 할 일 한 줄을 바로 추가
            os.environ.update({k: server["env"][k] for k in ("NOTES_DIR", "UPLOAD_FOLDER", "DECKS_DIR", "AI_EDITS_DIR", "SETTINGS_FILE")})
            sys.path.insert(0, server["env"]["PYTHONPATH"])
            from app import create_app
            from app.services import note_edits, note_tools

            with create_app().app_context():
                page_id = int(page_match.group(1))
                base = note_tools.read_page(page_id)["markdown"]
                note_edits.edit_page(server["env"]["MARKNOTE_RUN_ID"], page_id, base + "\n\n- [ ] AI가 추가한 줄", "할 일 한 줄 추가")
            emit({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "mcp__marknote__edit_page", "input": {"page_id": page_id}},
            ]}})
        deck_match = re.search(r'<presentation id="(\d+)"', context)
        if "슬라이드" in message and deck_match and server["env"].get("MARKNOTE_ALLOW_DECK_EDIT") == "1":
            # MCP 의 edit_deck 처럼 실제 deck_tools 로 첫 슬라이드 제목을 바꾼다
            os.environ.update({k: server["env"][k] for k in ("NOTES_DIR", "UPLOAD_FOLDER", "DECKS_DIR", "AI_EDITS_DIR", "SETTINGS_FILE")})
            sys.path.insert(0, server["env"]["PYTHONPATH"])
            from app import create_app
            from app.services import deck_tools

            with create_app().app_context():
                deck_tools.edit_deck(
                    server["env"]["MARKNOTE_RUN_ID"],
                    int(deck_match.group(1)),
                    [{"op": "update_element", "slide": 1, "element": "제목 1", "set": {"text": "AI가 고친 제목"}}],
                    "첫 슬라이드 제목 변경",
                )
            emit({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "mcp__marknote__edit_deck", "input": {"deck_id": int(deck_match.group(1))}},
            ]}})
    reply = (
        f"**받음**: {message}\n\n"
        f"- context={len(context)}\n- resume={resume}\n- tools={tools!r}\n"
        f"- api_key={'ANTHROPIC_API_KEY' in os.environ}\n"
        f"- mcp={mcp_server}\n- allowed={flag('--allowedTools')}\n"
        f"- custom={'MY-RULE' in (flag('--append-system-prompt') or '')}\n"
        f"- model={model} effort={flag('--effort')}\n"
        f"- stdin: {context.replace(chr(10), ' ⏎ ')[:1500]}"
    )
    usage = {"input_tokens": 12, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 500, "output_tokens": 88}
    emit({"type": "assistant", "message": {"content": [], "usage": usage}})
    for part in (reply[:10], reply[10:]):
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": part}}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": reply, "session_id": resume or "sess-1",
          "modelUsage": {model_id: {"inputTokens": 12, "outputTokens": 88, "cacheReadInputTokens": 3000,
                                    "cacheCreationInputTokens": 500, "contextWindow": 200000}}})
    sys.exit(0)

sys.exit(2)
