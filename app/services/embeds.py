"""한 줄짜리 영상 URL 을 플레이어 HTML 로 바꾸는 provider 레지스트리.

다른 사이트를 지원하려면 parse()/render() 를 가진 provider 를 PROVIDERS 에 추가하면 된다.
"""

import re
from urllib.parse import parse_qs, urlparse

_URL_LINE = re.compile(r"^https?://\S+$")


class YouTubeProvider:
    name = "youtube"

    _ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
    _TIME = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$")
    _HOSTS = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
    _SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
    _PATH_PREFIXES = {"shorts", "embed", "live", "v"}

    def parse(self, url: str) -> dict | None:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        query = parse_qs(u.query)

        video_id = None
        if host in self._SHORT_HOSTS:
            video_id = u.path.strip("/").split("/")[0]
        elif host in self._HOSTS:
            if u.path.rstrip("/") == "/watch":
                video_id = query.get("v", [None])[0]
            else:
                parts = u.path.strip("/").split("/")
                if len(parts) >= 2 and parts[0] in self._PATH_PREFIXES:
                    video_id = parts[1]

        if not video_id or not self._ID.match(video_id):
            return None

        fragment = parse_qs(u.fragment)
        raw_time = (query.get("t") or query.get("start") or fragment.get("t") or ["0"])[0]
        return {
            "id": video_id,
            "start": self._parse_time(raw_time),
            "vertical": u.path.startswith("/shorts/"),
        }

    def _parse_time(self, value: str) -> int:
        if value.isdigit():
            return int(value)
        m = self._TIME.match(value)
        if not m or not any(m.groups()):
            return 0
        h, mnt, s = (int(g or 0) for g in m.groups())
        return h * 3600 + mnt * 60 + s

    def render(self, info: dict) -> str:
        src = f"https://www.youtube-nocookie.com/embed/{info['id']}"
        if info["start"]:
            src += f"?start={info['start']}"
        css = "video-embed vertical" if info["vertical"] else "video-embed"
        return (
            f'<div class="{css}" data-provider="youtube">'
            f'<iframe src="{src}" title="YouTube 동영상" frameborder="0" '
            'allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
            'referrerpolicy="strict-origin-when-cross-origin" allowfullscreen loading="lazy"></iframe>'
            "</div>"
        )


PROVIDERS = [YouTubeProvider()]


def render_embed(line: str) -> str | None:
    """줄 전체가 지원하는 영상 URL 이면 플레이어 HTML, 아니면 None."""
    line = line.strip()
    if not _URL_LINE.match(line):
        return None
    for provider in PROVIDERS:
        info = provider.parse(line)
        if info:
            return provider.render(info)
    return None
