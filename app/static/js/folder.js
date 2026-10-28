// 폴더 화면: 이름(= 폴더 이름) 바꾸기, 목록을 끌어서 순서 바꾸기. 복사 버튼은 app.js 공통 처리
(() => {
  const data = JSON.parse(document.getElementById("page-data").textContent);
  const pageId = data.page.id;
  const titleInput = document.getElementById("page-title");
  let savedTitle = titleInput.value.trim();
  let timer = null;
  let queue = Promise.resolve();
  const enqueue = (fn) => (queue = queue.then(fn).catch((err) => alert(err.message)));

  // 같은 이름의 폴더가 있으면 제목 아래 경고 (입력은 막지 않음)
  const warning = document.createElement("p");
  warning.className = "title-warning";
  warning.hidden = true;
  titleInput.after(warning);
  function setWarning(text) {
    warning.hidden = !text;
    warning.innerHTML = text ? `${icon("alert")}<span></span>` : "";
    if (text) warning.querySelector("span").textContent = text;
    titleInput.classList.toggle("conflict", Boolean(text));
  }

  function saveTitle() {
    clearTimeout(timer);
    const title = titleInput.value.trim();
    if (title === savedTitle || titleInput.readOnly) return;
    savedTitle = title;
    enqueue(async () => {
      try {
        const res = await api("PATCH", `/api/pages/${pageId}`, { title });
        setWarning("");
        // 폴더 이름이 바뀌면 안의 것들 주소·위치도 모두 바뀜 → 새 주소로 다시 열기
        if (document.activeElement === titleInput) {
          document.querySelectorAll(`[data-title-for="${pageId}"]`).forEach((n) => (n.textContent = res.page.display_title));
          window.updatePagePath(pageId, res.path, res.abs_path, res.url);
          reloadOnLeave = true;
        } else {
          location.href = res.url;
        }
      } catch (err) {
        if (err.status !== 409) throw err;
        savedTitle = null; // 이름을 고치면 다시 저장 시도
        setWarning(`${err.message} 다른 이름으로 바꿔 주세요 (저장되지 않음).`);
      }
    });
  }
  let reloadOnLeave = false;
  titleInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(saveTitle, 800);
  });
  titleInput.addEventListener("blur", () => {
    saveTitle();
    if (reloadOnLeave) enqueue(async () => location.replace(location.pathname));
  });
  titleInput.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229 || e.key !== "Enter") return;
    e.preventDefault();
    titleInput.blur();
  });
  window.flushEditor = () => {
    saveTitle();
    return queue;
  };

  // ---------- 끌어서 순서 바꾸기 (폴더가 노트보다 먼저 — 같은 종류끼리만) ----------
  const list = document.querySelector(".folder-list");
  let dragged = null;
  list.addEventListener("dragstart", (e) => {
    dragged = e.target.closest("li[data-id]");
    if (!dragged) return;
    dragged.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragged.dataset.id);
  });
  list.addEventListener("dragover", (e) => {
    const over = e.target.closest("li[data-id]");
    if (!dragged || !over || over === dragged || over.dataset.kind !== dragged.dataset.kind) return;
    e.preventDefault();
    const rect = over.getBoundingClientRect();
    over[e.clientY < rect.top + rect.height / 2 ? "before" : "after"](dragged);
  });
  list.addEventListener("drop", (e) => e.preventDefault());
  list.addEventListener("dragend", () => {
    if (!dragged) return;
    dragged.classList.remove("dragging");
    const id = Number(dragged.dataset.id);
    dragged = null;
    const order = [...list.querySelectorAll("li[data-id]")].map((li) => Number(li.dataset.id));
    enqueue(() => api("PATCH", `/api/pages/${id}`, { order }));
  });
})();
