"""노트끼리 링크 = 보통 마크다운 상대 경로 (docs/storage.md "노트끼리 링크").

    [회의록](../회의록.md)   [2026-03](2026-03/)   [3월](<2026-03/3월 회의.md>)   [이름](%EC%9D%B4%EB%A6%84.md)

- 화면에서는 그 노트·폴더의 주소(/n/...)로 바꿔 연다
- 앱에서 이름을 바꾸거나 옮기면 그 경로를 가리키는 링크를 모든 노트에서 함께 고친다
"""

import posixpath
import re
from urllib.parse import quote, unquote

# [글](대상 "제목") / ![그림](대상) — 대상은 <...> 또는 공백 없는 문자열
LINK_RE = re.compile(r'(!?\[(?:[^\]\\]|\\.)*\]\()(<[^<>\n]*>|[^\s()<>]*(?:\([^\s()]*\)[^\s()<>]*)*)((?:\s+"[^"\n]*")?\))')
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")


def is_relative(target: str) -> bool:
    return bool(target) and not target.startswith(("/", "#", "\\")) and not SCHEME_RE.match(target)


def split_target(raw: str) -> tuple[str, str, bool]:
    """(경로(디코딩), #뒤·?뒤 꼬리, <...> 형태였는지)."""
    angle = raw.startswith("<") and raw.endswith(">")
    target = raw[1:-1] if angle else raw
    cut = min([i for i in (target.find("#"), target.find("?")) if i >= 0], default=len(target))
    return unquote(target[:cut]), target[cut:], angle


def resolve(note_path: str, target: str) -> str | None:
    """노트(notes/ 기준 경로)에 쓰인 상대 링크 → notes/ 기준 경로 (폴더 링크면 끝의 "/" 유지). 밖으로 나가면 None."""
    folder = target.endswith("/")
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(note_path), target))
    if joined in (".", "") or joined.startswith(("../", "/")) or joined == "..":
        return None
    return joined + ("/" if folder else "")


def relative(from_note: str, to_path: str) -> str:
    folder = to_path.endswith("/")
    rel = posixpath.relpath(to_path.rstrip("/"), posixpath.dirname(from_note) or ".")
    return rel + ("/" if folder else "")


def format_target(path: str, tail: str, angle: bool) -> str:
    if angle:
        return f"<{path}{tail}>"
    # 공백·괄호 등 마크다운 링크를 깨는 글자만 %인코딩 (한글은 그대로 읽히게)
    return "".join(f"%{ord(c):02X}" if c in " ()<>%" or ord(c) < 0x20 else c for c in path) + tail


def _outside_code(text: str, fn) -> str:
    """코드블록(```) 밖의 줄에만 fn 을 적용."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
        else:
            out.append(line if in_fence else fn(line))
    return "\n".join(out)


def rewrite(text: str, old_note: str, new_note: str, mapping) -> str:
    """노트 old_note 가 new_note 로 옮겨졌을 때(같을 수도) 링크를 고친다.
    mapping(옛 경로) → 새 경로 (그대로면 같은 값, 없는 대상이면 None — 끊어진 링크는 건드리지 않음)."""

    def fix(m: re.Match) -> str:
        raw = m.group(2)
        path, tail, angle = split_target(raw)
        if not is_relative(path):
            return m.group(0)
        target = resolve(old_note, path)
        if target is None:
            return m.group(0)
        moved = mapping(target)
        if moved is None or (moved == target and new_note == old_note):
            return m.group(0)
        new_rel = relative(new_note, moved)
        if new_rel == path:
            return m.group(0)
        return m.group(1) + format_target(new_rel, tail, angle) + m.group(3)

    if "](" not in text:
        return text
    return _outside_code(text, lambda line: LINK_RE.sub(fix, line))
