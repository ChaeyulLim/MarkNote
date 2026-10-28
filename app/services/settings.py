"""앱 설정 — instance/settings.json (key-value). 설정 화면에서 바꾼다."""

from flask import current_app

from . import filestore


def _path() -> str:
    return current_app.config["SETTINGS_FILE"]


def get_value(key: str, default: str | None = None) -> str | None:
    return filestore.read_json(_path(), {}).get(key, default)


def set_values(values: dict) -> None:
    with filestore.update_json(_path(), {}) as data:
        data.update(values)

MAX_PROMPT_CHARS = 4000

_DEFAULTS = {
    "ai_initial_prompt": "",
    "ai_notes_read": "1",  # Claude 가 노트를 검색·읽기
    "ai_notes_edit": "1",  # Claude 가 노트를 바로 수정 (되돌리기 | 확정)
    "ai_decks_edit": "1",  # Claude 가 프레젠테이션을 읽고 바로 수정 (채팅에서 되돌리기)
    "ai_notes_delete": "1",  # Claude 가 노트·폴더를 휴지통으로 옮기기 (영구 삭제는 못 함, 되돌리기 = 복원)
    "ai_model": "",  # claude --model 별칭 ("" = Claude Code 기본 / .env 의 CLAUDE_MODEL)
    "ai_effort": "",  # claude --effort ("" = 기본)
}


def get_settings() -> dict:
    values = dict(_DEFAULTS)
    stored = filestore.read_json(_path(), {})
    values.update({k: str(v) for k, v in stored.items() if k in _DEFAULTS})
    read = values["ai_notes_read"] == "1"
    return {
        "ai_initial_prompt": values["ai_initial_prompt"],
        "ai_notes_read": read,
        "ai_notes_edit": read and values["ai_notes_edit"] == "1",  # 수정 제안은 읽기가 켜져 있어야 가능
        "ai_decks_edit": values["ai_decks_edit"] == "1",
        "ai_notes_delete": read and values["ai_notes_delete"] == "1",  # 지울 노트를 찾으려면 읽기가 필요
        "ai_model": values["ai_model"],
        "ai_effort": values["ai_effort"],
    }


def save_settings(initial_prompt: str, notes_read: bool, notes_edit: bool, decks_edit: bool = True,
                  notes_delete: bool = True) -> None:
    values = {
        "ai_notes_delete": "1" if notes_read and notes_delete else "0",
        "ai_decks_edit": "1" if decks_edit else "0",
        "ai_initial_prompt": initial_prompt.strip()[:MAX_PROMPT_CHARS],
        "ai_notes_read": "1" if notes_read else "0",
        "ai_notes_edit": "1" if notes_read and notes_edit else "0",
    }
    set_values(values)


def save_ai_prefs(model: str | None = None, effort: str | None = None) -> None:
    """채팅 아래 모델·노력 수준 메뉴에서 고른 값 저장 (허용 목록 밖의 값은 무시)."""
    from .ai_usage import EFFORT_CHOICES, MODEL_CHOICES

    values = {}
    if model is not None and model in {v for v, _ in MODEL_CHOICES}:
        values["ai_model"] = model
    if effort is not None and effort in {v for v, _ in EFFORT_CHOICES}:
        values["ai_effort"] = effort
    if values:
        set_values(values)
