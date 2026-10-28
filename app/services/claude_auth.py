"""채팅의 /login, /logout — 서버에 설치된 공식 Claude Code 의 로그인 흐름을 실행만 한다.

로그인은 Anthropic 페이지에서 끝나고, 결과는 Claude Code 가 자기 localhost 콜백으로 직접 받는다.
앱은 Claude Code 가 출력한 로그인 링크만 보여주며 인증 코드·토큰을 입력받거나 저장하지 않는다.
그래서 브라우저가 서버와 같은 컴퓨터일 때만 패널 로그인을 허용한다 (routes/ai.py 에서 판별).
"""

import codecs
import re
import subprocess
import threading
import time
from urllib.parse import urlparse

from . import claude_runner as runner

LOGIN_TIMEOUT = 300
LINK_WAIT = 15  # 시작 요청에서 링크가 나올 때까지 기다리는 시간(초)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_URL = re.compile(r"https://[^\s\"'<>]+")
_ALLOWED_HOSTS = ("claude.ai", "claude.com", "anthropic.com")
_ACTIVE = ("starting", "waiting")

_lock = threading.Lock()
_job: dict | None = None


def _allowed_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _ALLOWED_HOSTS)


def _public(job: dict | None) -> dict:
    if job is None:
        return {"state": "idle", "url": None, "message": None}
    return {"state": job["state"], "url": job["url"], "message": job["message"]}


def login_state() -> dict:
    with _lock:
        return _public(_job)


def start_login(configured: list[str] | None, workdir: str) -> dict:
    """`claude auth login` 을 시작하고 로그인 링크가 나오면 돌려준다."""
    global _job
    with _lock:
        if _job and _job["state"] in _ACTIVE:
            return _public(_job)

    command = runner.find_command(configured)
    if command is None:
        return {"state": "failed", "url": None, "message": "서버에 Claude Code(claude)가 설치되어 있지 않습니다."}
    current = runner.status(configured, refresh=True)
    if current["state"] == "ready":
        return {
            "state": "already",
            "url": None,
            "message": "이미 Claude 계정이 연결되어 있습니다. 다른 계정으로 바꾸려면 /logout 후 /login 하세요.",
        }

    proc = subprocess.Popen(
        [*command, "auth", "login"],
        cwd=workdir,
        stdin=subprocess.PIPE,  # 코드 붙여넣기 입력은 절대 보내지 않음
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=runner._child_env(),
        creationflags=runner._popen_flags(),
    )
    job = {"proc": proc, "state": "starting", "url": None, "message": "로그인 링크를 준비하고 있습니다…", "tail": ""}
    with _lock:
        _job = job

    timer = threading.Timer(LOGIN_TIMEOUT, _stop, args=(job, "timeout"))
    timer.daemon = True
    timer.start()
    threading.Thread(target=_watch, args=(job, timer), daemon=True).start()

    deadline = time.monotonic() + LINK_WAIT
    while time.monotonic() < deadline and job["state"] == "starting":
        time.sleep(0.1)
    return login_state()


def _watch(job: dict, timer: threading.Timer) -> None:
    """출력에서 로그인 링크를 찾고, 프로세스가 끝나면 결과를 기록한다."""
    proc = job["proc"]
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buffer = ""
    while True:
        chunk = proc.stdout.read1(4096)
        if not chunk:
            break
        buffer = (buffer + _ANSI.sub("", decoder.decode(chunk)))[-8000:]
        job["tail"] = buffer[-500:]
        if job["url"] is None:
            for m in _URL.finditer(buffer):
                if m.end() == len(buffer):
                    break  # 링크가 아직 덜 출력됨
                url = m.group(0).rstrip(".,);")
                if _allowed_url(url):
                    with _lock:
                        if job["state"] == "starting":
                            job.update(
                                url=url,
                                state="waiting",
                                message="Anthropic 로그인 페이지에서 로그인을 마치면 자동으로 연결됩니다.",
                            )
                    break

    code = proc.wait()
    timer.cancel()
    with _lock:
        if job["state"] in _ACTIVE:
            if code == 0:
                job.update(state="done", message="Claude 계정이 연결되었습니다.")
            else:
                detail = job["tail"].strip().splitlines()[-1:] or [""]
                job.update(state="failed", message=f"로그인에 실패했습니다. {detail[0]}".strip())
    runner.invalidate_status()


def _stop(job: dict, reason: str) -> None:
    messages = {
        "cancelled": "로그인을 취소했습니다.",
        "timeout": f"{LOGIN_TIMEOUT // 60}분 안에 로그인을 마치지 않아 취소했습니다.",
    }
    with _lock:
        if job["state"] not in _ACTIVE:
            return
        job.update(state=reason, message=messages[reason])
    if job["proc"].poll() is None:
        job["proc"].kill()


def cancel_login() -> dict:
    with _lock:
        job = _job
    if job is not None:
        _stop(job, "cancelled")
    return login_state()


def logout(configured: list[str] | None) -> dict:
    command = runner.find_command(configured)
    if command is None:
        return {"ok": False, "message": "서버에 Claude Code(claude)가 설치되어 있지 않습니다."}
    try:
        result = runner._run_quick(command, "auth", "logout")
    except (OSError, subprocess.TimeoutExpired) as err:
        return {"ok": False, "message": f"로그아웃 실패: {err}"}
    runner.invalidate_status()
    after = runner.status(configured, refresh=True)

    if after["state"] == "ready" and after.get("auth_method") == "oauth_token":
        return {
            "ok": True,
            "message": "로그아웃했지만 .env 의 CLAUDE_CODE_OAUTH_TOKEN 토큰으로 계속 연결됩니다. "
            "완전히 해제하려면 .env 에서 지우고 서버를 다시 시작하세요.",
            "status": after,
        }
    if result.returncode != 0 and after["state"] == "ready":
        tail = (result.stdout + result.stderr).strip().splitlines()[-1:] or [""]
        return {"ok": False, "message": f"로그아웃에 실패했습니다. {tail[0]}".strip(), "status": after}
    return {"ok": True, "message": "Claude 계정 연결을 해제했습니다.", "status": after}
