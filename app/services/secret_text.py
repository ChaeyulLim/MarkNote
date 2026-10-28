"""비공개 표시 — AI 에게 보내지 않는 부분 (docs/claude-integration.md "비공개 표시").

  글 일부:  국민은행 {{123-456-7890}}
  여러 줄:  :::비공개
            아이디: chae
            :::

따로 저장하지 않는다. 표시는 노트 글 안에 그대로 있고, AI 로 내보낼 때마다 redact() 로 가린다.
AI 에게는 내용 대신 ⟦🔒a1b2c3⟧ 자리 표시만 보인다 (코드는 SECRET_KEY 로 만든 내용 해시라 같은 내용은 같은 코드).
AI 가 고친 글은 restore() 로 자리 표시를 원래 내용으로 되돌리고, check_kept() 로 비공개 내용이 사라지지 않았는지 확인한다.

규칙
- 코드(`…`, ``` 블록) 안의 {{ }} · ::: 는 그냥 글자
- 닫는 표시가 없으면: {{ 는 그 문단(빈 줄 전)까지, :::비공개 는 글 끝까지 비공개 — 새는 것보다 많이 가리는 쪽이 안전
"""

import hashlib
import hmac
import re
from collections import Counter

from flask import current_app

FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
BLOCK_OPEN_RE = re.compile(r"^\s{0,3}:::\s*비공개\s*$")
BLOCK_CLOSE_RE = re.compile(r"^\s{0,3}:::\s*$")
PLACEHOLDER_RE = re.compile(r"⟦🔒([0-9a-f]{6})⟧")
LOCK = "⟦🔒⟧"  # 선택한 글자 등에서 가린 자리 (되돌릴 필요 없음)


class SecretError(ValueError):
    pass


# ---------- 나누기 ----------

def _inline(line: str, out: list, pending: list | None):
    """한 줄을 [("plain"|"secret", 글)] 로. pending 은 이전 줄에서 닫히지 않은 {{ 의 내용."""
    i, n = 0, len(line)
    if pending is not None:
        end = line.find("}}")
        if end < 0:
            pending.append(line)
            return pending
        pending.append(line[: end + 2])
        out.append(("secret", "".join(pending)))
        i = end + 2
    plain_start = i
    while i < n:
        if line[i] == "`":
            run = len(line[i:]) - len(line[i:].lstrip("`"))
            close = line.find("`" * run, i + run)
            i = close + run if close >= 0 else i + run
            continue
        if line.startswith("{{", i):
            if i > plain_start:
                out.append(("plain", line[plain_start:i]))
            end = line.find("}}", i + 2)
            if end < 0:
                return [line[i:]]
            out.append(("secret", line[i : end + 2]))
            i = plain_start = end + 2
            continue
        i += 1
    if plain_start < n:
        out.append(("plain", line[plain_start:]))
    return None


def tokens(text: str) -> list[tuple[str, str]]:
    """글을 [("plain"|"secret", 원문)] 으로 나눈다. 이어 붙이면 원래 글과 같다."""
    out: list[tuple[str, str]] = []
    fence = None  # 코드 블록 안이면 여는 표시
    block: list[str] | None = None  # :::비공개 안이면 지금까지의 줄
    pending: list[str] | None = None  # 닫히지 않은 {{

    def close_pending():
        nonlocal pending
        raw = "".join(pending)
        tail = raw[len(raw.rstrip("\n")):]
        out.append(("secret", raw[: len(raw) - len(tail)]))
        if tail:
            out.append(("plain", tail))
        pending = None

    for line in (text or "").splitlines(keepends=True):
        body = line.rstrip("\n")
        eol = line[len(body):]
        if block is not None:
            block.append(body)
            if BLOCK_CLOSE_RE.match(body):
                out.append(("secret", "\n".join(block)))
                out.append(("plain", eol))
                block = None
            continue
        if pending is not None and not body.strip():
            close_pending()  # 닫지 않은 {{ 는 문단 끝까지
        if fence is not None:
            out.append(("plain", line))
            if body.strip().startswith(fence):
                fence = None
            continue
        if pending is None:
            m = FENCE_RE.match(body)
            if m:
                fence = m.group(1)
                out.append(("plain", line))
                continue
            if BLOCK_OPEN_RE.match(body):
                block = [body]
                continue
        pending = _inline(body, out, pending)
        if pending is not None:
            pending.append(eol)
        elif eol:
            out.append(("plain", eol))
    if block is not None:
        out.append(("secret", "\n".join(block)))
    if pending is not None:
        close_pending()
    return out


def secrets_in(text: str) -> list[str]:
    return [raw for kind, raw in tokens(text) if kind == "secret"]


def has_secret(text: str) -> bool:
    return any(kind == "secret" for kind, _ in tokens(text))


def inner(raw: str) -> tuple[str, bool]:
    """표시를 뺀 내용, 여러 줄 상자인지."""
    if BLOCK_OPEN_RE.match(raw.split("\n", 1)[0]):
        lines = raw.split("\n")[1:]
        if lines and BLOCK_CLOSE_RE.match(lines[-1]):
            lines = lines[:-1]
        return "\n".join(lines), True
    body = raw[2:]
    return (body[:-2] if body.endswith("}}") else body), False


# ---------- AI 로 내보낼 때 ----------

def tag(raw: str) -> str:
    key = str(current_app.config.get("SECRET_KEY") or "marknote").encode()
    return hmac.new(key, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:6]


def redact(text: str) -> str:
    if not text or ("{{" not in text and ":::" not in text):
        return text
    return "".join(raw if kind == "plain" else f"⟦🔒{tag(raw)}⟧" for kind, raw in tokens(text))


def redact_obj(value):
    """dict/list 안의 모든 글자를 redact (MCP 도구 결과용)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    return value


def scrub_selection(selection: str, sources: list[str]) -> str:
    """사용자가 드래그한 글자에 섞여 들어온 비공개 내용을 지운다 (화면에서도 빼지만 서버에서 한 번 더)."""
    selection = redact(selection)
    pieces = set()
    for text in sources:
        for raw in secrets_in(text):
            body, _ = inner(raw)
            pieces.update(line.strip() for line in body.split("\n") if len(line.strip()) >= 2)
    for piece in sorted(pieces, key=len, reverse=True):
        selection = selection.replace(piece, LOCK)
    return selection


# ---------- AI 가 고친 글을 받을 때 ----------

def tag_map(*texts: str) -> dict[str, str]:
    return {tag(raw): raw for text in texts for raw in secrets_in(text)}


def restore(text: str, known: dict[str, str]) -> str:
    """AI 가 돌려준 글의 자리 표시를 원래 내용으로."""
    def put_back(m: re.Match) -> str:
        raw = known.get(m.group(1))
        if raw is None:
            raise SecretError(f"알 수 없는 비공개 자리 표시 {m.group(0)} — 읽은 글에 있던 자리 표시만 그대로 쓰세요.")
        return raw

    return PLACEHOLDER_RE.sub(put_back, text) if text else text


def restore_obj(value, known: dict[str, str]):
    if isinstance(value, str):
        return restore(value, known)
    if isinstance(value, list):
        return [restore_obj(v, known) for v in value]
    if isinstance(value, dict):
        return {k: restore_obj(v, known) for k, v in value.items()}
    return value


def check_kept(before: list[str], after: list[str]) -> None:
    """비공개 내용이 하나라도 사라지거나 바뀌었으면 거절 (늘어나는 것은 괜찮음)."""
    missing = Counter(before) - Counter(after)
    if missing:
        raise SecretError(
            f"비공개 부분 {sum(missing.values())}개가 지워지거나 바뀌게 됩니다. "
            "⟦🔒…⟧ 자리 표시는 지우거나 고치지 말고 그대로 두세요 (아무것도 바뀌지 않음)."
        )
