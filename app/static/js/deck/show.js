// 슬라이드 쇼 (PowerPoint 와 같은 키: → ← Space N P B W G 숫자+Enter Ctrl+L Esc …)
window.DeckShow = (() => {
  function start({ slides, startIndex = 0, onExit = null }) {
    const R = window.DeckRender;
    // 숨긴 슬라이드는 건너뛴다. index 는 전체 슬라이드 기준 번호
    const order = slides.map((slide, index) => ({ slide, index })).filter((x) => !x.slide.hidden);
    if (!order.length) {
      alert("표시할 슬라이드가 없습니다.");
      return;
    }
    let pos = order.findIndex((x) => x.index >= startIndex);
    if (pos < 0) pos = 0;
    let lastShown = pos;
    let numberBuffer = "";
    let laser = false;
    let idleTimer = null;

    const root = document.createElement("div");
    root.className = "pp-show";
    root.tabIndex = -1;
    root.innerHTML =
      '<div class="pp-show-stage"></div>' +
      '<div class="pp-show-cover" hidden></div>' +
      '<div class="pp-show-end" hidden>슬라이드 쇼 끝. 끝내려면 클릭하십시오.</div>' +
      '<div class="pp-show-laser" hidden></div>' +
      '<div class="pp-show-bar">' +
      `<button type="button" data-show="prev" title="이전 (←)">${window.icon("chevron-left")}</button>` +
      `<button type="button" data-show="next" title="다음 (→)">${window.icon("chevron-right")}</button>` +
      `<button type="button" data-show="laser" title="레이저 포인터 (Ctrl+L)">${window.icon("sparkles")}</button>` +
      `<button type="button" data-show="grid" title="모든 슬라이드 (G)">${window.icon("layout")}</button>` +
      `<button type="button" data-show="end" title="쇼 마침 (Esc)">${window.icon("x")}</button>` +
      '<span class="pp-show-count"></span>' +
      "</div>";
    document.body.appendChild(root);
    const stageEl = root.querySelector(".pp-show-stage");
    const cover = root.querySelector(".pp-show-cover");
    const endEl = root.querySelector(".pp-show-end");
    const laserDot = root.querySelector(".pp-show-laser");
    const count = root.querySelector(".pp-show-count");
    let grid = null;

    root.focus();
    const wantFullscreen = Boolean(root.requestFullscreen);
    if (wantFullscreen) root.requestFullscreen().catch(() => {});

    function render() {
      const atEnd = pos >= order.length;
      endEl.hidden = !atEnd;
      stageEl.replaceChildren();
      if (atEnd) {
        count.textContent = "";
        return;
      }
      lastShown = pos;
      const scale = Math.min(window.innerWidth / R.W, window.innerHeight / R.H);
      const slideNode = R.renderSlide(order[pos].slide, "view", "show-");
      slideNode.style.transform = `scale(${scale})`;
      stageEl.style.width = `${R.W * scale}px`;
      stageEl.style.height = `${R.H * scale}px`;
      stageEl.appendChild(slideNode);
      count.textContent = `${order[pos].index + 1} / ${slides.length}`;
    }

    const next = () => {
      if (pos < order.length) pos += 1;
      else return exit();
      render();
    };
    const prev = () => {
      if (pos > 0) {
        pos -= 1;
        render();
      }
    };
    const goToNumber = (n) => {
      const target = order.findIndex((x) => x.index === n - 1);
      if (target >= 0) {
        pos = target;
        render();
      }
    };
    const setCover = (color) => {
      if (!cover.hidden && cover.dataset.color === color) {
        cover.hidden = true;
        return;
      }
      cover.dataset.color = color;
      cover.style.background = color;
      cover.hidden = false;
    };
    const toggleLaser = () => {
      laser = !laser;
      root.classList.toggle("laser", laser);
      laserDot.hidden = !laser;
    };

    function toggleGrid() {
      if (grid) {
        grid.remove();
        grid = null;
        return;
      }
      grid = document.createElement("div");
      grid.className = "pp-show-grid";
      order.forEach((x, i) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = `pp-show-grid-item${i === pos ? " active" : ""}`;
        item.appendChild(R.renderThumb(x.slide, 240));
        item.insertAdjacentHTML("beforeend", `<span>${x.index + 1}</span>`);
        item.addEventListener("click", (e) => {
          e.stopPropagation();
          pos = i;
          toggleGrid();
          render();
        });
        grid.appendChild(item);
      });
      root.appendChild(grid);
    }

    function onKey(e) {
      if (e.isComposing) return;
      const key = e.key;
      if (/^[0-9]$/.test(key)) {
        numberBuffer += key;
        return e.preventDefault();
      }
      if (key === "Enter" && numberBuffer) {
        goToNumber(Number(numberBuffer));
        numberBuffer = "";
        return e.preventDefault();
      }
      numberBuffer = "";
      if (key === "Escape") {
        if (grid) toggleGrid();
        else if (!cover.hidden) cover.hidden = true;
        else exit();
      } else if (grid) {
        if (key.toLowerCase() === "g" || key === "-") toggleGrid();
      } else if (["ArrowRight", "ArrowDown", " ", "Enter", "PageDown", "n", "N"].includes(key)) next();
      else if (["ArrowLeft", "ArrowUp", "Backspace", "PageUp", "p", "P"].includes(key)) prev();
      else if (key === "Home") {
        pos = 0;
        render();
      } else if (key === "End") {
        pos = order.length - 1;
        render();
      } else if (key === "b" || key === "B" || key === ".") setCover("#000");
      else if (key === "w" || key === "W" || key === ",") setCover("#fff");
      else if ((e.ctrlKey || e.metaKey) && window.keyIs(e, "l")) toggleLaser();
      else if (key === "g" || key === "G" || key === "-") toggleGrid();
      else if (key === "F5") {
        /* 이미 쇼 중 */
      } else return;
      e.preventDefault();
      e.stopPropagation();
    }

    function onClick(e) {
      if (e.target.closest(".pp-show-bar, .pp-show-grid")) return;
      if (!cover.hidden) {
        cover.hidden = true;
        return;
      }
      if (laser) return;
      if (pos >= order.length) return exit();
      next();
    }

    function onBar(e) {
      const action = e.target.closest("[data-show]")?.dataset.show;
      if (!action) return;
      e.stopPropagation();
      ({ prev, next, laser: toggleLaser, grid: toggleGrid, end: exit })[action]();
    }

    function onMove(e) {
      root.classList.add("pointer-active");
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => root.classList.remove("pointer-active"), 2500);
      if (laser) {
        laserDot.style.left = `${e.clientX}px`;
        laserDot.style.top = `${e.clientY}px`;
      }
    }

    function onFullscreenChange() {
      // 브라우저에서 Esc 로 전체 화면이 풀리면 쇼도 끝낸다 (PowerPoint 의 Esc 와 같게)
      if (wantFullscreen && !document.fullscreenElement) exit();
    }

    function onMenu(e) {
      e.preventDefault();
      e.stopPropagation();
      window.openContextMenu?.(e.clientX, e.clientY, [
        { label: "다음", icon: "chevron-right", hint: "N", action: next },
        { label: "이전", icon: "chevron-left", hint: "P", action: prev },
        { label: "모든 슬라이드 보기", icon: "layout", hint: "G", action: toggleGrid },
        "sep",
        { label: "화면 검게", hint: "B", action: () => setCover("#000") },
        { label: "화면 희게", hint: "W", action: () => setCover("#fff") },
        { label: laser ? "레이저 포인터 끄기" : "레이저 포인터", icon: "sparkles", hint: "Ctrl+L", action: toggleLaser },
        "sep",
        { label: "쇼 마침", icon: "x", hint: "Esc", action: exit },
      ]);
    }

    let exited = false;
    function exit() {
      if (exited) return;
      exited = true;
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("fullscreenchange", onFullscreenChange);
      window.removeEventListener("resize", render);
      clearTimeout(idleTimer);
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      root.remove();
      onExit?.(order[Math.min(lastShown, order.length - 1)].index);
    }

    document.addEventListener("keydown", onKey, true);
    document.addEventListener("fullscreenchange", onFullscreenChange);
    window.addEventListener("resize", render);
    root.addEventListener("click", onClick);
    root.addEventListener("contextmenu", onMenu);
    root.querySelector(".pp-show-bar").addEventListener("click", onBar);
    root.addEventListener("mousemove", onMove);
    render();
  }

  return { start };
})();
