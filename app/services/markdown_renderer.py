import re
import secrets
from html import escape, unescape

import bleach
import markdown
from markdown.extensions import Extension
from markdown.inlinepatterns import SimpleTagInlineProcessor

from . import secret_text
from .embeds import render_embed

FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
A_HREF_RE = re.compile(r'<a href="([^"]*)"')
TASK_RE = re.compile(r"<li>(\s*<p>)?\[([ xX])\]\s?")

ALLOWED_TAGS = [
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "del", "s", "code", "pre", "blockquote",
    "ul", "ol", "li", "a", "img", "table", "thead", "tbody", "tr", "th", "td",
    "span", "div", "sup", "sub",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
    "img": ["src", "alt", "title"],
    "th": ["align"],
    "td": ["align"],
    "ol": ["start"],
    "span": ["class"],
    "div": ["class"],
    "code": ["class"],
    "pre": ["class"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


class _StrikethroughExtension(Extension):
    def extendMarkdown(self, md):
        md.inlinePatterns.register(SimpleTagInlineProcessor(r"(~~)(.+?)~~", "del"), "del", 175)


def _markdown_to_html(text: str) -> str:
    return markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "sane_lists", "nl2br", "codehilite", _StrikethroughExtension()],
        extension_configs={
            "codehilite": {"guess_lang": False, "css_class": "codehilite"},
            "tables": {"use_align_attribute": True},
        },
    )


def split_markdown(text: str) -> list[str]:
    """빈 줄 기준으로 블록을 나눈다. 코드블록(```)·비공개 상자(:::비공개) 안의 빈 줄은 나누지 않는다."""
    chunks: list[str] = []
    current: list[str] = []
    in_fence = False
    in_secret = False
    for line in text.replace("\r\n", "\n").split("\n"):
        if in_secret:
            in_secret = not secret_text.BLOCK_CLOSE_RE.match(line)
            current.append(line.rstrip())
            continue
        if FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence and secret_text.BLOCK_OPEN_RE.match(line):
            in_secret = True
        if not in_fence and not line.strip():
            if current:
                chunks.append("\n".join(current))
                current = []
            continue
        current.append(line.rstrip())
    if current:
        chunks.append("\n".join(current))
    return chunks


def join_markdown(contents: list[str]) -> str:
    return "\n\n".join(c for c in contents if c.strip())


_CODE_RE = re.compile(r"<pre\b[\s\S]*?</pre>|<code\b[\s\S]*?</code>")


def _linkify_outside_code(html: str, nonce: str) -> str:
    """URL 을 링크로 바꾸되 코드 안은 건드리지 않는다.

    bleach.linkify(skip_tags=...) 는 건너뛴 태그 안의 &quot; 등을 이중으로 escape 하므로,
    코드 부분을 잠시 자리표시자로 빼 두었다가 되돌린다.
    """
    saved: list[str] = []

    def stash(m: re.Match) -> str:
        saved.append(m.group(0))
        return f"MNCODE{nonce}X{len(saved) - 1}X"

    html = bleach.linkify(_CODE_RE.sub(stash, html))
    for i, code in enumerate(saved):
        html = html.replace(f"MNCODE{nonce}X{i}X", code)
    return html


def _icon(name: str) -> str:
    return f'<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#i-{name}"></use></svg>'


def _fix_links(html: str, links) -> str:
    """노트끼리 상대 링크(../회의록.md) → 그 노트 주소(/n/...). 없는 대상은 끊어진 링크로 표시."""
    def fix(m: re.Match) -> str:
        found = links(unescape(m.group(1)))
        if found is None:
            return m.group(0)
        url, ok = found
        return f'<a href="{escape(url)}"' + ("" if ok else ' class="broken-link" title="없는 노트"')
    return A_HREF_RE.sub(fix, html)


SECRET_TITLE = "비공개 — 눌러서 보기 (AI 에게는 보내지 않음)"


def _stash_secrets(content: str, nonce: str, hidden: dict, links) -> str:
    """비공개 표시({{ }}, :::비공개)를 자리표시자로 빼 두고, 흐리게 보일 HTML 을 따로 만든다."""
    if "{{" not in content and ":::" not in content:
        return content
    parts = []
    for kind, raw in secret_text.tokens(content):
        if kind == "plain":
            parts.append(raw)
            continue
        key = f"MNSECRET{nonce}X{len(hidden)}X"
        body, box = secret_text.inner(raw)
        if box:
            hidden[key] = (
                f'<div class="secret secret-box" tabindex="0" title="{SECRET_TITLE}">'
                f'<div class="secret-label">{_icon("lock")}<span>비공개 · AI 에게 보내지 않음</span><span class="secret-hint">눌러서 보기</span></div>'
                f'<div class="secret-body">{render_block(body, links)}</div></div>'
            )
            parts.append(f"\n\n{key}\n\n")
        else:
            html = bleach.clean(
                _markdown_to_html(body), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, protocols=ALLOWED_PROTOCOLS, strip=True
            )
            html = re.sub(r"^<p>|</p>$", "", html.strip()).replace("</p>\n<p>", "<br>")
            hidden[key] = f'<span class="secret" tabindex="0" title="{SECRET_TITLE}">{_icon("lock")}<span class="secret-text">{html}</span></span>'
            parts.append(key)
    return "".join(parts)


def render_block(content: str, links=None) -> str:
    """블록 하나의 마크다운을 안전한 HTML 로 변환한다.
    links(href) → (주소, 있는지) 또는 None(그대로): 노트끼리 상대 링크를 화면 주소로 (page_tree.link_resolver)."""
    if not content.strip():
        return ""

    # 영상 URL 줄은 자리표시자로 바꿔두고, 정화(bleach)가 끝난 뒤 플레이어로 교체
    nonce = secrets.token_hex(6)
    embeds: dict[str, str] = {}
    hidden: dict[str, str] = {}
    content = _stash_secrets(content, nonce, hidden, links)
    lines: list[str] = []
    in_fence = False
    for line in content.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        embed = None if in_fence else render_embed(line)
        if embed:
            key = f"MNEMBED{nonce}X{len(embeds)}"
            embeds[key] = embed
            lines.extend(["", key, ""])
        else:
            lines.append(line)

    html = _markdown_to_html("\n".join(lines))
    html = bleach.clean(
        html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, protocols=ALLOWED_PROTOCOLS, strip=True
    )
    html = _linkify_outside_code(html, nonce)
    if links is not None:
        html = _fix_links(html, links)

    for key, embed in embeds.items():
        html = html.replace(f"<p>{key}</p>", embed)
    for key, secret in hidden.items():
        html = html.replace(f"<p>{key}</p>", secret).replace(key, secret)

    def _task(match: re.Match) -> str:
        checked = " checked" if match.group(2).lower() == "x" else ""
        return f'<li class="task">{match.group(1) or ""}<input type="checkbox" class="task-check"{checked}> '

    return TASK_RE.sub(_task, html)
