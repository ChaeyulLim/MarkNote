// 공통: API 호출, 아이콘, data-action 버튼, 새로고침 없는 화면 이동, 되돌리기 (탐색기는 explorer.js)
let csrfToken = document.querySelector('meta[name="csrf-token"]').content;

// ---------- CSRF 토큰이 어긋나면 팝업 대신 새 토큰으로 한 번 다시 보내기 ----------
// 모든 화면 스크립트의 fetch 가 X-CSRFToken 을 붙이므로, 여기서 늘 최신 토큰으로 바꿔 끼우고
// 서버가 { csrf: true } 로 거절하면 새 토큰을 받아 같은 요청을 한 번만 다시 보낸다.
const nativeFetch = window.fetch.bind(window);
async function refreshCsrfToken() {
  const res = await nativeFetch("/api/csrf", { credentials: "same-origin" });
  if (!res.ok) return false; // 로그인이 풀렸으면 원래 응답(401)대로
  csrfToken = (await res.json()).token;
  document.querySelector('meta[name="csrf-token"]').content = csrfToken;
  document.querySelectorAll('input[name="csrf_token"]').forEach((input) => (input.value = csrfToken));
  return true;
}
window.fetch = async (input, init = {}) => {
  const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
  if (!headers.has("X-CSRFToken")) return nativeFetch(input, init);
  headers.set("X-CSRFToken", csrfToken);
  const res = await nativeFetch(input, { ...init, headers });
  if (res.status !== 400) return res;
  const data = await res.clone().json().catch(() => null);
  if (!data?.csrf || !(await refreshCsrfToken())) return res;
  headers.set("X-CSRFToken", csrfToken);
  return nativeFetch(input, { ...init, headers });
};

// 아이콘 SVG (static/icons.svg 의 #i-이름). 템플릿의 icon() 매크로와 같은 모양
function icon(name, cls = "") {
  return `<svg class="icon${cls ? " " + cls : ""}" aria-hidden="true"><use href="/static/icons.svg#i-${name}"></use></svg>`;
}
window.icon = icon;

// 폴더 📁 / 노트 📄 고정
function pageIconHtml(kind) {
  return icon(kind === "folder" ? "folder" : "file");
}
window.pageIconHtml = pageIconHtml;

async function api(method, url, body, { keepalive = false } = {}) {
  const opts = { method, headers: { "X-CSRFToken": csrfToken } };
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
    // 페이지 이동 중에도 저장 요청이 끊기지 않게 (keepalive 는 64KB 제한)
    opts.keepalive = keepalive && opts.body.length < 60000;
  }
  const res = await fetch(url, opts);
  let data = null;
  try {
    data = await res.json();
  } catch {
    // 본문 없는 응답
  }
  if (!res.ok) {
    const error = new Error((data && data.error) || `요청 실패 (${res.status})`);
    error.status = res.status; // 네트워크 오류(재시도)와 서버 거절(재시도 안 함)을 구분하는 데 사용
    error.data = data;
    throw error;
  }
  return data;
}
window.api = api;

// ---------- 마크다운 가져오기 ----------
const IMPORT_ACCEPT = ".md,.markdown,.txt,.zip";
const isImportable = (f) => /\.(md|markdown|txt|zip)$/i.test(f.name);

function pickFiles(accept) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = true;
    input.addEventListener("change", () => resolve([...input.files]));
    input.addEventListener("cancel", () => resolve([]));
    input.click();
  });
}

// 같은 폴더에 같은 이름이 있으면 서버가 409 + conflicts 를 돌려줌 → 경고 후 덮어쓰기/이름 바꾸기/취소
async function importFiles(files, parentId, onConflict = null) {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  if (parentId) form.append("parent_id", parentId);
  if (onConflict) form.append("on_conflict", onConflict);
  try {
    return await api("POST", "/api/import", form);
  } catch (err) {
    if (err.status !== 409 || !err.data?.conflicts || onConflict) throw err;
    const choice = await askImportConflict(err.data.conflicts);
    return choice ? importFiles(files, parentId, choice) : null; // null = 취소
  }
}

function askImportConflict(conflicts) {
  return new Promise((resolve) => {
    const { box, close } = window.openModal("ui-confirm");
    box.innerHTML =
      `<h2>${icon("alert")}<span>같은 이름의 페이지가 이미 있습니다</span></h2>` +
      '<p class="muted">같은 폴더 안에는 같은 이름을 둘 수 없습니다. 어떻게 가져올까요?</p><ul class="ui-confirm-list"></ul>' +
      '<div class="ui-confirm-actions">' +
      '<button type="button" class="btn btn-ghost" data-choice="">취소</button>' +
      '<button type="button" class="btn btn-danger" data-choice="overwrite" title="기존 페이지와 그 하위 페이지는 휴지통으로 옮겨집니다 (복원 가능)">덮어쓰기</button>' +
      '<button type="button" class="btn btn-primary" data-choice="rename">이름 바꿔서 가져오기</button></div>';
    const list = box.querySelector(".ui-confirm-list");
    conflicts.forEach((c) => {
      const li = document.createElement("li");
      li.textContent = `“${c.title}”  →  이름 바꾸면 “${c.title} (2)” 처럼`;
      list.appendChild(li);
    });
    const note = document.createElement("p");
    note.className = "muted ui-confirm-note";
    note.textContent = "덮어쓰기를 고르면 기존 페이지(하위 포함)는 휴지통으로 옮겨져 언제든 복원할 수 있습니다.";
    list.after(note);
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      close();
      resolve(value);
    };
    box.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-choice]");
      if (btn) finish(btn.dataset.choice || null);
    });
    // 바깥 클릭·Esc 로 닫히면 취소
    new MutationObserver((_, obs) => {
      if (!box.isConnected) {
        obs.disconnect();
        finish(null);
      }
    }).observe(document.body, { childList: true });
    box.tabIndex = -1;
    box.querySelector('[data-choice="rename"]').focus();
  });
}
window.importFiles = importFiles;

// 파일을 골라(또는 끌어다 놓은 files) parentId 폴더로 가져오기 (null = 맨 위). 돌려줌: 가져왔으면 서버 답, 취소면 null
async function importInto(parentId, files = null) {
  files = files ? files.filter(isImportable) : await pickFiles(IMPORT_ACCEPT);
  if (!files.length) return null;
  if (window.flushEditor) await window.flushEditor();
  const res = await importFiles(files, parentId);
  if (res) window.toast?.(`${res.pages?.length || files.length}개를 가져왔습니다`);
  return res;
}
window.importInto = importInto;
window.isImportable = isImportable;

// ---------- data-action 버튼 ----------
const CONFIRMS = {
  delete: "영구 삭제하면 하위 페이지와 이미지까지 모두 사라지며 되돌릴 수 없습니다. 삭제할까요?",
  "deck-delete": "프레젠테이션을 영구 삭제하면 슬라이드와 그림이 모두 사라지며 되돌릴 수 없습니다. 삭제할까요?",
  "empty-trash": "휴지통의 모든 페이지를 영구 삭제합니다. 계속할까요?",
};

async function runAction(btn) {
  const { action, id } = btn.dataset;
  if (CONFIRMS[action] && !confirm(CONFIRMS[action])) return;
  // 편집 중인 내용을 먼저 저장
  if (window.flushEditor) await window.flushEditor();

  let res;
  switch (action) {
    case "new-deck":
      res = await api("POST", "/api/decks", {});
      window.go(res.url);
      return;
    case "deck-archive":
    case "deck-trash":
    case "deck-restore":
      res = await api("POST", `/api/decks/${id}/${action.slice(5)}`);
      break;
    case "deck-delete":
      res = await api("DELETE", `/api/decks/${id}`);
      break;
    case "import":
      res = await importInto(btn.dataset.parent ? Number(btn.dataset.parent) : null);
      if (res) await window.explorer?.refresh(); // 취소(같은 이름 경고 포함)면 null
      return;
    case "archive":
    case "trash":
    case "restore":
      res = await api("POST", `/api/pages/${id}/${action}`);
      break;
    case "delete":
      res = await api("DELETE", `/api/pages/${id}`);
      break;
    case "empty-trash":
      res = await api("POST", "/api/trash/empty");
      break;
    default:
      return;
  }
  if (btn.hasAttribute("data-reload")) window.reloadPage();
  else window.go(res.redirect);
}

// ---------- 파일 위치 (노트 = instance/notes/폴더/노트.md) ----------
// 화면에는 notes 폴더 기준 위치, 마우스를 올리거나 복사하면 컴퓨터의 실제 위치
function copyToClipboard(text) {
  // http 접속이거나 브라우저가 clipboard API 쓰기를 막으면(권한 거부) 예전 방식으로
  const legacy = () => {
    const focused = document.activeElement;
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    focused?.focus?.({ preventScroll: true });
    return ok ? Promise.resolve() : Promise.reject(new Error("클립보드에 복사하지 못했습니다."));
  };
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).catch(legacy);
  return legacy();
}
window.copyToClipboard = copyToClipboard;

function flashCopied(btn) {
  const title = btn.title;
  btn.classList.add("copied");
  btn.title = "복사됨";
  setTimeout(() => {
    btn.classList.remove("copied");
    btn.title = title;
  }, 1200);
}

// 이름을 바꾸면 파일 위치·주소(/n/경로)도 바뀐다 → 보이는 곳 모두 갱신 (탐색기는 목록을 다시 받음)
function updatePagePath(id, path, absPath, url) {
  if (url) {
    const current = JSON.parse(document.getElementById("page-data")?.textContent || "{}").page;
    if (current && current.id === id && location.pathname !== url) history.replaceState(history.state, "", url);
  }
  document.querySelectorAll(`[data-path-for="${id}"]`).forEach((n) => (n.textContent = path));
  if (absPath) {
    document.querySelectorAll(`[data-file-path="${id}"]`).forEach((n) => {
      n.dataset.abs = absPath;
      n.title = `파일 위치: ${absPath}`;
    });
  }
  window.explorer?.refresh().catch(console.error);
}
window.updatePagePath = updatePagePath;

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-copy-path], [data-copy-text]");
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  const text = btn.dataset.copyText ?? document.querySelector(`[data-file-path="${btn.dataset.copyPath}"]`)?.dataset.abs ?? "";
  copyToClipboard(text).then(() => flashCopied(btn), (err) => alert(err.message));
});

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  btn.closest("details")?.removeAttribute("open");
  runAction(btn).catch((err) => alert(err.message));
});


// ---------- 비공개 표시 ({{ }} · :::비공개) — AI 에게 보내지 않는 부분 (docs/claude-integration.md) ----------
// 마크다운 원문에서 비공개 부분을 가린다 (닫지 않은 {{ 는 문단 끝까지, :::비공개 는 끝까지)
function maskSecrets(text) {
  return String(text || "")
    .replace(/^[ ]{0,3}:::[ \t]*비공개[ \t]*$[\s\S]*?(?:^[ ]{0,3}:::[ \t]*$|(?![\s\S]))/gm, "🔒")
    .replace(/\{\{[\s\S]*?(?:\}\}|(?=\n[ \t]*\n)|(?![\s\S]))/g, "🔒");
}
window.maskSecrets = maskSecrets;

// 화면에서 드래그한 글자 — 흐린(비공개) 부분은 빼고
function selectionWithoutSecrets(sel) {
  if (!sel || !sel.rangeCount) return "";
  const box = document.createElement("div");
  for (let i = 0; i < sel.rangeCount; i++) box.appendChild(sel.getRangeAt(i).cloneContents());
  const inside = sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement)?.closest(".secret");
  if (inside) return "";
  if (!box.querySelector(".secret")) return maskSecrets(sel.toString());
  box.querySelectorAll(".secret").forEach((el) => el.replaceWith("🔒"));
  return maskSecrets(box.textContent);
}
window.selectionWithoutSecrets = selectionWithoutSecrets;

// 흐린 부분: 한 번 누르면 보이고(편집으로 들어가지 않음), 다른 곳을 누르면 다시 흐림
document.addEventListener(
  "click",
  (e) => {
    const target = e.target.closest?.(".secret");
    document.querySelectorAll(".secret.revealed").forEach((el) => {
      if (el !== target && !el.contains(target)) el.classList.remove("revealed");
    });
    if (target && !target.classList.contains("revealed")) {
      target.classList.add("revealed");
      e.preventDefault();
      e.stopPropagation();
    }
  },
  true
);
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.classList?.contains("secret")) e.target.classList.toggle("revealed");
});

// ---------- 안내 토스트 — toast("휴지통으로 옮겼습니다", { label: "되돌리기", action }) ----------
function toast(message, button = null) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const el = document.createElement("p");
  el.className = "toast";
  el.setAttribute("role", "status");
  el.textContent = message;
  if (button) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = button.label;
    btn.addEventListener("click", () => {
      el.remove();
      Promise.resolve(button.action()).catch((err) => alert(err.message));
    });
    el.appendChild(btn);
  }
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 5200);
}
window.toast = toast;

// ---------- 새로고침 없는 페이지 이동 ----------
// 노트 화면끼리는 본문(main)만 바꿔 끼운다 — 화면이 깜빡이지 않고 AI 도우미 대화·탐색기(선택·펼침·포커스)·스크롤이 그대로.
// 탐색기는 다시 만들지 않고 새 화면에 실려 온 목록(explorer-data)만 받는다.
// 화면마다 다른 스크립트(editor.js)는 다시 실행하고, 그 전에 이전 화면이 document 에 건 처리는 걷어 낸다.
// 프레젠테이션·로그인 등 다른 화면, 파일 내려받기는 예전처럼 새로 연다.
const pageLife = { ctrl: new AbortController(), cleanups: [] };
window.pageSignal = () => pageLife.ctrl.signal; // document.addEventListener(..., { signal: pageSignal() })
window.onPageLeave = (fn) => pageLife.cleanups.push(fn);
const PAGE_GLOBALS = ["flushEditor", "saveEditorNow", "getEditorLocation", "editorReload", "editorPageId", "refreshAiEdits", "editorAppend",
  "pageAiUndo", "pageSelectAll"];

function leavePage() {
  pageLife.ctrl.abort();
  pageLife.ctrl = new AbortController();
  pageLife.cleanups.splice(0).forEach((fn) => {
    try {
      fn();
    } catch (err) {
      console.error(err);
    }
  });
  PAGE_GLOBALS.forEach((name) => delete window[name]);
  undoManager.dropPage(); // 블록 되돌리기 기록은 그 화면에서만
}
window.leavePage = leavePage; // 내 PC 폴더 화면(local.js)이 노트를 바꿔 열 때도 같은 정리

const COMMON_SCRIPTS = ["/static/js/app.js", "/static/js/ui.js", "/static/js/explorer.js", "/static/js/chat.js"];
const SOFT_PATH_RE = /^\/(n\/.*|p\/\d+|archive|trash|settings)?$/;

function softUrl(url) {
  const u = new URL(url, location.href);
  if (u.origin !== location.origin || !SOFT_PATH_RE.test(u.pathname)) return null;
  if (!document.querySelector('.layout[data-mode="notes"]')) return null;
  return u;
}

function hardGo(url, replace) {
  if (replace) location.replace(url);
  else location.href = url;
}

function runScript(original) {
  return new Promise((resolve) => {
    const s = document.createElement("script");
    s.dataset.pageScript = "";
    if (original.getAttribute("src")) {
      s.src = original.getAttribute("src");
      s.onload = s.onerror = resolve;
    } else {
      s.textContent = original.textContent;
    }
    document.body.appendChild(s);
    if (!s.src) resolve();
  });
}

let navSeq = 0;
let shownUrl = location.pathname + location.search;

async function go(url, { replace = false, pop = false } = {}) {
  const u = softUrl(url);
  if (!u) return hardGo(url, replace);
  const seq = ++navSeq;
  try {
    if (window.flushEditor) await window.flushEditor(); // 편집 중인 내용부터 저장
  } catch (err) {
    console.error(err);
  }
  let res;
  try {
    res = await fetch(u.href, { headers: { "X-Soft-Nav": "1" } });
  } catch {
    return hardGo(u.href, replace);
  }
  if (seq !== navSeq) return; // 그사이 다른 곳을 눌렀음
  if (!(res.headers.get("content-type") || "").includes("text/html")) return hardGo(u.href, replace);
  const doc = new DOMParser().parseFromString(await res.text(), "text/html");
  const main = doc.querySelector("main.content");
  if (!main || !doc.querySelector('.layout[data-mode="notes"]') || seq !== navSeq) return hardGo(res.url, replace);

  // 본문을 옮기기 전에 실행할 스크립트 목록 (본문 안의 인라인 스크립트 포함, JSON 데이터 제외)
  const scripts = [...doc.querySelectorAll("script")].filter((s) => !s.type || s.type === "text/javascript");
  leavePage();
  document.querySelector("main.content").replaceWith(document.adoptNode(main));
  try {
    const fresh = doc.getElementById("explorer-data");
    if (fresh && window.explorer) window.explorer.load(window.explorer.driver.parse(fresh.textContent));
  } catch (err) {
    console.error(err);
  }
  // AI 도우미 패널은 그대로 두고, "참고할 페이지" 칩과 안내만 새 화면 것으로
  const panel = document.getElementById("chat-panel");
  const newPanel = doc.getElementById("chat-panel");
  if (panel && newPanel) {
    panel.dataset.pageId = newPanel.dataset.pageId;
    panel.dataset.deckId = newPanel.dataset.deckId;
    for (const sel of [".chat-context", ".chat-empty > p.muted"]) {
      const fresh = newPanel.querySelector(sel);
      if (fresh) panel.querySelector(sel)?.replaceWith(document.adoptNode(fresh));
    }
  }
  document.title = doc.title;
  const finalUrl = new URL(res.url);
  const shown = finalUrl.pathname + finalUrl.search + (finalUrl.pathname === u.pathname ? u.hash : "");
  if (!pop || shown !== location.pathname + location.search + location.hash) {
    history[replace || pop ? "replaceState" : "pushState"]({ soft: true }, "", shown);
  }
  shownUrl = finalUrl.pathname + finalUrl.search;
  if (!pop && !replace) window.scrollTo(0, 0);

  document.querySelectorAll("script[data-page-script]").forEach((s) => s.remove());
  for (const s of scripts) {
    const src = s.getAttribute("src");
    if (!src || !COMMON_SCRIPTS.includes(new URL(src, location.href).pathname)) await runScript(s);
  }
  document.dispatchEvent(new CustomEvent("marknote:pageload"));
}
window.go = go;
window.reloadPage = () => go(location.pathname + location.search, { replace: true });

history.replaceState({ soft: true }, "", location.href);
window.addEventListener("popstate", () => {
  if (location.pathname + location.search === shownUrl) return; // 같은 화면 안 #이동
  go(location.href, { pop: true });
});

document.addEventListener("click", (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest?.("a[href]");
  if (!a || a.target || a.hasAttribute("download") || !softUrl(a.href)) return;
  const u = new URL(a.href);
  if (u.pathname + u.search === location.pathname + location.search && u.hash) return; // 같은 화면 안 #이동
  e.preventDefault();
  if (a.closest(".explorer")) return; // 탐색기 안은 explorer.js
  if (window.explorer?.revealUrl(u.href)) return; // 폴더 링크(경로 표시·노트 안 링크) → 탐색기에서 펼치기
  go(a.href);
});

// ---------- 기본 단축키: 되돌리기(Ctrl+Z) · 다시 실행(Ctrl+Y / Ctrl+Shift+Z) · 전체 선택(Ctrl+A) ----------
// 탐색기 안의 키(방향키·Delete·복사·붙여넣기 등)는 explorer.js 가 먼저 처리한다.
// 글자 입력 칸 안에서는 브라우저 기본 동작(글자 되돌리기 등). 비어 있는 AI 입력창에서는 앱 되돌리기.
// 되돌리기 기록: 블록 편집·삭제·이동(editor.js, 그 페이지에 있는 동안만), 탐색기의 만들기·이름 바꾸기·옮기기·복사·휴지통.
const undoStack = [];
const redoStack = [];
const undoManager = {
  // entry: { label, undo: async fn, redo: async fn, page?: true(지금 화면에서만 유효) }
  push(entry) {
    undoStack.push(entry);
    if (undoStack.length > 100) undoStack.shift();
    redoStack.length = 0;
  },
  dropPage() {
    for (const stack of [undoStack, redoStack]) stack.splice(0, stack.length, ...stack.filter((e) => !e.page));
  },
  async undo() {
    if (window.pageAiUndo?.()) return; // AI 가 고친 부분이 강조돼 있으면 그것부터
    if (window.flushEditor) await window.flushEditor(); // 편집 중인 블록을 먼저 기록
    const entry = undoStack.pop();
    if (!entry) return toast("되돌릴 작업이 없습니다");
    await entry.undo();
    redoStack.push(entry);
    toast(`되돌림: ${entry.label}`);
  },
  async redo() {
    if (window.flushEditor) await window.flushEditor();
    const entry = redoStack.pop();
    if (!entry) return toast("다시 실행할 작업이 없습니다");
    await entry.redo();
    undoStack.push(entry);
    toast(`다시 실행: ${entry.label}`);
  },
};
window.undoManager = undoManager;

const isTyping = (t) => Boolean(t?.closest?.("input, textarea, select, [contenteditable='true'], [contenteditable='']"));
const isEmptyChatInput = (t) => t?.matches?.(".chat-input") && !t.value;

document.addEventListener("keydown", (e) => {
  if (e.defaultPrevented || e.isComposing || e.keyCode === 229 || e.altKey) return;
  const mod = e.ctrlKey || e.metaKey;
  const t = e.target;
  const typing = isTyping(t) && !isEmptyChatInput(t);
  const key = e.key.toLowerCase();
  const code = e.code;
  if (mod && (code === "KeyZ" || key === "z") && !typing) {
    e.preventDefault();
    (e.shiftKey ? undoManager.redo() : undoManager.undo()).catch((err) => alert(err.message));
  } else if (mod && !e.shiftKey && (code === "KeyY" || key === "y") && !typing) {
    e.preventDefault();
    undoManager.redo().catch((err) => alert(err.message));
  } else if (mod && !e.shiftKey && (code === "KeyA" || key === "a") && !isTyping(t)) {
    e.preventDefault();
    const side = t.closest?.(".sidebar, .chat-panel");
    if (!side && window.pageSelectAll?.()) return; // 노트 화면 본문: 블록 전체 선택
    const area = side || document.querySelector("main.content"); // 지금 칸 안에서만
    const range = document.createRange();
    range.selectNodeContents(area);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }
});
