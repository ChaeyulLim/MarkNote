// 슬라이드 그리기 — 편집기·축소판·목록·슬라이드 쇼가 함께 쓴다.
// 슬라이드는 1280×720 고정 크기로 그리고, 보여 줄 때 CSS transform 으로 확대/축소한다.
(() => {
  const W = 1280;
  const H = 720;
  const PX_PER_PT = 4 / 3; // 96dpi 기준 1pt = 1.333px (PowerPoint 와 같은 비율)
  const FONT_FALLBACK = '"Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif';

  // PowerPoint 한국어판의 개체 틀 안내 문구
  const PROMPTS = {
    title: "제목을 입력하려면 클릭하십시오",
    subtitle: "부제목을 입력하려면 클릭하십시오",
    body: "텍스트를 입력하려면 클릭하십시오",
    body2: "텍스트를 입력하려면 클릭하십시오",
    header: "텍스트를 입력하려면 클릭하십시오",
    header2: "텍스트를 입력하려면 클릭하십시오",
    caption: "텍스트를 입력하려면 클릭하십시오",
    picture: "그림을 추가하려면 클릭하십시오",
  };

  const LINE_SHAPES = new Set(["line", "arrow_line"]);

  function isEmptyText(html) {
    const div = document.createElement("div");
    div.innerHTML = html || "";
    return !div.textContent.trim();
  }

  function applyTextStyle(node, style = {}) {
    node.style.fontFamily = `"${style.fontFamily || "맑은 고딕"}", ${FONT_FALLBACK}`;
    node.style.fontSize = `${(style.fontSize || 18) * PX_PER_PT}px`;
    node.style.color = style.color || "#000000";
    node.style.textAlign = style.align || "left";
    node.style.fontWeight = style.bold ? "700" : "400";
    node.style.fontStyle = style.italic ? "italic" : "normal";
    const decorations = [style.underline && "underline", style.strike && "line-through"].filter(Boolean);
    node.style.textDecoration = decorations.join(" ") || "none";
  }

  // 도형 SVG. 색은 서버에서 #rrggbb 로 검증되고, 숫자만 들어가므로 문자열로 만들어도 안전하다
  function shapeSvg(el, uid = "") {
    const s = el.style || {};
    const w = Math.max(el.w, 0.01);
    const h = Math.max(el.h, 0.01);
    const lw = s.lineWidth ?? 1.5;
    const fill = s.fill || "none";
    const stroke = s.line || "none";
    const half = stroke === "none" ? 0 : lw / 2;
    const common = `fill="${fill}" stroke="${stroke}" stroke-width="${lw}" vector-effect="non-scaling-stroke"`;
    let body;
    switch (el.shape) {
      case "round_rect": {
        const r = Math.min(w, h) * 0.1667;
        body = `<rect x="${half}" y="${half}" width="${Math.max(w - lw, 0)}" height="${Math.max(h - lw, 0)}" rx="${r}" ${common}/>`;
        break;
      }
      case "ellipse":
        body = `<ellipse cx="${w / 2}" cy="${h / 2}" rx="${Math.max(w / 2 - half, 0)}" ry="${Math.max(h / 2 - half, 0)}" ${common}/>`;
        break;
      case "triangle":
        body = `<polygon points="${w / 2},${half} ${w - half},${h - half} ${half},${h - half}" ${common}/>`;
        break;
      case "pentagon":
        body = `<polygon points="${w / 2},${half} ${w - half},${h * 0.38} ${w * 0.81},${h - half} ${w * 0.19},${h - half} ${half},${h * 0.38}" ${common}/>`;
        break;
      case "right_arrow":
        body = `<polygon points="${half},${h * 0.25} ${w * 0.6},${h * 0.25} ${w * 0.6},${half} ${w - half},${h / 2} ${w * 0.6},${h - half} ${w * 0.6},${h * 0.75} ${half},${h * 0.75}" ${common}/>`;
        break;
      case "line":
      case "arrow_line": {
        const x1 = s.flipH ? w : 0;
        const x2 = s.flipH ? 0 : w;
        const y1 = s.flipV ? h : 0;
        const y2 = s.flipV ? 0 : h;
        const color = s.line || "#4472c4";
        const marker = el.shape === "arrow_line" ? ` marker-end="url(#pp-arrow-${uid}${el.id})"` : "";
        const defs =
          el.shape === "arrow_line"
            ? `<defs><marker id="pp-arrow-${uid}${el.id}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="${4 + lw}" markerHeight="${4 + lw}" markerUnits="userSpaceOnUse" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="${color}"/></marker></defs>`
            : "";
        body =
          `${defs}<line class="pp-line-hit" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="transparent" stroke-width="14"/>` +
          `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="${lw}" stroke-linecap="round" vector-effect="non-scaling-stroke"${marker}/>`;
        break;
      }
      default: // rect
        body = `<rect x="${half}" y="${half}" width="${Math.max(w - lw, 0)}" height="${Math.max(h - lw, 0)}" ${common}/>`;
    }
    return `<svg class="pp-shape" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" preserveAspectRatio="none">${body}</svg>`;
  }

  function textLayer(el, mode) {
    const style = el.style || {};
    const box = document.createElement("div");
    box.className = `pp-text valign-${style.valign || "top"}`;
    const content = document.createElement("div");
    content.className = "pp-text-content";
    if (style.bullets) content.classList.add("bullets");
    content.innerHTML = el.text || "";
    applyTextStyle(content, style);
    applyTextStyle(box, style); // 빈 개체 틀의 안내 문구도 같은 크기·정렬로
    if (mode === "edit" && el.placeholder && isEmptyText(el.text)) {
      box.classList.add("is-empty");
      box.dataset.prompt = PROMPTS[el.placeholder] || "";
    }
    box.appendChild(content);
    return box;
  }

  function renderElement(el, mode = "view", uid = "") {
    const empty = el.placeholder && (el.type === "image" ? !el.src : isEmptyText(el.text));
    if (mode !== "edit" && empty) return null; // 빈 개체 틀은 편집 화면에서만 보인다

    const node = document.createElement("div");
    node.className = `pp-el pp-${el.type}`;
    if (el.type === "shape" && LINE_SHAPES.has(el.shape)) node.classList.add("pp-lineshape");
    if (el.placeholder) node.classList.add("pp-placeholder");
    node.dataset.id = el.id;
    node.style.left = `${el.x}px`;
    node.style.top = `${el.y}px`;
    node.style.width = `${el.w}px`;
    node.style.height = `${el.h}px`;
    if (el.rotate) node.style.transform = `rotate(${el.rotate}deg)`;

    if (el.type === "image") {
      if (el.src) {
        const img = document.createElement("img");
        img.src = el.src;
        img.alt = el.name || "";
        img.draggable = false;
        node.appendChild(img);
      } else {
        node.innerHTML = `<div class="pp-picture-ph">${window.icon ? window.icon("image") : ""}<span></span></div>`;
        node.querySelector("span").textContent = PROMPTS.picture;
      }
    } else if (el.type === "shape") {
      node.insertAdjacentHTML("beforeend", shapeSvg(el, uid));
      if (!LINE_SHAPES.has(el.shape)) node.appendChild(textLayer(el, mode));
    } else {
      node.appendChild(textLayer(el, mode));
    }
    return node;
  }

  function renderSlide(slide, mode = "view", uid = "") {
    const root = document.createElement("div");
    root.className = `pp-slide pp-mode-${mode}`;
    root.style.background = slide.background || "#ffffff";
    (slide.elements || []).forEach((el) => {
      const node = renderElement(el, mode, uid);
      if (node) root.appendChild(node);
    });
    return root;
  }

  // 축소판: width(px) 에 맞게 줄인 슬라이드
  let thumbSeq = 0;
  function renderThumb(slide, width) {
    const frame = document.createElement("div");
    frame.className = "pp-thumb-frame";
    frame.style.width = `${width}px`;
    frame.style.height = `${(width * H) / W}px`;
    const inner = renderSlide(slide, "view", `t${thumbSeq++}-`);
    inner.style.transform = `scale(${width / W})`;
    frame.appendChild(inner);
    return frame;
  }

  window.DeckRender = { W, H, PX_PER_PT, PROMPTS, LINE_SHAPES, isEmptyText, applyTextStyle, shapeSvg, renderElement, renderSlide, renderThumb };
})();
