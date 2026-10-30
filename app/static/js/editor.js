// 블록 편집기: 편집 중인 블록만 마크다운 원문, 나머지는 렌더링 결과로 표시
(() => {
  const data = JSON.parse(document.getElementById("page-data").textContent);
  const pageId = data.page.id;
  const editable = data.page.status !== "trashed";
  // 내 PC 폴더 노트(local.js): 같은 편집기, 저장만 서버 대신 브라우저가 PC 파일에 (window.pageApi)
  const local = Boolean(data.page.local);
  const api = window.pageApi || window.api;

  const container = document.getElementById("blocks");
  const addArea = document.getElementById("add-block");
  const pageEl = document.getElementById("page");

  const FENCE_RE = /^\s{0,3}(```|~~~)/;
  const LIST_RE = /^(\s*)(?:([-*+])|(\d+)([.)]))\s+(\[[ xX]\]\s+)?/;
  const QUOTE_RE = /^\s*>+\s?/;
  const TASK_LINE_RE = /^(\s*(?:[-*+]|\d+[.)])\s+)\[([ xX])\]/;

  const blocks = new Map(); // id -> {id, content, html}
  let editing = null; // {id, el, ta}

  // 모든 서버 작업을 순서대로 실행 (저장 → 새 블록 생성 등이 꼬이지 않게).
  // 작업이 끝나면 페이지 내용이 바뀌었는지 보고 되돌리기 기록을 남긴다 (record: false = 기록 안 함 — 입력 중 자동 저장 등)
  let queue = Promise.resolve();
  function enqueue(task, { record = true } = {}) {
    queue = queue
      .then(task)
      .then(() => record && recordHistory())
      .catch((err) => {
        console.error(err);
        alert(err.message);
      });
    return queue;
  }
  window.flushEditor = () => {
    if (editing) editing.ta.blur();
    return queue;
  };

  // ---------- 입력 중 자동 저장 ----------
  // 타이핑을 1초 멈추면 편집 중인 블록을 그대로(블록 나누지 않고) 저장한다. 편집 상태는 유지.
  let liveTimer = null;
  function scheduleLiveSave() {
    clearTimeout(liveTimer);
    liveTimer = setTimeout(saveLive, 1000);
  }
  function saveLive() {
    clearTimeout(liveTimer);
    if (!editing) return queue;
    const { id, ta } = editing;
    const content = ta.value;
    const b = blocks.get(id);
    if (!b || content === b.content) return queue;
    return enqueue(async () => {
      const res = await writeApi("PATCH", `/api/blocks/${id}`, { content, split: false }, { keepalive: true });
      const saved = blocks.get(id);
      if (!saved) return;
      saved.content = res.blocks[0].content;
      saved.html = res.blocks[0].html;
      saved.unsplit = true; // 편집을 마칠 때 빈 줄 기준 블록 나누기를 다시 해야 함
    }, { record: false });
  }

  // AI 도우미가 질문 전에 부르는 저장: 편집을 끝내지 않고 지금 내용만 서버에 반영
  window.saveEditorNow = () => {
    saveTitle();
    return saveLive();
  };

  // ---------- 지금 위치 · 선택한 글자 (AI 도우미용) ----------
  let lastSelection = "";
  function noteSelection(text) {
    lastSelection = (text || "").trim().slice(0, 2000);
    window.setChatSelection?.(lastSelection);
  }
  document.addEventListener("selectionchange", () => {
    if (editing && document.activeElement === editing.ta) return; // 편집 중 선택은 textarea 이벤트로 처리
    const sel = window.getSelection();
    if (!sel.rangeCount || !container.contains(sel.anchorNode)) return; // 채팅창 등 다른 곳 선택은 무시
    noteSelection(window.selectionWithoutSecrets(sel));
  }, { signal: window.pageSignal() });
  window.getEditorLocation = () => ({
    view: "page",
    page_id: pageId,
    editing_block: editing ? [...container.children].indexOf(editing.el) + 1 : null,
    block_count: container.children.length,
  });

  // ---------- DOM ----------
  const elFor = (id) => container.querySelector(`.block[data-id="${id}"]`);
  const idOf = (el) => Number(el.dataset.id);

  function createEl(b) {
    const el = document.createElement("div");
    el.className = "block";
    el.dataset.id = b.id;
    el.innerHTML =
      `<div class="block-handle" draggable="true" title="드래그해서 순서 변경 · 우클릭 메뉴">${icon("grip")}</div>` +
      '<div class="block-body"></div>' +
      `<button type="button" class="block-delete" title="블록 삭제" aria-label="블록 삭제">${icon("x")}</button>`;
    if (!editable) el.querySelector(".block-handle").removeAttribute("draggable");
    render(el, b);
    return el;
  }

  function render(el, b) {
    const body = el.querySelector(".block-body");
    body.innerHTML = b.html || '<p class="placeholder">빈 블록</p>';
    body.querySelectorAll("a[href^='http']").forEach((a) => {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    });
    body.querySelectorAll("input.task-check").forEach((cb, i) => {
      cb.dataset.index = i;
      cb.disabled = !editable;
    });
  }

  function store(b) {
    blocks.set(b.id, b);
  }

  // 서버가 돌려준 블록 목록으로 el 을 교체 (나눠진 경우 뒤에 추가). 마지막 요소 반환
  function replaceWith(el, list) {
    const [first, ...rest] = list;
    store(first);
    render(el, first);
    let last = el;
    for (const b of rest) {
      store(b);
      const newEl = createEl(b);
      last.after(newEl);
      last = newEl;
    }
    updateEmptyState();
    return last;
  }

  function updateEmptyState() {
    container.classList.toggle("empty", container.children.length === 0);
  }

  function autosize(ta) {
    ta.style.height = "auto";
    ta.style.height = ta.scrollHeight + "px";
  }

  // ---------- 서버 작업 ----------
  async function createBlock(content, { afterId, beforeId } = {}) {
    const body = { content };
    if (afterId != null) body.after_id = afterId;
    if (beforeId != null) body.before_id = beforeId;
    const res = await writeApi("POST", `/api/pages/${pageId}/blocks`, body);
    store(res.block);
    const el = createEl(res.block);
    if (afterId != null) elFor(afterId).after(el);
    else if (beforeId != null) elFor(beforeId).before(el);
    else container.appendChild(el);
    updateEmptyState();
    return el;
  }

  async function removeBlock(el) {
    const id = idOf(el);
    await writeApi("DELETE", `/api/blocks/${id}`);
    blocks.delete(id);
    el.remove();
    updateEmptyState();
  }

  async function saveBlock(el, content) {
    const res = await writeApi("PATCH", `/api/blocks/${idOf(el)}`, { content }, { keepalive: true });
    return replaceWith(el, res.blocks);
  }

  // ---------- 편집 시작 / 완료 ----------
  function startEdit(id, caret = "end") {
    if (!editable) return;
    const el = elFor(id);
    if (!el) return;
    const b = blocks.get(id);
    clearSelection();
    const ta = document.createElement("textarea");
    ta.className = "block-editor";
    ta.rows = 1;
    ta.spellcheck = false;
    ta.value = b.content;
    ta.placeholder = "마크다운으로 작성…  Enter: 다음 블록 · Shift+Enter: 줄바꿈 · Esc: 완료";
    el.querySelector(".block-body").replaceChildren(ta);
    el.classList.add("editing");
    editing = { id, el, ta };

    autosize(ta);
    ta.focus();
    const pos = caret === "start" ? 0 : caret === "end" ? ta.value.length : caret;
    ta.setSelectionRange(pos, pos);

    ta.addEventListener("input", () => {
      autosize(ta);
      scheduleLiveSave();
    });
    ta.addEventListener("keydown", onKeyDown);
    ta.addEventListener("paste", onPaste);
    // 편집 중 글자를 드래그로 선택하면 AI 도우미의 "이 부분"으로 기억
    const trackSelection = () => noteSelection(window.maskSecrets(ta.value.slice(ta.selectionStart, ta.selectionEnd)));
    ta.addEventListener("select", trackSelection);
    ta.addEventListener("mouseup", trackSelection);
    ta.addEventListener("keyup", (e) => e.shiftKey && trackSelection());
    ta.addEventListener("blur", () => {
      if (editing && editing.ta === ta) enqueue(commit);
    });
  }

  // 편집 중인 블록을 저장하고 렌더링 모드로. 남은 블록의 마지막 요소(삭제되면 null) 반환
  async function commit() {
    if (!editing) return null;
    const { id, el, ta } = editing;
    editing = null;
    el.classList.remove("editing");
    const content = ta.value;
    const b = blocks.get(id);
    clearTimeout(liveTimer); // 이 저장이 입력 중 자동 저장을 대신함

    if (!content.trim()) {
      await removeBlock(el);
      return null;
    }
    if (content === b.content && !b.unsplit) {
      render(el, b);
      return el;
    }
    el.querySelector(".block-body").textContent = content;
    el.classList.add("saving");
    try {
      return await saveBlock(el, content);
    } catch (err) {
      // 저장 실패 시 입력한 내용을 잃지 않도록 다시 편집 상태로
      store({ ...b, content });
      startEdit(id);
      throw err;
    } finally {
      el.classList.remove("saving");
    }
  }

  function moveTo(target, caret) {
    const id = idOf(target);
    enqueue(async () => {
      await commit();
      startEdit(id, caret);
    });
  }

  // ---------- 키보드 ----------
  function onKeyDown(e) {
    if (e.isComposing || e.keyCode === 229) return; // 한글 조합 중에는 무시
    if (!editing) return;
    const ta = e.target;
    if ((e.ctrlKey || e.metaKey) && handleShortcut(e, ta)) {
      e.preventDefault();
      return;
    }
    const { selectionStart: s, selectionEnd: t, value: v } = ta;
    const { el } = editing;
    const plain = !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey;

    if (e.key === "Escape" || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s")) {
      e.preventDefault();
      const id = editing.id;
      ta.blur();
      if (e.key === "Escape") enqueue(async () => elFor(id) && selectBlocks([id]), { record: false });
    } else if (e.key === "Tab") {
      e.preventDefault();
      indentLine(ta, e.shiftKey);
    } else if (e.key === "Enter" && plain) {
      handleEnter(e, ta);
    } else if (e.key === "Backspace" && s === 0 && t === 0) {
      handleBackspace(e, ta);
    } else if (e.key === "ArrowUp" && plain && s === t && !v.slice(0, s).includes("\n")) {
      if (el.previousElementSibling) {
        e.preventDefault();
        moveTo(el.previousElementSibling, "end");
      }
    } else if (e.key === "ArrowDown" && plain && s === t && !v.slice(t).includes("\n")) {
      if (el.nextElementSibling) {
        e.preventDefault();
        moveTo(el.nextElementSibling, "start");
      }
    }
  }

  // ---------- 편집 단축키 (Ctrl/⌘ 조합) ----------
  function handleShortcut(e, ta) {
    const is = window.keyIs;
    const plain = !e.shiftKey && !e.altKey;
    const heading = ["1", "2", "3"].find((n) => is(e, n) || e.key === "!@#"[Number(n) - 1]);
    if (plain && is(e, "b")) wrapSelection(ta, "**");
    else if (plain && is(e, "i")) wrapSelection(ta, "*");
    else if (e.shiftKey && is(e, "s")) wrapSelection(ta, "~~");
    else if (e.shiftKey && is(e, "h")) wrapSelection(ta, "{{", "}}");
    else if (plain && is(e, "e")) wrapSelection(ta, "`");
    else if (plain && is(e, "k")) insertLink(ta);
    else if (e.shiftKey && heading) setHeading(ta, Number(heading));
    else if (plain && e.key === "Enter") toggleTaskLine(ta);
    else if (plain && is(e, "d")) duplicateBlock(editing.el);
    else if (e.shiftKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) moveBlock(editing.el, e.key === "ArrowUp" ? -1 : 1);
    else return false;
    return true;
  }

  // 선택한 글자를 mark 로 감싸기 (이미 감싸져 있으면 해제)
  function wrapSelection(ta, mark, close = mark) {
    const { selectionStart: s, selectionEnd: t, value: v } = ta;
    const selected = v.slice(s, t);
    if (v.slice(s - mark.length, s) === mark && v.slice(t, t + close.length) === close) {
      ta.setSelectionRange(s - mark.length, t + close.length);
      insertText(ta, selected);
      ta.setSelectionRange(s - mark.length, t - mark.length);
      return;
    }
    const inner = selected || "텍스트";
    insertText(ta, mark + inner + close);
    ta.setSelectionRange(s + mark.length, s + mark.length + inner.length);
  }

  function insertLink(ta) {
    const { selectionStart: s, selectionEnd: t, value: v } = ta;
    const label = v.slice(s, t) || "링크";
    insertText(ta, `[${label}](https://)`);
    const urlStart = s + label.length + 3;
    ta.setSelectionRange(urlStart, urlStart + "https://".length);
  }

  function currentLine(ta) {
    const { selectionStart: s, value: v } = ta;
    const start = v.lastIndexOf("\n", s - 1) + 1;
    const nl = v.indexOf("\n", s);
    const end = nl === -1 ? v.length : nl;
    return { start, end, text: v.slice(start, end), caret: s };
  }

  function replaceLine(ta, line, next) {
    ta.setSelectionRange(line.start, line.end);
    insertText(ta, next);
    const caret = Math.max(line.start, Math.min(line.caret + next.length - line.text.length, line.start + next.length));
    ta.setSelectionRange(caret, caret);
  }

  function setHeading(ta, level) {
    const line = currentLine(ta);
    const m = line.text.match(/^(#{1,6})\s+/);
    const body = m ? line.text.slice(m[0].length) : line.text;
    const same = m && m[1].length === level;
    replaceLine(ta, line, same ? body : `${"#".repeat(level)} ${body}`);
  }

  function toggleTaskLine(ta) {
    const line = currentLine(ta);
    const task = line.text.match(TASK_LINE_RE);
    let next;
    if (task) {
      const checked = task[2] !== " ";
      next = line.text.replace(TASK_LINE_RE, `${task[1]}[${checked ? " " : "x"}]`);
    } else {
      const bullet = line.text.match(/^(\s*)(?:[-*+]|\d+[.)])\s+/);
      next = bullet ? `${bullet[1]}- [ ] ${line.text.slice(bullet[0].length)}` : line.text.replace(/^(\s*)/, "$1- [ ] ");
    }
    replaceLine(ta, line, next);
  }

  function duplicateBlock(el) {
    const id = idOf(el);
    const editingThis = editing && editing.el === el;
    const content = editingThis ? editing.ta.value : blocks.get(id).content;
    if (!content.trim()) return;
    enqueue(async () => {
      const last = editingThis ? await commit() : el;
      if (!last) return;
      const copy = await createBlock("", { afterId: idOf(last) });
      await saveBlock(copy, content);
      if (editingThis) startEdit(idOf(copy));
    });
  }

  // 블록을 위(-1)/아래(1)로 한 칸 이동. 편집 중인 블록은 이웃 블록을 옮겨서 커서를 유지한다
  function moveBlock(el, dir) {
    const neighbor = dir < 0 ? el.previousElementSibling : el.nextElementSibling;
    if (!neighbor) return;
    if (dir < 0) el.after(neighbor);
    else el.before(neighbor);
    if (editing && editing.el === el) editing.ta.focus();
    const order = [...container.children].map(idOf);
    enqueue(() => writeApi("POST", `/api/pages/${pageId}/blocks/reorder`, { order }));
  }

  function insideFence(text) {
    return text.split("\n").filter((line) => FENCE_RE.test(line)).length % 2 === 1;
  }

  function insertText(ta, text) {
    ta.focus();
    // execCommand 는 되돌리기(Ctrl+Z) 기록을 유지한다
    if (!document.execCommand("insertText", false, text)) {
      ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, "end");
    }
    autosize(ta);
  }

  function handleEnter(e, ta) {
    const { selectionStart: s, selectionEnd: t, value: v } = ta;
    if (insideFence(v.slice(0, s))) return; // 코드블록 안: 일반 줄바꿈

    const lineStart = v.lastIndexOf("\n", s - 1) + 1;
    const nl = v.indexOf("\n", t);
    const lineEnd = nl === -1 ? v.length : nl;
    const line = v.slice(lineStart, s);
    const restOfLine = v.slice(t, lineEnd);

    if (/^\s*\|/.test(line)) return; // 표 작성 중: 일반 줄바꿈

    const list = line.match(LIST_RE);
    const quote = !list && line.match(QUOTE_RE);
    if ((list || quote) && s === t && !restOfLine) {
      e.preventDefault();
      const marker = (list || quote)[0];
      if (!line.slice(marker.length).trim()) {
        // 빈 항목에서 Enter: 목록/인용 종료 → 다음 블록으로
        const before = v.slice(0, lineStart).replace(/\n$/, "");
        const after = v.slice(lineEnd).replace(/^\n/, "");
        if (!before.trim() && !after.trim()) {
          ta.value = ""; // 블록에 빈 항목 하나뿐이면 표시만 지운다
          autosize(ta);
          return;
        }
        splitBlock(ta, before, after);
      } else {
        insertText(ta, "\n" + nextMarker(list, quote));
      }
      return;
    }

    e.preventDefault();
    splitBlock(ta, v.slice(0, s), v.slice(t));
  }

  function nextMarker(list, quote) {
    if (quote) return quote[0];
    const [, indent, bullet, num, delim, task] = list;
    const head = bullet || `${Number(num) + 1}${delim}`;
    return `${indent}${head} ${task ? "[ ] " : ""}`;
  }

  // 커서 위치에서 블록을 둘로 나눈다
  function splitBlock(ta, before, after) {
    const { el } = editing;
    if (!before.trim() && !after.trim()) return;
    if (!before.trim()) {
      // 맨 앞에서 Enter: 위에 빈 블록을 만들고 현재 블록 편집 유지
      const id = idOf(el);
      enqueue(() => createBlock("", { beforeId: id }));
      return;
    }
    ta.value = before;
    enqueue(async () => {
      const last = await commit();
      const newEl = await createBlock(after, last ? { afterId: idOf(last) } : {});
      startEdit(idOf(newEl), "start");
    });
  }

  function handleBackspace(e, ta) {
    const { el } = editing;
    const prev = el.previousElementSibling;
    const v = ta.value;

    if (!v) {
      // 빈 블록에서 Backspace: 블록 삭제 후 위 블록 편집
      e.preventDefault();
      enqueue(async () => {
        editing = null;
        await removeBlock(el);
        if (prev) startEdit(idOf(prev), "end");
      });
      return;
    }
    if (!prev) return;
    const prevBlock = blocks.get(idOf(prev));

    // 내용이 있으면 위 블록과 합치기
    e.preventDefault();
    const merged = prevBlock.content ? `${prevBlock.content}\n${v}` : v;
    const caret = prevBlock.content ? prevBlock.content.length + 1 : 0;
    enqueue(async () => {
      editing = null;
      await saveBlock(prev, merged);
      await removeBlock(el);
      startEdit(idOf(prev), caret);
    });
  }

  function indentLine(ta, outdent) {
    const { selectionStart: s, value: v } = ta;
    const lineStart = v.lastIndexOf("\n", s - 1) + 1;
    if (outdent) {
      const spaces = v.slice(lineStart).match(/^ {1,2}/);
      if (!spaces) return;
      ta.setRangeText("", lineStart, lineStart + spaces[0].length, "preserve");
      ta.setSelectionRange(Math.max(lineStart, s - spaces[0].length), Math.max(lineStart, s - spaces[0].length));
    } else {
      ta.setRangeText("  ", lineStart, lineStart, "preserve");
      ta.setSelectionRange(s + 2, s + 2);
    }
    autosize(ta);
  }

  // ---------- 체크박스 ----------
  function toggleTask(content, index, checked) {
    let count = 0;
    let inFence = false;
    return content
      .split("\n")
      .map((line) => {
        if (FENCE_RE.test(line)) inFence = !inFence;
        if (inFence) return line;
        const m = line.match(TASK_LINE_RE);
        if (!m) return line;
        if (count++ !== index) return line;
        return line.replace(TASK_LINE_RE, `${m[1]}[${checked ? "x" : " "}]`);
      })
      .join("\n");
  }

  container.addEventListener("change", (e) => {
    const cb = e.target.closest("input.task-check");
    if (!cb) return;
    const el = cb.closest(".block");
    const index = Number(cb.dataset.index);
    const checked = cb.checked;
    enqueue(() => saveBlock(el, toggleTask(blocks.get(idOf(el)).content, index, checked)));
  });

  // ---------- 클릭으로 편집 ----------
  container.addEventListener("click", (e) => {
    if (!editable) return;
    const del = e.target.closest(".block-delete");
    if (del) {
      const el = del.closest(".block");
      enqueue(async () => {
        if (editing && editing.el === el) editing = null;
        await removeBlock(el);
      });
      return;
    }
    const el = e.target.closest(".block");
    if (el && e.target.closest(".block-handle")) {
      enqueue(async () => {
        await commit();
        if (elFor(idOf(el))) clickSelect(idOf(el), e);
      }, { record: false });
      return;
    }
    if (!el || el.classList.contains("editing")) return;
    if (e.target.closest("a, input, button, iframe, .video-embed")) return;
    if (window.getSelection().toString()) return; // 텍스트 선택(복사) 중에는 편집 진입 안 함
    const id = idOf(el);
    enqueue(async () => {
      await commit();
      startEdit(id);
    });
  });

  addArea?.addEventListener("click", () => {
    enqueue(async () => {
      await commit();
      const last = container.lastElementChild;
      if (last && !blocks.get(idOf(last)).content) {
        startEdit(idOf(last));
        return;
      }
      const el = await createBlock("", last ? { afterId: idOf(last) } : {});
      startEdit(idOf(el));
    });
  });

  // ---------- 이미지 업로드 ----------
  async function uploadFile(file) {
    const form = new FormData();
    form.append("file", file);
    form.append("page_id", pageId);
    return api("POST", "/api/uploads", form);
  }

  const isImage = (f) => f.type.startsWith("image/");

  function onPaste(e) {
    const files = [...(e.clipboardData?.files || [])].filter(isImage);
    if (!files.length) return;
    e.preventDefault();
    files.forEach((f) => uploadIntoEditor(f));
  }

  // 편집 중인 블록에 "업로드 중" 자리표시를 넣고, 완료되면 이미지 문법으로 교체
  async function uploadIntoEditor(file) {
    const { id, ta } = editing;
    const token = `![업로드 중…](uploading-${Math.random().toString(36).slice(2, 10)})`;
    insertText(ta, token);
    let replacement = "";
    try {
      const r = await uploadFile(file);
      replacement = `![${r.name}](${r.url})`;
    } catch (err) {
      alert(err.message);
    }
    if (editing && editing.id === id) {
      const cur = editing.ta;
      const i = cur.value.indexOf(token);
      if (i >= 0) {
        cur.setRangeText(replacement, i, i + token.length, "end");
        autosize(cur);
      }
      return;
    }
    // 그새 편집을 마쳤다면 저장된 내용에서 교체
    enqueue(async () => {
      const b = blocks.get(id);
      if (b && b.content.includes(token)) await saveBlock(elFor(id), b.content.replace(token, replacement));
    });
  }

  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");

  pageEl.addEventListener("dragover", (e) => {
    if (editable && hasFiles(e)) e.preventDefault();
  });
  pageEl.addEventListener("drop", (e) => {
    if (!editable || !hasFiles(e)) return;
    e.preventDefault();
    // .md / .zip 은 이 노트가 있는 폴더로 가져오기
    const docs = local ? [] : [...e.dataTransfer.files].filter(window.isImportable);
    if (docs.length) {
      enqueue(async () => {
        await commit();
        if (await window.importFiles(docs, pageId)) window.reloadPage();
      });
      return;
    }
    const files = [...e.dataTransfer.files].filter(isImage);
    if (!files.length) return;
    if (editing && e.target === editing.ta) {
      files.forEach((f) => uploadIntoEditor(f));
      return;
    }
    const target = e.target.closest(".block");
    let afterId = target ? idOf(target) : container.lastElementChild ? idOf(container.lastElementChild) : null;
    enqueue(async () => {
      await commit();
      for (const f of files) {
        const r = await uploadFile(f);
        const el = await createBlock(`![${r.name}](${r.url})`, afterId != null ? { afterId } : {});
        afterId = idOf(el);
      }
    });
  });

  // ---------- 드래그로 블록 순서 변경 ----------
  let dragEl = null;
  const clearDropMarks = () =>
    container.querySelectorAll(".drop-before, .drop-after").forEach((n) => n.classList.remove("drop-before", "drop-after"));

  container.addEventListener("dragstart", (e) => {
    const handle = e.target.closest(".block-handle");
    if (!handle || !editable) return;
    dragEl = handle.closest(".block");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", "");
    e.dataTransfer.setDragImage(dragEl, 0, 0);
    dragEl.classList.add("dragging");
  });
  container.addEventListener("dragover", (e) => {
    if (!dragEl) return;
    e.preventDefault();
    clearDropMarks();
    const over = e.target.closest(".block");
    if (!over || over === dragEl) return;
    const r = over.getBoundingClientRect();
    over.classList.add(e.clientY < r.top + r.height / 2 ? "drop-before" : "drop-after");
  });
  container.addEventListener("drop", (e) => {
    if (!dragEl) return;
    e.preventDefault();
    e.stopPropagation();
    const over = container.querySelector(".drop-before, .drop-after");
    if (over) {
      if (over.classList.contains("drop-before")) over.before(dragEl);
      else over.after(dragEl);
      const order = [...container.children].map(idOf);
      enqueue(() => writeApi("POST", `/api/pages/${pageId}/blocks/reorder`, { order }));
    }
    clearDropMarks();
  });
  container.addEventListener("dragend", () => {
    dragEl?.classList.remove("dragging");
    dragEl = null;
    clearDropMarks();
  });

  // ---------- 제목 / 아이콘 ----------
  const titleInput = document.getElementById("page-title");
  let savedTitle = titleInput.value;

  function showTitle(text) {
    const shown = text || "제목 없음";
    document.querySelectorAll(`[data-title-for="${pageId}"]`).forEach((n) => (n.textContent = shown));
    document.title = `${shown} · MarkNote`;
  }

  let titleTimer = null;
  function saveTitle() {
    clearTimeout(titleTimer);
    const title = titleInput.value.trim();
    if (title === savedTitle) return;
    savedTitle = title;
    enqueue(async () => {
      try {
        const res = await writeApi("PATCH", `/api/pages/${pageId}`, { title }, { keepalive: true });
        window.updatePagePath?.(pageId, res.path, res.abs_path, res.url); // 제목 = 파일 이름 → 주소도 바뀜
        setTitleWarning("");
      } catch (err) {
        if (err.status !== 409) throw err;
        savedTitle = null; // 이름을 고치면 다시 저장 시도
        setTitleWarning(`${err.message} 다른 이름으로 바꿔 주세요 (저장되지 않음).`);
      }
    });
  }

  // 같은 폴더에 같은 이름이 있으면 제목 아래 경고 (입력은 막지 않음)
  const titleWarning = document.createElement("p");
  titleWarning.className = "title-warning";
  titleWarning.hidden = true;
  titleInput.after(titleWarning);
  function setTitleWarning(text) {
    titleWarning.hidden = !text;
    titleWarning.innerHTML = text ? `${icon("alert")}<span></span>` : "";
    if (text) titleWarning.querySelector("span").textContent = text;
    titleInput.classList.toggle("conflict", Boolean(text));
  }
  titleInput.addEventListener("input", () => {
    showTitle(titleInput.value.trim());
    clearTimeout(titleTimer);
    titleTimer = setTimeout(saveTitle, 800); // 제목도 입력 중 자동 저장
  });
  titleInput.addEventListener("blur", saveTitle);
  titleInput.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229 || e.key !== "Enter") return;
    e.preventDefault();
    titleInput.blur();
    const first = container.firstElementChild;
    if (first) moveTo(first, "start");
    else addArea?.click();
  });

  // ---------- 원문 전체 편집 ----------
  const sourceEditor = document.getElementById("source-editor");
  const sourceText = document.getElementById("source-text");
  const showSource = (on) => {
    sourceEditor.hidden = !on;
    container.hidden = on;
    if (addArea) addArea.hidden = on;
  };

  document.getElementById("toggle-source")?.addEventListener("click", () => {
    if (!sourceEditor.hidden) {
      showSource(false);
      return;
    }
    enqueue(async () => {
      await commit();
      const res = await api("GET", `/api/pages/${pageId}/source`);
      sourceText.value = res.markdown;
      showSource(true);
      autosize(sourceText);
      sourceText.focus();
    });
  });
  sourceText.addEventListener("input", () => autosize(sourceText));
  document.getElementById("source-cancel").addEventListener("click", () => showSource(false));
  document.getElementById("source-save").addEventListener("click", () => {
    setSource(sourceText.value).then(() => showSource(false));
  });

  // ---------- AI 도우미 답변을 페이지 끝에 추가 ----------
  window.editorAppend = (markdown) => {
    if (!editable) return Promise.reject(new Error("휴지통에 있는 페이지에는 추가할 수 없습니다."));
    return new Promise((resolve, reject) => {
      queue = queue
        .then(async () => {
          await commit();
          const last = container.lastElementChild;
          const el = await createBlock("", last ? { afterId: idOf(last) } : {});
          await saveBlock(el, markdown); // 빈 줄 기준으로 여러 블록으로 나뉨
          resolve();
        })
        .catch(reject);
    });
  };

  // ---------- 되돌리기 기록 (Ctrl+Z / Ctrl+Y 는 app.js) ----------
  // 페이지 전체 마크다운을 작업 전·후로 기억해 두고, 되돌리기 = 그 내용으로 원문 저장 후 블록 다시 불러오기
  let lastShot = "";
  function snapshot() {
    return [...container.children].map((el) => blocks.get(idOf(el))?.content || "").filter((c) => c.trim()).join("\n\n");
  }
  function recordHistory() {
    const now = snapshot();
    if (now === lastShot) return;
    const before = lastShot;
    lastShot = now;
    window.undoManager.push({ page: true, label: "블록 편집", undo: () => restoreShot(before), redo: () => restoreShot(now) });
  }
  async function loadBlocks() {
    const res = await api("GET", `/api/pages/${pageId}/blocks`);
    blocks.clear();
    selected.clear();
    container.replaceChildren();
    res.blocks.forEach((b) => {
      store(b);
      container.appendChild(createEl(b));
    });
    updateEmptyState();
    return res;
  }
  function restoreShot(markdown) {
    return enqueue(async () => {
      await commit();
      await writeApi("PUT", `/api/pages/${pageId}/source`, { markdown });
      await loadBlocks();
      lastShot = snapshot();
    }, { record: false });
  }
  // 페이지 전체를 이 마크다운으로 (되돌리기 기록 남음)
  function setSource(markdown) {
    return enqueue(async () => {
      await commit();
      await writeApi("PUT", `/api/pages/${pageId}/source`, { markdown });
      await loadBlocks();
    });
  }

  // ---------- 블록 고르기: 손잡이 클릭 · Esc · Ctrl+A → Delete / 복사 / 잘라내기 / 붙여넣기 / ↑↓ ----------
  const selected = new Set(); // 블록 번호
  let anchorId = null; // Shift 범위의 시작
  const orderedIds = () => [...container.children].map(idOf);
  function paintSelection() {
    container.querySelectorAll(".block").forEach((el) => el.classList.toggle("selected", selected.has(idOf(el))));
  }
  function selectBlocks(ids, anchor = ids[0]) {
    selected.clear();
    ids.forEach((id) => selected.add(id));
    anchorId = anchor ?? null;
    paintSelection();
    if (ids.length) {
      window.getSelection().removeAllRanges();
      document.activeElement?.blur?.();
      elFor(ids[ids.length - 1])?.scrollIntoView({ block: "nearest" });
    }
  }
  function clearSelection() {
    if (!selected.size) return;
    selected.clear();
    paintSelection();
  }
  function clickSelect(id, e) {
    const ids = orderedIds();
    if (e.shiftKey && anchorId != null && ids.includes(anchorId)) {
      const [a, b] = [ids.indexOf(anchorId), ids.indexOf(id)].sort((x, y) => x - y);
      selectBlocks(ids.slice(a, b + 1), anchorId);
    } else if (e.ctrlKey || e.metaKey) {
      const next = selected.has(id) ? [...selected].filter((x) => x !== id) : [...selected, id];
      selectBlocks(ids.filter((x) => next.includes(x)), id);
    } else {
      selectBlocks([id]);
    }
  }
  const selectedMarkdown = () => orderedIds().filter((id) => selected.has(id)).map((id) => blocks.get(id).content).join("\n\n");
  function deleteSelected() {
    const keep = orderedIds().filter((id) => !selected.has(id)).map((id) => blocks.get(id).content);
    const count = selected.size;
    clearSelection();
    return setSource(keep.join("\n\n")).then(() => window.toast(`블록 ${count}개를 지웠습니다`, { label: "되돌리기", action: () => window.undoManager.undo() }));
  }
  function pasteAfterSelection(text) {
    const ids = orderedIds();
    const last = Math.max(...ids.map((id, i) => (selected.has(id) ? i : -1)));
    const contents = ids.map((id) => blocks.get(id).content);
    contents.splice(last + 1, 0, text.replace(/\r\n/g, "\n").trim());
    clearSelection();
    return setSource(contents.filter((c) => c.trim()).join("\n\n"));
  }
  function moveSelection(dir, extend) {
    const ids = orderedIds();
    const idx = ids.map((id, i) => (selected.has(id) ? i : -1)).filter((i) => i >= 0);
    const edge = dir < 0 ? Math.min(...idx) - 1 : Math.max(...idx) + 1;
    if (edge < 0 || edge >= ids.length) return;
    if (!extend) return selectBlocks([ids[edge]]);
    const a = ids.indexOf(anchorId);
    const [from, to] = [a, edge].sort((x, y) => x - y);
    selectBlocks(ids.slice(from, to + 1), anchorId);
  }
  // 고른 블록째 위/아래로 (Ctrl+Shift+↑/↓) — 여러 개면 함께, 맨 끝에 닿은 쪽은 그대로
  function moveSelected(dir) {
    const els = [...container.children].filter((el) => selected.has(idOf(el)));
    if (dir > 0) els.reverse();
    let moved = false;
    for (const el of els) {
      const neighbor = dir < 0 ? el.previousElementSibling : el.nextElementSibling;
      if (!neighbor || selected.has(idOf(neighbor))) continue;
      if (dir < 0) el.after(neighbor);
      else el.before(neighbor);
      moved = true;
    }
    if (!moved) return;
    els[0].scrollIntoView({ block: "nearest" });
    const order = [...container.children].map(idOf);
    enqueue(() => writeApi("POST", `/api/pages/${pageId}/blocks/reorder`, { order }));
  }
  window.pageSelectAll = () => {
    if (!editable || editing || !sourceEditor.hidden || !container.children.length) return false;
    selectBlocks(orderedIds());
    return true;
  };
  const signal = window.pageSignal();
  const outsideKeys = (e) =>
    !editable || editing || !sourceEditor.hidden || !selected.size ||
    e.target.closest?.("input, textarea, select, [contenteditable='true'], .sidebar, .chat-panel, .ui-modal");
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.isComposing || e.keyCode === 229 || outsideKeys(e)) return;
    const mod = e.ctrlKey || e.metaKey;
    if ((e.key === "Delete" || e.key === "Backspace") && !mod) {
      e.preventDefault();
      deleteSelected();
    } else if (mod && !e.shiftKey && (window.keyIs(e, "c") || window.keyIs(e, "x"))) {
      if (window.getSelection().toString()) return; // 글자를 드래그해 골랐으면 그 글자 복사
      e.preventDefault();
      const cut = window.keyIs(e, "x");
      window.copyToClipboard(selectedMarkdown()).then(() => (cut ? deleteSelected() : window.toast(`블록 ${selected.size}개를 복사했습니다`)), (err) => alert(err.message));
    } else if ((e.key === "ArrowUp" || e.key === "ArrowDown") && mod && e.shiftKey) {
      e.preventDefault();
      moveSelected(e.key === "ArrowUp" ? -1 : 1);
    } else if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !mod) {
      e.preventDefault();
      moveSelection(e.key === "ArrowUp" ? -1 : 1, e.shiftKey);
    } else if (e.key === "Enter" && !mod) {
      e.preventDefault();
      const id = orderedIds().find((x) => selected.has(x));
      enqueue(async () => startEdit(id), { record: false });
    } else if (e.key === "Escape") {
      clearSelection();
    }
  }, { signal });
  document.addEventListener("paste", (e) => {
    if (outsideKeys(e)) return;
    const text = e.clipboardData?.getData("text/plain");
    if (!text?.trim()) return;
    e.preventDefault();
    pasteAfterSelection(text);
  }, { signal });
  document.addEventListener("mousedown", (e) => {
    if (selected.size && !e.target.closest(".block-handle, .ctx-menu, .toast")) clearSelection();
  }, { signal });

  // ---------- 우클릭 메뉴 ----------
  const K = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl";

  function copyText(text) {
    return navigator.clipboard.writeText(text);
  }

  // 블록 전체를 :::비공개 상자로 감싸기 / 풀기
  const SECRET_BOX_RE = /^\s{0,3}:::\s*비공개\s*\n([\s\S]*?)\n\s{0,3}:::\s*$/;
  function toggleSecretBox(content) {
    const m = content.match(SECRET_BOX_RE);
    return m ? m[1] : `:::비공개\n${content.trim()}\n:::`;
  }

  function askAI(text) {
    const quoted = window.maskSecrets(text) // 비공개 부분은 채팅에 옮기지 않음
      .split("\n")
      .map((line) => `> ${line}`)
      .join("\n");
    window.chatPrefill?.(`${quoted}\n\n`);
  }

  function addBlockNear(id, where) {
    enqueue(async () => {
      await commit();
      if (!elFor(id)) return; // 편집 중이던 빈 블록이라 지워진 경우
      const el = await createBlock("", { [where]: id });
      startEdit(idOf(el));
    });
  }

  function blockMenu(e, el) {
    const id = idOf(el);
    const b = blocks.get(id);
    const items = [];
    const selection = window.getSelection().toString();
    const link = e.target.closest("a[href]");
    const img = e.target.closest("img");

    if (selection) items.push({ label: "선택한 텍스트 복사", icon: "copy", hint: `${K}+C`, action: () => copyText(selection) });
    if (link) {
      items.push(
        { label: "링크 열기", icon: "external", action: () => window.open(link.href, link.target === "_blank" ? "_blank" : "_self") },
        { label: "링크 주소 복사", icon: "link", action: () => copyText(link.href) }
      );
    }
    if (img) items.push({ label: "이미지 새 탭에서 열기", icon: "image", action: () => window.open(img.src, "_blank") });
    if (items.length) items.push("sep");

    if (editable) {
      items.push(
        {
          label: "편집",
          icon: "pencil",
          hint: "클릭",
          action: () =>
            enqueue(async () => {
              await commit();
              startEdit(id);
            }),
        },
        { label: "위에 블록 추가", icon: "plus", action: () => addBlockNear(id, "beforeId") },
        { label: "아래에 블록 추가", icon: "plus", action: () => addBlockNear(id, "afterId") },
        { label: "복제", icon: "copy", hint: `${K}+D`, disabled: !b.content.trim(), action: () => duplicateBlock(el) },
        {
          label: SECRET_BOX_RE.test(b.content) ? "비공개 해제" : "비공개로 (AI 에게 보내지 않음)",
          icon: "lock",
          disabled: !b.content.trim() || (editing && editing.el === el),
          action: () => enqueue(() => saveBlock(el, toggleSecretBox(b.content))),
        },
        { label: "위로 이동", icon: "arrow-up", hint: `${K}+Shift+↑`, disabled: !el.previousElementSibling, action: () => moveBlock(el, -1) },
        { label: "아래로 이동", icon: "arrow-down", hint: `${K}+Shift+↓`, disabled: !el.nextElementSibling, action: () => moveBlock(el, 1) },
        "sep"
      );
    }
    items.push(
      { label: "마크다운 복사", icon: "code", action: () => copyText(b.content) },
      { label: "AI 에게 이 블록 묻기", icon: "sparkles", hint: `${K}+J`, disabled: !b.content.trim(), action: () => askAI(b.content) }
    );
    if (editable) {
      items.push("sep", {
        label: "삭제",
        icon: "trash",
        danger: true,
        action: () =>
          enqueue(async () => {
            if (editing && editing.el === el) editing = null;
            await removeBlock(el);
          }),
      });
    }
    return items;
  }

  function pageMenu() {
    const items = [];
    if (editable) {
      items.push(
        { label: "블록 추가", icon: "plus", action: () => addArea?.click() },
        {
          label: "마크다운 붙여넣기 (새 블록)",
          icon: "clipboard",
          action: async () => {
            const text = await navigator.clipboard.readText();
            if (text.trim()) await window.editorAppend(text);
          },
        },
        { label: "원문 편집", icon: "code", action: () => document.getElementById("toggle-source")?.click() },
        { label: "페이지 전체 마크다운 복사", icon: "copy", action: async () => window.copyText((await api("GET", `/api/pages/${pageId}/source`)).markdown) },
        "sep"
      );
    } else {
      items.push({ label: "페이지 전체 마크다운 복사", icon: "copy", action: async () => window.copyText((await api("GET", `/api/pages/${pageId}/source`)).markdown) }, "sep");
    }
    items.push(
      { label: "AI 도우미", icon: "sparkles", hint: `${K}+J`, action: () => document.querySelector("[data-chat-toggle]")?.click() },
      ...(local ? [] : [{ label: "Markdown 다운로드", icon: "download", action: () => (location.href = `/export/page/${pageId}.md`) }]),
      { label: "단축키 보기", icon: "keyboard", hint: `${K}+/`, action: () => window.openShortcutHelp?.() }
    );
    return items;
  }

  const linkItems = (a) => [
    { label: "열기", icon: "open", action: () => window.go(a.getAttribute("href")) },
    { label: "새 탭에서 열기", icon: "external", action: () => window.open(a.href, "_blank") },
  ];
  const clickAction = (selector) => () => document.querySelector(selector)?.click();

  // 상단 바 (경로 · 원문 편집 · ⋯) 와 보관/휴지통 안내 띠
  function pageTopMenu(e) {
    const crumb = e.target.closest(".breadcrumbs a");
    const items = crumb ? [...linkItems(crumb), "sep"] : [];
    const status = data.page.status;
    if (editable) {
      items.push(
        { label: "원문 편집", icon: "code", action: clickAction("#toggle-source") }
      );
    }
    if (local) {
      items.push("sep", { label: "휴지통으로 (연결만 끊기)", icon: "trash", danger: true, action: clickAction("[data-local-trash]") });
      return items;
    }
    items.push(
      { label: "페이지 링크 복사", icon: "link", action: () => window.copyText(location.origin + location.pathname) },
      "sep",
      { label: "Markdown(.md) 다운로드", icon: "download", action: () => (location.href = `/export/page/${pageId}.md`) },
      { label: "하위 포함 .zip 다운로드", icon: "package", action: () => (location.href = `/export/page/${pageId}.zip`) },
      "sep"
    );
    if (status === "active") {
      items.push(
        { label: "보관", icon: "archive", action: clickAction('.page-actions [data-action="archive"]') },
        { label: "휴지통으로 이동", icon: "trash", danger: true, action: clickAction('.page-actions [data-action="trash"]') }
      );
    } else {
      items.push({ label: "복원", icon: "undo", action: clickAction('.page-actions [data-action="restore"]') });
      if (status === "trashed") {
        items.push({ label: "영구 삭제", icon: "file-x", danger: true, action: clickAction('.page-actions [data-action="delete"]') });
      }
    }
    return items;
  }

  // 제목 영역 (제목 입력칸 자체는 브라우저 기본 메뉴)
  function pageHeadMenu() {
    if (!editable) return pageMenu();
    return [
      { label: "제목 편집", icon: "type", action: () => titleInput.focus() },
      "sep",
      { label: "첫 블록 편집", icon: "pencil", disabled: !container.firstElementChild, action: () => moveTo(container.firstElementChild, "start") },
    ];
  }

  // 이전/다음 페이지 영역
  function siblingMenu(e) {
    const link = e.target.closest(".sibling-nav a");
    if (link) return linkItems(link);
    const prev = document.querySelector(".sibling-nav .prev");
    const next = document.querySelector(".sibling-nav .next");
    return [
      { label: "이전 페이지", icon: "chevron-left", disabled: !prev, action: () => prev.click() },
      { label: "다음 페이지", icon: "chevron-right", disabled: !next, action: () => next.click() },
      "sep",
      { label: "페이지 빠른 이동", icon: "search", hint: `${K}+P`, action: () => window.openSwitcher?.() },
    ];
  }

  const menuProvider = (e) => {
    if (!e.target.closest(".content")) return null;
    const el = e.target.closest("#blocks .block");
    if (el) return el.classList.contains("editing") ? null : blockMenu(e, el);
    if (e.target.closest(".page-top, .banner")) return pageTopMenu(e);
    if (e.target.closest(".page-head")) return pageHeadMenu();
    if (e.target.closest(".sibling-nav")) return siblingMenu(e);
    if (e.target.closest("#source-editor")) return null; // 원문 편집 중에는 기본 메뉴
    return pageMenu(); // 본문 빈 곳 · 블록 추가 영역 · 여백
  };
  window.contextMenuProviders?.push(menuProvider);
  window.onPageLeave(() => window.contextMenuProviders.splice(window.contextMenuProviders.indexOf(menuProvider), 1));

  // ---------- AI 가 바로 고친 부분: 강조 + [되돌리기 | 확정] ----------
  // 되돌리기 = 버튼 또는 Ctrl+Z (블록 편집 중이 아닐 때), 확정 = 버튼 또는 Ctrl+S.
  // 직접 고쳐 저장하면 강조만 지운다 (되돌리기는 채팅 카드에서 계속 가능 — AI 가 바꾼 부분만 되돌림).
  let aiEdits = []; // 강조 중인 수정 (edit_dict)
  const aiBar = document.createElement("div");
  aiBar.className = "ai-edit-bar";
  aiBar.hidden = true;
  container.before(aiBar);

  const aiShown = () => aiEdits.some((e) => e.changed_block_ids.length);

  function renderAiEdits() {
    container.querySelectorAll(".block.ai-changed").forEach((el) => el.classList.remove("ai-changed"));
    aiEdits.forEach((e) => e.changed_block_ids.forEach((id) => elFor(id)?.classList.add("ai-changed")));
    aiBar.hidden = !aiShown();
    if (aiBar.hidden) return;
    const last = aiEdits[aiEdits.length - 1];
    const count = aiEdits.reduce((n, e) => n + e.changed_block_ids.length, 0);
    aiBar.innerHTML =
      `${icon("sparkles")}<span class="ai-edit-text"></span>` +
      `<button type="button" class="btn-ghost" data-ai-undo title="AI 가 고친 부분을 원래대로 (Ctrl+Z)">${icon("undo")}<span>되돌리기</span></button>` +
      `<button type="button" class="btn-primary" data-ai-confirm title="이대로 두고 표시 지우기 (Ctrl+S)">${icon("check")}<span>확정</span></button>`;
    aiBar.querySelector(".ai-edit-text").textContent =
      `AI 가 블록 ${count}개를 고쳤습니다${last.summary ? ` · ${last.summary}` : ""}`;
  }

  async function loadAiEdits() {
    if (!editable) return;
    const res = await api("GET", `/api/ai/pages/${pageId}/note-edits`);
    aiEdits = res.edits;
    renderAiEdits();
  }
  const announce = (edit) => document.dispatchEvent(new CustomEvent("note-edit-changed", { detail: edit }));

  async function undoAiEdit() {
    const edit = [...aiEdits].reverse().find((e) => e.changed_block_ids.length);
    if (!edit) return;
    await window.flushEditor();
    let res;
    try {
      res = await api("POST", `/api/ai/note-edits/${edit.id}/undo`, {});
    } catch (err) {
      if (err.status !== 409) throw err;
      if (!confirm(`${err.message}\n그래도 AI 수정 전으로 되돌릴까요?`)) return;
      res = await api("POST", `/api/ai/note-edits/${edit.id}/undo`, { force: true });
    }
    announce(res.edit);
    if (res.edit.kind === "create") window.go("/");
    else await window.editorReload(); // 다시 불러오면서 강조도 새로 받음
  }

  async function confirmAiEdits() {
    const res = await api("POST", `/api/ai/pages/${pageId}/note-edits/confirm`, {});
    aiEdits = [];
    renderAiEdits();
    res.edits.forEach(announce);
  }

  // 직접 고쳐 저장 → 강조만 지움
  function userSaved() {
    if (!aiShown()) return;
    aiEdits.forEach((e) => (e.changed_block_ids = []));
    renderAiEdits();
    api("POST", `/api/ai/pages/${pageId}/note-edits/seen`, {}).then((res) => res.edits.forEach(announce), console.error);
  }
  function writeApi(...args) {
    return api(...args).then((res) => {
      userSaved();
      return res;
    });
  }

  aiBar.addEventListener("click", (e) => {
    if (e.target.closest("[data-ai-undo]")) undoAiEdit().catch((err) => alert(err.message));
    else if (e.target.closest("[data-ai-confirm]")) confirmAiEdits().catch((err) => alert(err.message));
  });
  document.addEventListener(
    "keydown",
    (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || !aiShown()) return;
      const key = e.key.toLowerCase();
      if (key === "s") {
        // 블록 편집 중이면 그 블록 저장(기존 동작)도 함께 일어남
        if (!editing) e.preventDefault();
        confirmAiEdits().catch((err) => alert(err.message));
      }
    },
    { capture: true, signal: window.pageSignal() }
  );
  // Ctrl+Z (app.js): AI 가 고친 부분이 강조돼 있으면 그 수정부터 되돌린다
  window.pageAiUndo = () => {
    if (!aiShown()) return false;
    undoAiEdit().catch((err) => alert(err.message));
    return true;
  };
  window.refreshAiEdits = () => loadAiEdits().catch(console.error);

  // ---------- AI 수정 제안이 이 페이지에 적용되면 다시 불러오기 ----------
  window.editorReload = () =>
    new Promise((resolve, reject) => {
      queue = queue
        .then(async () => {
          await commit();
          const res = await loadBlocks();
          lastShot = snapshot(); // 바깥(AI 등)에서 바뀐 내용 — 그 전 기록으로 되돌리면 AI 수정을 덮으므로 새로 시작
          window.undoManager.dropPage();
          data.page = res.page;
          titleInput.value = res.page.title;
          savedTitle = res.page.title;
          showTitle(res.page.title);
          await loadAiEdits().catch(console.error);
          resolve();
        })
        .catch(reject);
    });
  window.editorPageId = pageId;

  // ---------- 초기 렌더링 ----------
  for (const b of data.blocks) {
    store(b);
    container.appendChild(createEl(b));
  }
  updateEmptyState();
  lastShot = snapshot();
  if (editable && !data.page.title && !data.blocks.length) titleInput.focus();
  loadAiEdits().catch(console.error);
})();
