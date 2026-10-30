// 공통 UI: 우클릭 메뉴, 전역 단축키, 단축키 도움말, 페이지 빠른 이동, 사이드바 접기
(() => {
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
  const mod = (e) => (isMac ? e.metaKey : e.ctrlKey);

  // 물리 키(e.code)로 먼저 판단 — 한글 입력 상태에서도 Ctrl+B 등이 동작하도록. 없으면 e.key 로
  const CODE_OF = { "/": "Slash", "\\": "Backslash" };
  function keyIs(e, ch) {
    const code = CODE_OF[ch] || (/^[a-z]$/.test(ch) ? `Key${ch.toUpperCase()}` : /^\d$/.test(ch) ? `Digit${ch}` : null);
    return (code && e.code === code) || e.key.toLowerCase() === ch;
  }
  window.keyIs = keyIs;

  // ---------- 우클릭 메뉴 ----------
  // 각 영역이 window.contextMenuProviders 에 (event) => items | null 함수를 등록한다 (블록, 채팅 등 구체적인 곳).
  // 아무도 처리하지 않으면 fallbacks(사이드바 빈 곳, 화면별 빈 곳)를 순서대로 확인한다.
  // item: { label, icon?, hint?, action, danger?, disabled? } 또는 "sep"
  const providers = (window.contextMenuProviders = window.contextMenuProviders || []);
  const fallbacks = [];
  const menu = document.createElement("div");
  menu.className = "ctx-menu";
  menu.setAttribute("role", "menu");
  menu.hidden = true;
  document.body.appendChild(menu);
  let menuIndex = -1;

  function closeMenu() {
    menu.hidden = true;
    menu.replaceChildren();
    menuIndex = -1;
  }

  function menuButtons() {
    return [...menu.querySelectorAll("button:not(:disabled)")];
  }

  function focusMenuItem(index) {
    const buttons = menuButtons();
    if (!buttons.length) return;
    menuIndex = (index + buttons.length) % buttons.length;
    buttons[menuIndex].focus();
  }

  function openMenu(x, y, items) {
    menu.replaceChildren();
    items.forEach((item) => {
      if (item === "sep") {
        const sep = document.createElement("div");
        sep.className = "ctx-sep";
        menu.appendChild(sep);
        return;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      btn.className = item.danger ? "danger" : "";
      btn.disabled = Boolean(item.disabled);
      btn.innerHTML = `<span class="ctx-icon">${item.icon ? icon(item.icon) : ""}</span><span class="ctx-label"></span><span class="ctx-hint"></span>`;
      btn.querySelector(".ctx-label").textContent = item.label;
      btn.querySelector(".ctx-hint").textContent = item.hint || "";
      btn.addEventListener("click", () => {
        closeMenu();
        Promise.resolve(item.action()).catch((err) => alert(err.message));
      });
      menu.appendChild(btn);
    });
    menu.hidden = false;
    // 화면 밖으로 나가지 않게 위치 조정
    const rect = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(4, Math.min(x, window.innerWidth - rect.width - 4))}px`;
    menu.style.top = `${Math.max(4, Math.min(y, window.innerHeight - rect.height - 4))}px`;
    menuIndex = -1;
  }
  window.openContextMenu = openMenu;

  document.addEventListener("contextmenu", (e) => {
    if (e.shiftKey) return; // Shift+우클릭 = 브라우저 기본 메뉴
    if (e.target.closest("input, textarea, select, [contenteditable='true'], iframe")) return; // 글 입력 칸은 기본 메뉴 (붙여넣기 등)
    for (const provider of [...providers, ...fallbacks]) {
      let items = provider(e);
      if (!items || !items.length) continue;
      const selection = window.getSelection().toString().trim();
      if (selection && !items.some((i) => i.label === "선택한 텍스트 복사")) {
        items = [{ label: "선택한 텍스트 복사", icon: "copy", hint: `${K}+C`, action: () => copyText(selection) }, "sep", ...items];
      }
      e.preventDefault();
      openMenu(e.clientX, e.clientY, items);
      return;
    }
  });

  function copyText(text) {
    return navigator.clipboard.writeText(text);
  }
  window.copyText = copyText;
  document.addEventListener("mousedown", (e) => {
    if (!menu.hidden && !menu.contains(e.target)) closeMenu();
  });
  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key === "Escape" && !menu.hidden) {
        e.preventDefault();
        e.stopPropagation();
        closeMenu();
      }
    },
    true
  );
  window.addEventListener("blur", closeMenu);
  window.addEventListener("resize", closeMenu);
  document.addEventListener("scroll", closeMenu, true);
  menu.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      focusMenuItem(menuIndex + (e.key === "ArrowDown" ? 1 : -1));
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeMenu();
    }
  });

  // ---------- 모달 (도움말 · 빠른 이동) ----------
  function openModal(className) {
    const backdrop = document.createElement("div");
    backdrop.className = "ui-modal-backdrop";
    const box = document.createElement("div");
    box.className = `ui-modal ${className}`;
    box.setAttribute("role", "dialog");
    backdrop.appendChild(box);
    document.body.appendChild(backdrop);
    const close = () => backdrop.remove();
    backdrop.addEventListener("mousedown", (e) => {
      if (e.target === backdrop) close();
    });
    backdrop.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close();
      }
    });
    return { box, close };
  }

  const K = isMac ? "⌘" : "Ctrl";
  const SHORTCUTS = [
    ["전체", [
      [`${K}+Z / ${K}+Y`, "되돌리기 / 다시 실행 (블록 편집·삭제·이동, 휴지통으로 옮기기, AI 수정)"],
      ["Delete", "고른 블록 삭제 · 탐색기 항목 휴지통으로"],
      [`${K}+A`, "지금 칸 전체 선택 (탐색기 = 항목 전부, 노트 본문 = 블록 전부)"],
      [`${K}+P`, "노트 빠른 이동 · 검색 (없는 제목이면 새 노트)"],
      [`${K}+Alt+N`, "새 파일 (탐색기에서 고른 폴더 안에 이름 입력)"],
      [`F2 / ${K}+J`, "AI 도우미 열기/닫기 (오른쪽 서랍, 프레젠테이션에서는 F2 만)"],
      [`${K}+\\`, "사이드바 접기/펴기"],
      [`${K}+S`, "저장 (편집 내용 즉시 저장)"],
      [`${K}+/`, "단축키 보기"],
    ]],
    ["탐색기 (VS Code 처럼)", [
      ["↑ / ↓ · Home / End", "위아래 항목 · 처음 / 끝 (Shift = 범위로 고르기)"],
      ["→ / ←", "펼치기(펼쳐져 있으면 첫 항목) / 접기(접혀 있으면 상위 폴더)"],
      ["Enter", "파일 열기 · 폴더 펼치기/접기"],
      [`${K}+C / ${K}+X → ${K}+V`, "복사 / 잘라내기 → 고른 폴더 안(파일을 골랐으면 그 옆)에 붙여넣기"],
      ["Delete", "휴지통으로 (되돌리기 가능)"],
      [`클릭 · ${K}+클릭 · Shift+클릭`, "열기 / 하나씩 더하기 / 범위로 고르기"],
      [`끌어 놓기 (${K} 누르면 복사)`, "폴더 안으로 · 빈 곳 = 맨 위로"],
      ["우클릭 → 이름 바꾸기", "트리 안에서 바로 입력 (Enter 저장, Esc 취소)"],
    ]],
    ["블록 고르기 (편집 중이 아닐 때)", [
      ["블록 왼쪽 손잡이 클릭", "블록 고르기 (Shift = 범위, Ctrl = 하나씩 더하기)"],
      ["Esc", "편집을 마치고 그 블록 고르기 / 고르기 풀기"],
      ["↑ / ↓ (Shift)", "위·아래 블록 고르기 (범위 넓히기)"],
      ["Enter", "고른 블록 편집"],
      [`${K}+C / ${K}+X / ${K}+V`, "고른 블록 복사 / 잘라내기 / 그 아래에 붙여넣기"],
      [`${K}+Shift+↑ / ↓`, "고른 블록째 위·아래로 옮기기"],
    ]],
    ["블록 편집 중", [
      [`${K}+B / ${K}+I`, "굵게 / 기울임"],
      [`${K}+Shift+S`, "취소선"],
      [`${K}+Shift+H`, "비공개로 감싸기 {{ }} (AI 에게 보내지 않음) / 풀기"],
      [`${K}+E`, "인라인 코드"],
      [`${K}+K`, "링크"],
      [`${K}+Shift+1~3`, "제목 1~3"],
      [`${K}+Enter`, "체크박스 만들기 / 체크"],
      [`${K}+D`, "블록 복제"],
      [`${K}+Shift+↑ / ↓`, "블록 위/아래로 이동"],
      ["Tab / Shift+Tab", "들여쓰기 / 내어쓰기"],
      ["Enter / Shift+Enter", "다음 블록 / 줄바꿈"],
      ["Esc", "편집 완료"],
    ]],
    ["마우스", [
      ["우클릭", "블록 · 탐색기 항목 · 빈 곳마다 다른 메뉴"],
      ["Shift+우클릭", "브라우저 기본 메뉴"],
    ]],
  ];

  function openHelp() {
    const { box } = openModal("ui-help");
    box.innerHTML = `<h2 class="heading-with-icon">${icon("keyboard")}<span>단축키</span></h2><div class="ui-help-groups"></div><p class="muted">Esc 로 닫기</p>`;
    const groups = box.querySelector(".ui-help-groups");
    SHORTCUTS.forEach(([title, rows]) => {
      const section = document.createElement("section");
      const h = document.createElement("h3");
      h.textContent = title;
      const dl = document.createElement("dl");
      rows.forEach(([keys, desc]) => {
        const dt = document.createElement("dt");
        keys.split(" / ").forEach((k, i) => {
          if (i) dt.append(" / ");
          const kbd = document.createElement("kbd");
          kbd.textContent = k;
          dt.appendChild(kbd);
        });
        const dd = document.createElement("dd");
        dd.textContent = desc;
        dl.append(dt, dd);
      });
      section.append(h, dl);
      groups.appendChild(section);
    });
    box.tabIndex = -1;
    box.focus();
  }

  // 탐색기에 있는 폴더·노트 목록
  function sidebarPages() {
    return (window.explorer?.items() || []).map((item) => ({
      key: item.key,
      url: item.url,
      kind: item.kind,
      iconHtml: window.pageIconHtml(item.kind),
      title: item.name,
      path: item.trail,
    }));
  }

  function openSwitcher() {
    const { box, close } = openModal("ui-switcher");
    box.innerHTML =
      `<input class="ui-switcher-input" placeholder="노트 제목으로 이동… (새 제목이면 맨 위에 새 노트)" autocomplete="off">` +
      '<div class="ui-switcher-list" role="listbox"></div>';
    const input = box.querySelector("input");
    const list = box.querySelector(".ui-switcher-list");
    const pages = sidebarPages();
    let results = [];
    let index = 0;

    function render() {
      const q = input.value.trim().toLowerCase();
      results = pages
        .filter((p) => !q || p.title.toLowerCase().includes(q) || p.path.toLowerCase().includes(q))
        .slice(0, 50)
        .map((p) => ({ ...p, go: () => (p.kind === "folder" ? window.explorer.reveal(p.key) : window.go(p.url)) }));
      if (q) {
        results.push({
          iconHtml: icon("plus"),
          title: `새 노트 만들기: "${input.value.trim()}"`,
          path: "맨 위",
          go: () => window.explorer.commands.create(null, "note", input.value.trim()).catch((err) => alert(err.message)), // 같은 이름이 있으면 알림
        });
      }
      index = Math.min(index, Math.max(results.length - 1, 0));
      list.replaceChildren(
        ...results.map((r, i) => {
          const item = document.createElement("button");
          item.type = "button";
          item.className = `ui-switcher-item${i === index ? " active" : ""}`;
          item.innerHTML = '<span class="icon"></span><span class="title"></span><span class="path"></span>';
          item.querySelector(".icon").innerHTML = r.iconHtml;
          item.querySelector(".title").textContent = r.title;
          item.querySelector(".path").textContent = r.path;
          item.addEventListener("click", () => choose(i));
          return item;
        })
      );
      if (!results.length) list.innerHTML = '<p class="muted ui-switcher-empty">노트가 없습니다.</p>';
      list.querySelector(".active")?.scrollIntoView({ block: "nearest" });
    }

    async function choose(i) {
      const r = results[i];
      if (!r) return;
      if (window.flushEditor) await window.flushEditor();
      close();
      r.go();
    }

    input.addEventListener("input", () => {
      index = 0;
      render();
    });
    input.addEventListener("keydown", (e) => {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        index = (index + (e.key === "ArrowDown" ? 1 : -1) + results.length) % Math.max(results.length, 1);
        render();
      } else if (e.key === "Enter") {
        e.preventDefault();
        choose(index);
      }
    });
    render();
    input.focus();
  }

  // ---------- 사이드바 접기 ----------
  const SIDEBAR_KEY = "marknote.sidebarCollapsed";
  function setSidebarCollapsed(collapsed) {
    if (!collapsed && !document.body.classList.contains("sidebar-collapsed")) return;
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
    } catch {
      // 저장소 사용 불가
    }
  }
  try {
    if (localStorage.getItem(SIDEBAR_KEY) === "1") setSidebarCollapsed(true);
  } catch {
    // 저장소 사용 불가
  }
  const reopen = document.createElement("button");
  reopen.type = "button";
  reopen.className = "sidebar-reopen";
  reopen.title = `사이드바 펴기 (${K}+\\)`;
  reopen.innerHTML = icon("panel-left");
  reopen.addEventListener("click", () => setSidebarCollapsed(false));
  document.body.appendChild(reopen);

  window.setSidebarCollapsed = setSidebarCollapsed;

  // ---------- 전역 단축키 ----------
  // 새 파일 = 탐색기에서 고른 폴더 안(없으면 맨 위)에 이름 칸 (내 PC 화면이면 내 PC 탐색기)
  function newFile() {
    if (!window.explorer) return;
    setSidebarCollapsed(false);
    window.explorer.run("new-file");
  }

  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.isComposing || e.keyCode === 229) return;
    if (!mod(e)) return;
    let handled = true;
    if (keyIs(e, "p") && !e.shiftKey && !e.altKey) openSwitcher();
    else if (keyIs(e, "n") && e.altKey) newFile();
    // 프레젠테이션 편집기에서는 Ctrl+J = 양쪽 맞춤 (PowerPoint), AI 도우미는 F2
    else if (keyIs(e, "j") && !e.shiftKey && !document.querySelector(".pp-app")) document.querySelector("[data-chat-toggle]")?.click();
    else if (keyIs(e, "\\")) setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed"));
    else if (keyIs(e, "/")) openHelp();
    else if (keyIs(e, "s") && !e.shiftKey) window.flushEditor?.(); // 브라우저의 "페이지 저장" 대신
    else handled = false;
    if (handled) e.preventDefault();
  });

  window.openShortcutHelp = openHelp;
  window.openModal = openModal;
  window.openSwitcher = openSwitcher;
  document.querySelector("[data-sidebar-collapse]")?.addEventListener("click", () => setSidebarCollapsed(true));
  document.querySelector("[data-shortcut-help]")?.addEventListener("click", openHelp);

  // ---------- 빈 공간 우클릭: 영역·화면마다 다른 메뉴 ----------
  const view = () => document.querySelector(".content")?.dataset.view || "";
  const go = (url) => () => (window.go(url));
  const clickEl = (selector) => () => document.querySelector(selector)?.click();
  const commonItems = () => [
    { label: "노트 빠른 이동", icon: "search", hint: `${K}+P`, action: openSwitcher },
    { label: "AI 도우미", icon: "sparkles", hint: `${K}+J`, action: clickEl("[data-chat-toggle]") },
    { label: "단축키 보기", icon: "keyboard", hint: `${K}+/`, action: openHelp },
  ];
  const newFileItems = () =>
    window.explorer
      ? [
          { label: "새 파일", icon: "file-plus", hint: `${K}+Alt+N`, action: newFile },
          { label: "새 폴더", icon: "folder-plus", action: () => (setSidebarCollapsed(false), window.explorer.run("new-folder")) },
        ]
      : [];
  window.menuCommonItems = commonItems;

  // 사이드바의 탐색기 밖 빈 곳 (탐색기 안은 explorer.js 가 먼저 처리)
  fallbacks.push((e) => {
    if (!e.target.closest(".sidebar")) return null;
    return [
      ...newFileItems(),
      { label: "노트 빠른 이동", icon: "search", hint: `${K}+P`, action: openSwitcher },
      "sep",
      { label: "보관함", icon: "archive", action: go("/archive") },
      { label: "휴지통", icon: "trash", action: go("/trash") },
      { label: "설정", icon: "settings", action: go("/settings") },
      "sep",
      { label: "사이드바 접기", icon: "panel-left", hint: `${K}+\\`, action: () => setSidebarCollapsed(true) },
    ];
  });

  // 홈 · 보관함 · 휴지통 목록의 페이지 행
  fallbacks.push((e) => {
    const row = e.target.closest(".content .page-list li");
    const link = row?.querySelector("a[href]");
    if (!link) return null;
    const items = [
      { label: "열기", icon: "open", action: go(link.getAttribute("href")) },
      { label: "새 탭에서 열기", icon: "external", action: () => window.open(link.href, "_blank") },
    ];
    const buttons = [...row.querySelectorAll("[data-action]")];
    if (buttons.length) items.push("sep");
    const ACTION_ICONS = { restore: "undo", trash: "trash", delete: "file-x", archive: "archive" };
    buttons.forEach((btn) =>
      items.push({
        label: btn.textContent.trim(),
        icon: ACTION_ICONS[btn.dataset.action],
        danger: btn.classList.contains("danger"),
        action: () => btn.click(),
      })
    );
    return items;
  });

  // 화면별 빈 곳 (페이지 화면은 editor.js 가 처리)
  fallbacks.push((e) => {
    if (!e.target.closest(".content")) return null;
    switch (view()) {
      case "home":
        return [
          ...newFileItems(),
          { label: "가져오기 (.md / .zip)", icon: "import", action: clickEl('.sidebar [data-action="import"]') },
          { label: "전체 내보내기", icon: "package", action: go("/export/all.zip") },
          "sep",
          ...commonItems(),
        ];
      case "archive":
        return [{ label: "휴지통 보기", icon: "trash", action: go("/trash") }, "sep", ...commonItems()];
      case "trash": {
        const empty = document.querySelector('[data-action="empty-trash"]');
        return [
          { label: "휴지통 비우기", icon: "trash", danger: true, disabled: !empty, action: () => empty.click() },
          { label: "보관함 보기", icon: "archive", action: go("/archive") },
          "sep",
          ...commonItems(),
        ];
      }
      case "settings":
        return [
          { label: "설정 저장", icon: "save", action: () => document.querySelector(".settings-form")?.requestSubmit() },
          { label: "설정 되돌리기 (저장 전 상태)", icon: "undo", action: () => window.reloadPage() },
          "sep",
          ...commonItems(),
        ];
      default:
        return [...newFileItems(), "sep", ...commonItems()];
    }
  });
})();
