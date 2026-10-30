// AI 도우미 채팅 패널 — 서버(/api/ai)가 실행하는 Claude Code 와 대화
(() => {
  const panel = document.getElementById("chat-panel");
  if (!panel) return;

  const body = panel.querySelector(".chat-body");
  const messages = panel.querySelector(".chat-messages");
  const empty = panel.querySelector(".chat-empty");
  const setup = panel.querySelector(".chat-setup");
  const input = panel.querySelector(".chat-input");
  const sendBtn = panel.querySelector(".chat-send");
  const statusBtn = panel.querySelector(".chat-status");
  // 지금 화면의 페이지·프레젠테이션 — 새로고침 없이 화면이 바뀌면 app.js 가 data-* 를 바꿔 준다
  let pageId = null;
  let deckId = null;
  function readPanelContext() {
    pageId = panel.dataset.pageId ? Number(panel.dataset.pageId) : null;
    deckId = panel.dataset.deckId ? Number(panel.dataset.deckId) : null;
  }
  readPanelContext();

  const OPEN_KEY = "marknote.chatOpen";
  const WIDTH_KEY = "marknote.chatWidth";
  const MIN_WIDTH = 300;
  const MAX_WIDTH = 640;

  // 내 PC 폴더 화면(local.js): 물을 때만 지금 노트를 보내고(/api/ai/local-chat), 대화는 서버에 남기지 않는다 — 이 화면에만
  const localMode = Boolean(document.querySelector('.layout[data-mode="local"]'));
  const localHistory = []; // [{role, text}] 이어 묻기용 (이 화면을 떠나면 사라짐)
  let sessionId = null; // Claude Code 대화 ID (이어서 묻기용) — 서버 대화 파일에도 있음
  let conversationId = null; // 서버에 기록되는 대화 (instance/ai-chats/<id>.jsonl)
  let streaming = null; // {controller}
  let lastUserMessage = "";
  let statusChecked = false;

  const storage = {
    get(key) {
      try {
        return localStorage.getItem(key);
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, value);
      } catch {
        // 저장소 사용 불가
      }
    },
  };

  // ---------- 연결 상태 ----------
  async function checkStatus(refresh = false) {
    statusBtn.classList.remove("connected", "problem");
    statusBtn.querySelector(".chat-status-text").textContent = "확인 중…";
    let info;
    try {
      info = await api("GET", `/api/ai/status${refresh ? "?refresh=1" : ""}`);
    } catch (err) {
      info = { state: "error", message: err.message };
    }
    const ready = info.state === "ready";
    statusBtn.classList.add(ready ? "connected" : "problem");
    statusBtn.querySelector(".chat-status-text").textContent = ready ? "연결됨" : "연결 안 됨";
    statusBtn.title = ready
      ? `Claude Code ${info.version || ""} · ${info.auth_method === "oauth_token" ? "토큰" : "로그인"} 인증`
      : info.message;
    setup.hidden = ready;
    setup.querySelector(".chat-setup-message").textContent = info.message || "";
    statusChecked = true;
  }
  statusBtn.addEventListener("click", () => checkStatus(true));

  // ---------- 열기 / 닫기 (오른쪽 서랍) ----------
  const drawer = document.getElementById("chat-drawer");
  const isOpen = () => drawer.classList.contains("open");

  function setOpen(open) {
    drawer.classList.toggle("open", open);
    document.body.classList.toggle("chat-open", open);
    storage.set(OPEN_KEY, open ? "1" : "0");
    document.querySelectorAll("[data-chat-toggle]").forEach((b) => b.setAttribute("aria-pressed", String(open)));
    if (open) {
      input.focus({ preventScroll: true });
      if (!statusChecked) {
        checkStatus();
        loadUsage();
      }
    } else if (drawer.contains(document.activeElement)) {
      document.activeElement.blur();
    }
  }
  window.toggleChat = () => setOpen(!isOpen());

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-chat-toggle]")) setOpen(!isOpen());
  });
  panel.querySelector("[data-chat-close]").addEventListener("click", () => setOpen(false));
  // 새로고침 없이 화면이 바뀌면 새 본문의 AI 버튼 표시를 맞춘다 (app.js go)
  document.addEventListener("marknote:pageload", () =>
    document.querySelectorAll("[data-chat-toggle]").forEach((btn) => btn.setAttribute("aria-pressed", String(isOpen())))
  );
  document.addEventListener("keydown", (e) => {
    if (document.querySelector(".pp-show")) return; // 슬라이드 쇼 중에는 열지 않음
    if (e.key === "F2" && !e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey) {
      e.preventDefault();
      setOpen(!isOpen());
    } else if (e.key === "Escape" && isOpen() && panel.contains(document.activeElement)) {
      setOpen(false);
    }
  });

  // ---------- 너비 조절 (CSS 변수 --chat-w: 서랍 너비 = 넓은 화면에서 본문을 미는 폭) ----------
  const setWidth = (w) => document.documentElement.style.setProperty("--chat-w", `${Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, w))}px`);
  const savedWidth = Number(storage.get(WIDTH_KEY));
  if (savedWidth) setWidth(savedWidth);

  const handle = panel.querySelector(".chat-resize");
  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    panel.classList.add("resizing");
    drawer.classList.add("no-anim");
    document.body.classList.add("chat-resizing");
  });
  handle.addEventListener("pointermove", (e) => {
    if (!panel.classList.contains("resizing")) return;
    setWidth(window.innerWidth - e.clientX);
  });
  handle.addEventListener("pointerup", () => {
    panel.classList.remove("resizing");
    drawer.classList.remove("no-anim");
    document.body.classList.remove("chat-resizing");
    storage.set(WIDTH_KEY, String(panel.offsetWidth));
  });

  // ---------- 메시지 DOM ----------
  function scrollToBottom() {
    body.scrollTop = body.scrollHeight;
  }

  function addUserMessage(text) {
    empty.hidden = true;
    const msg = document.createElement("div");
    msg.className = "chat-msg user";
    msg.innerHTML = '<div class="chat-bubble"></div>';
    msg.querySelector(".chat-bubble").textContent = text;
    messages.appendChild(msg);
    scrollToBottom();
  }

  function addAssistantMessage() {
    const msg = document.createElement("div");
    msg.className = "chat-msg assistant";
    msg.innerHTML =
      '<div class="chat-msg-label">AI 도우미</div>' +
      '<div class="chat-bubble"><span class="chat-typing"><span></span><span></span><span></span></span></div>';
    messages.appendChild(msg);
    scrollToBottom();
    return msg;
  }

  function finishAssistantMessage(msg, { markdown, html }) {
    const bubble = msg.querySelector(".chat-bubble");
    bubble.classList.add("rendered");
    bubble.innerHTML = html || "";
    bubble.querySelectorAll("a[href^='http']").forEach((a) => {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    });
    bubble.querySelectorAll("input").forEach((cb) => (cb.disabled = true));
    msg.dataset.markdown = markdown;

    const canInsert = typeof window.editorAppend === "function";
    const actions = document.createElement("div");
    actions.className = "chat-msg-actions";
    actions.innerHTML =
      `<button type="button" data-msg-action="copy">${icon("copy")}<span>복사</span></button>` +
      `<button type="button" data-msg-action="insert" ${canInsert ? "" : 'disabled title="페이지를 열었을 때 사용할 수 있습니다"'}>${icon("insert")}<span>페이지에 추가</span></button>` +
      `<button type="button" data-msg-action="retry">${icon("refresh")}<span>다시 묻기</span></button>`;
    msg.appendChild(actions);
    scrollToBottom();
  }

  // 도구 사용 기록 ("노트 검색: …")을 아이콘과 함께 답변 위에 한 줄씩 표시
  function addToolLine(msg, label, iconName = "info") {
    let list = msg.querySelector(".chat-tools");
    if (!list) {
      list = document.createElement("div");
      list.className = "chat-tools";
      msg.insertBefore(list, msg.querySelector(".chat-bubble"));
    }
    const line = document.createElement("div");
    line.innerHTML = `${icon(iconName)}<span></span>`;
    line.lastChild.textContent = label;
    list.appendChild(line);
    scrollToBottom();
  }

  // ---------- 수정 제안 카드 ----------
  const PROPOSAL_STATUS = { applied: ["check", "적용됨"], rejected: ["x", "거절됨"] };

  function renderProposal(p) {
    const card = document.createElement("div");
    card.className = "chat-proposal";
    card.dataset.id = p.id;

    const head = document.createElement("div");
    head.className = "chat-proposal-head";
    head.innerHTML = `${icon(p.kind === "create" ? "file-plus" : "pencil")}<span></span>`;
    head.lastChild.textContent =
      p.kind === "create"
        ? `새 페이지 제안 · ${p.title}${p.parent_title ? ` (${p.parent_title} 아래)` : ""}`
        : `수정 제안 · ${p.page_title ?? "#" + p.page_id}`;
    card.appendChild(head);

    if (p.summary) {
      const summary = document.createElement("p");
      summary.className = "chat-proposal-summary";
      summary.textContent = p.summary;
      card.appendChild(summary);
    }
    if (p.kind === "update" && p.title !== null && p.title !== p.old_title) {
      const rename = document.createElement("p");
      rename.className = "chat-proposal-summary";
      rename.textContent = `제목: ${p.old_title || "제목 없음"} → ${p.title}`;
      card.appendChild(rename);
    }

    if (p.diff.length) {
      const diff = document.createElement("pre");
      diff.className = "chat-diff";
      p.diff.forEach((line) => {
        const row = document.createElement("span");
        row.className = `diff-${line.t}`;
        const mark = { add: "+ ", del: "- ", same: "  ", hunk: "" }[line.t];
        row.textContent = `${mark}${line.s}\n`;
        diff.appendChild(row);
      });
      card.appendChild(diff);
    }

    const actions = document.createElement("div");
    actions.className = "chat-card-actions";
    card.appendChild(actions);
    renderProposalActions(card, p);
    return card;
  }

  function renderProposalActions(card, p, note = "") {
    const actions = card.querySelector(".chat-card-actions");
    actions.replaceChildren();
    card.classList.toggle("done", p.status !== "pending");

    if (note) {
      const warn = document.createElement("p");
      warn.className = "chat-proposal-warn";
      warn.textContent = note;
      actions.appendChild(warn);
    }
    if (p.status === "pending") {
      actions.append(
        cardButton(note ? "그래도 적용" : "적용", "primary", () => decideProposal(card, p, "apply", Boolean(note))),
        cardButton("거절", "ghost", () => decideProposal(card, p, "reject"))
      );
    } else {
      const label = document.createElement("span");
      label.className = `chat-proposal-status ${p.status}`;
      const [statusIcon, statusText] = PROPOSAL_STATUS[p.status] || ["info", p.status];
      label.innerHTML = `${icon(statusIcon)}<span>${statusText}</span>`;
      actions.appendChild(label);
    }
    if (p.page_id && p.status !== "rejected" && p.page_id !== window.editorPageId) {
      const link = document.createElement("a");
      link.className = "ghost";
      link.href = `/p/${p.page_id}`;
      link.innerHTML = `${icon("open")}<span>페이지 열기</span>`;
      actions.appendChild(link);
    }
  }

  async function decideProposal(card, p, action, force = false) {
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    const response = await fetch(`/api/ai/proposals/${p.id}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify({ force }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 409 && data.conflict) {
      renderProposalActions(card, p, data.error); // 그사이 페이지가 바뀜 → 그래도 적용할지 묻기
      return;
    }
    if (!response.ok) {
      renderProposalActions(card, p, data.error || `요청 실패 (${response.status})`);
      return;
    }
    renderProposalActions(card, data.proposal);
    // 지금 보고 있는 페이지가 바뀌었으면 편집기를 다시 불러온다
    if (action === "apply" && data.page_id === window.editorPageId && window.editorReload) {
      await window.editorReload();
    }
  }

  // ---------- 노트 수정 카드 (AI 가 바로 적용 → 되돌리기 | 확정) ----------
  const NOTE_EDIT_STATUS = { applied: ["check", "적용됨"], confirmed: ["check", "확정됨"], undone: ["undo", "되돌림"] };
  const NOTE_EDIT_HEAD = {
    create: ["file-plus", "새 노트 만듦"],
    update: ["pencil", "노트 수정함"],
    trash: ["trash", "휴지통으로 옮김"], // 영구 삭제 아님 — 되돌리기 = 원래 자리로 복원
  };

  // AI 가 휴지통으로 옮긴 노트·폴더: 탐색기 목록을 다시 받고, 지금 보는 페이지가 그 안이면 휴지통 화면으로
  function applyTrashEdit(edit) {
    const current = window.editorPageId ?? JSON.parse(document.getElementById("page-data")?.textContent || "{}").page?.id;
    const ex = window.explorer;
    const affected = current === edit.page_id || Boolean(ex?.isInside(String(current), String(edit.page_id)));
    if (affected) window.go(`/p/${current}`);
    else ex?.refresh().catch(console.error);
  }

  function renderNoteEdit(edit) {
    const card = document.createElement("div");
    card.className = "chat-proposal";
    card.dataset.noteEdit = edit.id;
    const head = document.createElement("div");
    head.className = "chat-proposal-head";
    const [headIcon, headText] = NOTE_EDIT_HEAD[edit.kind] || NOTE_EDIT_HEAD.update;
    head.innerHTML = `${icon(headIcon)}<span></span>`;
    head.lastChild.textContent = `${headText} · ${edit.page_title}`;
    card.appendChild(head);
    if (edit.kind === "trash") {
      const where = document.createElement("p");
      where.className = "chat-proposal-summary";
      where.textContent = `${edit.file}${edit.inside_notes ? ` (안의 노트 ${edit.inside_notes}개 포함)` : ""} — 영구 삭제 아님, 되돌리면 원래 자리로`;
      card.appendChild(where);
    }
    if (edit.summary) {
      const summary = document.createElement("p");
      summary.className = "chat-proposal-summary";
      summary.textContent = edit.summary;
      card.appendChild(summary);
    }
    if (edit.title_change) {
      const rename = document.createElement("p");
      rename.className = "chat-proposal-summary";
      rename.textContent = `제목: ${edit.title_change[0] || "제목 없음"} → ${edit.title_change[1]}`;
      card.appendChild(rename);
    }
    if (edit.diff.length) {
      const diff = document.createElement("pre");
      diff.className = "chat-diff";
      edit.diff.forEach((line) => {
        const row = document.createElement("span");
        row.className = `diff-${line.t}`;
        row.textContent = `${{ add: "+ ", del: "- ", same: "  ", hunk: "" }[line.t]}${line.s}\n`;
        diff.appendChild(row);
      });
      card.appendChild(diff);
    }
    const actions = document.createElement("div");
    actions.className = "chat-card-actions";
    card.appendChild(actions);
    renderNoteEditActions(card, edit);
    return card;
  }

  function renderNoteEditActions(card, edit, note = "") {
    const actions = card.querySelector(".chat-card-actions");
    actions.replaceChildren();
    card.classList.toggle("done", edit.status !== "applied");
    if (note) {
      const warn = document.createElement("p");
      warn.className = "chat-proposal-warn";
      warn.textContent = note;
      actions.appendChild(warn);
    }
    const label = document.createElement("span");
    label.className = `chat-proposal-status ${edit.status === "undone" ? "rejected" : edit.status}`;
    const [statusIcon, statusText] = NOTE_EDIT_STATUS[edit.status] || ["info", edit.status];
    label.innerHTML = `${icon(statusIcon)}<span>${statusText}</span>`;
    actions.appendChild(label);
    if (edit.status === "applied") {
      actions.append(
        cardButton(note ? "그래도 되돌리기" : "되돌리기", "ghost", () => decideNoteEdit(card, edit, "undo", Boolean(note))),
        cardButton("확정", "primary", () => decideNoteEdit(card, edit, "confirm"))
      );
    }
    if (edit.page_alive && edit.page_id !== window.editorPageId) {
      const link = document.createElement("a");
      link.className = "ghost";
      link.href = `/p/${edit.page_id}`;
      link.innerHTML = `${icon("open")}<span>열기</span>`;
      actions.appendChild(link);
    }
  }

  async function decideNoteEdit(card, edit, action, force = false) {
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    const here = edit.page_id === window.editorPageId;
    if (here) await window.flushEditor?.();
    const response = await fetch(`/api/ai/note-edits/${edit.id}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify({ force }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      renderNoteEditActions(card, edit, data.error || `요청 실패 (${response.status})`);
      return;
    }
    renderNoteEditActions(card, data.edit);
    if (action === "undo" && edit.kind === "trash") {
      // 원래 자리로 복원됨 → 사이드바·화면을 새 상태로
      if (window.editorPageId === edit.page_id || !window.explorer) window.go(`/p/${edit.page_id}`);
      else window.reloadPage();
      return;
    }
    if (!here) return;
    if (action === "undo" && edit.kind === "create") window.go("/");
    else if (action === "undo") await window.editorReload?.();
    else window.refreshAiEdits?.();
  }

  // 페이지의 [되돌리기 | 확정]·Ctrl+Z·Ctrl+S 로 바뀌면 채팅 카드도 맞춘다
  document.addEventListener("note-edit-changed", (e) => {
    messages.querySelectorAll(`[data-note-edit="${e.detail.id}"]`).forEach((card) => renderNoteEditActions(card, e.detail));
  });

  // ---------- 프레젠테이션 수정 카드 (AI 가 바로 적용 → 되돌리기) ----------
  function renderDeckEdit(edit) {
    const card = document.createElement("div");
    card.className = "chat-proposal";
    card.dataset.deckEdit = edit.id;
    const head = document.createElement("div");
    head.className = "chat-proposal-head";
    head.innerHTML = `${icon("presentation")}<span></span>`;
    head.lastChild.textContent = `슬라이드 수정함 · ${edit.deck_title ?? "#" + edit.deck_id}`;
    card.appendChild(head);
    const summary = document.createElement("p");
    summary.className = "chat-proposal-summary";
    summary.textContent = `${edit.summary || "프레젠테이션을 수정했습니다."} (작업 ${edit.op_count}개)`;
    card.appendChild(summary);
    const actions = document.createElement("div");
    actions.className = "chat-card-actions";
    card.appendChild(actions);
    renderDeckEditActions(card, edit);
    return card;
  }

  function renderDeckEditActions(card, edit, note = "") {
    const actions = card.querySelector(".chat-card-actions");
    actions.replaceChildren();
    card.classList.toggle("done", edit.status !== "applied");
    if (note) {
      const warn = document.createElement("p");
      warn.className = "chat-proposal-warn";
      warn.textContent = note;
      actions.appendChild(warn);
    }
    const label = document.createElement("span");
    label.className = `chat-proposal-status ${edit.status === "applied" ? "applied" : "rejected"}`;
    label.innerHTML = edit.status === "applied" ? `${icon("check")}<span>적용됨</span>` : `${icon("undo")}<span>되돌림</span>`;
    actions.appendChild(label);
    if (edit.status === "applied") {
      actions.appendChild(cardButton(note ? "그래도 되돌리기" : "되돌리기", "ghost", () => undoDeckEdit(card, edit, Boolean(note))));
    }
    if (edit.deck_id !== window.editorDeckId) {
      const link = document.createElement("a");
      link.className = "ghost";
      link.href = `/d/${edit.deck_id}`;
      link.innerHTML = `${icon("open")}<span>열기</span>`;
      actions.appendChild(link);
    }
  }

  async function undoDeckEdit(card, edit, force = false) {
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    await window.saveEditorNow?.();
    const response = await fetch(`/api/ai/deck-edits/${edit.id}/undo`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify({ force }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      renderDeckEditActions(card, edit, data.error || `요청 실패 (${response.status})`);
      return;
    }
    renderDeckEditActions(card, data.edit);
    if (edit.deck_id === window.editorDeckId) await window.deckReload?.();
  }

  function failAssistantMessage(msg, message) {
    msg.classList.add("error");
    msg.querySelector(".chat-msg-label").textContent = "오류";
    msg.querySelector(".chat-bubble").textContent = message;
    scrollToBottom();
  }

  // ---------- 보내기 (스트리밍) ----------
  function setStreaming(state) {
    streaming = state;
    sendBtn.classList.toggle("stop", Boolean(state));
    sendBtn.innerHTML = icon(state ? "stop" : "arrow-up");
    sendBtn.title = state ? "중단" : "보내기";
    updateSendState();
  }

  function updateSendState() {
    sendBtn.disabled = !streaming && !input.value.trim();
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${input.scrollHeight}px`;
  }

  function contextOptions() {
    const pressed = (ctx) => panel.querySelector(`.ctx-chip[data-ctx="${ctx}"]`)?.getAttribute("aria-pressed") === "true";
    const usePage = pageId && pressed("page");
    const location = window.getEditorLocation?.() || { view: document.querySelector(".content")?.dataset.view || "" };
    return {
      page_id: usePage ? pageId : null,
      deck_id: deckId && pressed("deck") ? deckId : null,
      location: { ...location, selection: chatSelection },
    };
  }

  // ---------- 선택한 글자 ("이 부분") ----------
  // 편집기가 드래그로 선택한 글자를 알려 주면 입력창 위에 보여 주고, 다음 질문에 글자 그대로 함께 보낸다
  let chatSelection = "";
  const selectionBox = panel.querySelector(".chat-selection");
  window.setChatSelection = (text) => {
    chatSelection = (text || "").trim();
    selectionBox.hidden = !chatSelection;
    const shown = chatSelection.replace(/\s+/g, " ");
    selectionBox.querySelector(".chat-selection-text").textContent = `선택: “${shown.length > 60 ? `${shown.slice(0, 60)}…` : shown}”`;
    selectionBox.title = chatSelection;
  };
  panel.querySelector(".chat-selection-clear").addEventListener("click", () => window.setChatSelection(""));

  // SSE("data: {...}\n\n") 응답을 읽어 이벤트마다 onEvent 호출
  async function readEvents(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (line) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }

  // 내 메시지 아래에 "선택한 글자와 함께 보냄" 표시
  function addSelectionNote(assistantMsg, selection) {
    addSelectionNoteTo(assistantMsg.previousElementSibling, selection);
  }
  function addSelectionNoteTo(userMsg, selection) {
    if (!userMsg?.classList.contains("user")) return;
    const note = document.createElement("div");
    note.className = "chat-msg-selection";
    const shown = selection.replace(/\s+/g, " ");
    note.innerHTML = `${icon("text-box")}<span></span>`;
    note.lastChild.textContent = `선택한 글자: “${shown.length > 80 ? `${shown.slice(0, 80)}…` : shown}”`;
    note.title = selection;
    userMsg.appendChild(note);
  }

  async function send(text) {
    text = (text ?? input.value).trim();
    if (!text || streaming) return;
    if (text.startsWith("/")) {
      runCommand(text);
      return;
    }
    if (!(await checkSessionLimit())) return;
    lastUserMessage = text;
    addUserMessage(text);
    input.value = "";
    storage.set(DRAFT_KEY, "");
    autosize();

    const msg = addAssistantMessage();
    const controller = new AbortController();
    setStreaming({ controller });
    const handler = streamHandler(msg);
    try {
      // 입력 중이던 내용까지 먼저 저장 → 서버가 최신 페이지를 읽어 함께 보냄 (AI 의 노트 읽기 도구도 최신을 봄)
      try {
        await window.saveEditorNow?.();
      } catch (err) {
        console.warn("질문 전 저장 실패", err);
      }
      const options = contextOptions();
      if (options.location.selection) addSelectionNote(msg, options.location.selection);
      window.setChatSelection(""); // 선택한 글자는 이번 질문에만
      let url = "/api/ai/chat";
      let payload = { message: text, conversation_id: conversationId, ...options };
      if (localMode) {
        const note = window.localNoteForAI?.() || {};
        url = "/api/ai/local-chat";
        payload = { message: text, title: note.title, markdown: note.markdown, selection: window.maskSecrets(options.location.selection || ""), history: localHistory.slice(-10) };
        localHistory.push({ role: "user", text });
      }
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error((data && data.error) || `요청 실패 (${response.status})`);
      }
      await readEvents(response, handler.onEvent);
      if (!handler.finished) failAssistantMessage(msg, "응답이 중간에 끊겼습니다.");
    } catch (err) {
      if (leaving) return; // 다른 페이지로 이동 중 — 답변은 서버에서 계속되고 새 페이지가 이어 받음
      if (err.name === "AbortError") failAssistantMessage(msg, "중단했습니다.");
      else failAssistantMessage(msg, err.message);
    } finally {
      if (!leaving) {
        setStreaming(null);
        input.focus();
      }
    }
  }

  // 답변 스트림의 이벤트를 메시지 하나에 그린다 (새 질문·이어 받기 공용)
  function streamHandler(msg) {
    const bubble = msg.querySelector(".chat-bubble");
    let streamed = "";
    const state = {
      finished: false,
      onEvent(event) {
        if (event.type === "run") {
          if (event.conversation_id) conversationId = event.conversation_id;
        } else if (event.type === "session" && event.session_id) {
          sessionId = event.session_id;
          if (event.model_label) {
            liveModel = event.model_label;
            renderMeta();
          }
        } else if (event.type === "delta") {
          streamed += event.text;
          bubble.textContent = streamed;
          scrollToBottom();
        } else if (event.type === "tool") {
          addToolLine(msg, event.label, event.icon);
        } else if (event.type === "result") {
          state.finished = true;
          if (event.session_id) sessionId = event.session_id;
          applyUsage(event.usage);
          if (localMode && event.ok) {
            finishLocalAnswer(msg, event);
            return;
          }
          if (event.ok) finishAssistantMessage(msg, { markdown: event.text, html: event.html });
          else failAssistantMessage(msg, event.text || "Claude Code가 오류를 반환했습니다.");
          (event.proposals || []).forEach((p) => {
            msg.appendChild(renderProposal(p));
            scrollToBottom();
          });
          const deckEdits = event.deck_edits || [];
          deckEdits.forEach((edit) => {
            msg.appendChild(renderDeckEdit(edit));
            scrollToBottom();
          });
          if (event.focus) renderFocus(event.focus);
          const noteEdits = event.note_edits || [];
          noteEdits.forEach((edit) => {
            msg.appendChild(renderNoteEdit(edit));
            scrollToBottom();
          });
          noteEdits.filter((edit) => edit.kind === "trash" && edit.status === "applied").forEach(applyTrashEdit);
          // 지금 열린 노트가 바뀌었으면 다시 불러와 고친 부분을 강조
          if (noteEdits.some((edit) => edit.page_id === window.editorPageId)) window.editorReload?.();
          // 지금 열린 프레젠테이션이 바뀌었으면 편집기에 바로 반영
          if (deckEdits.some((edit) => edit.deck_id === window.editorDeckId)) window.deckReload?.();
        } else if (event.type === "error") {
          state.finished = true;
          failAssistantMessage(msg, event.message);
        }
      },
    };
    return state;
  }

  // 내 PC 노트: 답에 고친 노트(<marknote-note>)가 있으면 PC 파일에 반영하고 [되돌리기] 카드
  async function finishLocalAnswer(msg, event) {
    localHistory.push({ role: "assistant", text: event.text.replace(/<marknote-note>[\s\S]*?<\/marknote-note>/, "(노트를 고침)") });
    let applied = null;
    let problem = "";
    try {
      applied = await window.localApplyAi?.(event.text);
    } catch (err) {
      problem = err.message;
    }
    if (!applied) {
      finishAssistantMessage(msg, { markdown: event.text, html: event.html });
      if (problem) msg.appendChild(addSystemNote(problem));
      return;
    }
    const tmp = document.createElement("div");
    tmp.textContent = applied.text;
    finishAssistantMessage(msg, { markdown: applied.text, html: `<p>${tmp.innerHTML.replace(/\n/g, "<br>")}</p>` });
    const card = document.createElement("div");
    card.className = "chat-edit-card";
    const label = document.createElement("span");
    label.innerHTML = `${icon("pencil")}<span>내 PC 노트를 고쳤습니다 (서버에는 저장 안 함)</span>`;
    const btn = cardButton("되돌리기", "ghost", async () => {
      btn.disabled = true;
      try {
        if (btn.dataset.state === "undone") {
          await applied.redo();
          btn.dataset.state = "";
          btn.lastChild.textContent = "되돌리기";
        } else {
          await applied.undo();
          btn.dataset.state = "undone";
          btn.lastChild.textContent = "다시 적용";
        }
      } finally {
        btn.disabled = false;
      }
    });
    card.append(label, btn);
    msg.appendChild(card);
    scrollToBottom();
  }
  function addSystemNote(text) {
    const note = document.createElement("p");
    note.className = "chat-msg-selection";
    note.innerHTML = `${icon("alert")}<span></span>`;
    note.lastChild.textContent = text;
    return note;
  }

  // ---------- 대화 복원 (페이지를 옮겨도 서버 파일에서 다시 불러옴) ----------
  let leaving = false;
  window.addEventListener("pagehide", () => (leaving = true));

  function renderSaved(m) {
    if (m.role === "user") {
      addUserMessage(m.text);
      if (m.selection) addSelectionNoteTo(messages.lastElementChild, m.selection);
      lastUserMessage = m.text;
      return;
    }
    const msg = addAssistantMessage();
    m.tools.forEach((t) => addToolLine(msg, t.label, t.icon));
    if (m.ok) finishAssistantMessage(msg, { markdown: m.text, html: m.html });
    else failAssistantMessage(msg, m.error || m.text || "응답이 완료되지 않았습니다.");
    m.proposals.forEach((p) => msg.appendChild(renderProposal(p)));
    m.deck_edits.forEach((edit) => msg.appendChild(renderDeckEdit(edit)));
    (m.note_edits || []).forEach((edit) => msg.appendChild(renderNoteEdit(edit)));
  }

  // ---------- 다루는 노트 ("OO폴더의 OO노트" 로 부르거나 AI 가 읽고 고친 노트) ----------
  const focusBar = panel.querySelector(".chat-focus");
  function renderFocus(list = []) {
    focusBar.replaceChildren();
    focusBar.hidden = !list.length;
    if (!list.length) return;
    const label = document.createElement("span");
    label.className = "muted";
    label.textContent = "다루는 노트";
    focusBar.appendChild(label);
    list.forEach((note) => {
      const chip = document.createElement("span");
      chip.className = "ctx-chip chat-focus-chip";
      chip.title = `${note.path} — 질문할 때마다 최신 내용을 함께 보냄. "거기에 추가해줘"는 맨 앞 노트를 가리킴`;
      chip.innerHTML = `${icon("file")}<a></a><button type="button" aria-label="다루는 노트에서 빼기" title="빼기">${icon("x")}</button>`;
      const link = chip.querySelector("a");
      link.href = `/p/${note.id}`;
      link.textContent = note.title;
      chip.querySelector("button").addEventListener("click", async () => {
        const res = await api("POST", `/api/ai/conversations/${conversationId}/focus/remove`, { page_id: note.id });
        renderFocus(res.focus);
      });
      focusBar.appendChild(chip);
    });
  }

  function showConversation(conv) {
    conversationId = conv.id;
    sessionId = conv.session_id;
    renderFocus(conv.focus);
    messages.replaceChildren();
    conv.messages.forEach(renderSaved);
    empty.hidden = conv.messages.length > 0 || Boolean(conv.running);
    scrollToBottom();
  }

  // 답변 중에 페이지를 옮겼으면: 서버에서 계속 중인 답을 처음부터 다시 받아 이어 그림
  async function attach(runId) {
    if (streaming) return;
    const msg = addAssistantMessage();
    const controller = new AbortController();
    setStreaming({ controller, runId });
    const handler = streamHandler(msg);
    try {
      const response = await fetch(`/api/ai/runs/${runId}/events?after=0`, { signal: controller.signal });
      if (!response.ok) throw new Error("답변을 이어 받지 못했습니다.");
      await readEvents(response, handler.onEvent);
      if (!handler.finished) failAssistantMessage(msg, "응답이 중간에 끊겼습니다.");
    } catch (err) {
      if (leaving) return;
      if (err.name === "AbortError") failAssistantMessage(msg, "중단했습니다.");
      else failAssistantMessage(msg, err.message);
    } finally {
      if (!leaving) setStreaming(null);
    }
  }

  async function restoreConversation() {
    try {
      const conv = await api("GET", "/api/ai/conversation");
      showConversation(conv);
      if (conv.running) attach(conv.running.run_id);
    } catch (err) {
      console.warn("대화 기록을 불러오지 못했습니다", err);
    }
  }

  // 입력하다 만 글도 페이지를 옮겨도 남김
  const DRAFT_KEY = "marknote.chatDraft";
  input.addEventListener("input", () => storage.set(DRAFT_KEY, input.value));
  const draft = storage.get(DRAFT_KEY);
  if (draft && !input.value) {
    input.value = draft;
    autosize();
    updateSendState();
  }

  async function stop() {
    if (!streaming) return;
    try {
      await api("POST", "/api/ai/cancel");
    } catch {
      // 이미 끝난 경우
    }
    streaming?.controller.abort();
  }

  // ---------- / 명령어 ----------
  const COMMANDS = [
    { name: "/login", desc: "Claude 계정 연결 (서버와 같은 컴퓨터에서만)" },
    { name: "/logout", desc: "Claude 계정 연결 해제" },
    { name: "/status", desc: "연결 상태 보기" },
    { name: "/resume", desc: "이전 대화 목록에서 골라 이어 가기 (/resume 검색어)" },
    { name: "/rename", desc: "지금 대화 이름 바꾸기 (/rename 새 이름)" },
    { name: "/new", desc: "새 대화 시작" },
  ];
  const commandMenu = panel.querySelector(".chat-commands");
  let commandMatches = [];
  let commandIndex = 0;

  function updateCommandMenu() {
    const value = input.value;
    commandMatches = /^\/\S*$/.test(value) ? COMMANDS.filter((c) => c.name.startsWith(value)) : [];
    commandMenu.hidden = commandMatches.length === 0;
    commandIndex = Math.min(commandIndex, Math.max(commandMatches.length - 1, 0));
    commandMenu.replaceChildren(
      ...commandMatches.map((c, i) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = `chat-command${i === commandIndex ? " active" : ""}`;
        item.setAttribute("role", "option");
        item.innerHTML = '<span class="chat-command-name"></span><span class="chat-command-desc"></span>';
        item.firstChild.textContent = c.name;
        item.lastChild.textContent = c.desc;
        item.addEventListener("mousedown", (e) => {
          e.preventDefault(); // 입력창 포커스 유지
          chooseCommand(i);
        });
        return item;
      })
    );
  }

  function chooseCommand(index) {
    const command = commandMatches[index];
    if (!command) return;
    input.value = "";
    autosize();
    updateCommandMenu();
    updateSendState();
    runCommand(command.name);
  }

  function addSystemCard(title, iconName = "info") {
    empty.hidden = true;
    const msg = document.createElement("div");
    msg.className = "chat-msg system";
    msg.innerHTML = '<div class="chat-card"><div class="chat-card-title"></div><div class="chat-card-body"></div></div>';
    msg.querySelector(".chat-card-title").innerHTML = `${icon(iconName)}<span></span>`;
    msg.querySelector(".chat-card-title span").textContent = title;
    messages.appendChild(msg);
    scrollToBottom();
    return msg.querySelector(".chat-card");
  }

  // 카드 내용 갱신. text 안의 `코드` 는 <code> 로 표시
  function setCard(card, { tone = "", text = "", actions = [] }) {
    card.className = `chat-card ${tone}`.trim();
    const body = card.querySelector(".chat-card-body");
    const p = document.createElement("p");
    text.split(/(`[^`]+`)/).forEach((part) => {
      if (part.startsWith("`") && part.endsWith("`") && part.length > 1) {
        const code = document.createElement("code");
        code.textContent = part.slice(1, -1);
        p.appendChild(code);
      } else {
        p.appendChild(document.createTextNode(part));
      }
    });
    body.replaceChildren(p);
    if (actions.length) {
      const row = document.createElement("div");
      row.className = "chat-card-actions";
      row.append(...actions);
      body.appendChild(row);
    }
    scrollToBottom();
  }

  function cardButton(label, className, onClick) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = className;
    btn.textContent = label;
    btn.addEventListener("click", onClick);
    return btn;
  }

  async function runCommand(text) {
    const name = text.split(/\s/)[0].toLowerCase();
    const arg = text.slice(name.length).trim();
    addUserMessage(text.trim());
    input.value = "";
    storage.set(DRAFT_KEY, "");
    autosize();
    updateSendState();
    try {
      if (name === "/login") await runLogin();
      else if (name === "/logout") await runLogout();
      else if (name === "/status") await runStatus();
      else if (name === "/resume") await runResume(arg);
      else if (name === "/rename") await runRename(arg);
      else if (name === "/new") panel.querySelector("[data-chat-new]").click();
      else setCard(addSystemCard("알 수 없는 명령어", "alert"), { tone: "warn", text: `사용할 수 있는 명령어: ${COMMANDS.map((c) => c.name).join(", ")}` });
    } catch (err) {
      setCard(addSystemCard("오류", "alert"), { tone: "fail", text: err.message });
    }
  }

  // ---------- /resume: 저장된 대화 목록에서 골라 이어 가기 ----------
  function whenText(iso) {
    if (!iso) return "";
    const at = new Date(iso);
    const today = new Date();
    const yesterday = new Date(today.getTime() - 86400000);
    const time = at.toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
    if (at.toDateString() === today.toDateString()) return `오늘 ${time}`;
    if (at.toDateString() === yesterday.toDateString()) return `어제 ${time}`;
    return at.toLocaleDateString("ko-KR", { month: "long", day: "numeric" });
  }

  async function runResume(query = "") {
    const card = addSystemCard(query ? `이전 대화 — “${query}” 검색` : "이전 대화", "message");
    const data = await api("GET", `/api/ai/conversations${query ? `?q=${encodeURIComponent(query)}` : ""}`);
    const list = data.conversations.filter((c) => c.messages > 0 || c.current);
    if (!list.length) {
      setCard(card, { tone: "warn", text: query ? "찾는 대화가 없습니다." : "저장된 대화가 없습니다." });
      return;
    }
    setCard(card, { text: "이어 갈 대화를 고르세요. 대화는 질문할 때마다 자동으로 저장됩니다." });
    const rows = document.createElement("div");
    rows.className = "chat-conv-list";
    list.forEach((c) => {
      const row = document.createElement("div");
      row.className = `chat-conv${c.current ? " current" : ""}`;
      const open = document.createElement("button");
      open.type = "button";
      open.className = "chat-conv-open";
      open.innerHTML = '<span class="chat-conv-title"></span><span class="chat-conv-meta"></span>';
      open.querySelector(".chat-conv-title").textContent = c.title;
      open.querySelector(".chat-conv-meta").textContent = [
        c.current ? "지금 대화" : whenText(c.updated_at),
        `메시지 ${c.messages}`,
        c.where,
      ].filter(Boolean).join(" · ");
      open.addEventListener("click", () => resumeConversation(c));
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "chat-conv-act";
      edit.title = "이름 바꾸기";
      edit.setAttribute("aria-label", `${c.title} 이름 바꾸기`);
      edit.innerHTML = icon("pencil");
      edit.addEventListener("click", () => renameRow(row, c));
      const del = document.createElement("button");
      del.type = "button";
      del.className = "chat-conv-act chat-conv-del";
      del.title = "대화 삭제 (서버의 trash 폴더로 옮김)";
      del.setAttribute("aria-label", `${c.title} 삭제`);
      del.innerHTML = icon("trash");
      del.addEventListener("click", async () => {
        if (!confirm(`“${c.title}” 대화를 삭제할까요?`)) return;
        if (c.current && streaming) {
          alert("답변이 끝난 뒤에 지우세요.");
          return;
        }
        const res = await api("DELETE", `/api/ai/conversations/${c.id}`);
        row.remove();
        if (c.current) {
          // 지금 대화를 지웠으면 서버가 새 대화를 만들어 둠 — 목록 카드는 남겨 둔다
          conversationId = res.current;
          sessionId = null;
          conversation = null;
          renderMeta();
        }
      });
      row.append(open, edit, del);
      rows.appendChild(row);
    });
    card.querySelector(".chat-card-body").appendChild(rows);
    rows.querySelector(".chat-conv-open")?.focus({ preventScroll: true });
    scrollToBottom();
  }

  // 목록에서 바로 이름 바꾸기: 제목 자리에 입력칸 (Enter 저장 · Esc 취소)
  function renameRow(row, c) {
    if (row.querySelector(".chat-conv-input")) return;
    const titleEl = row.querySelector(".chat-conv-title");
    const field = document.createElement("input");
    field.className = "chat-conv-input";
    field.value = c.title;
    field.maxLength = 80;
    field.setAttribute("aria-label", "새 대화 이름");
    titleEl.hidden = true;
    row.querySelector(".chat-conv-open").before(field);
    row.querySelector(".chat-conv-open").hidden = true;
    let done = false;
    const close = () => {
      done = true;
      field.remove();
      titleEl.hidden = false;
      row.querySelector(".chat-conv-open").hidden = false;
    };
    const save = async () => {
      if (done) return;
      const title = field.value.trim();
      if (!title || title === c.title) return close();
      try {
        c.title = (await api("POST", `/api/ai/conversations/${c.id}/rename`, { title })).title;
        titleEl.textContent = c.title;
      } catch (err) {
        alert(err.message);
      }
      close();
    };
    field.addEventListener("keydown", (e) => {
      e.stopPropagation(); // Esc 가 패널을 닫지 않게
      if (e.key === "Enter") { e.preventDefault(); save(); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });
    field.addEventListener("blur", save);
    field.focus();
    field.select();
  }

  async function resumeConversation(c) {
    if (streaming) {
      alert("답변이 끝난 뒤에 다른 대화로 바꾸세요.");
      return;
    }
    const conv = await api("POST", `/api/ai/conversations/${c.id}/resume`);
    showConversation(conv);
    conversation = null;
    liveModel = "";
    renderMeta();
    const note = addSystemCard(`“${conv.title}” 대화를 이어 갑니다`, "message");
    setCard(note, { tone: "ok", text: "이전 맥락을 기억한 채로 이어서 질문하면 됩니다." });
    input.focus();
  }

  async function runRename(title) {
    if (!title) {
      setCard(addSystemCard("대화 이름 바꾸기", "pencil"), { tone: "warn", text: "`/rename 새 이름` 처럼 이름을 함께 입력하세요." });
      return;
    }
    const res = await api("POST", `/api/ai/conversations/${conversationId}/rename`, { title });
    setCard(addSystemCard("대화 이름을 바꿨습니다", "pencil"), { tone: "ok", text: res.title });
  }

  const LOGIN_TONES = { done: "ok", already: "ok", waiting: "", starting: "", remote: "warn" };

  async function runLogin() {
    const card = addSystemCard("Claude 계정 연결", "key");
    setCard(card, { text: "로그인을 준비하고 있습니다…" });
    let state = await api("POST", "/api/ai/login");

    for (;;) {
      const actions = [];
      if (state.state === "waiting" && state.url?.startsWith("https://")) {
        const link = document.createElement("a");
        link.className = "primary";
        link.href = state.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.innerHTML = `${icon("external")}<span>Anthropic 로그인 페이지 열기</span>`;
        actions.push(link);
      }
      if (state.state === "waiting" || state.state === "starting") {
        actions.push(
          cardButton("취소", "ghost", async () => {
            state = await api("POST", "/api/ai/login/cancel");
          })
        );
      }
      setCard(card, { tone: LOGIN_TONES[state.state] ?? "fail", text: state.message || "", actions });

      if (state.state !== "waiting" && state.state !== "starting") break;
      await new Promise((r) => setTimeout(r, 1500));
      if (state.state === "cancelled") continue; // 취소 버튼이 바꾼 상태를 그대로 표시
      state = await api("GET", "/api/ai/login");
    }
    if (state.state === "done") checkStatus(true);
  }

  async function runLogout() {
    const card = addSystemCard("Claude 계정 연결 해제", "unlock");
    setCard(card, { text: "연결을 해제하고 있습니다…" });
    const res = await api("POST", "/api/ai/logout");
    setCard(card, { tone: res.ok ? (res.status?.state === "ready" ? "warn" : "ok") : "fail", text: res.message });
    sessionId = null; // 다른 계정의 대화는 이어갈 수 없음
    checkStatus(true);
  }

  const STATE_LABELS = {
    ready: "연결됨",
    not_installed: "설치 안 됨",
    logged_out: "로그인 안 됨",
    api_billing: "API 키로 인증됨 (사용 불가)",
    error: "오류",
  };
  const AUTH_LABELS = { "claude.ai": "구독 계정 로그인", oauth_token: ".env 토큰 (CLAUDE_CODE_OAUTH_TOKEN)" };

  async function runStatus() {
    const card = addSystemCard("연결 상태", "info");
    const info = await api("GET", "/api/ai/status?refresh=1");
    setCard(card, { tone: info.state === "ready" ? "ok" : "warn", text: info.message || "" });
    const dl = document.createElement("dl");
    [
      ["상태", STATE_LABELS[info.state] || info.state],
      ["버전", info.version || "-"],
      ["인증", AUTH_LABELS[info.auth_method] || info.auth_method || "-"],
    ].forEach(([k, v]) => {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      dl.append(dt, dd);
    });
    card.querySelector(".chat-card-body").appendChild(dl);
    checkStatus(true);
  }

  input.addEventListener("input", () => {
    autosize();
    updateSendState();
    updateCommandMenu();
  });
  input.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return; // 한글 조합 중 Enter 무시
    if (!commandMenu.hidden) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const step = e.key === "ArrowDown" ? 1 : -1;
        commandIndex = (commandIndex + step + commandMatches.length) % commandMatches.length;
        updateCommandMenu();
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        chooseCommand(commandIndex);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation(); // 패널은 닫지 않고 메뉴만 닫기
        commandMenu.hidden = true;
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  input.addEventListener("blur", () => (commandMenu.hidden = true));
  sendBtn.addEventListener("click", () => (streaming ? stop() : send()));

  // 추천 질문 → 입력창에 채우기
  panel.querySelectorAll(".chat-suggestions [data-prompt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      input.value = btn.dataset.prompt;
      autosize();
      updateSendState();
      input.focus();
    });
  });

  // ---------- 모델 · 노력 수준 · 사용량 ----------
  const modelBtn = panel.querySelector(".chat-model");
  const usageBtn = panel.querySelector(".chat-usage");
  const usagePop = panel.querySelector(".chat-usage-pop");
  let usageInfo = null; // /api/ai/usage
  let liveModel = ""; // 이번 대화에서 Claude Code 가 알려 준 모델 이름
  let conversation = null; // {tokens, window} 이 대화가 차지하는 컨텍스트

  const tokens = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n || 0));
  const labelOf = (list, value) => (list || []).find((o) => o.value === value)?.label || "";
  const level = (pct) => (pct >= 95 ? "full" : pct >= 80 ? "warn" : "");
  const resetText = (sec) => {
    if (!sec) return "";
    const at = new Date(sec * 1000);
    const sameDay = at.toDateString() === new Date().toDateString();
    const time = at.toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
    return `${sameDay ? "" : at.toLocaleDateString("ko-KR", { month: "short", day: "numeric", weekday: "short" }) + " "}${time} 재설정`;
  };
  // 고리에 보일 한도: 세션(5시간) 우선
  const mainLimit = () => {
    const known = (usageInfo?.limits || []).filter((l) => l.percent !== null);
    return known.find((l) => l.type === "session" || l.type === "five_hour") || known[0] || null;
  };
  const limitReset = (l) => (l.resets_text ? `${l.resets_text} 초기화` : resetText(l.resets_at));

  function renderMeta() {
    if (!usageInfo) return;
    const chosen = usageInfo.model ? labelOf(usageInfo.models, usageInfo.model) : "";
    // 실제로 쓴 모델 이름(Opus 5.5)이 있으면 그걸, 아니면 고른 별칭이나 마지막으로 쓴 모델
    const shown = liveModel || (usageInfo.model ? chosen : usageInfo.last_model_label) || "기본 모델";
    modelBtn.querySelector(".chat-model-name").textContent = shown;
    modelBtn.querySelector(".chat-effort").textContent = usageInfo.effort ? labelOf(usageInfo.efforts, usageInfo.effort) : "";

    // 고리: 구독 사용 한도(5시간)를 알면 그것, 모르면 이 대화의 컨텍스트 사용량
    const limit = mainLimit();
    const ctxPct = conversation?.window ? (conversation.tokens / conversation.window) * 100 : null;
    const pct = limit ? limit.percent : ctxPct;
    const offset = 47.12 * (1 - Math.min(100, pct || 0) / 100);
    usageBtn.querySelector(".ring-value").style.strokeDashoffset = String(offset);
    usageBtn.classList.remove("warn", "full", "unknown");
    if (pct === null) usageBtn.classList.add("unknown");
    else if (level(pct)) usageBtn.classList.add(level(pct));
    usageBtn.querySelector(".chat-usage-pct").textContent = limit ? `${Math.round(limit.percent)}%` : "";
    usageBtn.title = limit
      ? `구독 사용량 (${limit.name}) ${Math.round(limit.percent)}%${limitReset(limit) ? ` · ${limitReset(limit)}` : ""}`
      : pct !== null
        ? `이 대화 컨텍스트 ${Math.round(pct)}%`
        : "사용량 보기";
    if (!usagePop.hidden) renderUsagePop();
  }

  function row(label, value) {
    const div = document.createElement("div");
    div.className = "usage-row";
    div.innerHTML = "<span></span><span></span>";
    div.firstChild.textContent = label;
    div.lastChild.textContent = value;
    return div;
  }
  function bar(pct) {
    const div = document.createElement("div");
    div.className = `usage-bar ${level(pct)}`;
    div.innerHTML = `<i style="width:${Math.max(1, Math.min(100, pct))}%"></i>`;
    return div;
  }
  function heading(text) {
    const h = document.createElement("h4");
    h.textContent = text;
    return h;
  }
  function note(text) {
    const p = document.createElement("p");
    p.className = "usage-note";
    p.textContent = text;
    return p;
  }

  function renderUsagePop() {
    const info = usageInfo || { today: {}, limits: [] };
    const parts = [heading("모델")];
    parts.push(row(liveModel || info.last_model_label || "기본 모델", info.effort ? `노력: ${labelOf(info.efforts, info.effort)}` : "노력: 기본"));

    parts.push(heading("구독 사용 한도"));
    if (info.limits.length) {
      info.limits.forEach((l) => {
        const value = l.percent !== null ? `${Math.round(l.percent)}%` : { allowed: "여유", allowed_warning: "곧 한도", rejected: "한도 도달" }[l.status] || l.status;
        parts.push(row(`${l.name}${l.overage ? " (추가 사용량)" : ""}`, [value, limitReset(l)].filter(Boolean).join(" · ")));
        if (l.percent !== null) parts.push(bar(l.percent));
      });
    } else {
      parts.push(note(info.plan_error ? `한도를 확인하지 못했습니다: ${info.plan_error}` : "아직 정보 없음 — 질문하면 Claude Code 가 알려 주는 대로 표시합니다."));
    }
    if (info.plan_checked_at) parts.push(note(`Claude Code /usage 기준 · ${new Date(info.plan_checked_at * 1000).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" })} 확인`));

    parts.push(heading("이 대화"));
    if (conversation) {
      const pct = conversation.window ? (conversation.tokens / conversation.window) * 100 : null;
      parts.push(row("컨텍스트", `${tokens(conversation.tokens)}${conversation.window ? ` / ${tokens(conversation.window)} (${Math.round(pct)}%)` : ""}`));
      if (pct !== null) parts.push(bar(pct));
    } else {
      parts.push(note("새 대화 — 아직 보낸 메시지가 없습니다."));
    }

    parts.push(heading("오늘 (이 앱에서)"));
    parts.push(row(`질문 ${info.today.requests || 0}회`, `입력 ${tokens(info.today.input_tokens)} · 출력 ${tokens(info.today.output_tokens)} 토큰`));
    parts.push(note("입력에는 캐시에서 다시 읽은 토큰도 포함됩니다. Claude 앱·Claude Code 등 다른 곳에서 쓴 양은 구독 사용 한도에만 반영됩니다."));
    usagePop.replaceChildren(...parts);
  }

  async function loadUsage(refresh = false) {
    try {
      usageInfo = await api("GET", `/api/ai/usage${refresh ? "?refresh=1" : ""}`);
      renderMeta();
    } catch {
      // 표시용 — 실패해도 채팅은 계속
    }
  }

  // 보내기 전 세션 한도 확인: 100% 면 막고, 95% 이상이면 한 번 경고하고 보냄
  let warnedAt = 0;
  async function checkSessionLimit() {
    let limit = mainLimit();
    if (limit && limit.percent >= 95) {
      await loadUsage(true); // 초기화됐을 수도 있으니 최신 값으로 다시 확인
      limit = mainLimit();
    }
    if (!limit || limit.percent < 95) return true;
    const when = limitReset(limit);
    if (limit.percent >= 100) {
      setCard(addSystemCard("세션 사용 한도를 다 썼습니다", "alert"), {
        tone: "fail",
        text: `Claude 구독의 ${limit.name} 한도가 100% 입니다.${when ? ` ${when} 뒤에 다시 질문하세요.` : ""} 보내지 않았습니다 (입력한 글은 그대로 둠).`,
      });
      return false;
    }
    if (Math.floor(limit.percent) > warnedAt) {
      warnedAt = Math.floor(limit.percent);
      setCard(addSystemCard(`세션 사용 한도 ${Math.round(limit.percent)}%`, "alert"), {
        tone: "warn",
        text: `곧 한도에 닿아 답이 중간에 막힐 수 있습니다.${when ? ` ${when}.` : ""}`,
      });
    }
    return true;
  }

  function applyUsage(usage) {
    if (!usage) return;
    if (usage.model_label) liveModel = usage.model_label;
    if (usage.context_tokens) conversation = { tokens: usage.context_tokens, window: usage.context_window };
    usageInfo = { ...(usageInfo || { models: [], efforts: [] }), today: usage.today, limits: usage.limits };
    renderMeta();
  }

  function setUsagePop(open) {
    usagePop.hidden = !open;
    usageBtn.setAttribute("aria-expanded", String(open));
    if (open) renderUsagePop();
  }
  usageBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (usagePop.hidden) loadUsage();
    setUsagePop(usagePop.hidden);
  });
  document.addEventListener("click", (e) => {
    if (!usagePop.hidden && !usagePop.contains(e.target)) setUsagePop(false);
  });
  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key !== "Escape" || usagePop.hidden) return;
      e.stopImmediatePropagation(); // Esc 는 사용량 창만 닫고 서랍은 그대로
      setUsagePop(false);
      usageBtn.focus();
    },
    true
  );

  async function savePrefs(change) {
    usageInfo = await api("POST", "/api/ai/prefs", change);
    if ("model" in change) liveModel = ""; // 다음 응답에서 실제 모델 이름을 다시 받음
    renderMeta();
  }
  modelBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!usageInfo) await loadUsage();
    if (!usageInfo) return;
    const rect = modelBtn.getBoundingClientRect();
    const item = (o, current, key) => ({
      label: o.label,
      icon: o.value === current ? "check" : null,
      action: () => savePrefs({ [key]: o.value }),
    });
    const items = [
      ...usageInfo.models.map((o) => item(o, usageInfo.model, "model")),
      "sep",
      ...usageInfo.efforts.map((o) => ({ ...item(o, usageInfo.effort, "effort"), label: `노력: ${o.label}` })),
    ];
    window.openContextMenu?.(rect.left, rect.top, items);
    const menu = document.querySelector(".ctx-menu");
    if (menu) menu.style.top = `${Math.max(4, rect.top - menu.offsetHeight - 6)}px`; // 버튼 바로 위로
  });

  // 새 대화: 대화 기록을 비우고 다음 메시지에 페이지 내용을 다시 보냄
  panel.querySelector("[data-chat-new]").addEventListener("click", async () => {
    if (streaming) await stop();
    try {
      showConversation(await api("POST", "/api/ai/conversation/new"));
    } catch (err) {
      alert(err.message);
      return;
    }
    sessionId = null;
    conversation = null;
    renderMeta();
    messages.replaceChildren();
    empty.hidden = false;
    input.focus();
  });

  // 기록 버튼 = /resume
  panel.querySelector("[data-chat-history]").addEventListener("click", () => {
    if (!streaming) runCommand("/resume");
  });

  // 참고 범위 칩 토글 (대화 중에 바꾸면 새 대화부터 반영). 칩은 화면이 바뀌면 새로 그려지므로 위임
  panel.querySelector(".chat-composer").addEventListener("click", (e) => {
    const chip = e.target.closest(".chat-context .ctx-chip");
    if (chip) chip.setAttribute("aria-pressed", String(chip.getAttribute("aria-pressed") !== "true"));
  });
  document.addEventListener("marknote:pageload", readPanelContext);

  // 답변 동작: 복사 / 페이지에 추가 / 다시 묻기
  messages.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-msg-action]");
    if (!btn || btn.disabled) return;
    const msg = btn.closest(".chat-msg");
    const markdown = msg.dataset.markdown || "";
    const action = btn.dataset.msgAction;

    if (action === "copy") {
      try {
        await navigator.clipboard.writeText(markdown);
        btn.innerHTML = `${icon("check")}<span>복사됨</span>`;
        setTimeout(() => (btn.innerHTML = `${icon("copy")}<span>복사</span>`), 1500);
      } catch {
        alert("클립보드에 복사할 수 없습니다.");
      }
    } else if (action === "insert") {
      btn.disabled = true;
      try {
        await window.editorAppend(markdown);
        btn.innerHTML = `${icon("check")}<span>추가됨</span>`;
      } catch (err) {
        btn.disabled = false;
        alert(err.message);
      }
    } else if (action === "retry" && lastUserMessage) {
      send(lastUserMessage);
    }
  });

  // ---------- 우클릭 메뉴: 빈 곳 / 내 메시지 / AI 답변 / 수정 제안 카드 ----------
  const K = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl";
  const clickIn = (root, selector) => () => root.querySelector(selector)?.click();

  function conversationText() {
    return [...messages.querySelectorAll(".chat-msg")]
      .map((m) => {
        const text = m.dataset.markdown ?? m.querySelector(".chat-bubble, .chat-card")?.innerText ?? "";
        const who = m.classList.contains("user") ? "나" : m.classList.contains("system") ? "시스템" : "AI";
        return `**${who}**: ${text.trim()}`;
      })
      .join("\n\n");
  }

  function proposalMenu(card) {
    const pending = card.querySelector(".chat-card-actions .primary");
    const link = card.querySelector(".chat-card-actions a[href]");
    return [
      { label: pending?.textContent || "적용", icon: "check", disabled: !pending, action: () => pending.click() },
      { label: "거절", icon: "x", disabled: !pending, action: clickIn(card, ".chat-card-actions .ghost:not(a)") },
      { label: "페이지 열기", icon: "open", disabled: !link, action: () => link.click() },
      "sep",
      { label: "바뀌는 내용 복사", icon: "copy", action: () => window.copyText(card.querySelector(".chat-diff")?.innerText || "") },
    ];
  }

  function emptyPanelMenu() {
    const connected = statusBtn.classList.contains("connected");
    return [
      { label: "새 대화", icon: "message-plus", action: clickIn(panel, "[data-chat-new]") },
      { label: "대화 내용 복사", icon: "copy", disabled: !messages.children.length, action: () => window.copyText(conversationText()) },
      "sep",
      { label: "연결 상태 확인", icon: "info", action: () => runCommand("/status") },
      connected
        ? { label: "Claude 연결 해제 (/logout)", icon: "unlock", action: () => runCommand("/logout") }
        : { label: "Claude 연결 (/login)", icon: "key", action: () => runCommand("/login") },
      { label: "AI 설정", icon: "settings", action: () => window.go("/settings") },
      "sep",
      { label: "패널 닫기", icon: "x", hint: "F2", action: () => setOpen(false) },
    ];
  }

  window.contextMenuProviders?.push((e) => {
    if (!panel.contains(e.target)) return null;
    const card = e.target.closest(".chat-proposal");
    if (card) return proposalMenu(card);
    const msg = e.target.closest(".chat-msg");
    const link = e.target.closest(".chat-bubble a[href]");
    const linkItems = link
      ? [
          { label: "링크 열기", icon: "external", action: () => window.open(link.href, "_blank") },
          { label: "링크 주소 복사", icon: "link", action: () => window.copyText(link.href) },
          "sep",
        ]
      : [];

    if (msg?.classList.contains("user")) {
      const text = msg.querySelector(".chat-bubble").textContent;
      return [
        { label: "복사", icon: "copy", action: () => window.copyText(text) },
        {
          label: "입력창에 다시 넣기",
          icon: "pencil",
          action: () => {
            input.value = text;
            autosize();
            updateSendState();
            input.focus();
          },
        },
        { label: "다시 보내기", icon: "refresh", disabled: Boolean(streaming), action: () => send(text) },
      ];
    }
    if (msg?.classList.contains("assistant") && msg.dataset.markdown !== undefined) {
      const insert = msg.querySelector('[data-msg-action="insert"]');
      return [
        ...linkItems,
        { label: "답변 복사 (마크다운)", icon: "copy", action: () => window.copyText(msg.dataset.markdown) },
        { label: "페이지에 추가", icon: "insert", disabled: !insert || insert.disabled, action: () => insert.click() },
        { label: "다시 묻기", icon: "refresh", disabled: Boolean(streaming), action: clickIn(msg, '[data-msg-action="retry"]') },
      ];
    }
    if (msg?.classList.contains("error")) {
      return [
        { label: "오류 내용 복사", icon: "copy", action: () => window.copyText(msg.querySelector(".chat-bubble").textContent) },
        { label: "같은 질문 다시 보내기", icon: "refresh", disabled: !lastUserMessage || Boolean(streaming), action: () => send(lastUserMessage) },
        "sep",
        { label: "연결 상태 확인", icon: "info", action: () => runCommand("/status") },
      ];
    }
    if (e.target.closest(".chat-context")) {
      return [...panel.querySelectorAll(".ctx-chip")].map((chip) => ({
        label: chip.textContent.trim(),
        icon: chip.getAttribute("aria-pressed") === "true" ? "check" : undefined,
        action: () => chip.click(),
      }));
    }
    return emptyPanelMenu();
  });

  // 다른 곳(블록 우클릭 등)에서 입력창을 채워 패널 열기
  window.chatPrefill = (text) => {
    setOpen(true);
    input.value = text;
    autosize();
    updateSendState();
    input.focus();
    input.setSelectionRange(text.length, text.length);
  };

  // 지난번 열림 상태 복원 (모바일에서는 자동으로 열지 않음). 처음엔 애니메이션·포커스 이동 없이
  if (storage.get(OPEN_KEY) === "1" && window.innerWidth > 760) {
    drawer.classList.add("open");
    document.body.classList.add("chat-open");
    document.querySelectorAll("[data-chat-toggle]").forEach((b) => b.setAttribute("aria-pressed", "true"));
    checkStatus();
    loadUsage();
  }
  requestAnimationFrame(() => requestAnimationFrame(() => drawer.classList.remove("no-anim")));
  if (localMode) {
    // 내 PC 노트 대화는 서버 대화 기록과 섞지 않는다
    empty.querySelector(".muted").textContent = "지금 노트를 바탕으로 답합니다. 물을 때만 노트가 서버를 거쳐 Claude 에게 전달되고, 대화는 저장되지 않습니다 (이 화면을 떠나면 사라짐). 비공개 표시 부분은 보내지 않습니다.";
  } else {
    restoreConversation(); // 어느 페이지에서든 이어 가던 대화를 그대로
  }
})();
