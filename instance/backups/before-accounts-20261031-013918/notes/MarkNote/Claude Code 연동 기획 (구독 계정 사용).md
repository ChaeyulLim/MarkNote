> 상태: **1~3단계 구현됨** (채팅 패널 ↔ 서버의 `claude -p`, 스트리밍, 이어서 묻기, 페이지에 추가, `/login` `/logout`, 설정 화면의 초기 프롬프트, MCP 노트 도구 + 승인 후 적용). 실제 Claude Code CLI 로는 아직 미검증 (가짜 CLI·실제 MCP 서버로만 테스트).
> 확인일: 2026-10-26 (Anthropic 공식 문서 기준, 정책은 바뀔 수 있으니 구현 전에 다시 확인)

## 1. 목표

- MarkNote 안에서 Claude 기능(요약·다듬기·생성·페이지 질의 등)을 쓴다.
- **API 과금이 아니라, 내가 로그인한 Claude Code 계정(Pro/Max 구독)** 의 사용량으로 동작한다.
- 지금은 **단일 사용자**. 나중에 사용자를 늘리면 **각자 자기 Claude 계정**으로 쓴다 (내 구독 공유 X).

## 2. 가능 여부 결론

**가능합니다. 단, "앱이 Claude 로그인을 처리하는 방식"이 아니라 "서버에 설치된 공식 Claude Code를 내 계정으로 로그인해 두고, 앱이 그 CLI를 호출하는 방식"이어야 합니다.**

### ✅ 허용되는 방식 (이 방식으로 진행)
- 라즈베리파이(개발 중엔 이 PC)에 **수정하지 않은 공식 Claude Code 바이너리**를 설치
- 서버 터미널(SSH)에서 **본인이 Anthropic 공식 로그인 흐름**으로 로그인 (`claude` 실행 → 브라우저 로그인, 또는 `claude setup-token`)
- MarkNote 서버가 `claude -p ...` 를 **서브프로세스로 실행**하고 결과를 화면에 표시
- 근거 (공식 문서 *Legal and compliance*):
  - OAuth(구독) 인증은 구독자의 "ordinary use of Claude Code" 를 위한 것
  - "end user signing in to the unmodified Claude Code binary with their own Claude subscription" 은 막지 않음 (플랫폼이 Claude Code를 호스팅하는 경우 포함)
  - 구독 사용량 한도는 "ordinary, individual usage of Claude Code and the Agent SDK" 기준

### ❌ 하면 안 되는 방식
- MarkNote 화면에 **"Claude로 로그인" 버튼**을 만들어 앱이 OAuth를 처리하는 것
  → "third-party developers [may not] offer Claude.ai login into their own applications"
- 앱이 Claude 로그인 정보·세션 토큰을 **입력받거나 저장·중계**하는 것 (예: 웹 화면에 토큰 붙여넣기 칸)
  → "may not collect, store, or intermediate Claude.ai credentials or session tokens — sign-in must complete through Anthropic's own flow"
- **Agent SDK(파이썬/TS 라이브러리)에 구독 로그인**을 넣어 제품처럼 만드는 것 → Agent SDK는 API 키 사용 안내
- 다른 사람의 요청을 **내 구독으로 처리**하는 것 (사용자 확장 시 특히 주의)

### ⚠️ 주의할 회색 지대
- 혼자 쓰는 개인 서버에서 내 계정으로 CLI를 호출하는 것은 위 "허용" 범위로 판단되지만, **자동화로 대량 호출**(크론으로 계속 돌리기 등)은 "ordinary individual usage" 를 넘어설 수 있음 → 사용자가 버튼을 누를 때만 실행
- **다중 사용자 서비스로 확장**하면 "Can customers offer Claude Code in their products?" 조건(Commercial Terms 동의, 바이너리 무수정, 사용자별 본인 인증, 사용량 중계 금지)이 적용됨 → 그 단계에서 다시 검토. 애매하면 Anthropic 영업팀 문의
- Claude 브랜드: 기능 이름에 "Claude Code" 를 제품명처럼 쓰지 않기 (예: "AI 도우미" 로 표기, "Claude Code로 실행됨" 정도의 사실 표기는 가능)

## 3. 환경 요건

| 항목 | 내용 |
|---|---|
| 하드웨어 | **ARM64 + RAM 4GB 이상** → 라즈베리파이 4/5 (4GB+), **64비트** Raspberry Pi OS (Debian 기반, Debian 10+ 지원) |
| 설치 | `curl -fsSL https://claude.ai/install.sh \| bash` 또는 apt 저장소 |
| 계정 | Pro / Max (무료 플랜은 Claude Code 불가) |
| 인증 위치 | MarkNote(gunicorn)를 실행하는 **같은 OS 사용자**로 로그인해야 그 자격증명을 사용 (`~/.claude/.credentials.json`) |
| 개발 중 | 이 Windows PC에 이미 Claude Code가 있으므로 로컬에서 바로 개발·테스트 가능 |

### 로그인 방법 비교
| 방법 | 장점 | 단점 |
|---|---|---|
| SSH로 접속해 `claude` 실행 → 로그인 (추천) | 자동 갱신, 공식 기본 흐름 | 최초 1회 브라우저 로그인 필요 (헤드리스면 로그인 URL 복사 → 코드 붙여넣기) |
| `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` | 스크립트용으로 문서화된 방법 | 1년 만료·자동 갱신 없음, 원격 기능 일부 불가, 커뮤니티에 첫 실행 시 인식 안 되는 이슈 보고 있음 |

> `--bare` 모드는 구독 로그인(OAuth)을 **읽지 않음** → 구독으로 쓰려면 `--bare` 를 쓰지 않는다.
> `ANTHROPIC_API_KEY` 가 환경에 있으면 구독보다 **API 키가 우선** → 서버 환경에 넣지 않는다.

## 4. 구조 설계 (안)

```
브라우저 ──(로그인된 MarkNote 세션)──▶ Flask
                                         │  POST /api/ai/run  (페이지/블록 + 지시)
                                         ▼
                              작업 큐 (동시 1개, 타임아웃)
                                         │  subprocess
                                         ▼
                 claude -p "<지시>" --output-format stream-json --verbose
                        (작업 폴더: 빈 전용 폴더, 도구 최소 허용)
                                         │  stdout 스트림
                                         ▼
                    SSE 로 브라우저에 실시간 전달 → 미리보기 → [적용]/[취소]
```

### 구현 현황
| 파일 | 역할 |
|---|---|
| `app/services/claude_runner.py` | `claude` 찾기(설정 → `CLAUDE_BIN` → PATH → `~/.local/bin`), `claude auth status` 로 상태 확인(60초 캐시), `claude -p` 실행·stream-json 파싱, 동시 1개 잠금, 취소·시간초과 |
| `app/routes/ai.py` | `GET /api/ai/status`, `POST /api/ai/chat`(SSE 스트림), `POST /api/ai/cancel`, 페이지 내용 → `<page>` 태그로 stdin 전달 |
| `app/static/js/chat.js` | 상태 배지, 스트리밍 표시, 중단(■), 이어서 묻기(`session_id`), 복사 / 페이지에 추가 / 다시 묻기 |
| `app/services/claude_auth.py` | `/login`: `claude auth login` 실행 → 출력에서 claude.ai·claude.com·anthropic.com 링크만 골라 표시, 완료·실패·취소·5분 시간초과 추적. `/logout`: `claude auth logout` |
| `tests/fake_claude.py` | 테스트용 가짜 claude (실제 구독 사용량을 쓰지 않음, 로그인 흐름도 흉내) |

### 설정 화면 (`/settings`, 사이드바 ⚙️ 설정)
| 항목 | 저장 키 | 동작 |
|---|---|---|
| 초기 프롬프트 | `ai_initial_prompt` (최대 4000자) | 모든 대화의 `--append-system-prompt` 끝에 "User's custom instructions" 로 붙음 |
| 노트 읽기 | `ai_notes_read` (기본 켜짐) | MarkNote MCP 서버 연결 + `search_pages` `find_page` `list_pages` `read_page` 허용, 채팅에서 노트 지목·다루는 노트 |
| 노트 바로 수정 | `ai_notes_edit` (기본 켜짐, 읽기 필요) | `edit_page` `create_page` 추가 허용 |
값은 `Setting` 테이블(key-value)에 저장, 다음 대화부터 적용.

### 노트 도구 (MCP) — 바로 적용 + 되돌리기 | 확정 (2026-10-28 바뀜)
- `app/mcp_server.py`: 공식 MCP Python SDK(`mcp==2.3.0`, `MCPServer`)로 만든 stdio 서버. 채팅 요청마다 `claude` 가 `--mcp-config` 로 `python -m app.mcp_server` 를 띄운다 (노트 폴더 등 저장 위치·run_id·수정 허용 여부는 환경변수로 전달. AI 는 노트 파일을 직접 열지 않고 이 도구로만 읽고 쓴다 — 비공개 표시를 가리기 위해. 도구 결과에는 `file`(예: `회사/회의록.md`)이 들어 있다)
- `app/services/note_tools.py`: 실제 도구 로직. 휴지통 페이지는 읽기·수정 대상에서 제외
- `--tools ""` 는 그대로라 Claude 는 **PC 파일·명령 실행에 접근 불가**, `--allowedTools` 로 `mcp__marknote__*` 중 허용된 것만 실행
- 수정 도구 `edit_page` / `create_page` 는 **바로 적용**하고 적용 전·후를 `AiNoteEdit` 에 저장 (`app/services/note_edits.py`, `run_id` 로 이번 응답과 연결)
  - 바뀐 블록만 바꾼다 (같은 내용 블록은 id 유지) → 바뀐 블록 id 를 기억해 페이지에서 강조
  - 비공개 표시·같은 이름 금지·하위 페이지 링크가 빠지면 경고 — 예전 제안 방식과 같은 규칙
- 응답이 끝나면 채팅에 diff 카드 `되돌리기 | 확정`, 지금 보고 있는 노트면 편집기를 다시 불러와 바뀐 블록을 파란 띠로 강조 + 페이지 위 막대 `되돌리기 | 확정`
  - **둘 다 안 누르면**: 블록 편집 중이 아닐 때 **Ctrl+Z = 되돌리기**, **Ctrl+S = 확정** (강조가 보일 때만)
  - **직접 고쳐 저장하면** 강조만 지움 (`POST /api/ai/pages/<id>/note-edits/seen`). 되돌리기는 채팅 카드에서 계속 가능
  - **되돌리기**: 그 뒤 페이지가 그대로면 통째로. 다른 곳을 고쳤으면 **AI 가 바꾼 부분만** 되돌림(3-way). AI 가 바꾼 바로 그곳을 또 고쳤으면 409 → "그래도 되돌리기"(수정 전 전체로). 새로 만든 노트는 휴지통으로
  - API: `POST /api/ai/note-edits/<id>/undo {force}`, `POST /api/ai/note-edits/<id>/confirm`, `GET /api/ai/pages/<id>/note-edits`, `POST /api/ai/pages/<id>/note-edits/confirm`
- 예전 `propose_*` → [적용] 카드는 지난 대화 기록 표시용으로만 남아 있음
- 대화 중 도구 사용은 "🔎 노트 검색: …", "📖 페이지 읽기: …" 처럼 답변 위에 표시

### 채팅 명령어 `/login` `/logout` `/status` `/resume` `/rename` `/new`
입력창에 `/` 를 치면 명령어 메뉴가 뜬다 (↑↓ 선택, Enter/Tab 실행, Esc 닫기).

| 명령 | 동작 |
|---|---|
| `/login` | **브라우저가 서버와 같은 컴퓨터일 때만**: 서버가 공식 `claude auth login` 을 실행하고, 패널에 Anthropic 로그인 링크를 표시. 사용자가 Anthropic 페이지에서 로그인하면 Claude Code 가 자기 localhost 콜백으로 결과를 직접 받는다 → 카드가 "연결되었습니다" 로 바뀌고 배지 초록. **원격 접속이면** 실행하지 않고 "서버 터미널(SSH)에서 `claude` 로 로그인" 안내만 표시 |
| `/logout` | `claude auth logout`. `.env` 의 `CLAUDE_CODE_OAUTH_TOKEN` 때문에 계속 연결돼 있으면 그 사실을 안내 |
| `/status` | 상태·버전·인증 방식 표시 |
| `/resume [검색어]` | 저장된 대화 목록(최근 순, 제목·시각·메시지 수·마지막 위치). 골라 누르면 그 대화로 바꿔 이어 감. 줄마다 연필 버튼(이름 바꾸기, Enter 저장·Esc 취소)과 휴지통 버튼(삭제). 이름만 바꾼 것은 목록 순서에 영향 없음. 패널 위쪽 `기록` 버튼과 같음 |
| `/rename 새 이름` | 지금 대화 이름 바꾸기 (없으면 첫 질문이 제목) |
| `/new` | 새 대화 (`새 대화` 버튼과 같음) |

같은 컴퓨터 판별: 접속한 IP 가 `127.0.0.1`/`::1` **이고** Host 가 `localhost`/`127.0.0.1`/`[::1]` 일 때만. 리버스 프록시 뒤에서는 Host 가 외부 주소라 원격으로 판별된다.
원격에서 인증 코드를 패널에 붙여넣는 방식은 **만들지 않았다** — 앱이 Claude 인증 정보를 중계하게 되어 정책("sign-in must complete through Anthropic's own flow")에 걸릴 수 있기 때문.

실행 옵션: `-p --output-format stream-json --verbose --include-partial-messages --tools "" --disallowedTools "mcp__*" --strict-mcp-config --permission-mode dontAsk --max-turns 2 --append-system-prompt ...` (+ `--resume`, `--model`), 작업 폴더 `instance/ai-workdir`.
자식 프로세스 환경에서 `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` 을 제거하고, 상태가 API 키 인증(`api_key` 등)이면 "연결 안 됨"으로 표시한다.
#### 지금 보고 있는 화면 알려주기 (화면 공유 없이, 텍스트로)
화면을 이미지로 보내지 않는다. 1280×720 캡처 한 장이 약 1,200~1,600 토큰인 데 비해, 아래 텍스트는 보통 100 토큰 안팎이다.

- **입력 중 자동 저장**: 노트 블록은 입력 1초 뒤, 제목은 0.8초 뒤, 슬라이드는 0.8초 뒤 저장. 질문을 보내기 직전에도 `saveEditorNow()` 로 한 번 더 저장
- **매 질문마다 최신 내용 다시 읽기**: 질문마다 서버가 노트 파일에서 현재 페이지(또는 프레젠테이션)를 새로 읽어 stdin 으로 보냄. 같은 대화에서 지난번에 보낸 내용과 해시가 같으면 전체 대신 `(지난 메시지 이후 바뀐 내용 없음)` 한 줄만 보냄 (`ai_context.py`, 대화 50개까지 기억)
- **`<location>`**: 사용자가 있는 곳. 예) `노트 페이지 "회의록" (경로: 프로젝트 / 회의록, id 2), 3번째 블록을 편집 중`, `프레젠테이션 "발표" (id 1), 슬라이드 2/5 보는 중, 선택한 개체: 제목 1`
- **`<selection>`**: 드래그로 고른 글자(최대 2,000자). 채팅 입력창 위에 "선택: …" 칩으로 보이고 ✕ 로 뺄 수 있음. "이 부분" 은 이 선택을 뜻한다고 시스템 프롬프트에 안내
- **`<presentation>`**: 모든 슬라이드의 글자 + 지금 보는 슬라이드는 개체 이름·위치·크기까지

#### 대화 유지 — 페이지를 옮겨도 그대로 (2026-10-28)
예전에는 대화가 브라우저 화면에만 있어서 페이지를 옮기면 사라졌고, 답변 중에 옮기면 서버가 Claude 를 종료했다.
지금은 **대화를 서버 파일에 기록**하고, **답변은 브라우저 연결과 상관없이 서버에서 끝까지** 실행한다.

- **저장 위치**: `instance/ai-chats/` (`.env` 의 `AI_CHAT_DIR` 로 변경). 파일로 유지 (SQLite 이전 계획은 취소 — docs/storage.md)
  - `state.json` — `{"current": "<대화 id>"}` 지금 이어 가는 대화
  - `<대화 id>.jsonl` — 한 줄 = 한 레코드, 덧붙이기만 함

  | `t` | 내용 |
  |---|---|
  | `meta` | 대화 id, 만든 시각 |
  | `session` | Claude Code 대화 ID (`--resume` 용). `null` 이면 다음 질문부터 새 Claude 대화 (`/logout` 때) |
  | `user` | 질문, 선택한 글자, 그때 위치(페이지/프레젠테이션 id) |
  | `assistant` | 답(마크다운·HTML), 성공 여부/오류, 도구 사용 줄, 수정안·슬라이드 수정 id |

  수정안·슬라이드 수정은 id 만 기록하고, 불러올 때 `instance/ai-edits/` 기록에서 **지금 상태**(적용됨/거절됨/되돌림)로 다시 읽는다.
- **답변 실행** (`app/services/chat_runs.py`): 채팅 요청이 백그라운드 스레드에서 Claude 를 돌리고 화면용 이벤트를 쌓는다. 브라우저는 그것을 SSE 로 받아 볼 뿐이라, 연결이 끊겨도(페이지 이동) 답은 끝까지 만들어져 파일에 기록된다
- **다른 페이지에서 열면**: 채팅 패널이 `GET /api/ai/conversation` 으로 지금 대화를 그리고, 답변이 아직 진행 중이면 `GET /api/ai/runs/<id>/events?after=0` 으로 처음부터 이어 받는다 (중단 버튼도 그대로)
- **새 대화**: `POST /api/ai/conversation/new` — 이전 대화 파일은 지우지 않고 남김. 답변 중에는 거절(409)
- 입력하다 만 글은 브라우저에 남겨 둠 (`localStorage`)
- 질문마다 그 순간의 위치·페이지 최신 내용을 보내는 방식은 그대로라, 페이지를 옮겨 가며 같은 대화를 이어 가도 AI 가 지금 페이지를 안다
- **이전 대화 이어 가기 (`/resume`)**: 대화는 질문할 때마다 자동 저장되므로 따로 저장할 필요 없음
  - `GET /api/ai/conversations?q=` 목록 (검색어는 제목·내용), `POST /api/ai/conversations/<id>/resume` 바꾸기, `POST …/rename`, `DELETE /api/ai/conversations/<id>` (`trash/` 로 옮김 — 파일을 되돌려 놓으면 복원). 답변 중에는 바꾸기·삭제 거절(409)
  - 바꾼 대화의 다음 질문은 그 대화의 Claude 세션을 `--resume` 으로 이어 간다
  - **Claude 쪽 기록이 지워졌으면** (Claude Code 는 오래된 세션 파일을 정리함, 기본 약 30일): "No conversation found" 오류를 받으면 자동으로 최근 메시지 12개(최대 12,000자)를 `<previous_conversation>` 으로 붙이고 페이지 내용도 전체를 다시 보내 새 Claude 세션으로 이어 간다. 패널에는 "지난 대화를 다시 불러와 이어 가기" 줄이 보임
- 남은 것 (3단계): 대화 중 "페이지 이동" 구분선, 보관 기간(오래된 대화를 `ai-chats/archive/` 로)

#### 모델 · 노력 수준 · 사용량 표시 (채팅 입력창 아래)
Claude 앱처럼 입력창 아래 오른쪽에 `Opus 5.5 중간 ◔` 를 표시한다. 모두 `claude -p` 의 stream-json 출력에서 얻으므로 추가 요청·토큰이 들지 않는다 (`app/services/ai_usage.py`).

| 표시 | 출처 |
|---|---|
| 모델 이름 (`claude-opus-5-5` → "Opus 5.5") | `system/init` 의 `model` — 첫 질문 전에는 마지막으로 쓴 모델 |
| 노력 수준 | 설정값 → `claude --effort low/medium/high/xhigh/max` (기본이면 표시 안 함) |
| 고리 | 구독 사용 한도(5시간) 사용률. Claude Code 가 `rate_limit_event` 로 알려 줄 때만 있음 → 없으면 이 대화의 컨텍스트 사용률 |
| 고리를 누르면 | 모델·노력, 구독 한도(5시간·주간 %, 재설정 시각), 이 대화 컨텍스트(토큰/창 크기), 오늘 이 앱에서 쓴 질문 수·입력/출력 토큰 |

- 모델 이름을 누르면 메뉴: 기본 모델 / Opus / Sonnet / Haiku / Fable, 노력: 기본·낮음·중간·높음·매우 높음·최대 → `POST /api/ai/prefs` (허용 목록만 저장), 다음 질문부터 적용. 기본 모델은 `.env` 의 `CLAUDE_MODEL`, 그것도 없으면 Claude Code 기본
- 오늘 사용량은 `ai_usage` 테이블(날짜별 질문 수·입력·출력 토큰), 구독 한도는 서버 메모리(재시작하면 다음 질문 때 다시 채워짐)
- 고리 색: 80% 이상 주황, 95% 이상 빨강. 앱 밖(Claude 앱·터미널의 Claude Code)에서 쓴 양은 구독 한도에만 반영되고 "오늘" 합계에는 없음
- API: `GET /api/ai/usage`, `POST /api/ai/prefs {model, effort}`, 채팅 `result` 이벤트의 `usage`
- **세션 한도 연동 (`/usage`) — 2026-10-28 화면까지 완료**
  - `claude -p "/usage" --output-format json` 은 토큰을 쓰지 않는 로컬 명령. 결과 글(`Current session: 90% used · resets …`, `Current week (all models): 40% used · …`)을 `ai_usage.parse_usage` 로 읽고 60초 캐시 (`plan_usage`), `GET /api/ai/usage` 가 이를 우선 사용하고 없으면 `rate_limit_event` 값
  - 고리 옆에 세션 %, 고리를 누르면 "○시에 초기화". 95% 이상이면 보낼 때 한 번 경고, 100% 면 보내기 전에 막음 (초기화 시각 안내)

#### 채팅에서 노트 지목 · 다루는 노트 (2026-10-28 구현, `app/services/note_focus.py`)
- "마케팅 프로젝트의 주간 보고 확인해줘" → 서버가 메시지에 나온 **노트 제목**을 찾는다 (공백·대소문자 무시, 두 글자 이상)
  - 같은 이름이 여러 프로젝트에 있으면 메시지에 함께 나온 상위(프로젝트) 이름으로 고름. 프로젝트 이름이 경로로만 쓰였으면 프로젝트 자체는 빼고 노트만
  - "회의"·"회의록" 처럼 겹치면 긴 제목만
- AI 도 `find_page("프로젝트 / 노트")` 로 찾을 수 있음 (제목만 써도 됨)
- **다루는 노트**: 이름으로 부른 노트 + AI 가 읽거나(`read_page`) 고치거나 만든 노트 → 대화 파일에 `{"t": "focus", "page_ids": [...]}` (최신이 앞, 최대 3개)
  - 질문마다 `<working_note id path>` 로 **최신 내용**을 함께 보냄 (비공개는 가림, 바뀐 게 없으면 한 줄로 대체). 지금 보고 있는 페이지와 같으면 생략
  - 시스템 프롬프트: "거기", "그 노트" 는 맨 앞 `<working_note>`
  - 입력창 위 `다루는 노트` 칩 (누르면 열기, × 로 빼기 → `POST /api/ai/conversations/<id>/focus/remove`)
  - 설정에서 노트 읽기를 끄면 지목·다루는 노트 모두 안 함

#### 프레젠테이션도 AI 가 바로 수정
- MCP 도구 `list_decks` / `read_deck` / `edit_deck` (설정 "프레젠테이션 바로 수정", 기본 켜짐 → `MARKNOTE_ALLOW_DECK_EDIT`)
- `edit_deck` 은 승인 없이 즉시 적용, 적용 전 상태를 `AiDeckEdit` 에 저장 → 채팅 카드 [되돌리기] (`POST /api/ai/deck-edits/<id>/undo`)
- 노트와 같은 방식(노트는 `되돌리기 | 확정`). 자세한 내용은 [presentation.md 12장](/p/3)

#### 프로젝트별 대화 관리 (기획, 2026-10-28 — 대화 묶기는 아직 안 만듦)
대화가 많아지면 `/resume` 목록 하나로는 찾기 어렵다 → **대화를 프로젝트에 묶어** 지금 작업 중인 프로젝트의 대화만 먼저 보여 준다.

- **프로젝트 = 사이드바 최상위 페이지** (예: `프로젝트`, `회사`, `개인`). 그 아래 하위 페이지 어디에서 대화해도 같은 프로젝트
  - 대화의 프로젝트는 **첫 질문을 한 곳**으로 정한다. 다른 프로젝트 페이지로 옮겨 가며 이어 묻는 건 그대로 허용 (질문마다 위치는 따로 기록됨)
  - 노트 홈 등 페이지 밖에서 시작한 대화는 `프로젝트 없음`
  - 프레젠테이션은 아직 페이지 트리에 없으므로 `프레젠테이션` 묶음 하나로 (나중에 덱을 프로젝트에 연결할 수 있게 되면 그 프로젝트로)
- **기록 방식**: 대화 파일 `meta` 다음에 `{"t": "project", "page_id"}` 레코드 추가 (바꾸면 한 줄 더 덧붙임 — 마지막 것이 유효).
- **`/resume` 화면**: 위에 `이 프로젝트 · 전체` 전환 (기본은 지금 보고 있는 페이지의 프로젝트). 줄마다 프로젝트 이름 표시. `/resume 검색어` 는 전체에서 검색
- 목록에서 대화를 다른 프로젝트로 옮기기 (연필 메뉴 옆)
- 프로젝트 페이지를 휴지통으로 보내도 대화는 지우지 않음 → `프로젝트 없음` 으로 보임
- **결정됨 (2026-10-28)**: 프로젝트 = 최상위 페이지. 사이드바에서 📁 폴더 아이콘·`새 프로젝트` 버튼으로 이미 보이게 함 (아래 roadmap.md "프로젝트 폴더"). 남은 것은 `/resume` 을 프로젝트별로 묶는 부분

#### 비공개 표시 — AI 가 읽지도 쓰지도 못하는 사용자 전용 (2026-10-28 구현)
노트 안의 일부를 **특정 문자로 감싸면** 그 부분은 AI 에게 보내지 않는다. 화면에서는 흐리게(블러) 보이고 사용자가 눌러야 보인다.
따로 저장하는 곳(새 표·컬럼)은 없다 — **표시가 노트 글 안에 그대로 들어 있고**, 서버가 AI 로 내보낼 때마다 그 표시를 보고 가린다.

- **판단**: Claude 는 이 앱이 보내 주는 글만 볼 수 있고(`--tools ""` 로 파일·명령 도구가 꺼져 있음), 보내는 길은 모두 서버 코드에 있으므로 **서버에서 가리고 보내면 확실히 막을 수 있다**
  - 정책 측면: Anthropic 정책이 문제 삼는 것은 Claude 로그인·토큰 처리이지 노트 내용이 아니다. 이 기능은 정책상 필수는 아니고, **Anthropic 으로 나가는 민감 정보를 줄이는 개인정보 보호 장치**다
  - 한계: 채팅창에 직접 적은 글은 보호되지 않는다. 노트는 원래대로 `instance/notes/` 의 .md 파일에 평문으로 저장된다

##### 문법
| 범위 | 쓰는 법 | 예 |
|---|---|---|
| 글 일부 (인라인) | `{{` … `}}` 로 감싸기 | `국민은행 {{123-456-7890}} (월급 통장)` |
| 블록 전체 / 여러 줄 | `:::비공개` 줄과 `:::` 줄 사이 | 아래 |

```
:::비공개
아이디: chae
비밀번호 힌트: 첫 강아지 이름
:::
```

- 왜 이 문자인가: `||…||`(디스코드 스포일러)는 마크다운 표의 `|` 와 겹치고, `%%…%%` 는 옵시디언에서 "주석(숨김)" 뜻이라 헷갈림. `{{ }}`·`:::` 는 지금 노트 문법과 겹치지 않음 (`:::` 는 다른 마크다운 앱의 "상자" 문법과 같은 모양이라 낯설지 않음)
- 입력 도우미: 블록 편집 중 글자를 고르고 `Ctrl+Shift+H` → `{{ }}` 로 감싸기/풀기. 블록 우클릭 메뉴 `비공개로 (AI 에게 보내지 않음)` / `비공개 해제` → 블록 전체를 `:::비공개` 상자로 (노트 편집기에는 슬래시 메뉴가 없어 메뉴로 대신)
- 규칙
  - 코드(`` ` ``, ```` ``` ````) 안의 `{{ }}` 는 그냥 글자 (문법 설명을 적을 수 있게)
  - **닫는 표시를 빼먹으면 그 블록 끝까지 비공개로 처리** — 실수로 열어 두었을 때 새는 것보다 많이 가리는 쪽이 안전
  - 한 줄에 여러 개 가능, `{{ }}` 안에 다른 서식(굵게 등) 가능
- 내보내기(.md)에는 표시가 글자 그대로 남는다 → 다시 가져와도 비공개 유지

##### 화면
- 보기 모드: **보라색 바탕 + 🔒 + 흐린 글자**로 "숨긴 부분" 이 한눈에 보이게 (`--secret` 색). `:::비공개` 는 보라색 상자 + "🔒 비공개 · AI 에게 보내지 않음 / 눌러서 보기" 머리글. 누르면 또렷해지고(편집으로 들어가지 않음) 다른 곳을 누르면 다시 흐림
- 블록을 편집할 때는 원문(`{{…}}`)이 그대로 보임 — 본인 화면이므로
- 흐린 부분을 드래그해도 채팅의 "선택한 글자" 칩에 넣지 않음

##### 서버 처리 — 함수 두 개로 모든 길을 막는다 (`app/services/secret_text.py`)
1. **`redact(글)`**: 표시된 부분을 `⟦🔒a1b2c3⟧` 자리 표시로 바꾼다 (6자리 코드 = `SECRET_KEY` 로 만든 내용 해시 → 순서를 바꿔도 다른 비공개 내용과 뒤섞이지 않음). AI 는 "여기 비공개 내용이 있다"는 것만 안다
2. **`restore()` + `check_kept()`**: 자리 표시를 원래 내용으로 되돌리고, 수정 전 비공개 내용이 **하나라도 사라졌으면 수정을 통째로 거절**하고 AI 에게 "자리 표시는 그대로 두세요" 오류를 돌려줌 → AI 가 다시 시도. 모르는 자리 표시도 거절. 슬라이드는 글 덮어쓰기·개체/슬라이드 삭제도 같은 검사로 막힘

| AI 로 가는 길 | 처리 |
|---|---|
| 질문마다 보내는 페이지 내용 (`ai_context.page_context`) | `redact` |
| 프레젠테이션 글 (`deck_context`, `read_deck`) | `redact` (슬라이드 글상자에도 같은 문법) |
| MCP `read_page` | `redact` |
| MCP `search_pages` | 노트 파일 검색 후 **가린 글로 다시 걸러서** 비공개 부분으로만 걸린 페이지는 결과에서 뺌 (안 그러면 "비밀번호" 검색 결과로 어느 페이지에 있는지 새어 나감) |
| 선택한 글자 `<selection>` | 화면에서 빼고, 서버도 현재 페이지의 비공개 내용이 섞여 있으면 그 부분을 지움 |
| AI 노트 수정안 / 슬라이드 직접 수정 | 적용할 때 `restore` |

- AI 가 새로 `{{ }}` 를 써 넣는 것은 허용 (원래 AI 가 쓴 글이므로)
- 같은 원리로 **페이지 단위 "AI 사용 안 함"**도 서버에서 막는다 (아직 안 만듦): `{{ }}` 가 페이지 일부라면 이건 페이지 전체를 AI 에게서 빼는 스위치. 켜면 질문할 때 내용을 보내지 않고, AI 의 검색·목록·읽기에서 없는 것처럼(제목 포함) 보이고, AI 가 고칠 수도 없음
- 그 밖에: AI 시스템 프롬프트에 자리 표시 규칙 안내, 블록 메뉴 `AI 에게 이 블록 묻기` 도 비공개 부분을 🔒 로 바꿔 채팅에 옮김, `:::비공개` 상자 안의 빈 줄로는 블록이 나뉘지 않음
- 코드: [`secret_text.py`](../app/services/secret_text.py), 화면 `markdown_renderer._stash_secrets`, `app.js` 의 `maskSecrets`·`selectionWithoutSecrets`·클릭해서 보기. 테스트 [`tests/test_secret_text.py`](../tests/test_secret_text.py)
- 아직 안 한 것: **슬라이드 화면에서는 흐리게 보이지 않음** (`{{ }}` 글자가 그대로 보임 — AI 에게는 가려서 보냄). 프레젠테이션 편집기 작업을 다시 시작할 때 함께
- 나중(선택): 비공개 부분만 별도 암호로 암호화해서 저장 — 노트 파일이 새어도 안전. 암호를 잊으면 복구 불가라 1단계에서는 하지 않음

> 라즈베리파이 배포 시 gunicorn 은 스트리밍 때문에 `--worker-class gthread --threads 4 --timeout 330` 처럼 스레드 워커와 넉넉한 타임아웃을 쓴다. 동시 실행 1개 잠금은 프로세스 단위이므로 워커는 1개(`-w 1`)로 둔다.

### 이미 만들어 둔 UI
- 채팅 패널: [`_chat_panel.html`](../app/templates/_chat_panel.html), [`chat.js`](../app/static/js/chat.js), `style.css` 의 "AI 도우미 채팅 패널" 구역
- **오른쪽 서랍**: 화면 오른쪽 끝에 늘 보이는 "AI 도우미 · F2" 손잡이를 누르거나 **`F2`** 를 누르면 오른쪽에서 밀려 나옴 (노트·프레젠테이션 편집기 공통, 슬라이드 쇼 중에는 무시)
  - 그 밖에 사이드바 `AI 도우미`, 페이지 상단 `AI`, 노트에서 `Ctrl+J` 로도 열고 닫음 (열림 상태·너비는 브라우저에 기억)
- 넓은 화면(1101px 이상)은 열린 서랍 너비만큼 본문/편집기를 밀어 가리지 않음, 그보다 좁으면 본문 위에 겹침, 모바일은 전체 화면
- 구성: 상태 배지(현재 "미연결"), 새 대화, 추천 질문 4개, 참고 범위 칩(이 페이지 / 하위 페이지 포함), 입력창(Enter 전송, 한글 조합 중 Enter 무시)
- 응답 메시지 동작: 복사, 페이지에 추가(답변 마크다운을 페이지 끝에 블록으로), 다시 묻기
- 상태 배지: 초록 "연결됨" / 주황 "연결 안 됨"(설치·로그인 안내 표시). 배지를 누르면 다시 확인

### 실행 방식 원칙
- **작업 폴더 분리**: 앱 소스 폴더가 아닌 빈 전용 폴더(예: `instance/ai-workdir`)에서 실행 → 프로젝트의 CLAUDE.md·hooks·MCP 설정이 섞이지 않게
- **권한 최소화**: 1단계는 도구 없이 텍스트만 (`--permission-mode dontAsk`, `--permission-prompts none`, 필요한 도구만 `--allowedTools`), Bash·파일 쓰기·웹 접근 금지
- **입력 전달**: 페이지 원문은 stdin 으로 (최대 10MB), 지시는 `-p`, 역할은 `--append-system-prompt`
- **결과 반영**: AI 결과를 바로 덮어쓰지 않고 미리보기 후 사용자가 적용
- **대화 이어가기**: 결과 JSON 의 `session_id` 로 `--resume <id>` 후속 질문 (현재 페이지 최신 내용은 매번 다시 확인, 바뀌지 않았으면 생략)
- **취소**: 실행 중 취소 버튼 → SIGINT
- **동시 실행 1개**: 라즈베리파이 메모리 보호 + 구독 사용량 보호

### 기능 단계
| 단계 | 기능 | 도구 |
|---|---|---|
| 1 | 블록 메뉴: 요약 / 다듬기 / 번역 / 이어쓰기 / 표로 변환 / 할 일 추출 | 없음 (텍스트 입출력) |
| 2 | `/ai` 슬래시 명령으로 블록 생성, 페이지 옆 대화 패널 (페이지 내용 기반 Q&A) | 없음 + `--resume` |
| 3 | 워크스페이스 작업: "보관함 정리", "여러 페이지 요약 페이지 만들기" | MarkNote **MCP 서버**(검색/읽기/쓰기)를 `--mcp-config` 로 연결 |
| 4 | (선택) PC의 Claude Code 에서 MarkNote 접속 | 같은 MCP 서버 재사용 + 개인 접근 토큰 |

### 설정 화면 (관리 페이지)
- Claude Code 설치 여부 / 버전 표시
- 로그인 상태 표시 (만료 시 "서버에서 `claude` 실행 후 `/login` 하세요" 안내) — **로그인 자체는 앱에서 하지 않음**
- 페이지별 "AI 사용 안 함" 옵션 (민감한 페이지 보호)
- 기본 모델 선택 (가벼운 작업 / 일반 작업)

## 5. 개인정보·보안 체크리스트
- [ ] AI 실행은 버튼을 눌렀을 때만, 선택한 블록/페이지만 전송
- [ ] "AI 사용 안 함" 페이지는 서버에서도 차단 (프론트만 막지 않기)
- [ ] 비공개 표시(`{{ }}`, `:::비공개`)는 AI 로 가는 모든 길(페이지 내용·read_page·search_pages·선택 글자·덱)에서 서버가 가림, AI 수정으로 지워지거나 바뀌지 않음
- [ ] AI 엔드포인트는 MarkNote 로그인 필수 + CSRF
- [ ] Claude 프로세스는 권한 없는 전용 사용자·전용 폴더에서 실행, 도구 최소화
- [ ] 서버 환경변수에 `ANTHROPIC_API_KEY` 없음 확인 (있으면 API 과금으로 바뀜)
- [ ] 로그인 토큰/자격증명을 앱 DB·로그·화면에 절대 남기지 않음
- [ ] 실행 타임아웃, 최대 턴 수(`--max-turns`) 설정

## 6. 다중 사용자 확장 시 (나중)
- 사용자별 `CLAUDE_CONFIG_DIR` 로 자격증명 분리, 각자 **공식 흐름으로 직접 로그인**
- 한 사용자의 요청은 그 사용자의 자격증명으로만 실행 (교차 사용 금지)
- 서비스로 남에게 제공한다면 Commercial Terms 및 "Claude Code in products" 조건 재확인 필수

## 7. 참고 문서
- [Legal and compliance — Authentication and credential use](https://code.claude.com/docs/en/legal-and-compliance)
- [Authentication — 로그인, setup-token, 인증 우선순위](https://code.claude.com/docs/en/authentication)
- [Run Claude Code programmatically (`claude -p`)](https://code.claude.com/docs/en/headless)
- [Agent SDK overview — claude.ai 로그인 제공 금지 안내](https://code.claude.com/docs/en/agent-sdk/overview)
- [Advanced setup — 시스템 요구사항](https://code.claude.com/docs/en/setup)
