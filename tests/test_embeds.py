import pytest

from app.services.embeds import YouTubeProvider, render_embed

yt = YouTubeProvider()


@pytest.mark.parametrize(
    "url, start",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", 0),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&list=abc", 0),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", 0),
        ("https://youtu.be/dQw4w9WgXcQ", 0),
        ("https://youtu.be/dQw4w9WgXcQ?si=xyz&t=90", 90),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1m30s", 90),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1h2m3s", 3723),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", 0),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ?start=5", 5),
    ],
)
def test_parses_youtube_urls(url, start):
    info = yt.parse(url)
    assert info["id"] == "dQw4w9WgXcQ"
    assert info["start"] == start


def test_shorts_are_vertical():
    info = yt.parse("https://www.youtube.com/shorts/dQw4w9WgXcQ")
    assert info["vertical"] is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ\"><script>",
        "https://evil.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/channel/UCxxxx",
        "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_rejects_invalid_urls(url):
    assert yt.parse(url) is None


def test_render_embed_requires_whole_line_url():
    assert render_embed("보세요 https://youtu.be/dQw4w9WgXcQ") is None
    html = render_embed("  https://youtu.be/dQw4w9WgXcQ  ")
    assert "youtube-nocookie.com/embed/dQw4w9WgXcQ" in html
