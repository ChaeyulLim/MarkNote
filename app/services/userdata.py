"""계정마다 다른 저장 위치 (docs/storage.md "계정").

    <DATA_DIR>/users/<아이디>/notes/        노트 (.md)
    <DATA_DIR>/users/<아이디>/uploads/      그림
    <DATA_DIR>/users/<아이디>/ai-chats/     AI 도우미 대화
    <DATA_DIR>/users/<아이디>/decks/        프레젠테이션
    <DATA_DIR>/users/<아이디>/ai-edits/     AI 수정 기록 (되돌리기용)
    <DATA_DIR>/users/<아이디>/settings.json, ai-usage.json

요청마다 로그인한 계정의 위치를 g 에 두고(use), 저장소 코드는 path(KEY) 로 읽는다.
로그인한 계정이 없을 때(AI 도구 MCP 서버·관리 명령)는 app.config 의 위치 — MCP 서버는 띄울 때 그 계정의 위치를 환경변수로 받는다.
"""

import os

from flask import current_app, g, has_app_context

KEYS = {
    "NOTES_DIR": "notes",
    "UPLOAD_FOLDER": "uploads",
    "AI_CHAT_DIR": "ai-chats",
    "DECKS_DIR": "decks",
    "AI_EDITS_DIR": "ai-edits",
    "SETTINGS_FILE": "settings.json",
    "USAGE_FILE": "ai-usage.json",
}
DIRS = ("NOTES_DIR", "UPLOAD_FOLDER", "DECKS_DIR", "AI_EDITS_DIR")


def user_root(username: str) -> str:
    return os.path.join(current_app.config["DATA_DIR"], "users", username)


def paths_for(username: str) -> dict[str, str]:
    base = user_root(username)
    return {key: os.path.join(base, name) for key, name in KEYS.items()}


def use(paths: dict[str, str] | None) -> None:
    """이 요청(또는 AI 실행 스레드)에서 쓸 저장 위치."""
    g.data_paths = paths
    for key in DIRS if paths else ():
        os.makedirs(paths[key], exist_ok=True)


def current() -> dict[str, str] | None:
    return g.get("data_paths") if has_app_context() else None


def path(key: str) -> str:
    paths = current()
    return paths[key] if paths else current_app.config[key]
