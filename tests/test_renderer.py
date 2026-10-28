from app.services.markdown_renderer import render_block, split_markdown


def test_basic_markdown():
    html = render_block("# 제목\n**굵게** ~~취소~~")
    assert "<h1>제목</h1>" in html
    assert "<strong>굵게</strong>" in html
    assert "<del>취소</del>" in html


def test_strips_dangerous_html():
    html = render_block('<script>alert(1)</script><img src=x onerror="alert(1)"> [x](javascript:alert(1))')
    assert "<script" not in html
    assert "onerror" not in html
    assert "javascript:" not in html


def test_youtube_line_becomes_player_but_inline_link_does_not():
    html = render_block("https://youtu.be/dQw4w9WgXcQ")
    assert "<iframe" in html
    html = render_block("링크 https://youtu.be/dQw4w9WgXcQ 참고")
    assert "<iframe" not in html
    assert 'href="https://youtu.be/dQw4w9WgXcQ"' in html


def test_youtube_url_inside_code_block_is_not_embedded():
    html = render_block("```\nhttps://youtu.be/dQw4w9WgXcQ\n```")
    assert "<iframe" not in html


def test_code_is_not_double_escaped_or_linkified():
    html = render_block('```python\nprint("hi") & <b> https://example.com\n```\n\n`a < b` https://x.com')
    assert "&amp;quot;" not in html and "&amp;amp;" not in html and "&amp;lt;" not in html
    assert "&quot;hi&quot;" in html
    assert html.count("<a ") == 1  # 코드 밖의 URL 만 링크
    assert 'href="https://x.com"' in html


def test_task_list_checkboxes():
    html = render_block("- [ ] 할 일\n- [x] 완료")
    assert html.count('class="task-check"') == 2
    assert html.count("checked") == 1


def test_relative_note_links_use_resolver():
    def links(href):
        if href.startswith("http"):
            return None
        return ("/n/a/" + href, True) if href == "b.md" else (href, False)

    html = render_block("[B](b.md) [없음](c.md) [밖](https://x.y)", links)
    assert 'href="/n/a/b.md"' in html
    assert '<a href="c.md" class="broken-link"' in html
    assert 'href="https://x.y"' in html


def test_split_markdown_keeps_code_fences_together():
    text = "첫 문단\n\n```\na\n\nb\n```\n\n\n둘째 문단\n"
    assert split_markdown(text) == ["첫 문단", "```\na\n\nb\n```", "둘째 문단"]
