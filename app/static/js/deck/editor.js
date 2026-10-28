// 프레젠테이션 편집기 (PowerPoint 기본 보기 기준)
// 모든 변경은 작업(op)으로 서버에 보낸다: add_slide / delete_slide / move_slide / update_slide / set_elements …
(() => {
  const R = window.DeckRender;
  const { W, H } = R;
  const boot = JSON.parse(document.getElementById("deck-data").textContent);
  const deck = boot.deck;
  const LAYOUTS = boot.layouts;
  const FONTS = boot.fonts;
  const editable = deck.status !== "trashed";
  const K = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl";

  const $ = (s) => document.querySelector(s);
  const stageWrap = $("#pp-stage-wrap");
  const stage = $("#pp-stage");
  const thumbs = $("#pp-thumbs");
  const fileInput = $("#pp-file");

  const slides = deck.slides;
  let current = new URLSearchParams(location.search).get("slide");
  if (!slides.some((s) => s.id === current)) current = slides[0]?.id || null;
  let selection = []; // 선택한 개체 ID
  let editingId = null; // 글자 편집 중인 개체
  let slideRoot = null; // 슬라이드 창에 그려진 슬라이드
  let overlay = null; // 선택 테두리·핸들 층
  let zoom = 1;
  let fitMode = true;
  let insertMode = null; // {type:"text"} 또는 {type:"shape", shape}
  let clipboard = null; // {kind:"elements", items} 또는 {kind:"slide", slide}
  let thumbsFocused = false; // 마지막으로 누른 곳이 축소판 창인지

  const SIZES = [8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 44, 48, 54, 60, 66, 72, 80, 88, 96];
  const SHAPES = [
    ["rect", "직사각형"],
    ["round_rect", "사각형: 둥근 모서리"],
    ["ellipse", "타원"],
    ["triangle", "이등변 삼각형"],
    ["pentagon", "오각형"],
    ["right_arrow", "화살표: 오른쪽"],
    ["line", "직선 연결선"],
    ["arrow_line", "직선 화살표 연결선"],
  ];
  const SHAPE_NAMES = Object.fromEntries(SHAPES);

  const slideById = (id) => slides.find((s) => s.id === id);
  const cur = () => slideById(current);
  const elById = (id, slide = cur()) => slide?.elements.find((e) => e.id === id);
  const selectedEls = () => selection.map((id) => elById(id)).filter(Boolean);
  const newId = (prefix) => prefix + Array.from(crypto.getRandomValues(new Uint8Array(5)), (b) => b.toString(16).padStart(2, "0")).join("");
  const clone = (v) => JSON.parse(JSON.stringify(v));
  const hasText = (el) => el.type === "text" || (el.type === "shape" && !R.LINE_SHAPES.has(el.shape));

  // ---------- 저장 (작업 큐 · 자동 저장) ----------
  const saveLabel = $("#pp-save");
  const SAVE_TEXT = { pending: "저장 대기", saving: "저장 중…", saved: "저장됨", error: "저장 실패 — 다시 시도하는 중" };
  let queue = [];
  let inflight = null;
  let saveTimer = null;
  let stopped = false; // 서버가 작업을 거절하면 더 보내지 않음

  function setSave(state, title = "") {
    saveLabel.textContent = SAVE_TEXT[state];
    saveLabel.dataset.state = state;
    saveLabel.title = title;
  }

  function push(op) {
    if (!editable || stopped) return;
    // 같은 슬라이드의 개체 전체 교체는 마지막 것만 있으면 된다
    if (op.op === "set_elements" || op.op === "delete_slide") {
      queue = queue.filter((o) => !(o.op === "set_elements" && o.slide === op.slide));
    }
    queue.push(op);
    setSave("pending");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 800);
  }

  function changed(slide = cur()) {
    push({ op: "set_elements", slide: slide.id, elements: slide.elements });
    refreshThumb(slide.id);
  }

  async function flush() {
    clearTimeout(saveTimer);
    if (inflight) {
      await inflight;
      return queue.length ? flush() : undefined;
    }
    if (!queue.length || stopped) return;
    const batch = JSON.parse(JSON.stringify(queue)); // 보내는 순간의 상태로 고정
    queue = [];
    setSave("saving");
    inflight = api("POST", `/api/decks/${deck.id}/ops`, { ops: batch }, { keepalive: true })
      .then(() => setSave(queue.length ? "pending" : "saved"))
      .catch((err) => {
        if (err.status && err.status < 500) {
          stopped = true; // 잘못된 작업 — 되풀이해도 소용없음
          setSave("error", err.message);
          saveLabel.textContent = "저장하지 못함";
          alert(`변경 내용을 저장하지 못했습니다: ${err.message}\n새로고침하면 마지막으로 저장된 상태로 돌아갑니다.`);
          return;
        }
        queue = batch.concat(queue); // 네트워크 문제 — 다시 시도
        setSave("error", err.message);
        saveTimer = setTimeout(flush, 3000);
      })
      .finally(() => {
        inflight = null;
      });
    await inflight;
    if (queue.length && !stopped) return flush();
  }
  window.flushEditor = () => {
    if (editingId) stopEdit();
    return flush();
  };
  // AI 도우미가 질문 전에 부르는 저장: 글자 편집을 끝내지 않고 대기 중인 변경만 보냄
  window.saveEditorNow = () => flush();
  window.editorDeckId = deck.id;
  // AI 가 슬라이드를 고쳤거나 되돌렸을 때: 저장할 것을 먼저 보내고 서버의 최신 상태로 다시 그림
  window.deckReload = async () => {
    if (editingId) stopEdit();
    await flush();
    const data = await api("GET", `/api/decks/${deck.id}`);
    slides.splice(0, slides.length, ...data.deck.slides);
    deck.version = data.deck.version;
    if (!slideById(current)) current = slides[0]?.id || null;
    selection = [];
    renderThumbs();
    renderStage();
  };

  // ---------- 지금 위치 · 선택한 글자 (AI 도우미용) ----------
  let lastSelection = "";
  document.addEventListener("selectionchange", () => {
    const sel = window.getSelection();
    if (!sel.rangeCount || !stage.contains(sel.anchorNode)) return; // 채팅창 등 다른 곳 선택은 무시
    lastSelection = window.selectionWithoutSecrets(sel).trim().slice(0, 2000);
    window.setChatSelection?.(lastSelection);
  });
  window.getEditorLocation = () => ({
    view: "deck",
    deck_id: deck.id,
    slide_index: slides.findIndex((s) => s.id === current) + 1,
    slide_count: slides.length,
    selected: selectedEls().map((el) => el.name).slice(0, 20),
    editing: editingId ? elById(editingId)?.name || null : null,
  });
  window.addEventListener("beforeunload", (e) => {
    if (queue.length || inflight) {
      flush();
      e.preventDefault();
      e.returnValue = "";
    }
  });

  // ---------- 축소판 창 ----------
  const thumbTimers = new Map();

  function thumbItem(slide, index) {
    const item = document.createElement("div");
    item.className = `pp-thumb${slide.id === current ? " active" : ""}${slide.hidden ? " is-hidden" : ""}`;
    item.dataset.id = slide.id;
    item.draggable = editable;
    item.innerHTML = `<span class="pp-thumb-no">${index + 1}</span>`;
    item.appendChild(R.renderThumb(slide, 168));
    return item;
  }

  function renderThumbs() {
    thumbs.replaceChildren(...slides.map(thumbItem));
    thumbs.querySelector(".active")?.scrollIntoView({ block: "nearest" });
  }

  function refreshThumb(id) {
    clearTimeout(thumbTimers.get(id));
    thumbTimers.set(
      id,
      setTimeout(() => {
        const item = thumbs.querySelector(`.pp-thumb[data-id="${id}"]`);
        const slide = slideById(id);
        if (item && slide) item.replaceWith(thumbItem(slide, slides.indexOf(slide)));
      }, 120)
    );
  }

  // ---------- 슬라이드 창 ----------
  function renderStage() {
    const slide = cur();
    stage.replaceChildren();
    slideRoot = null;
    overlay = null;
    if (!slide) {
      stage.innerHTML = '<div class="pp-no-slide">슬라이드를 추가하려면 클릭하십시오</div>';
      updateStatus();
      return;
    }
    slideRoot = R.renderSlide(slide, "edit", "ed-");
    overlay = document.createElement("div");
    overlay.className = "pp-overlay";
    slideRoot.appendChild(overlay);
    stage.appendChild(slideRoot);
    applyZoom();
    drawSelection();
    updateStatus();
    syncRibbon();
  }

  function nodeOf(id) {
    return slideRoot?.querySelector(`.pp-el[data-id="${id}"]`);
  }

  // 개체 하나만 다시 그리기 (크기가 바뀌면 도형 SVG 도 다시 만들어야 해서)
  function redrawElement(el) {
    const old = nodeOf(el.id);
    const fresh = R.renderElement(el, "edit", "ed-");
    if (old && fresh) old.replaceWith(fresh);
  }

  function updateStatus() {
    const index = slides.findIndex((s) => s.id === current);
    $("#pp-slide-no").textContent = slides.length ? `슬라이드 ${index + 1} / ${slides.length}` : "슬라이드 없음";
  }

  function selectSlide(id, { keepFocus = false } = {}) {
    if (editingId) stopEdit();
    current = id;
    selection = [];
    thumbs.querySelectorAll(".pp-thumb").forEach((t) => t.classList.toggle("active", t.dataset.id === id));
    thumbs.querySelector(".active")?.scrollIntoView({ block: "nearest" });
    renderStage();
    if (keepFocus) thumbs.focus();
  }

  // ---------- 확대/축소 ----------
  const zoomInput = $("#pp-zoom");
  function fitZoom() {
    const aw = stageWrap.clientWidth - 64;
    const ah = stageWrap.clientHeight - 64;
    zoom = Math.max(0.1, Math.min(aw / W, ah / H));
    applyZoom();
  }
  function applyZoom() {
    stage.style.width = `${W * zoom}px`;
    stage.style.height = `${H * zoom}px`;
    stage.style.setProperty("--z", zoom);
    if (slideRoot) slideRoot.style.transform = `scale(${zoom})`;
    zoomInput.value = Math.round(zoom * 100);
    $("#pp-zoom-label").textContent = `${Math.round(zoom * 100)}%`;
  }
  function setZoom(value) {
    fitMode = false;
    zoom = Math.max(0.1, Math.min(3, value));
    applyZoom();
  }
  new ResizeObserver(() => fitMode && fitZoom()).observe(stageWrap);
  zoomInput.addEventListener("input", () => setZoom(zoomInput.value / 100));

  // ---------- 선택 표시 ----------
  const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

  function setSelection(ids) {
    selection = ids;
    drawSelection();
    syncRibbon();
  }

  function drawSelection() {
    if (!overlay) return;
    overlay.replaceChildren();
    const single = selection.length === 1;
    selectedEls().forEach((el) => {
      const box = document.createElement("div");
      box.className = `pp-sel${el.id === editingId ? " editing" : ""}${R.LINE_SHAPES.has(el.shape) ? " line" : ""}`;
      box.style.left = `${el.x}px`;
      box.style.top = `${el.y}px`;
      box.style.width = `${el.w}px`;
      box.style.height = `${el.h}px`;
      if (el.rotate) box.style.transform = `rotate(${el.rotate}deg)`;
      if (single && editable) {
        HANDLES.forEach((dir) => {
          const h = document.createElement("div");
          h.className = `pp-h pp-h-${dir}`;
          h.dataset.dir = dir;
          h.dataset.id = el.id;
          box.appendChild(h);
        });
      }
      overlay.appendChild(box);
    });
  }

  // ---------- 좌표 ----------
  function toSlide(e) {
    const rect = slideRoot.getBoundingClientRect();
    return { x: (e.clientX - rect.left) / zoom, y: (e.clientY - rect.top) / zoom };
  }

  // ---------- 포인터: 선택 · 이동 · 크기 조절 · 그리기 ----------
  stageWrap.addEventListener("pointerdown", (e) => {
    thumbsFocused = false;
    if (e.button !== 0) return;
    if (!slideRoot) {
      if (editable) addSlide();
      return;
    }
    const handle = e.target.closest(".pp-h");
    if (handle && editable) return startResize(e, handle.dataset.id, handle.dataset.dir);
    if (insertMode && editable) return startDraw(e);

    const node = e.target.closest(".pp-el");
    if (node && node.dataset.id === editingId) {
      // 글자 편집 중: 글자 위면 브라우저 기본 동작, 도형의 빈 곳이면 포커스를 잃지 않게 커서만 옮김
      if (!e.target.closest(".pp-text-content")) {
        e.preventDefault();
        placeCaret(contentOf(editingId), e);
      }
      return;
    }
    if (editingId) stopEdit();
    if (!node) {
      setSelection([]);
      return;
    }
    const id = node.dataset.id;
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      setSelection(selection.includes(id) ? selection.filter((s) => s !== id) : [...selection, id]);
      return;
    }
    const wasOnlySelected = selection.length === 1 && selection[0] === id;
    if (!selection.includes(id)) setSelection([id]);
    startMove(e, id, wasOnlySelected);
  });

  function track(e, onMove, onUp) {
    e.preventDefault();
    stageWrap.setPointerCapture(e.pointerId);
    const move = (ev) => onMove(ev);
    const up = (ev) => {
      stageWrap.removeEventListener("pointermove", move);
      stageWrap.removeEventListener("pointerup", up);
      stageWrap.removeEventListener("pointercancel", up);
      onUp(ev);
    };
    stageWrap.addEventListener("pointermove", move);
    stageWrap.addEventListener("pointerup", up);
    stageWrap.addEventListener("pointercancel", up);
  }

  function startMove(e, id, wasOnlySelected) {
    const start = toSlide(e);
    const startScreen = { x: e.clientX, y: e.clientY };
    const origins = selectedEls().map((el) => ({ el, x: el.x, y: el.y }));
    let moving = false;
    track(
      e,
      (ev) => {
        if (!editable) return;
        if (!moving && Math.hypot(ev.clientX - startScreen.x, ev.clientY - startScreen.y) < 3) return;
        moving = true;
        const p = toSlide(ev);
        let dx = p.x - start.x;
        let dy = p.y - start.y;
        if (ev.shiftKey) {
          if (Math.abs(dx) > Math.abs(dy)) dy = 0; // Shift: 가로·세로로만
          else dx = 0;
        }
        origins.forEach(({ el, x, y }) => {
          el.x = Math.round(x + dx);
          el.y = Math.round(y + dy);
          const node = nodeOf(el.id);
          node.style.left = `${el.x}px`;
          node.style.top = `${el.y}px`;
        });
        drawSelection();
      },
      () => {
        if (moving) {
          changed();
          return;
        }
        const el = elById(id);
        if (!el || !editable) return;
        const emptyPlaceholder = el.placeholder && el.type === "text" && R.isEmptyText(el.text);
        if (el.type === "image" && !el.src) pickImage(el.id); // 그림 개체 틀
        else if (emptyPlaceholder || (wasOnlySelected && hasText(el))) startEdit(id, e); // 빈 개체 틀은 바로, 그 외엔 다시 클릭하면 글자 편집
      }
    );
  }

  function startResize(e, id, dir) {
    const el = elById(id);
    const s = { x: el.x, y: el.y, w: el.w, h: el.h };
    const start = toSlide(e);
    const ratio = s.w / Math.max(s.h, 1);
    const isLine = el.type === "shape" && R.LINE_SHAPES.has(el.shape);
    track(
      e,
      (ev) => {
        const p = toSlide(ev);
        let dx = p.x - start.x;
        let dy = p.y - start.y;
        let { x, y, w, h } = s;
        if (dir.includes("e")) w = s.w + dx;
        if (dir.includes("s")) h = s.h + dy;
        if (dir.includes("w")) w = s.w - dx;
        if (dir.includes("n")) h = s.h - dy;
        const corner = dir.length === 2;
        // Shift = 비율 유지, 그림은 모서리 핸들에서 기본으로 비율 유지 (PowerPoint 와 같음)
        if (corner && !isLine && (ev.shiftKey || el.type === "image")) {
          if (Math.abs(w / ratio) > Math.abs(h)) h = w / ratio;
          else w = h * ratio;
        }
        const min = isLine ? 0 : 8;
        w = Math.max(min, w);
        h = Math.max(min, h);
        if (ev.ctrlKey) {
          // Ctrl = 가운데를 기준으로
          x = s.x + (s.w - w) / 2;
          y = s.y + (s.h - h) / 2;
        } else {
          if (dir.includes("w")) x = s.x + s.w - w;
          if (dir.includes("n")) y = s.y + s.h - h;
        }
        Object.assign(el, { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) });
        redrawElement(el);
        drawSelection();
      },
      () => changed()
    );
  }

  function nextNumber(slide = cur()) {
    const numbers = slide.elements.map((e) => Number((e.name || "").match(/(\d+)$/)?.[1] || 0));
    return Math.max(slide.elements.length, ...numbers) + 1;
  }

  function newElement(kind, rect) {
    const n = nextNumber();
    if (kind.type === "text") {
      return {
        id: newId("e"), type: "text", name: `TextBox ${n}`, placeholder: null, ...rect, rotate: 0, text: "", src: null,
        style: { fontFamily: FONTS[0], fontSize: 18, color: "#000000", align: "left", valign: "top" },
      };
    }
    const line = R.LINE_SHAPES.has(kind.shape);
    return {
      id: newId("e"), type: "shape", shape: kind.shape, name: `${SHAPE_NAMES[kind.shape]} ${n}`, placeholder: null,
      ...rect, rotate: 0, text: "", src: null,
      style: line
        ? { fontFamily: FONTS[0], fontSize: 18, color: "#000000", align: "center", valign: "middle", fill: null, line: "#4472c4", lineWidth: 1.5, ...rect.flip }
        : { fontFamily: FONTS[0], fontSize: 18, color: "#ffffff", align: "center", valign: "middle", fill: "#4472c4", line: "#2f528f", lineWidth: 1.5 },
    };
  }

  function startDraw(e) {
    const kind = insertMode;
    setInsertMode(null);
    const start = toSlide(e);
    const ghost = document.createElement("div");
    ghost.className = "pp-ghost";
    overlay.appendChild(ghost);
    let rect = null;
    track(
      e,
      (ev) => {
        const p = toSlide(ev);
        let w = p.x - start.x;
        let h = p.y - start.y;
        if (ev.shiftKey && kind.type === "shape") {
          const size = Math.max(Math.abs(w), Math.abs(h));
          w = Math.sign(w || 1) * size;
          h = Math.sign(h || 1) * size;
        }
        rect = { x: Math.min(start.x, start.x + w), y: Math.min(start.y, start.y + h), w: Math.abs(w), h: Math.abs(h), flipH: w < 0, flipV: h < 0 };
        Object.assign(ghost.style, { left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.w}px`, height: `${rect.h}px` });
      },
      () => {
        ghost.remove();
        const line = kind.type === "shape" && R.LINE_SHAPES.has(kind.shape);
        if (!rect || (rect.w < 4 && rect.h < 4)) {
          // 클릭만 했으면 기본 크기 (텍스트 상자는 글자에 맞춰 늘어남)
          const size = kind.type === "text" ? { w: 300, h: 40 } : line ? { w: 200, h: 0 } : { w: 160, h: 160 };
          rect = { x: start.x, y: start.y, ...size };
        }
        const r = { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.w), h: Math.round(rect.h) };
        if (line) r.flip = { flipH: Boolean(rect.flipH), flipV: Boolean(rect.flipV) };
        const el = newElement(kind, r);
        delete el.flip;
        addElements([el]);
        if (kind.type === "text") startEdit(el.id);
      }
    );
  }

  function setInsertMode(mode) {
    insertMode = mode;
    stageWrap.classList.toggle("pp-drawing", Boolean(mode));
  }

  // ---------- 글자 편집 ----------
  function contentOf(id) {
    return nodeOf(id)?.querySelector(".pp-text-content");
  }

  function placeCaret(content, e) {
    const range = e && document.caretRangeFromPoint ? document.caretRangeFromPoint(e.clientX, e.clientY) : null;
    const selectionObj = window.getSelection();
    if (range && content.contains(range.startContainer)) {
      selectionObj.removeAllRanges();
      selectionObj.addRange(range);
      return;
    }
    const end = document.createRange();
    end.selectNodeContents(content);
    end.collapse(false);
    selectionObj.removeAllRanges();
    selectionObj.addRange(end);
  }

  function startEdit(id, e = null) {
    const el = elById(id);
    if (!el || !hasText(el) || !editable) return;
    const node = nodeOf(id);
    const content = contentOf(id);
    if (!content) return;
    editingId = id;
    setSelection([id]);
    node.querySelector(".pp-text")?.classList.remove("is-empty");
    if (el.style.bullets && !content.querySelector("ul, ol")) {
      content.innerHTML = `<ul><li>${content.innerHTML || "<br>"}</li></ul>`;
    }
    content.contentEditable = "true";
    content.focus();
    placeCaret(content, e);
    drawSelection();
  }

  function stopEdit() {
    const id = editingId;
    if (!id) return;
    editingId = null;
    const el = elById(id);
    const content = contentOf(id);
    if (el && content) {
      content.contentEditable = "false";
      el.text = R.isEmptyText(content.innerHTML) ? "" : content.innerHTML;
      redrawElement(el); // 비어 있으면 개체 틀 안내 문구가 다시 보이도록
      changed();
    }
    window.getSelection().removeAllRanges();
    drawSelection();
  }

  // 텍스트 상자(개체 틀이 아닌 것)는 PowerPoint 처럼 글자에 맞게 높이가 늘어난다
  function autoFit(el) {
    if (el.type !== "text" || el.placeholder) return;
    const node = nodeOf(el.id);
    const box = node?.querySelector(".pp-text");
    if (!box) return;
    node.style.height = "auto";
    const needed = Math.ceil(box.offsetHeight);
    el.h = Math.max(needed, 20);
    node.style.height = `${el.h}px`;
  }

  stage.addEventListener("input", (e) => {
    const content = e.target.closest(".pp-text-content");
    if (!content || !editingId) return;
    const el = elById(editingId);
    el.text = content.innerHTML;
    autoFit(el);
    drawSelection();
    push({ op: "set_elements", slide: current, elements: cur().elements });
    refreshThumb(current);
  });

  stage.addEventListener("dblclick", (e) => {
    const node = e.target.closest(".pp-el");
    if (node && node.dataset.id !== editingId) startEdit(node.dataset.id, e);
  });

  // 편집 중인 글상자 밖으로 포커스가 나가면 (리본의 목록 상자 등) 편집 끝내기
  stage.addEventListener("focusout", (e) => {
    if (!editingId || !e.target.closest(".pp-text-content")) return;
    setTimeout(() => {
      if (editingId && !stage.contains(document.activeElement)) stopEdit();
    }, 0);
  });

  // ---------- 개체 추가·삭제·순서 ----------
  function addElements(els) {
    cur().elements.push(...els);
    els.forEach((el) => {
      const node = R.renderElement(el, "edit", "ed-");
      if (node) slideRoot.insertBefore(node, overlay);
    });
    setSelection(els.map((e) => e.id));
    changed();
  }

  function deleteSelected() {
    const ids = new Set(selection);
    if (!ids.size) return;
    const slide = cur();
    slide.elements = slide.elements.filter((e) => !ids.has(e.id));
    selection = [];
    renderStage();
    changed(slide);
  }

  function arrange(where) {
    const slide = cur();
    const ids = new Set(selection);
    const picked = slide.elements.filter((e) => ids.has(e.id));
    const rest = slide.elements.filter((e) => !ids.has(e.id));
    if (!picked.length) return;
    if (where === "front") slide.elements = [...rest, ...picked];
    else if (where === "back") slide.elements = [...picked, ...rest];
    else {
      // 한 단계 앞으로/뒤로
      const els = slide.elements;
      const order = where === "forward" ? [...els.keys()].reverse() : [...els.keys()];
      order.forEach((i) => {
        const j = where === "forward" ? i + 1 : i - 1;
        if (ids.has(els[i].id) && els[j] && !ids.has(els[j].id)) [els[i], els[j]] = [els[j], els[i]];
      });
    }
    renderStage();
    changed(slide);
  }

  function nudge(dx, dy) {
    selectedEls().forEach((el) => {
      el.x += dx;
      el.y += dy;
      const node = nodeOf(el.id);
      node.style.left = `${el.x}px`;
      node.style.top = `${el.y}px`;
    });
    drawSelection();
    changed();
  }

  function pasteElements(items, offset) {
    const copies = items.map((item) => ({ ...clone(item), id: newId("e"), placeholder: null, x: item.x + offset, y: item.y + offset }));
    addElements(copies);
  }

  function duplicateSelected() {
    const items = selectedEls();
    if (items.length) pasteElements(items, 20);
  }

  // ---------- 슬라이드 추가·삭제·이동 ----------
  function slideFromLayout(layoutId) {
    const layout = LAYOUTS.find((l) => l.id === layoutId) || LAYOUTS[1];
    return {
      id: newId("s"),
      layout: layout.id,
      hidden: false,
      notes: "",
      background: null,
      elements: layout.placeholders.map((ph, i) => ({
        id: newId("e"),
        type: ph.role === "picture" ? "image" : "text",
        name: `${ph.name} ${i + 1}`,
        placeholder: ph.role,
        x: ph.x, y: ph.y, w: ph.w, h: ph.h, rotate: 0, text: "", src: null,
        style: { fontFamily: FONTS[0], fontSize: 18, color: "#000000", align: "left", valign: "top", ...ph.style },
      })),
    };
  }

  function addSlide(layoutId) {
    const base = cur();
    // 기본: 제목 슬라이드 다음엔 "제목 및 내용", 그 외엔 현재 슬라이드와 같은 레이아웃 (PowerPoint 와 같음)
    const layout = layoutId || (!base || base.layout === "title_slide" ? "title_content" : base.layout);
    const slide = slideFromLayout(layout);
    const index = base ? slides.indexOf(base) + 1 : slides.length;
    slides.splice(index, 0, slide);
    push({ op: "add_slide", id: slide.id, index, layout, elements: slide.elements });
    renderThumbs();
    selectSlide(slide.id);
  }

  function duplicateSlide(id = current) {
    const source = slideById(id);
    if (!source) return;
    const copy = { ...clone(source), id: newId("s") };
    const index = slides.indexOf(source) + 1;
    slides.splice(index, 0, copy);
    push({ op: "add_slide", id: copy.id, index, layout: copy.layout, elements: copy.elements });
    if (copy.notes || copy.hidden || copy.background) {
      push({ op: "update_slide", slide: copy.id, set: { notes: copy.notes, hidden: copy.hidden, background: copy.background } });
    }
    renderThumbs();
    selectSlide(copy.id);
  }

  function deleteSlide(id = current) {
    const index = slides.findIndex((s) => s.id === id);
    if (index < 0) return;
    slides.splice(index, 1);
    push({ op: "delete_slide", slide: id });
    renderThumbs();
    const next = slides[Math.min(index, slides.length - 1)];
    current = next?.id || null;
    selectSlide(current, { keepFocus: thumbsFocused });
  }

  function moveSlide(id, to) {
    const from = slides.findIndex((s) => s.id === id);
    if (from < 0) return;
    const [slide] = slides.splice(from, 1);
    to = Math.max(0, Math.min(to, slides.length));
    slides.splice(to, 0, slide);
    push({ op: "move_slide", slide: id, to });
    renderThumbs();
    updateStatus();
  }

  // 레이아웃 변경: 같은 역할의 개체 틀은 내용 유지, 위치만 새 레이아웃대로 (서버의 apply_layout 과 같은 규칙)
  function applyLayout(layoutId, { reset = false } = {}) {
    const slide = cur();
    const layout = LAYOUTS.find((l) => l.id === layoutId);
    if (!slide || !layout) return;
    const byRole = new Map(slide.elements.filter((e) => e.placeholder).map((e) => [e.placeholder, e]));
    layout.placeholders.forEach((ph) => {
      const existing = byRole.get(ph.role);
      if (existing) {
        Object.assign(existing, { x: ph.x, y: ph.y, w: ph.w, h: ph.h, rotate: 0 });
        if (reset) existing.style = { ...existing.style, fontFamily: FONTS[0], fontSize: 18, color: "#000000", align: "left", valign: "top", bold: false, italic: false, underline: false, strike: false, ...ph.style };
      } else {
        slide.elements.push({
          id: newId("e"), type: ph.role === "picture" ? "image" : "text", name: `${ph.name} ${nextNumber(slide)}`,
          placeholder: ph.role, x: ph.x, y: ph.y, w: ph.w, h: ph.h, rotate: 0, text: "", src: null,
          style: { fontFamily: FONTS[0], fontSize: 18, color: "#000000", align: "left", valign: "top", ...ph.style },
        });
      }
    });
    slide.layout = layoutId;
    push({ op: "update_slide", slide: slide.id, set: { layout: layoutId } });
    push({ op: "set_elements", slide: slide.id, elements: slide.elements }); // 서버가 만든 개체 틀 대신 편집기 상태로
    renderStage();
    refreshThumb(slide.id);
  }

  // ---------- 그림 ----------
  let pictureTarget = null; // 그림 개체 틀에 넣을 때 그 개체 ID

  function pickImage(placeholderId = null) {
    pictureTarget = placeholderId;
    fileInput.value = "";
    fileInput.click();
  }

  fileInput.addEventListener("change", () => insertImages([...fileInput.files], pictureTarget));

  async function uploadImage(file) {
    const form = new FormData();
    form.append("file", file);
    return api("POST", `/api/decks/${deck.id}/images`, form);
  }

  function fitInto(w, h, box) {
    const scale = Math.min(box.w / w, box.h / h, 1);
    const fw = Math.round(w * scale);
    const fh = Math.round(h * scale);
    return { x: Math.round(box.x + (box.w - fw) / 2), y: Math.round(box.y + (box.h - fh) / 2), w: fw, h: fh };
  }

  async function insertImages(files, placeholderId = null) {
    const images = files.filter((f) => f.type.startsWith("image/"));
    if (!images.length || !editable) return;
    const added = [];
    for (const file of images) {
      let res;
      try {
        res = await uploadImage(file);
      } catch (err) {
        alert(err.message);
        continue;
      }
      const target = placeholderId && elById(placeholderId);
      if (target && target.type === "image") {
        // 그림 개체 틀 채우기 · 그림 바꾸기: 그 자리 안에 비율 맞춰 넣기
        Object.assign(target, fitInto(res.width, res.height, target), { src: res.url });
        redrawElement(target);
        setSelection([target.id]);
        changed();
        placeholderId = null;
        continue;
      }
      const rect = fitInto(res.width, res.height, { x: W * 0.1, y: H * 0.1, w: W * 0.8, h: H * 0.8 });
      added.push({
        id: newId("e"), type: "image", name: `그림 ${nextNumber() + added.length}`, placeholder: null, ...rect, rotate: 0,
        text: "", src: res.url, style: { fontFamily: FONTS[0], fontSize: 18, color: "#000000", align: "left", valign: "top" },
      });
    }
    if (added.length) addElements(added);
  }

  stageWrap.addEventListener("dragover", (e) => {
    if ([...(e.dataTransfer?.types || [])].includes("Files")) e.preventDefault();
  });
  stageWrap.addEventListener("drop", (e) => {
    if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
    e.preventDefault();
    const target = e.target.closest(".pp-el");
    const el = target && elById(target.dataset.id);
    insertImages([...e.dataTransfer.files], el && el.type === "image" && !el.src ? el.id : null);
  });

  // ---------- 서식 (리본) ----------
  function textTargets() {
    return selectedEls().filter(hasText);
  }

  function setStyle(changes, els = textTargets()) {
    if (!els.length) return;
    els.forEach((el) => {
      el.style = { ...el.style, ...changes };
      if (el.id !== editingId) redrawElement(el);
      else R.applyTextStyle(contentOf(el.id), el.style);
      if (el.id === editingId) contentOf(el.id).classList.toggle("bullets", Boolean(el.style.bullets));
      autoFit(el);
    });
    drawSelection();
    syncRibbon();
    changed();
  }

  function toggleFlag(flag, command) {
    if (editingId) {
      document.execCommand(command); // 편집 중: 선택한 글자에만
      const el = elById(editingId);
      el.text = contentOf(editingId).innerHTML;
      changed();
      return;
    }
    const els = textTargets();
    if (!els.length) return;
    const on = !els.every((el) => el.style[flag]);
    setStyle({ [flag]: on }, els);
  }

  function changeSize(direction) {
    const els = textTargets();
    if (!els.length) return;
    els.forEach((el) => {
      const size = el.style.fontSize;
      const next = direction > 0 ? SIZES.find((s) => s > size) ?? size + 4 : [...SIZES].reverse().find((s) => s < size) ?? Math.max(1, size - 1);
      el.style = { ...el.style, fontSize: next };
    });
    setStyle({}, els);
  }

  function toggleBullets() {
    const els = textTargets();
    if (!els.length) return;
    const on = !els.every((el) => el.style.bullets);
    els.forEach((el) => {
      const holder = document.createElement("div");
      holder.innerHTML = el.id === editingId ? contentOf(el.id).innerHTML : el.text;
      if (on && !holder.querySelector("ul, ol") && holder.textContent.trim()) {
        const lines = holder.innerHTML.split(/<br\s*\/?>|<\/div>\s*<div>|<\/p>\s*<p>/i).map((l) => l.replace(/<\/?(div|p)>/gi, ""));
        el.text = `<ul>${lines.map((l) => `<li>${l || "<br>"}</li>`).join("")}</ul>`;
      } else if (!on) {
        holder.querySelectorAll("li").forEach((li) => li.replaceWith(Object.assign(document.createElement("div"), { innerHTML: li.innerHTML })));
        holder.querySelectorAll("ul, ol").forEach((list) => list.replaceWith(...list.childNodes));
        el.text = holder.innerHTML;
      }
      if (el.id === editingId) contentOf(el.id).innerHTML = el.text;
    });
    setStyle({ bullets: on }, els);
  }

  const shapeTargets = () => selectedEls().filter((el) => el.type === "shape");

  function setShapeStyle(changes) {
    const els = shapeTargets();
    if (!els.length) return;
    els.forEach((el) => {
      el.style = { ...el.style, ...changes };
      redrawElement(el);
    });
    drawSelection();
    syncRibbon();
    changed();
  }

  // 리본 상태를 선택한 개체에 맞추기
  const fontSelect = document.querySelector('select[data-cmd="font"]');
  const sizeSelect = document.querySelector('select[data-cmd="size"]');
  fontSelect.innerHTML = FONTS.map((f) => `<option value="${f}" style="font-family:'${f}'">${f}</option>`).join("");
  sizeSelect.innerHTML = SIZES.map((s) => `<option value="${s}">${s}</option>`).join("");

  function syncRibbon() {
    const el = textTargets()[0];
    const shape = shapeTargets()[0];
    const st = el?.style || {};
    fontSelect.value = st.fontFamily || FONTS[0];
    if (st.fontSize && !SIZES.includes(st.fontSize) && !sizeSelect.querySelector(`[value="${st.fontSize}"]`)) {
      sizeSelect.insertAdjacentHTML("afterbegin", `<option value="${st.fontSize}">${st.fontSize}</option>`);
    }
    sizeSelect.value = String(st.fontSize || 18);
    const active = (cmd, on) => document.querySelectorAll(`[data-cmd="${cmd}"]`).forEach((b) => b.classList.toggle("active", Boolean(on)));
    ["bold", "italic", "underline", "strike", "bullets"].forEach((f) => active(f, el && st[f]));
    ["left", "center", "right", "justify"].forEach((a) => active(`align-${a}`, el && st.align === a));
    document.querySelector('[data-swatch="color"]').style.background = st.color || "#000000";
    document.querySelector('[data-swatch="fill"]').style.background = shape?.style.fill || "transparent";
    document.querySelector('[data-swatch="line"]').style.background = shape?.style.line || "transparent";
    document.querySelectorAll(".pp-ribbon [data-cmd]").forEach((b) => {
      const needsText = ["font", "size", "grow", "shrink", "bold", "italic", "underline", "strike", "color", "bullets", "align-left", "align-center", "align-right", "align-justify"].includes(b.dataset.cmd);
      const needsShape = ["fill", "fill-menu", "line", "line-menu"].includes(b.dataset.cmd);
      const needsSel = ["arrange-menu"].includes(b.dataset.cmd);
      b.disabled = !editable || (needsText && !el) || (needsShape && !shape) || (needsSel && !selection.length);
    });
  }

  // ---------- 메뉴 (리본의 ▾ 와 우클릭이 함께 씀) ----------
  function menuAt(button, items) {
    const rect = button.getBoundingClientRect();
    window.openContextMenu(rect.left, rect.bottom + 2, items);
  }
  const layoutItems = (action) => LAYOUTS.map((l) => ({ label: l.name, icon: "layout", action: () => action(l.id) }));
  const shapeItems = () =>
    SHAPES.map(([shape, name]) => ({ label: name, icon: "shapes", action: () => setInsertMode({ type: "shape", shape }) }));
  const arrangeItems = () => [
    { label: "맨 앞으로 가져오기", icon: "bring-front", action: () => arrange("front") },
    { label: "앞으로 가져오기", icon: "arrow-up", action: () => arrange("forward") },
    { label: "뒤로 보내기", icon: "arrow-down", action: () => arrange("backward") },
    { label: "맨 뒤로 보내기", icon: "send-back", action: () => arrange("back") },
  ];

  // ---------- 리본 명령 ----------
  const COMMANDS = {
    "new-slide": () => addSlide(),
    "new-slide-menu": (b) => menuAt(b, layoutItems((id) => addSlide(id))),
    "layout-menu": (b) => menuAt(b, layoutItems((id) => applyLayout(id))),
    "reset-layout": () => cur() && applyLayout(cur().layout, { reset: true }),
    "delete-slide": () => deleteSlide(),
    grow: () => changeSize(1),
    shrink: () => changeSize(-1),
    bold: () => toggleFlag("bold", "bold"),
    italic: () => toggleFlag("italic", "italic"),
    underline: () => toggleFlag("underline", "underline"),
    strike: () => toggleFlag("strike", "strikeThrough"),
    bullets: () => toggleBullets(),
    "align-left": () => setStyle({ align: "left" }),
    "align-center": () => setStyle({ align: "center" }),
    "align-right": () => setStyle({ align: "right" }),
    "align-justify": () => setStyle({ align: "justify" }),
    "shapes-menu": (b) => menuAt(b, shapeItems()),
    "arrange-menu": (b) => menuAt(b, arrangeItems()),
    "fill-menu": (b) => menuAt(b, [{ label: "채우기 없음", icon: "x", action: () => setShapeStyle({ fill: null }) }]),
    "line-menu": (b) =>
      menuAt(b, [
        { label: "윤곽선 없음", icon: "x", action: () => setShapeStyle({ line: null }) },
        ...[0.75, 1.5, 3, 6].map((w) => ({ label: `두께 ${w}pt`, icon: "outline", action: () => setShapeStyle({ lineWidth: w * (4 / 3) }) })),
      ]),
    "select-all": () => setSelection(cur()?.elements.map((e) => e.id) || []),
    "insert-picture": () => pickImage(),
    "insert-text": () => setInsertMode({ type: "text" }),
    "show-start": () => startShow(0),
    "show-current": () => startShow(Math.max(0, slides.findIndex((s) => s.id === current))),
    "zoom-in": () => setZoom(zoom + 0.1),
    "zoom-out": () => setZoom(zoom - 0.1),
    "zoom-fit": () => {
      fitMode = true;
      fitZoom();
    },
  };

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-cmd]");
    if (!btn || btn.disabled) return;
    const run = COMMANDS[btn.dataset.cmd];
    if (run) run(btn);
  });
  // 리본 버튼을 눌러도 글자 편집이 끝나지 않도록 (굵게 등을 선택한 글자에 적용)
  document.querySelector(".pp-ribbon").addEventListener("mousedown", (e) => {
    if (e.target.closest("button")) e.preventDefault();
  });
  fontSelect.addEventListener("change", () => setStyle({ fontFamily: fontSelect.value }));
  sizeSelect.addEventListener("change", () => setStyle({ fontSize: Number(sizeSelect.value) }));
  document.querySelector('input[data-cmd="color"]').addEventListener("input", (e) => setStyle({ color: e.target.value }));
  document.querySelector('input[data-cmd="fill"]').addEventListener("input", (e) => setShapeStyle({ fill: e.target.value }));
  document.querySelector('input[data-cmd="line"]').addEventListener("input", (e) => setShapeStyle({ line: e.target.value }));

  // 리본 탭
  document.querySelectorAll(".pp-tabs [data-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".pp-tabs [data-tab]").forEach((t) => t.classList.toggle("active", t === tab));
      document.querySelectorAll(".pp-panel").forEach((p) => (p.hidden = p.dataset.panel !== tab.dataset.tab));
    });
  });

  // ---------- 축소판 창 조작 ----------
  thumbs.addEventListener("mousedown", () => (thumbsFocused = true));
  thumbs.addEventListener("click", (e) => {
    const item = e.target.closest(".pp-thumb");
    if (item) selectSlide(item.dataset.id, { keepFocus: true });
  });
  let dragSlide = null;
  thumbs.addEventListener("dragstart", (e) => {
    const item = e.target.closest(".pp-thumb");
    if (!item || !editable) return;
    dragSlide = item.dataset.id;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", "");
  });
  thumbs.addEventListener("dragover", (e) => {
    if (!dragSlide) return;
    e.preventDefault();
    thumbs.querySelectorAll(".drop-before, .drop-after").forEach((n) => n.classList.remove("drop-before", "drop-after"));
    const over = e.target.closest(".pp-thumb");
    if (!over || over.dataset.id === dragSlide) return;
    const r = over.getBoundingClientRect();
    over.classList.add(e.clientY < r.top + r.height / 2 ? "drop-before" : "drop-after");
  });
  thumbs.addEventListener("drop", (e) => {
    if (!dragSlide) return;
    e.preventDefault();
    const over = thumbs.querySelector(".drop-before, .drop-after");
    if (over) {
      const others = slides.filter((s) => s.id !== dragSlide);
      let to = others.findIndex((s) => s.id === over.dataset.id);
      if (over.classList.contains("drop-after")) to += 1;
      moveSlide(dragSlide, to);
    }
    dragSlide = null;
  });
  thumbs.addEventListener("dragend", () => {
    dragSlide = null;
    thumbs.querySelectorAll(".drop-before, .drop-after").forEach((n) => n.classList.remove("drop-before", "drop-after"));
  });

  // ---------- 복사 · 붙여넣기 ----------
  function copySelection(cut = false) {
    if (thumbsFocused && current) {
      clipboard = { kind: "slide", slide: clone(cur()) };
      if (cut) deleteSlide();
      return;
    }
    const items = selectedEls();
    if (!items.length) return;
    clipboard = { kind: "elements", items: clone(items), pasted: 0 };
    const text = items.map((el) => (el.text ? Object.assign(document.createElement("div"), { innerHTML: el.text }).innerText : "")).filter(Boolean).join("\n");
    if (text) navigator.clipboard?.writeText(text).catch(() => {});
    if (cut) deleteSelected();
  }

  // 앱 안에서 복사한 슬라이드·개체 붙여넣기
  function pasteInternal() {
    if (!clipboard || !editable) return;
    if (clipboard.kind === "slide") {
      const copy = { ...clone(clipboard.slide), id: newId("s") };
      const index = slides.findIndex((s) => s.id === current) + 1;
      slides.splice(index, 0, copy);
      push({ op: "add_slide", id: copy.id, index, layout: copy.layout, elements: copy.elements });
      renderThumbs();
      selectSlide(copy.id);
    } else if (cur()) {
      clipboard.pasted += 1;
      pasteElements(clipboard.items, 20 * clipboard.pasted);
    }
  }

  document.addEventListener("paste", (e) => {
    if (!editable || editingId || e.target.closest("input, textarea, select, [contenteditable='true']")) return;
    const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
    if (files.length) {
      e.preventDefault();
      insertImages(files);
      return;
    }
    if (clipboard?.kind === "slide" || (clipboard?.kind === "elements" && cur())) {
      e.preventDefault();
      pasteInternal();
      return;
    }
    const text = e.clipboardData?.getData("text/plain");
    if (text && cur()) {
      e.preventDefault();
      const el = newElement({ type: "text" }, { x: 120, y: 120, w: 600, h: 40 });
      el.text = text.split("\n").map((line) => `<div>${line.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]) || "<br>"}</div>`).join("");
      addElements([el]);
      autoFit(el);
      changed();
    }
  });

  // ---------- 키보드 (PowerPoint 단축키) ----------
  document.addEventListener("keydown", (e) => {
    if (document.querySelector(".pp-show")) return; // 슬라이드 쇼가 처리
    const mod = e.ctrlKey || e.metaKey;
    const is = (ch) => window.keyIs(e, ch);

    if (e.key === "F5") {
      e.preventDefault();
      COMMANDS[e.shiftKey ? "show-current" : "show-start"]();
      return;
    }
    if (e.target.closest("input, select, textarea")) return; // 제목 입력칸 등

    // 글자 편집 중
    if (editingId) {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === "Escape") {
        e.preventDefault();
        stopEdit();
      } else if (mod && (is("e") || is("l") || is("r") || is("j"))) {
        e.preventDefault();
        setStyle({ align: { e: "center", l: "left", r: "right", j: "justify" }[["e", "l", "r", "j"].find(is)] });
      } else if (mod && e.shiftKey && (e.key === ">" || e.key === "." || e.key === "<" || e.key === ",")) {
        e.preventDefault();
        changeSize(e.key === ">" || e.key === "." ? 1 : -1);
      } else if (e.key === "Tab" && elById(editingId)?.style.bullets) {
        e.preventDefault();
        document.execCommand(e.shiftKey ? "outdent" : "indent"); // 목록 수준
      } else if (mod && is("s")) {
        e.preventDefault();
        window.flushEditor();
      }
      return; // 나머지는 브라우저의 글자 편집 (Ctrl+B/I/U 포함)
    }

    if (!editable && !["Escape", "PageUp", "PageDown", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) return;
    let handled = true;
    if (e.key === "Delete" || e.key === "Backspace") {
      if (thumbsFocused && !selection.length) deleteSlide();
      else deleteSelected();
    } else if (e.key === "Escape") {
      if (insertMode) setInsertMode(null);
      else setSelection([]);
    } else if (selection.length && e.key.startsWith("Arrow")) {
      const step = mod ? 1 : 8; // Ctrl = 미세 이동
      const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      nudge(...d);
    } else if (["PageUp", "PageDown", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) {
      const i = slides.findIndex((s) => s.id === current);
      const to = { PageUp: i - 1, ArrowUp: i - 1, PageDown: i + 1, ArrowDown: i + 1, Home: 0, End: slides.length - 1 }[e.key];
      if (slides[to]) selectSlide(slides[to].id, { keepFocus: thumbsFocused });
    } else if (e.key === "Enter" && selection.length === 1 && hasText(selectedEls()[0] || {})) { // F2 는 AI 도우미
      startEdit(selection[0]);
    } else if (mod && is("m")) addSlide();
    else if (mod && is("d")) thumbsFocused && !selection.length ? duplicateSlide() : duplicateSelected();
    else if (mod && is("a")) COMMANDS["select-all"]();
    else if (mod && is("c")) copySelection(false);
    else if (mod && is("x")) copySelection(true);
    else if (mod && is("v")) handled = false; // paste 이벤트에서 처리
    else if (mod && is("b")) toggleFlag("bold", "bold");
    else if (mod && is("i")) toggleFlag("italic", "italic");
    else if (mod && is("u")) toggleFlag("underline", "underline");
    else if (mod && (is("e") || is("l") || is("r") || is("j"))) setStyle({ align: { e: "center", l: "left", r: "right", j: "justify" }[["e", "l", "r", "j"].find(is)] });
    else if (mod && e.shiftKey && (e.key === ">" || e.key === ".")) changeSize(1);
    else if (mod && e.shiftKey && (e.key === "<" || e.key === ",")) changeSize(-1);
    else handled = false;
    if (handled) e.preventDefault();
  });

  // ---------- 우클릭 메뉴 ----------
  const KEY = (k) => `${K}+${k}`;
  window.contextMenuProviders?.push((e) => {
    const thumb = e.target.closest(".pp-thumb");
    if (thumb) {
      thumbsFocused = true;
      if (thumb.dataset.id !== current) selectSlide(thumb.dataset.id, { keepFocus: true });
      const index = slides.findIndex((s) => s.id === thumb.dataset.id);
      if (!editable) return [{ label: "슬라이드 쇼 (이 슬라이드부터)", icon: "play", action: () => startShow(index) }];
      return [
        { label: "새 슬라이드", icon: "slide-plus", hint: KEY("M"), action: () => addSlide() },
        { label: "슬라이드 복제", icon: "copy", hint: KEY("D"), action: () => duplicateSlide() },
        { label: "슬라이드 삭제", icon: "trash", hint: "Delete", danger: true, action: () => deleteSlide() },
        "sep",
        { label: "레이아웃 ▸", icon: "layout", action: () => window.openContextMenu(e.clientX, e.clientY, layoutItems((id) => applyLayout(id))) },
        { label: "슬라이드 다시 설정", icon: "refresh", action: () => applyLayout(cur().layout, { reset: true }) },
        "sep",
        { label: "위로 이동", icon: "arrow-up", disabled: index === 0, action: () => moveSlide(current, index - 1) },
        { label: "아래로 이동", icon: "arrow-down", disabled: index === slides.length - 1, action: () => moveSlide(current, index + 1) },
        "sep",
        { label: "이 슬라이드부터 쇼", icon: "play", hint: "Shift+F5", action: () => startShow(index) },
      ];
    }
    if (thumbs.contains(e.target)) {
      return editable ? [{ label: "새 슬라이드", icon: "slide-plus", hint: KEY("M"), action: () => addSlide() }] : null;
    }
    if (!stageWrap.contains(e.target) || !editable) return null;

    thumbsFocused = false;
    const node = e.target.closest(".pp-el");
    if (node && node.dataset.id === editingId) return null; // 글자 편집 중에는 브라우저 메뉴 (붙여넣기 등)
    if (node) {
      if (!selection.includes(node.dataset.id)) setSelection([node.dataset.id]);
      const el = elById(node.dataset.id);
      return [
        { label: "잘라내기", icon: "x", hint: KEY("X"), action: () => copySelection(true) },
        { label: "복사", icon: "copy", hint: KEY("C"), action: () => copySelection(false) },
        { label: "복제", icon: "copy", hint: KEY("D"), action: duplicateSelected },
        "sep",
        { label: "텍스트 편집", icon: "pencil", disabled: !hasText(el), action: () => startEdit(el.id) },
        ...(el.type === "image" ? [{ label: el.src ? "그림 바꾸기" : "그림 넣기", icon: "image", action: () => pickImage(el.id) }] : []),
        "sep",
        ...arrangeItems(),
        "sep",
        { label: "삭제", icon: "trash", hint: "Delete", danger: true, action: deleteSelected },
      ];
    }
    return [
      { label: "붙여넣기", icon: "clipboard", hint: KEY("V"), disabled: !clipboard, action: pasteInternal },
      "sep",
      { label: "새 슬라이드", icon: "slide-plus", hint: KEY("M"), action: () => addSlide() },
      { label: "레이아웃 ▸", icon: "layout", action: () => window.openContextMenu(e.clientX, e.clientY, layoutItems((id) => applyLayout(id))) },
      { label: "슬라이드 다시 설정", icon: "refresh", action: () => applyLayout(cur().layout, { reset: true }) },
      "sep",
      { label: "텍스트 상자 그리기", icon: "text-box", action: () => setInsertMode({ type: "text" }) },
      { label: "그림 넣기", icon: "image", action: () => pickImage() },
      { label: "모두 선택", icon: "select-all", hint: KEY("A"), action: COMMANDS["select-all"] },
    ];
  });

  // ---------- 제목 ----------
  const titleInput = $("#pp-title");
  let titleTimer = null;
  titleInput.addEventListener("input", () => {
    document.title = `${titleInput.value.trim() || "제목 없는 프레젠테이션"} · MarkNote`;
    clearTimeout(titleTimer);
    titleTimer = setTimeout(() => api("PATCH", `/api/decks/${deck.id}`, { title: titleInput.value }).catch((err) => alert(err.message)), 600);
  });
  titleInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) titleInput.blur();
  });

  // ---------- 슬라이드 쇼 ----------
  async function startShow(index) {
    if (editingId) stopEdit();
    if (!slides.length) return;
    window.DeckShow.start({
      slides,
      startIndex: index,
      onExit: (i) => {
        if (slides[i]) selectSlide(slides[i].id);
      },
    });
    flush();
  }

  // ---------- 시작 ----------
  renderThumbs();
  renderStage();
  fitZoom();
  syncRibbon();
  if (!editable) document.querySelectorAll(".pp-ribbon button, .pp-ribbon select, .pp-ribbon input").forEach((b) => (b.disabled = !["show-start", "show-current", "zoom-in", "zoom-out", "zoom-fit"].includes(b.dataset.cmd)));
})();
