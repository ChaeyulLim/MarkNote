"""AI 수정 기록 — instance/ai-edits/<종류>/<번호>.json (docs/storage.md).

웹 서버와 MCP 서버(AI 도구)가 함께 쓰므로 번호는 잠금 파일로 매기고, 파일은 바꿔치기로 쓴다.
"""

import os
from datetime import datetime
from types import SimpleNamespace

from flask import current_app

from . import filestore


def _dir(kind: str) -> str:
    path = os.path.join(current_app.config["AI_EDITS_DIR"], kind)
    os.makedirs(path, exist_ok=True)
    return path


class Record(SimpleNamespace):
    kind: str

    def save(self) -> "Record":
        data = {k: v for k, v in vars(self).items() if k != "kind"}
        filestore.write_json(os.path.join(_dir(self.kind), f"{self.id}.json"), data)
        return self


def create(kind: str, **fields) -> Record:
    record_id = filestore.Counter(os.path.join(_dir(kind), "_next.json")).next()
    return Record(kind=kind, id=record_id, created_at=datetime.now().isoformat(timespec="seconds"), **fields).save()


def get(kind: str, record_id) -> Record | None:
    try:
        data = filestore.read_json(os.path.join(_dir(kind), f"{int(record_id)}.json"), None)
    except (TypeError, ValueError):
        return None
    return Record(kind=kind, **data) if data else None


def query(kind: str, **filters) -> list[Record]:
    result = []
    for name in os.listdir(_dir(kind)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        data = filestore.read_json(os.path.join(_dir(kind), name), None)
        if data and all(data.get(k) == v for k, v in filters.items()):
            result.append(Record(kind=kind, **data))
    return sorted(result, key=lambda r: r.id)
