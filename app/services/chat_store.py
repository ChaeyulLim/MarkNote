"""AI 도우미 대화 기록 — 파일(JSONL)로 단순 기록한다 (DB 없음, docs/storage.md). 한 줄 = 한 레코드.

  AI_CHAT_DIR/
    state.json            {"current": "<대화 id>"}   지금 이어 가는 대화
    <대화 id>.jsonl        레코드를 차례로 덧붙임 (고치지 않음)

레코드 ("t" 가 종류):
  {"t": "meta", "id", "created_at"}
  {"t": "session", "session_id"}                       Claude Code 대화 ID (--resume 용)
  {"t": "user", "id", "text", "selection", "location", "at"}
  {"t": "assistant", "id", "run_id", "ok", "text", "html", "tools", "note_edit_ids", "deck_edit_ids", "error", "at"}
                                                       (예전 기록의 "proposal_ids" 는 무시)
"""

import json
import os
import re
import secrets
import threading
from datetime import datetime

from . import userdata

_lock = threading.Lock()
ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
MAX_MESSAGES = 500  # 화면에 불러오는 최대 메시지 수 (최근 것부터)


def _dir() -> str:
    path = userdata.path("AI_CHAT_DIR")
    os.makedirs(path, exist_ok=True)
    return path


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")  # 같은 초 안의 순서까지 구분


def _path(conv_id: str) -> str:
    if not ID_RE.match(conv_id or ""):
        raise ValueError("대화 ID 가 올바르지 않습니다.")
    return os.path.join(_dir(), f"{conv_id}.jsonl")


def _write_state(conv_id: str) -> None:
    path = os.path.join(_dir(), "state.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"current": conv_id}, f)
    os.replace(tmp, path)


def new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def new_conversation() -> str:
    conv_id = new_id()
    with _lock:
        with open(_path(conv_id), "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": "meta", "id": conv_id, "created_at": _now()}, ensure_ascii=False) + "\n")
        _write_state(conv_id)
    return conv_id


def current_id() -> str:
    """지금 이어 가는 대화. 없으면 새로 만든다."""
    try:
        with open(os.path.join(_dir(), "state.json"), encoding="utf-8") as f:
            conv_id = json.load(f).get("current")
        if conv_id and os.path.exists(_path(conv_id)):
            return conv_id
    except (OSError, ValueError):
        pass
    return new_conversation()


def exists(conv_id: str) -> bool:
    try:
        return os.path.exists(_path(conv_id))
    except ValueError:
        return False


def append(conv_id: str, record: dict) -> None:
    line = json.dumps({**record, "at": record.get("at") or _now()}, ensure_ascii=False)
    with _lock:
        with open(_path(conv_id), "a", encoding="utf-8") as f:
            f.write(line + "\n")


def message_id() -> str:
    return secrets.token_hex(6)


TITLE_MAX = 80
RECAP_MESSAGES = 12  # Claude 쪽 대화 기록이 지워졌을 때 다시 보내는 최근 메시지 수
RECAP_CHARS = 12_000


def _records(conv_id: str) -> list[dict]:
    try:
        with open(_path(conv_id), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def set_current(conv_id: str) -> None:
    if not exists(conv_id):
        raise ValueError("대화를 찾을 수 없습니다.")
    with _lock:
        _write_state(conv_id)


def rename(conv_id: str, title: str) -> str:
    title = " ".join((title or "").split())[:TITLE_MAX]
    if not title:
        raise ValueError("이름을 입력하세요.")
    if not exists(conv_id):
        raise ValueError("대화를 찾을 수 없습니다.")
    append(conv_id, {"t": "title", "title": title})
    return title


def delete(conv_id: str) -> None:
    """바로 지우지 않고 trash/ 로 옮긴다 (파일을 되돌려 놓으면 복원)."""
    path = _path(conv_id)
    if not os.path.exists(path):
        raise ValueError("대화를 찾을 수 없습니다.")
    trash = os.path.join(_dir(), "trash")
    os.makedirs(trash, exist_ok=True)
    with _lock:
        os.replace(path, os.path.join(trash, os.path.basename(path)))


def summary(conv_id: str) -> dict:
    """목록 한 줄: 제목(이름 또는 첫 질문), 메시지 수, 마지막 시각, 마지막 위치."""
    title, first_question, count, updated, location, created = None, None, 0, None, None, None
    for rec in _records(conv_id):
        kind = rec.get("t")
        if kind != "title":  # 이름만 바꾼 것은 "최근에 쓴 대화"로 치지 않음
            updated = rec.get("at") or updated
        if kind == "meta":
            created = rec.get("created_at")
        elif kind == "title":
            title = rec.get("title")
        elif kind in ("user", "assistant"):
            count += 1
            if kind == "user":
                first_question = first_question or rec.get("text")
                location = rec.get("location") or location
    auto = " ".join((first_question or "").split())
    return {
        "id": conv_id,
        "title": title or (auto[:40] + ("…" if len(auto) > 40 else "")) or "빈 대화",
        "named": bool(title),
        "messages": count,
        "created_at": created,
        "updated_at": updated or created,
        "location": location,
    }


def list_conversations(query: str = "", limit: int = 50) -> list[dict]:
    """최근에 쓴 대화부터. query 가 있으면 제목·내용에 들어 있는 것만."""
    query = (query or "").strip().lower()
    ids = [name[:-6] for name in os.listdir(_dir()) if name.endswith(".jsonl") and ID_RE.match(name[:-6])]
    rows = []
    for conv_id in ids:
        if query:
            try:
                with open(_path(conv_id), encoding="utf-8") as f:
                    if query not in f.read().lower():
                        continue
            except OSError:
                continue
        rows.append(summary(conv_id))
    # 같은 초 안의 순서는 id(만든 시각 + 임의값)로
    rows.sort(key=lambda r: (r["updated_at"] or "", r["id"]), reverse=True)
    return rows[:limit]


def recap(conv_id: str) -> str:
    """Claude 쪽 대화 기록이 없어 새로 시작할 때, 이전 대화를 글로 다시 넘겨 주기 위한 요약."""
    lines = []
    for rec in load(conv_id)["messages"][-RECAP_MESSAGES:]:
        text = (rec.get("text") or rec.get("error") or "").strip()
        if text:
            lines.append(f"{'사용자' if rec['t'] == 'user' else 'AI'}: {text}")
    body = "\n\n".join(lines)[-RECAP_CHARS:]
    if not body:
        return ""
    return (
        "<previous_conversation>\n이 대화는 예전에 나눈 대화를 이어 가는 것입니다. 지난 대화(최근 것만):\n\n"
        f"{body}\n</previous_conversation>\n"
    )


def load(conv_id: str) -> dict:
    """레코드를 차례로 읽어 대화 하나로 모은다. 깨진 줄은 건너뛴다."""
    conv = {"id": conv_id, "session_id": None, "created_at": None, "messages": [], "focus": []}
    try:
        with open(_path(conv_id), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return conv
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = rec.get("t")
        if kind == "meta":
            conv["created_at"] = rec.get("created_at")
        elif kind == "session":
            conv["session_id"] = rec.get("session_id")  # None 이면 다음 질문부터 새 Claude 대화
        elif kind == "focus":
            conv["focus"] = [int(i) for i in rec.get("page_ids") or [] if str(i).isdigit()]  # 다루는 노트 (최신이 앞)
        elif kind in ("user", "assistant"):
            conv["messages"].append(rec)
    conv["messages"] = conv["messages"][-MAX_MESSAGES:]
    return conv
