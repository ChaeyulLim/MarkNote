// 탐색기 — VS Code 탐색기처럼 (marknote_project.md 3장). 서버 노트와 내 PC 폴더가 같은 탐색기를 쓰고 저장하는 곳(드라이버)만 다르다.
// 선택·펼침·포커스·잘라낸 항목은 탐색기가 스스로 기억하고, 화면을 옮겨도 탐색기는 다시 만들지 않는다 (목록만 새로 받음).
// 명령은 아래 commands 한 곳에만 있고 키보드·마우스·우클릭·끌어 놓기·위쪽 버튼이 모두 이것을 부른다.
//
// 드라이버: {
//   id, openKey(펼침 상태 저장 이름), titleFor?(서버: 편집기 제목과 함께 바뀌는 data-title-for),
//   list() → [{ key, kind: "folder"|"note", name, path, url?, abs?, children? }],
//   activeKey() → 지금 열린 노트의 key, open(item, { newTab }),
//   create(parentKey, kind, name) → key, rename(key, name) → 새 key, move(keys, parentKey) → 새 keys, copy(keys, parentKey) → 새 keys,
//   trash(keys) → 되돌리기 표, untrash(표), trashDone?(이름) → 안내 문구, activeAffected("gone"|"moved", 옛 key, 새 key),
//   선택: archive(keys) → 표, download(item), importInto(parentKey, files?), menuExtras(item) → 메뉴 항목, emptyMenu() → 메뉴 항목
// }
(() => {
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
  const K = isMac ? "⌘" : "Ctrl";
  const modKey = (e) => (isMac ? e.metaKey : e.ctrlKey);
  const providers = (window.contextMenuProviders = window.contextMenuProviders || []);

  function createExplorer(root, driver) {
    const list = root.querySelector(".explorer-list");
    let nodes = [];
    let index = new Map(); // key → { item, parent, depth }
    let open = loadOpen();
    let selected = new Set();
    let anchor = null; // Shift 범위 시작
    let focus = null; // 키보드 위치
    let active = null; // 지금 열린 노트
    let clip = null; // { mode: "copy" | "cut", keys }
    let editing = null; // { mode: "new" | "rename", parentKey, kind, key?, value, busy? }
    let drag = null; // { keys }
    let dropMark = null;
    let hoverTimer = null;
    let rowSeq = 0;

    // ---------- 상태 ----------
    function loadOpen() {
      try {
        return new Set(JSON.parse(localStorage.getItem(driver.openKey) || "[]").map(String));
      } catch {
        return new Set();
      }
    }
    function saveOpen() {
      try {
        localStorage.setItem(driver.openKey, JSON.stringify([...open]));
      } catch {
        // 저장소 사용 불가
      }
    }
    const get = (key) => index.get(key)?.item;
    const parentOf = (key) => index.get(key)?.parent ?? null;
    function isInside(key, ancestor) {
      for (let p = parentOf(key); p != null; p = parentOf(p)) if (p === ancestor) return true;
      return false;
    }
    const topLevel = (keys) => keys.filter((k) => index.has(k) && !keys.some((o) => o !== k && isInside(k, o)));
    const folderFor = (key) => (key == null || !index.has(key) ? null : get(key).kind === "folder" ? key : parentOf(key));
    const nameOf = (keys) => (keys.length === 1 ? `“${get(keys[0])?.name ?? ""}”` : `${keys.length}개 항목`);
    const selection = () => topLevel(selected.size ? [...selected] : focus != null ? [focus] : []);

    function load(fresh) {
      nodes = fresh || [];
      index = new Map();
      const walk = (items, parent, depth) =>
        items.forEach((item) => {
          index.set(item.key, { item, parent, depth });
          if (item.children) walk(item.children, item.key, depth + 1);
        });
      walk(nodes, null, 0);
      selected = new Set([...selected].filter((k) => index.has(k)));
      if (focus != null && !index.has(focus)) focus = null;
      if (anchor != null && !index.has(anchor)) anchor = null;
      if (clip) clip.keys = clip.keys.filter((k) => index.has(k));
      if (clip && !clip.keys.length) clip = null;
      active = driver.activeKey();
      render();
    }

    async function refresh() {
      load(await driver.list());
    }

    function expandTo(key) {
      for (let p = parentOf(key); p != null; p = parentOf(p)) open.add(p);
      saveOpen();
    }

    // ---------- 그리기 ----------
    function visibleRows() {
      const rows = [];
      const walk = (items, depth, parentKey) => {
        if (editing?.mode === "new" && editing.parentKey === parentKey) rows.push({ pending: true, depth });
        items.forEach((item) => {
          rows.push({ item, depth });
          if (item.kind === "folder" && open.has(item.key)) walk(item.children || [], depth + 1, item.key);
        });
      };
      walk(nodes, 0, null);
      return rows;
    }
    const visibleKeys = () => visibleRows().filter((r) => r.item).map((r) => r.item.key);

    function render() {
      const hadFocus = root.contains(document.activeElement);
      const rows = visibleRows();
      rowSeq++;
      list.replaceChildren(...rows.map((row, i) => (row.pending || (editing?.mode === "rename" && editing.key === row.item.key) ? inputRow(row) : itemRow(row, i))));
      if (!rows.length) {
        const empty = document.createElement("p");
        empty.className = "muted tree-empty";
        empty.textContent = "비어 있습니다. 위쪽 버튼으로 새 파일을 만드세요.";
        list.appendChild(empty);
      }
      const focused = list.querySelector(".tree-row.focused");
      if (focused) list.setAttribute("aria-activedescendant", focused.id);
      else list.removeAttribute("aria-activedescendant");
      const input = list.querySelector(".tree-input");
      if (input) {
        input.focus({ preventScroll: true });
        if (editing.mode === "rename" && !editing.selected) {
          input.select(); // 처음 한 번만 이름 전체 선택 (VS Code 처럼 바로 덮어쓰기)
          editing.selected = true;
        }
        input.scrollIntoView({ block: "nearest" });
      } else if (hadFocus && !list.contains(document.activeElement)) list.focus({ preventScroll: true });
    }

    function itemRow({ item, depth }, i) {
      const row = document.createElement("div");
      row.className = "tree-row";
      row.id = `${driver.id}-row-${rowSeq}-${i}`;
      row.setAttribute("role", "treeitem");
      row.setAttribute("aria-level", depth + 1);
      row.setAttribute("aria-selected", selected.has(item.key) ? "true" : "false");
      row.dataset.key = item.key;
      row.dataset.kind = item.kind;
      row.draggable = true;
      row.title = item.abs || item.path;
      row.style.paddingLeft = `${4 + depth * 14}px`;
      const isOpen = item.kind === "folder" && open.has(item.key);
      if (item.kind === "folder") row.setAttribute("aria-expanded", isOpen ? "true" : "false");
      row.classList.toggle("open", isOpen);
      row.classList.toggle("active", item.key === active);
      row.classList.toggle("selected", selected.has(item.key));
      row.classList.toggle("focused", item.key === focus);
      row.classList.toggle("clip-cut", clip?.mode === "cut" && clip.keys.includes(item.key));
      row.innerHTML =
        `<span class="tree-toggle${item.kind === "folder" ? "" : " leaf"}">${icon("chevron-right")}</span>` +
        `<span class="tree-icon">${window.pageIconHtml(item.kind)}</span><span class="tree-title"></span>`;
      const title = row.querySelector(".tree-title");
      title.textContent = item.name;
      if (driver.titleFor) title.dataset.titleFor = item.key; // 편집기에서 제목을 고치는 동안 함께 바뀜
      return row;
    }

    function inputRow({ item, depth }) {
      const kind = item ? item.kind : editing.kind;
      const row = document.createElement("div");
      row.className = "tree-row editing";
      row.style.paddingLeft = `${4 + depth * 14}px`;
      row.innerHTML = `<span class="tree-toggle leaf">${icon("chevron-right")}</span><span class="tree-icon">${window.pageIconHtml(kind)}</span><input class="tree-input" autocomplete="off" spellcheck="false">`;
      const input = row.querySelector("input");
      input.value = editing.value;
      input.placeholder = kind === "folder" ? "새 폴더 이름" : "새 파일 이름 (.md)";
      input.setAttribute("aria-label", input.placeholder);
      if (editing.error) input.classList.add("error");
      input.addEventListener("input", () => {
        editing.value = input.value;
        input.classList.remove("error");
      });
      input.addEventListener("keydown", (e) => {
        e.stopPropagation(); // 탐색기·전역 단축키로 가지 않게
        if (e.isComposing || e.keyCode === 229) return;
        if (e.key === "Enter") {
          e.preventDefault();
          commitEdit(false);
        } else if (e.key === "Escape") {
          e.preventDefault();
          cancelEdit();
        }
      });
      input.addEventListener("blur", () => setTimeout(() => editing && !editing.busy && list.contains(input) && commitEdit(true)));
      return row;
    }

    function scrollToFocus() {
      list.querySelector(".tree-row.focused")?.scrollIntoView({ block: "nearest" });
    }

    // ---------- 고르기 ----------
    function selectOnly(key) {
      selected = new Set(key != null ? [key] : []);
      anchor = focus = key;
    }
    function selectRange(from, to) {
      const keys = visibleKeys();
      let a = keys.indexOf(from);
      const b = keys.indexOf(to);
      if (a < 0) a = b;
      selected = new Set(keys.slice(Math.min(a, b), Math.max(a, b) + 1));
    }
    function moveFocus(target, extend) {
      if (target == null) return;
      if (extend) selectRange(anchor ?? focus ?? target, target);
      else selectOnly(target);
      focus = target;
      render();
      scrollToFocus();
    }
    function toggleOpen(key, value = !open.has(key)) {
      if (value) open.add(key);
      else open.delete(key);
      saveOpen();
      render();
    }

    // ---------- 바꾸기 공통: 편집 중인 내용 저장 → 바꾸기 → 목록 다시 받기 → 열린 노트가 영향을 받았으면 처리 ----------
    async function mutate(keys, fn, effect) {
      await window.flushEditor?.();
      const was = driver.activeKey();
      const hit = was != null && keys.some((k) => k === was || isInside(was, k));
      const before = hit ? keys.find((k) => k === was || isInside(was, k)) : null;
      const result = await fn();
      await refresh();
      if (hit && effect) {
        // 옮긴 것이 열린 노트의 상위 폴더면 열린 노트의 새 key 는 드라이버가 안다 (서버는 번호 그대로)
        const moved = Array.isArray(result) ? result[keys.indexOf(before)] : result;
        await driver.activeAffected(effect, was, before, moved);
      }
      return result;
    }

    function done(message, undoable = true) {
      window.toast(message, undoable ? { label: "되돌리기", action: () => window.undoManager.undo() } : null);
    }

    // ---------- 명령 ----------
    const commands = {
      open(item, newTab = false) {
        if (item.kind === "folder") return toggleOpen(item.key);
        return driver.open(item, { newTab });
      },

      // 새 파일·폴더: 고른 폴더 안(파일을 골랐으면 그 옆)에 이름 칸을 띄운다 → Enter 로 만들기
      startNew(kind, target = focus) {
        if (editing) cancelEdit();
        const parentKey = folderFor(target);
        if (parentKey != null) {
          open.add(parentKey);
          expandTo(parentKey);
        }
        editing = { mode: "new", parentKey, kind, value: "" };
        render();
      },

      startRename(key) {
        if (editing) cancelEdit();
        const item = get(key);
        if (!item) return;
        selectOnly(key);
        editing = { mode: "rename", key, kind: item.kind, value: item.name };
        render();
      },

      async create(parentKey, kind, name) {
        const key = await mutate([], () => driver.create(parentKey, kind, name), null);
        if (parentKey != null) open.add(parentKey);
        saveOpen();
        selectOnly(key);
        render();
        let token = null;
        window.undoManager.push({
          label: `“${name}” 만들기`,
          undo: async () => {
            token = await mutate([key], () => driver.trash([key]), "gone");
          },
          redo: async () => {
            await mutate([], () => driver.untrash(token), null);
            selectOnly(key);
            render();
          },
        });
        // 만들기는 끝났다 — 여는 데 실패해도 이름 칸으로 돌아가지 않게 따로 알림
        if (kind === "note") Promise.resolve(driver.open(get(key) || { key }, { newTab: false })).catch((err) => alert(err.message));
        else list.focus();
      },

      async rename(key, name) {
        const old = get(key).name;
        const fresh = await mutate([key], () => driver.rename(key, name), "moved");
        selectOnly(fresh);
        render();
        let cur = fresh;
        window.undoManager.push({
          label: `“${old}” 이름 바꾸기`,
          undo: async () => {
            cur = await mutate([cur], () => driver.rename(cur, old), "moved");
          },
          redo: async () => {
            cur = await mutate([cur], () => driver.rename(cur, name), "moved");
          },
        });
      },

      async trash(keys) {
        keys = topLevel(keys);
        if (!keys.length) return;
        const label = nameOf(keys);
        // 지운 뒤 포커스는 바로 아래 항목으로 (없으면 위)
        const rows = visibleKeys();
        const gone = (k) => keys.some((g) => k === g || isInside(k, g));
        const last = Math.max(...keys.map((k) => rows.indexOf(k)));
        const next = rows.slice(last + 1).find((k) => !gone(k)) ?? rows.slice(0, last).reverse().find((k) => !gone(k)) ?? null;
        let token = await mutate(keys, () => driver.trash(keys), "gone");
        selectOnly(next);
        render();
        window.undoManager.push({
          label: `${label} 휴지통으로 옮기기`,
          undo: async () => {
            await mutate([], () => driver.untrash(token), null);
            selected = new Set(keys.filter((k) => index.has(k)));
            anchor = focus = [...selected][0] ?? focus;
            keys.forEach((k) => index.has(k) && expandTo(k));
            render();
          },
          redo: async () => {
            token = await mutate(keys, () => driver.trash(keys), "gone");
          },
        });
        done(driver.trashDone ? driver.trashDone(label) : `${label} 을(를) 휴지통으로 옮겼습니다`);
      },

      async archive(keys) {
        keys = topLevel(keys);
        if (!keys.length || !driver.archive) return;
        let token = await mutate(keys, () => driver.archive(keys), "gone");
        window.undoManager.push({
          label: `${nameOf(keys)} 보관`,
          undo: () => mutate([], () => driver.untrash(token), null),
          redo: async () => {
            token = await mutate(keys, () => driver.archive(keys), "gone");
          },
        });
        done(`${nameOf(keys)} 을(를) 보관함으로 옮겼습니다`);
      },

      clip(keys, mode) {
        keys = topLevel(keys);
        if (!keys.length) return;
        clip = { mode, keys };
        render();
        window.toast(`${nameOf(keys)} ${mode === "cut" ? "잘라냄 — 옮길 곳을 고르고 " : "복사함 — 붙일 곳을 고르고 "}${K}+V`);
      },

      async paste(target = focus) {
        if (!clip) return window.toast(`먼저 ${K}+C 또는 ${K}+X 로 고르세요`);
        const parentKey = folderFor(target);
        if (clip.mode === "copy") return commands.copyTo(clip.keys, parentKey);
        const keys = clip.keys;
        clip = null;
        await commands.moveTo(keys, parentKey);
      },

      async copyTo(keys, parentKey) {
        keys = topLevel(keys);
        if (keys.some((k) => parentKey === k || (parentKey != null && isInside(parentKey, k)))) {
          return window.toast("자기 자신이나 그 안의 폴더에는 붙여 넣을 수 없습니다");
        }
        const label = nameOf(keys);
        const made = await mutate([], () => driver.copy(keys, parentKey), null);
        if (parentKey != null) open.add(parentKey);
        saveOpen();
        selected = new Set(made);
        anchor = focus = made[0] ?? null;
        render();
        scrollToFocus();
        let token = null;
        window.undoManager.push({
          label: `${label} 붙여넣기`,
          undo: async () => {
            token = await mutate(made, () => driver.trash(made), "gone");
          },
          redo: () => mutate([], () => driver.untrash(token), null),
        });
        done(`${label} 을(를) 붙여 넣었습니다`);
      },

      async moveTo(keys, parentKey) {
        keys = topLevel(keys).filter((k) => parentOf(k) !== parentKey);
        if (!keys.length) return render();
        if (keys.some((k) => parentKey === k || (parentKey != null && isInside(parentKey, k)))) {
          return window.toast("자기 자신이나 그 안의 폴더로는 옮길 수 없습니다");
        }
        const label = nameOf(keys);
        const from = keys.map(parentOf);
        let cur = await mutate(keys, () => driver.move(keys, parentKey), "moved");
        if (parentKey != null) open.add(parentKey);
        saveOpen();
        selected = new Set(cur);
        anchor = focus = cur[0] ?? null;
        render();
        scrollToFocus();
        window.undoManager.push({
          label: `${label} 옮기기`,
          undo: async () => {
            const back = [];
            for (let i = 0; i < cur.length; i++) back.push((await mutate([cur[i]], () => driver.move([cur[i]], from[i]), "moved"))[0]);
            cur = back;
          },
          redo: async () => {
            cur = await mutate(cur, () => driver.move(cur, parentKey), "moved");
          },
        });
        done(`${label} 을(를) 옮겼습니다`);
      },

      selectAll() {
        const keys = visibleKeys();
        selected = new Set(keys);
        anchor = keys[0] ?? null;
        focus = focus ?? anchor;
        render();
      },

      collapseAll() {
        open.clear();
        saveOpen();
        render();
      },

      expandAll() {
        index.forEach(({ item }) => item.kind === "folder" && open.add(item.key));
        saveOpen();
        render();
      },

      async refresh() {
        await refresh();
      },

      // 가져오기 (파일을 고르거나 끌어다 놓은 files) → 그 폴더를 펼쳐 보여 줌
      async importInto(parentKey, files) {
        const res = await driver.importInto(parentKey, files);
        if (!res) return;
        if (parentKey != null) open.add(parentKey);
        saveOpen();
        await refresh();
      },

      copyPath(item) {
        return window.copyToClipboard(item.abs || item.path).then(() => window.toast("파일 위치를 복사했습니다"));
      },
    };

    async function commitEdit(fromBlur) {
      const ed = editing;
      if (!ed || ed.busy) return;
      const name = ed.value.trim();
      if (!name || (ed.mode === "rename" && name === get(ed.key)?.name)) return cancelEdit(fromBlur);
      ed.busy = true;
      try {
        if (ed.mode === "new") {
          editing = null;
          await commands.create(ed.parentKey, ed.kind, name);
        } else {
          editing = null;
          await commands.rename(ed.key, name);
        }
      } catch (err) {
        window.toast(err.message);
        if (fromBlur) return render(); // 다른 곳을 눌렀으면 포기
        editing = { ...ed, busy: false, error: true }; // Enter 였으면 칸을 그대로 두고 다시 입력
        render();
      }
    }

    function cancelEdit(fromBlur = false) {
      editing = null;
      render();
      if (!fromBlur) list.focus();
    }

    // ---------- 키보드 (탐색기에 포커스가 있을 때) ----------
    list.addEventListener("keydown", (e) => {
      if (e.target !== list || e.isComposing || e.keyCode === 229 || e.altKey) return;
      const mod = modKey(e);
      const keys = visibleKeys();
      const at = keys.indexOf(focus);
      const item = get(focus);
      let handled = true;
      if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !mod) {
        const step = e.key === "ArrowDown" ? 1 : -1;
        moveFocus(keys[at < 0 ? (step > 0 ? 0 : keys.length - 1) : Math.max(0, Math.min(keys.length - 1, at + step))], e.shiftKey);
      } else if ((e.key === "Home" || e.key === "End") && !mod) {
        moveFocus(e.key === "Home" ? keys[0] : keys.at(-1), e.shiftKey);
      } else if (e.key === "ArrowRight" && !mod && item) {
        if (item.kind !== "folder") handled = false;
        else if (!open.has(item.key)) toggleOpen(item.key, true);
        else if (item.children?.length) moveFocus(item.children[0].key, false);
      } else if (e.key === "ArrowLeft" && !mod && item) {
        if (item.kind === "folder" && open.has(item.key)) toggleOpen(item.key, false);
        else moveFocus(parentOf(item.key), false);
      } else if (e.key === "Enter" && !mod && item) {
        commands.open(item);
      } else if ((e.key === "Delete" || (isMac && mod && e.key === "Backspace")) && !e.shiftKey) {
        commands.trash(selection()).catch((err) => alert(err.message));
      } else if (mod && !e.shiftKey && window.keyIs(e, "c")) {
        commands.clip(selection(), "copy");
      } else if (mod && !e.shiftKey && window.keyIs(e, "x")) {
        commands.clip(selection(), "cut");
      } else if (mod && !e.shiftKey && window.keyIs(e, "v")) {
        commands.paste().catch((err) => alert(err.message));
      } else if (mod && !e.shiftKey && window.keyIs(e, "a")) {
        commands.selectAll();
      } else if (e.key === "Escape") {
        if (clip) clip = null;
        else selectOnly(focus);
        render();
      } else handled = false;
      if (handled) e.preventDefault();
    });

    // ---------- 마우스 ----------
    list.addEventListener("click", (e) => {
      if (e.target.closest(".tree-input")) return;
      const row = e.target.closest(".tree-row[data-key]");
      if (!row) {
        if (e.target === list || e.target.closest(".tree-empty")) {
          selected.clear();
          anchor = focus = null; // 빈 곳 = 맨 위 (새 파일·붙여넣기가 맨 위로)
          render();
        }
        return;
      }
      const key = row.dataset.key;
      if (modKey(e)) {
        if (selected.has(key)) selected.delete(key);
        else selected.add(key);
        anchor = focus = key;
        return render();
      }
      if (e.shiftKey) {
        selectRange(anchor ?? key, key);
        focus = key;
        return render();
      }
      selectOnly(key);
      const item = get(key);
      if (item.kind === "folder") toggleOpen(key);
      else {
        render();
        Promise.resolve(driver.open(item, { newTab: false })).catch((err) => alert(err.message));
      }
    });
    list.addEventListener("auxclick", (e) => {
      const row = e.target.closest(".tree-row[data-key]");
      if (e.button === 1 && row && row.dataset.kind === "note") {
        e.preventDefault();
        driver.open(get(row.dataset.key), { newTab: true });
      }
    });
    list.addEventListener("mousedown", (e) => {
      if (e.button === 1 && e.target.closest(".tree-row")) e.preventDefault(); // 가운데 버튼 자동 스크롤 막기
    });

    // ---------- 끌어 놓기: 폴더 위 = 그 안으로, 파일 위 = 그 파일이 있는 폴더로, 빈 곳 = 맨 위로. Ctrl(⌥) = 복사 ----------
    function dropTarget(e) {
      const row = e.target.closest(".tree-row[data-key]");
      if (!row && !list.contains(e.target)) return undefined;
      const parentKey = row ? folderFor(row.dataset.key) : null;
      if (drag && drag.keys.some((k) => parentKey === k || (parentKey != null && isInside(parentKey, k)))) return undefined;
      return parentKey;
    }
    function markDrop(parentKey) {
      if (dropMark === parentKey) return;
      clearDrop();
      dropMark = parentKey;
      if (parentKey === undefined) return;
      if (parentKey === null) list.classList.add("drop-root");
      else list.querySelector(`.tree-row[data-key="${CSS.escape(parentKey)}"]`)?.classList.add("drop-into");
      if (parentKey != null && !open.has(parentKey)) {
        hoverTimer = setTimeout(() => {
          open.add(parentKey);
          saveOpen();
          render();
          dropMark = undefined; // 다시 그린 폴더 행에 표시 다시 달기
          markDrop(parentKey);
        }, 700);
      }
    }
    function clearDrop() {
      clearTimeout(hoverTimer);
      list.classList.remove("drop-root");
      list.querySelectorAll(".drop-into").forEach((r) => r.classList.remove("drop-into"));
      dropMark = undefined;
    }
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");

    list.addEventListener("dragstart", (e) => {
      const row = e.target.closest?.(".tree-row[data-key]");
      if (!row) return;
      const key = row.dataset.key;
      if (!selected.has(key)) {
        selectOnly(key);
        list.querySelectorAll(".tree-row.selected").forEach((r) => r.classList.remove("selected"));
        row.classList.add("selected");
      }
      drag = { keys: topLevel([...selected]) };
      e.dataTransfer.effectAllowed = "copyMove";
      e.dataTransfer.setData("text/plain", drag.keys.map((k) => get(k).name).join("\n"));
      const item = get(key);
      if (drag.keys.length === 1 && item.url) e.dataTransfer.setData("text/uri-list", new URL(item.url, location.href).href);
    });
    list.addEventListener("dragover", (e) => {
      if (!drag && !(hasFiles(e) && driver.importInto)) return;
      const target = dropTarget(e);
      if (target === undefined) return clearDrop();
      e.preventDefault();
      e.dataTransfer.dropEffect = drag && !(e.ctrlKey || e.altKey) ? "move" : "copy";
      markDrop(target);
    });
    list.addEventListener("dragleave", (e) => {
      if (!list.contains(e.relatedTarget)) clearDrop();
    });
    list.addEventListener("drop", (e) => {
      const target = dropTarget(e);
      const moving = drag;
      drag = null;
      clearDrop();
      if (target === undefined) return;
      if (moving) {
        e.preventDefault();
        const copy = e.ctrlKey || e.altKey;
        (copy ? commands.copyTo(moving.keys, target) : commands.moveTo(moving.keys, target)).catch((err) => alert(err.message));
      } else if (hasFiles(e) && driver.importInto) {
        e.preventDefault();
        commands.importInto(target, [...e.dataTransfer.files]).catch((err) => alert(err.message));
      }
    });
    document.addEventListener("dragend", () => {
      drag = null;
      clearDrop();
    });

    // ---------- 위쪽 버튼 ----------
    root.addEventListener("click", (e) => {
      const btn = e.target.closest(".explorer-head [data-explorer-cmd]");
      if (!btn) return;
      e.preventDefault();
      run(btn.dataset.explorerCmd);
    });

    function run(cmd, target) {
      const job = {
        "new-file": () => commands.startNew("note", target),
        "new-folder": () => commands.startNew("folder", target),
        refresh: () => commands.refresh(),
        "collapse-all": () => commands.collapseAll(),
        "expand-all": () => commands.expandAll(),
      }[cmd];
      return Promise.resolve(job?.()).catch((err) => alert(err.message));
    }

    // ---------- 우클릭 ----------
    providers.push((e) => {
      if (!list.contains(e.target) || e.target.closest(".tree-input")) return null;
      const row = e.target.closest(".tree-row[data-key]");
      const paste = { label: "붙여넣기", icon: "clipboard", hint: `${K}+V`, disabled: !clip, action: () => commands.paste(row ? row.dataset.key : null) };
      if (!row) {
        return [
          { label: "새 파일", icon: "file-plus", hint: `${K}+Alt+N`, action: () => commands.startNew("note", null) },
          { label: "새 폴더", icon: "folder-plus", action: () => commands.startNew("folder", null) },
          paste,
          "sep",
          { label: "모두 펼치기", icon: "expand", action: commands.expandAll },
          { label: "모두 접기", icon: "collapse", action: commands.collapseAll },
          { label: "새로고침", icon: "refresh", action: commands.refresh },
          ...(driver.emptyMenu ? ["sep", ...driver.emptyMenu()] : []),
        ];
      }
      const key = row.dataset.key;
      if (!selected.has(key)) selectOnly(key);
      focus = key;
      render();
      list.focus({ preventScroll: true });
      const item = get(key);
      const keys = selection();
      const many = keys.length > 1;
      const isFolder = item.kind === "folder";
      return [
        ...(many
          ? []
          : [
              isFolder
                ? { label: open.has(key) ? "접기" : "펼치기", icon: "folder", action: () => toggleOpen(key) }
                : { label: "열기", icon: "open", hint: "Enter", action: () => driver.open(item, { newTab: false }) },
              ...(!isFolder && item.url ? [{ label: "새 탭에서 열기", icon: "external", action: () => driver.open(item, { newTab: true }) }] : []),
              "sep",
              { label: "새 파일", icon: "file-plus", action: () => commands.startNew("note", key) },
              { label: "새 폴더", icon: "folder-plus", action: () => commands.startNew("folder", key) },
            ]),
        "sep",
        { label: "잘라내기", icon: "x", hint: `${K}+X`, action: () => commands.clip(keys, "cut") },
        { label: "복사", icon: "copy", hint: `${K}+C`, action: () => commands.clip(keys, "copy") },
        paste,
        "sep",
        ...(many
          ? []
          : [
              { label: "이름 바꾸기", icon: "type", action: () => commands.startRename(key) },
              { label: "파일 위치 복사", icon: "link", action: () => commands.copyPath(item) },
              ...(driver.download ? [{ label: isFolder ? "폴더째 내려받기 (.zip)" : "Markdown 다운로드 (.md)", icon: isFolder ? "package" : "download", action: () => driver.download(item) }] : []),
              ...(driver.importInto && isFolder ? [{ label: "이 폴더로 가져오기 (.md / .zip)", icon: "import", action: () => commands.importInto(key) }] : []),
              ...(driver.menuExtras ? driver.menuExtras(item) : []),
            ]),
        "sep",
        ...(driver.archive ? [{ label: "보관", icon: "archive", action: () => commands.archive(keys) }] : []),
        { label: driver.trashLabel || "휴지통으로 이동", icon: "trash", danger: true, hint: "Delete", action: () => commands.trash(keys) },
      ];
    });

    // ---------- 밖에서 부르는 것 ----------
    const api = {
      root,
      driver,
      load,
      refresh,
      run,
      commands,
      // 지금 열린 노트 강조 + 그 노트가 든 폴더 펼치기
      syncActive({ reveal = true } = {}) {
        active = driver.activeKey();
        if (reveal && active != null && index.has(active)) expandTo(active);
        render();
      },
      // 폴더를 탐색기에서 펼쳐 보여 주기 (폴더 주소·경로 표시의 폴더 링크)
      reveal(key, { focusList = true } = {}) {
        if (!index.has(key)) return false;
        expandTo(key);
        if (get(key).kind === "folder") open.add(key);
        saveOpen();
        selectOnly(key);
        render();
        if (focusList) list.focus({ preventScroll: true });
        scrollToFocus();
        return true;
      },
      revealUrl(url) {
        const path = decodeURI(new URL(url, location.href).pathname);
        for (const { item } of index.values()) if (item.kind === "folder" && item.url && decodeURI(item.url) === path) return api.reveal(item.key);
        return false;
      },
      items() {
        return [...index.values()].map(({ item }) => {
          const trail = [];
          for (let p = parentOf(item.key); p != null; p = parentOf(p)) trail.unshift(get(p).name);
          return { ...item, trail: trail.join(" / ") };
        });
      },
      get: (key) => get(key),
      isInside,
    };
    load(driver.initial?.() || []);
    return api;
  }

  window.createExplorer = createExplorer;

  // 탐색기 밖의 "새 파일"·"새 폴더" 버튼 (처음 화면 등) — 지금 탐색기의 명령으로
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-explorer-cmd]");
    if (!btn || btn.closest(".explorer-head") || !window.explorer) return;
    e.preventDefault();
    window.setSidebarCollapsed?.(false);
    window.explorer.run(btn.dataset.explorerCmd, null);
  });

  // ---------- 서버 노트 드라이버 ----------
  const pageData = () => {
    try {
      return JSON.parse(document.getElementById("page-data")?.textContent || "{}");
    } catch {
      return {};
    }
  };
  const toKey = (n) => ({ key: String(n.id), kind: n.kind, name: n.name, path: n.path, abs: n.abs, url: n.url, children: n.children?.map(toKey) });
  const parentId = (key) => (key == null ? null : Number(key));

  const serverDriver = {
    id: "server",
    openKey: "marknote.openNodes",
    titleFor: true,
    parse: (text) => JSON.parse(text || "[]").map(toKey),
    initial() {
      try {
        return serverDriver.parse(document.getElementById("explorer-data")?.textContent);
      } catch {
        return [];
      }
    },
    async list() {
      return (await window.api("GET", "/api/tree")).nodes.map(toKey);
    },
    activeKey() {
      const page = pageData().page;
      const id = window.editorPageId ?? (page?.status === "active" ? page.id : null);
      return id != null ? String(id) : null;
    },
    open(item, { newTab = false } = {}) {
      if (newTab) return window.open(item.url, "_blank");
      return window.go(item.url || `/p/${item.key}`);
    },
    async create(parentKey, kind, name) {
      const res = await window.api("POST", "/api/pages", { parent_id: parentId(parentKey), kind, title: name });
      return String(res.page.id);
    },
    async rename(key, name) {
      await window.api("PATCH", `/api/pages/${key}`, { title: name });
      return key;
    },
    async move(keys, parentKey) {
      for (const key of keys) await window.api("PATCH", `/api/pages/${key}`, { parent_id: parentId(parentKey) });
      return keys;
    },
    async copy(keys, parentKey) {
      const made = [];
      for (const key of keys) made.push(String((await window.api("POST", `/api/pages/${key}/copy`, { parent_id: parentId(parentKey) })).page.id));
      return made;
    },
    async trash(keys) {
      for (const key of keys) await window.api("POST", `/api/pages/${key}/trash`);
      return keys;
    },
    async archive(keys) {
      for (const key of keys) await window.api("POST", `/api/pages/${key}/archive`);
      return keys;
    },
    async untrash(keys) {
      for (const key of keys) await window.api("POST", `/api/pages/${key}/restore`);
      return keys;
    },
    // 열린 노트가 휴지통으로 갔으면 처음 화면으로, 경로가 바뀌었으면 번호 주소로 다시 열어 새 주소로
    activeAffected(effect, was) {
      if (effect === "gone") return window.go("/");
      return window.go(`/p/${was}`, { replace: true });
    },
    download(item) {
      location.href = `/export/page/${item.key}.${item.kind === "folder" ? "zip" : "md"}`;
    },
    importInto(parentKey, files) {
      return window.importInto(parentKey == null ? null : Number(parentKey), files);
    },
    emptyMenu() {
      const go = (url) => () => window.go(url);
      return [
        { label: "가져오기 (.md / .zip)", icon: "import", action: () => window.importInto(null).then(() => window.explorer.refresh()) },
        { label: "전체 내보내기", icon: "package", action: () => (location.href = "/export/all.zip") },
        "sep",
        { label: "보관함", icon: "archive", action: go("/archive") },
        { label: "휴지통", icon: "trash", action: go("/trash") },
      ];
    },
  };

  const serverRoot = document.querySelector('.explorer[data-explorer="server"]');
  if (serverRoot) {
    window.explorer = createExplorer(serverRoot, serverDriver);
    const sync = () => {
      window.explorer.syncActive();
      const reveal = new URLSearchParams(location.search).get("reveal");
      if (reveal && window.explorer.reveal(reveal)) history.replaceState(history.state, "", location.pathname); // 한 번 보여 주면 주소에서 뺌
    };
    sync();
    document.addEventListener("marknote:pageload", sync);
  }
})();
