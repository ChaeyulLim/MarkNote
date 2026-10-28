// 공통: API 호출, 아이콘, 사이드바 트리, data-action 버튼
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

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

// ---------- 사이드바 트리 펼침 상태 ----------
const OPEN_KEY = "marknote.openNodes";
let openNodes = new Set();
try {
  openNodes = new Set(JSON.parse(localStorage.getItem(OPEN_KEY) || "[]"));
} catch {
  // 저장소 사용 불가
}
document.querySelectorAll(".tree-node").forEach((node) => {
  if (openNodes.has(node.dataset.id)) node.classList.add("open");
});
document.addEventListener("click", (e) => {
  const toggle = e.target.closest(".tree-toggle");
  if (!toggle) return;
  const node = toggle.closest(".tree-node");
  node.classList.toggle("open");
  if (node.classList.contains("open")) openNodes.add(node.dataset.id);
  else openNodes.delete(node.dataset.id);
  try {
    localStorage.setItem(OPEN_KEY, JSON.stringify([...openNodes]));
  } catch {
    // 저장소 사용 불가
  }
});

// ---------- 사이드바 트리 끌어 놓기: 순서 바꾸기 · 다른 폴더로 옮기기 ----------
// 행 위/아래 1/4 = 그 앞/뒤(같은 종류끼리 — 폴더가 노트보다 먼저), 폴더 행 가운데 = 그 폴더 안으로. 맨 위에는 폴더만
let treeDragged = null;
const treeMarks = ["drop-before", "drop-after", "drop-into"];
function clearTreeMarks() {
  document.querySelectorAll(".tree-row.drop-before, .tree-row.drop-after, .tree-row.drop-into").forEach((r) => r.classList.remove(...treeMarks));
}
function treeDropSpot(e) {
  const row = e.target.closest(".tree-row");
  const node = row?.closest(".tree-node");
  if (!treeDragged || !node || treeDragged === node || treeDragged.contains(node)) return null;
  const rect = row.getBoundingClientRect();
  const y = (e.clientY - rect.top) / rect.height;
  const topLevel = !node.parentElement.closest(".tree-node");
  if (node.dataset.kind === "folder" && y > 0.25 && y < 0.75) return { row, node, where: "into" };
  if (node.dataset.kind !== treeDragged.dataset.kind) return null;
  if (topLevel && treeDragged.dataset.kind !== "folder") return null;
  return { row, node, where: y < 0.5 ? "before" : "after" };
}
document.addEventListener("dragstart", (e) => {
  const link = e.target.closest?.(".tree-link");
  if (!link) return;
  treeDragged = link.closest(".tree-node");
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/uri-list", link.href);
});
document.addEventListener("dragover", (e) => {
  if (!treeDragged) return;
  const spot = treeDropSpot(e);
  clearTreeMarks();
  if (!spot) return;
  e.preventDefault();
  spot.row.classList.add(`drop-${spot.where}`);
});
document.addEventListener("dragend", () => {
  treeDragged = null;
  clearTreeMarks();
});
document.addEventListener("drop", async (e) => {
  if (!treeDragged) return;
  const spot = treeDropSpot(e);
  const dragged = treeDragged;
  treeDragged = null;
  clearTreeMarks();
  if (!spot) return;
  e.preventDefault();
  const id = Number(dragged.dataset.id);
  let body;
  if (spot.where === "into") {
    body = { parent_id: Number(spot.node.dataset.id) };
  } else {
    const parentNode = spot.node.parentElement.closest(".tree-node");
    const siblings = [...spot.node.parentElement.children].filter((n) => n !== dragged);
    siblings.splice(siblings.indexOf(spot.node) + (spot.where === "after" ? 1 : 0), 0, dragged);
    body = { parent_id: parentNode ? Number(parentNode.dataset.id) : null, order: siblings.map((n) => Number(n.dataset.id)) };
  }
  try {
    if (window.flushEditor) await window.flushEditor();
    await api("PATCH", `/api/pages/${id}`, body);
    // 지금 보는 페이지의 경로가 바뀌었을 수 있다 → 번호 주소로 다시 열면 새 경로로 보내 준다
    const current = JSON.parse(document.getElementById("page-data")?.textContent || "{}").page;
    if (current && (current.id === id || dragged.querySelector(`.tree-node[data-id="${current.id}"]`))) location.href = `/p/${current.id}`;
    else location.reload();
  } catch (err) {
    alert(err.message);
  }
});

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
    case "new-page":
      res = await api("POST", "/api/pages", {});
      location.href = res.url;
      return;
    case "new-deck":
      res = await api("POST", "/api/decks", {});
      location.href = res.url;
      return;
    case "deck-archive":
    case "deck-trash":
    case "deck-restore":
      res = await api("POST", `/api/decks/${id}/${action.slice(5)}`);
      break;
    case "deck-delete":
      res = await api("DELETE", `/api/decks/${id}`);
      break;
    case "new-child": // 폴더 안에 새 노트 (data-kind="folder" 면 새 폴더)
      res = await api("POST", "/api/pages", { parent_id: Number(btn.dataset.parent), kind: btn.dataset.kind || "note" });
      location.href = res.url;
      return;
    case "new-readme": // 폴더 설명 README.md
      res = await api("POST", "/api/pages", { parent_id: Number(btn.dataset.parent), title: "README" });
      location.href = res.url;
      return;
    case "import": {
      const files = await pickFiles(IMPORT_ACCEPT);
      if (!files.length) return;
      res = await importFiles(files, btn.dataset.parent);
      if (!res) return; // 같은 이름 경고에서 취소
      location.href = res.redirect;
      return;
    }
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
  if (btn.hasAttribute("data-reload")) location.reload();
  else location.href = res.redirect;
}

// ---------- 파일 위치 (노트 = instance/notes/프로젝트/노트.md) ----------
// 화면에는 notes 폴더 기준 위치, 마우스를 올리거나 복사하면 컴퓨터의 실제 위치
function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const ta = document.createElement("textarea"); // http 접속 등 clipboard API 를 못 쓸 때
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  ta.remove();
  return Promise.resolve();
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

// 이름을 바꾸면 파일 위치·주소(/n/경로)도 바뀐다 → 보이는 곳 모두 갱신
function updatePagePath(id, path, absPath, url) {
  if (url) {
    document.querySelectorAll(`.tree-node[data-id="${id}"] > .tree-row .tree-link`).forEach((a) => (a.href = url));
    const current = JSON.parse(document.getElementById("page-data")?.textContent || "{}").page;
    if (current && current.id === id && location.pathname !== url) history.replaceState(null, "", url);
  }
  document.querySelectorAll(`[data-path-for="${id}"]`).forEach((n) => (n.textContent = path));
  if (absPath) {
    document.querySelectorAll(`[data-file-path="${id}"]`).forEach((n) => {
      n.dataset.abs = absPath;
      n.title = `파일 위치: ${absPath}`;
    });
  }
  const node = document.querySelector(`.tree-node[data-id="${id}"]`);
  if (node && path) {
    node.dataset.path = path;
    if (absPath) {
      node.dataset.abs = absPath;
      node.querySelector(":scope > .tree-row .tree-link").title = absPath;
    }
  }
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
