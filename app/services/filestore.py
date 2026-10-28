"""파일 저장 공통 — 바꿔치기 쓰기 + 잠금 파일 (docs/storage.md).

웹 서버와 MCP 서버(AI 도구)는 다른 프로세스라서 같은 JSON 파일을 동시에 고칠 수 있다.
update() 는 잠금 파일로 한 번에 하나만 읽고-고치고-쓰게 한다.
쓰기는 항상 임시 파일에 쓴 뒤 os.replace — 중간에 꺼져도 파일이 반쯤 쓰인 채로 남지 않는다.
"""

import copy
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager

LOCK_TIMEOUT = 10  # 초
STALE_LOCK = 30  # 이보다 오래된 잠금 파일은 죽은 프로세스가 남긴 것으로 보고 지움

_thread_lock = threading.RLock()


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        for attempt in range(20):  # Windows: 다른 프로세스가 잠깐 열고 있으면 실패할 수 있음
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_text(path: str) -> str:
    with open(path, encoding="utf-8-sig") as f:
        return f.read().replace("\r\n", "\n")


@contextmanager
def locked(path: str):
    lock = path + ".lock"
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    deadline = time.time() + LOCK_TIMEOUT
    with _thread_lock:
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(lock) > STALE_LOCK:
                        os.remove(lock)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    raise TimeoutError(f"잠금을 얻지 못했습니다: {lock}") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            os.close(fd)
            try:
                os.remove(lock)
            except FileNotFoundError:
                pass


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return copy.deepcopy(default)


def write_json(path: str, data) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=1, default=str))


@contextmanager
def update_json(path: str, default):
    """with update_json(p, {}) as data: data[...] = ... → 끝나면 저장 (잠금 안에서)."""
    with locked(path):
        data = read_json(path, default)
        yield data
        write_json(path, data)


class Counter:
    """여러 프로세스가 함께 쓰는 번호 매기기 (AI 수정 기록 번호 등)."""

    def __init__(self, path: str):
        self.path = path

    def next(self) -> int:
        with update_json(self.path, {"next": 1}) as data:
            value = int(data.get("next", 1))
            data["next"] = value + 1
        return value
