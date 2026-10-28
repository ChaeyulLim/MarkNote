"""AI 사용량 표시: 사용 중인 모델, 대화의 컨텍스트 사용량, 오늘 쓴 토큰, 구독 사용 한도.

모두 claude -p 의 stream-json 출력에서 얻는다 (화면 캡처·추가 요청 없음).
  system/init        → model
  assistant.usage    → 지금 대화가 컨텍스트에서 차지하는 토큰
  rate_limit_event   → 구독 한도 (5시간·주간 사용률, 재설정 시각). Claude Code 가 보낼 때만 있음
  result.modelUsage  → 모델별 토큰 합계와 컨텍스트 크기
  claude -p /usage   → 구독 세션(5시간)·주간 한도 사용률과 재설정 시각 (로컬 명령, 토큰 0개)
"""

import re
import threading
import time
from datetime import date

from flask import current_app

from . import filestore
from .settings import get_value, set_values

MODEL_CHOICES = [  # (설정 값, 화면 이름) — 값은 claude --model 별칭
    ("", "기본 모델"),
    ("opus", "Opus"),
    ("sonnet", "Sonnet"),
    ("haiku", "Haiku"),
    ("fable", "Fable"),
]
EFFORT_CHOICES = [  # claude --effort
    ("", "기본"),
    ("low", "낮음"),
    ("medium", "중간"),
    ("high", "높음"),
    ("xhigh", "매우 높음"),
    ("max", "최대"),
]
LIMIT_NAMES = {"five_hour": "5시간", "seven_day": "주간", "seven_day_opus": "주간 (Opus)", "seven_day_sonnet": "주간 (Sonnet)"}
LAST_MODEL_KEY = "ai_last_model"

_limits: dict[str, dict] = {}  # rateLimitType → 마지막으로 받은 정보 (서버 메모리)

PLAN_TTL = 60  # /usage 결과를 다시 쓰는 시간 (초)
PLAN_ERROR_TTL = 30
_plan: dict = {"at": None, "data": None}
_plan_lock = threading.Lock()

_USAGE_LINE_RE = re.compile(
    r"^Current (session|week)(?: \(([^)]*)\))?:\s*([\d.]+)%\s*used(?:\s*[·•-]\s*resets\s+(.+?))?\s*$", re.I
)
_RESET_RE = re.compile(
    r"^(?:(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}),?\s*)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)"
    r"(?:\s*\((?P<tz>[^)]+)\))?$",
    re.I,
)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def model_label(model_id: str | None) -> str:
    """claude-opus-5-5 → Opus 5.5, claude-sonnet-4-5-20250929 → Sonnet 4.5"""
    if not model_id:
        return ""
    name = re.sub(r"\[.*\]$", "", model_id)  # claude-opus-5-5[1m] 같은 표시 제거
    m = re.match(r"^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$", name)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")
    m = re.match(r"^claude-(\d+)(?:-(\d))?-([a-z]+)", name)  # 예전 형식 claude-3-5-sonnet
    if m:
        return f"{m.group(3).capitalize()} {m.group(1)}" + (f".{m.group(2)}" if m.group(2) else "")
    return name


def context_tokens(usage: dict | None) -> int:
    """API 응답 하나의 usage → 그 시점 대화가 차지하는 토큰 (입력 + 캐시 + 출력)."""
    usage = usage or {}
    return sum(
        int(usage.get(k) or 0)
        for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
    )


def summarize(model: str | None, last_context: int, model_usage: dict | None) -> dict:
    """한 번의 응답에 대한 사용량 요약 (채팅 결과에 붙여 보냄)."""
    model_usage = model_usage or {}
    input_tokens = output_tokens = 0
    window = None
    for name, u in model_usage.items():
        input_tokens += sum(int(u.get(k) or 0) for k in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))
        output_tokens += int(u.get("outputTokens") or 0)
        if name == model or window is None:
            window = u.get("contextWindow") or window
    return {
        "model": model,
        "model_label": model_label(model),
        "context_tokens": last_context,
        "context_window": window,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def record(summary: dict) -> None:
    """오늘 사용량에 더하고, 마지막으로 쓴 모델을 기억한다."""
    today = date.today().isoformat()
    with filestore.update_json(current_app.config["USAGE_FILE"], {}) as days:  # instance/ai-usage.json {날짜: {…}}
        row = days.setdefault(today, {"requests": 0, "input_tokens": 0, "output_tokens": 0})
        row["requests"] += 1
        row["input_tokens"] += summary["input_tokens"]
        row["output_tokens"] += summary["output_tokens"]
    if summary.get("model"):
        set_values({LAST_MODEL_KEY: summary["model"]})


def set_limit(info: dict | None) -> None:
    if isinstance(info, dict):
        _limits[info.get("rateLimitType") or "unknown"] = info


def reset_korean(text: str | None) -> str:
    """'Oct 28, 12:19am (Asia/Seoul)' → '10월 28일 오전 12:19', '3pm' → '오후 3시'. 모르는 형식은 그대로."""
    if not text:
        return ""
    m = _RESET_RE.match(text.strip())
    if not m:
        return text.strip()
    hour = int(m["h"]) % 12
    minute = f":{m['m']}" if m["m"] and m["m"] != "00" else "시"
    when = f"{'오후' if m['ap'].lower() == 'pm' else '오전'} {hour or 12}{minute}"
    if m["mon"] and m["mon"].lower() in _MONTHS:
        when = f"{_MONTHS.index(m['mon'].lower()) + 1}월 {int(m['day'])}일 {when}"
    return when


def parse_usage(text: str) -> list[dict]:
    """claude /usage 출력에서 'Current session: 90% used · resets …' 같은 줄을 뽑는다."""
    result = []
    for line in (text or "").splitlines():
        m = _USAGE_LINE_RE.match(line.strip())
        if not m:
            continue
        kind, scope, percent, reset = m.groups()
        if kind.lower() == "session":
            key, name = "session", "세션 (5시간)"
        else:
            scope_l = (scope or "").lower()
            if not scope or "all" in scope_l:
                key, name = "week_all", "주간 (전체 모델)"
            else:
                label = re.sub(r"\s*only$", "", scope, flags=re.I).strip()
                key, name = "week_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_"), f"주간 ({label})"
        pct = float(percent)
        result.append(
            {
                "type": key,
                "name": name,
                "status": "rejected" if pct >= 100 else "allowed_warning" if pct >= 80 else "allowed",
                "percent": round(pct, 1),
                "resets_at": None,
                "resets_text": reset_korean(reset),
                "overage": False,
            }
        )
    return result


def plan_usage(configured, workdir: str, refresh: bool = False) -> dict:
    """구독 한도 (claude -p /usage). 1분 동안은 저장해 둔 결과를 쓴다."""
    from .claude_runner import usage_text

    with _plan_lock:
        at, data = _plan["at"], _plan["data"]
        ttl = PLAN_ERROR_TTL if data and data.get("error") else PLAN_TTL
        if not refresh and at is not None and time.monotonic() - at < ttl:
            return data
        try:
            text = usage_text(configured, workdir)
            parsed = parse_usage(text)
            data = {"limits": parsed, "raw": None if parsed else text.strip()[:1000], "error": None}
        except RuntimeError as err:
            data = {"limits": [], "raw": None, "error": str(err)}
        data["checked_at"] = time.time()
        _plan.update(at=time.monotonic(), data=data)
        return data


def cached_plan() -> dict | None:
    return _plan["data"]


def limits() -> list[dict]:
    """구독 한도: /usage 결과가 있으면 그것, 없으면 대화 중 받은 rate_limit_event."""
    plan = cached_plan()
    if plan and plan.get("limits"):
        return plan["limits"]
    result = []
    for kind, info in _limits.items():
        utilization = info.get("utilization")
        if utilization is not None:
            utilization = float(utilization)
            if utilization <= 1:  # 0~1 비율로 오면 %로
                utilization *= 100
        result.append(
            {
                "type": kind,
                "name": LIMIT_NAMES.get(kind, kind),
                "status": info.get("status"),  # allowed / allowed_warning / rejected
                "percent": round(utilization, 1) if utilization is not None else None,
                "resets_at": info.get("resetsAt"),  # 유닉스 초
                "resets_text": "",
                "overage": bool(info.get("isUsingOverage")),
            }
        )
    order = list(LIMIT_NAMES)
    return sorted(result, key=lambda r: order.index(r["type"]) if r["type"] in order else len(order))


def overview(prefs: dict) -> dict:
    today = filestore.read_json(current_app.config["USAGE_FILE"], {}).get(date.today().isoformat(), {})
    last = get_value(LAST_MODEL_KEY)
    return {
        "model": prefs["ai_model"],
        "effort": prefs["ai_effort"],
        "last_model": last,
        "last_model_label": model_label(last) if last else "",
        "today": {
            "requests": today.get("requests", 0),
            "input_tokens": today.get("input_tokens", 0),
            "output_tokens": today.get("output_tokens", 0),
        },
        "limits": limits(),
        "plan_error": (cached_plan() or {}).get("error"),
        "plan_raw": (cached_plan() or {}).get("raw"),
        "plan_checked_at": (cached_plan() or {}).get("checked_at"),
        "models": [{"value": v, "label": label} for v, label in MODEL_CHOICES],
        "efforts": [{"value": v, "label": label} for v, label in EFFORT_CHOICES],
    }
