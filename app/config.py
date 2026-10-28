import os
import secrets
from datetime import timedelta


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD", "temp")  # LOGIN_PASSWORD_HASH 가 없을 때만 (로컬 개발용)
    # 공용 계정 (.env.auth) — 비밀번호는 해시로만
    LOGIN_USER = os.environ.get("LOGIN_USER", "netnsecu")
    LOGIN_PASSWORD_HASH = os.environ.get("LOGIN_PASSWORD_HASH") or None
    TRUST_PROXY = os.environ.get("TRUST_PROXY", "false").lower() == "true"  # nginx 뒤에서 운영할 때 true

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)  # "이 기기에서 로그인 유지" 기간

    # 로그인 실패 제한: LOCK_SECONDS 초 안에 MAX_ATTEMPTS 회 실패하면 잠금
    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_LOCK_SECONDS = 900

    # 저장 위치 — DB 없이 파일 (docs/storage.md). DATA_DIR 이 없으면 instance/ 폴더
    DATA_DIR = os.environ.get("DATA_DIR")
    NOTES_DIR = os.environ.get("NOTES_DIR")  # 노트: <DATA_DIR>/notes/프로젝트/노트.md
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER")  # 그림
    AI_CHAT_DIR = os.environ.get("AI_CHAT_DIR")  # AI 도우미 대화 기록 (JSONL 파일)
    # 아래는 보통 비워 둔다 (DATA_DIR 아래). AI 도구(MCP 서버)를 띄울 때 같은 위치를 넘겨주는 데 쓴다
    DECKS_DIR = os.environ.get("DECKS_DIR")  # 프레젠테이션 <번호>.json
    AI_EDITS_DIR = os.environ.get("AI_EDITS_DIR")  # AI 수정 기록 (되돌리기용)
    SETTINGS_FILE = os.environ.get("SETTINGS_FILE")
    USAGE_FILE = os.environ.get("USAGE_FILE")
    # AI 도우미 (Claude Code CLI). 비우면 Claude Code 기본 모델 사용
    CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or None
    CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "300"))
    CLAUDE_COMMAND = None  # 테스트에서 가짜 claude 로 바꿀 때만 사용 (["python", "fake.py"])

    # 요청 하나의 최대 크기 (zip 가져오기 대비). 이미지 한 장은 별도로 10MB 제한
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024
