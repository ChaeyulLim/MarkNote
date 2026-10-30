"""AI 답변을 브라우저 연결과 상관없이 서버에서 끝까지 실행한다.

채팅 요청은 백그라운드 스레드에서 Claude Code 를 돌리고, 화면용 이벤트를 Run 에 쌓는다.
브라우저는 Run 을 처음부터(또는 n번째부터) 받아 보므로, 답변 중에 다른 페이지로 옮겨도
새 페이지에서 이어 받을 수 있다.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Iterator

KEEP_RUNS = 20  # 끝난 실행도 잠시 보관 (이동 직후 이어 받기용)
HEARTBEAT_SECONDS = 15


class Run:
    def __init__(self, run_id: str, conversation_id: str):
        self.id = run_id
        self.conversation_id = conversation_id
        self.owner: int | None = None  # 질문한 계정 (답변 이어 받기·멈추기는 그 계정만)
        self.paths: dict | None = None  # 그 계정의 저장 위치 (userdata)
        self.events: list[dict] = []
        self.done = False
        self._cond = threading.Condition()

    def add(self, event: dict) -> None:
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    def finish(self) -> None:
        with self._cond:
            self.done = True
            self._cond.notify_all()

    def follow(self, after: int = 0) -> Iterator[dict | None]:
        """after 번째 이벤트부터 끝날 때까지 내보낸다. 오래 조용하면 None (연결 유지용)."""
        index = max(0, after)
        while True:
            with self._cond:
                if index >= len(self.events) and not self.done:
                    self._cond.wait(timeout=HEARTBEAT_SECONDS)
                new = self.events[index:]
                done = self.done
            index += len(new)
            yield from new
            if not new and not done:
                yield None
            if done and index >= len(self.events):
                return


_runs: "OrderedDict[str, Run]" = OrderedDict()
_lock = threading.Lock()


def start(run: Run, target, *args) -> Run:
    with _lock:
        _runs[run.id] = run
        while len(_runs) > KEEP_RUNS:
            oldest = next(iter(_runs))
            if not _runs[oldest].done:
                break
            _runs.pop(oldest)

    def work():
        try:
            target(run, *args)
        except Exception as err:  # noqa: BLE001 — 어떤 오류든 화면에 알리고 끝냄
            run.add({"type": "error", "message": f"서버 오류: {err}"})
        finally:
            run.finish()

    threading.Thread(target=work, name=f"ai-run-{run.id[:8]}", daemon=True).start()
    return run


def get(run_id: str) -> Run | None:
    return _runs.get(run_id)


def active(conversation_id: str | None = None) -> Run | None:
    for run in reversed(list(_runs.values())):
        if not run.done and (conversation_id is None or run.conversation_id == conversation_id):
            return run
    return None


def wait_all(timeout: float = 10) -> None:
    """테스트용: 실행 중인 것이 끝날 때까지 기다림."""
    end = time.monotonic() + timeout
    while any(not r.done for r in list(_runs.values())) and time.monotonic() < end:
        time.sleep(0.02)
