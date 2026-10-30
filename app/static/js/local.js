// 내 PC 폴더 모드 — 브라우저가 PC 의 마크다운 폴더를 직접 읽고 쓴다 (File System Access API, Chrome·Edge).
// 노트 내용·파일 이름은 서버로 보내지 않는다. 화면은 서버 노트와 같은 편집기(editor.js)를 쓰고,
// 편집기가 부르는 /api/... 요청을 window.pageApi 가 받아 PC 파일에 반영한다.
// 휴지통 = MarkNote 에서 연결만 끊기 (파일은 그대로, 이 브라우저에만 기억). AI 도우미는 물을 때만 지금 노트를 보낸다 (chat.js).
// 왼쪽 탐색기는 서버 노트와 같은 explorer.js — 여기서는 PC 폴더용 드라이버(localDriver)만 붙인다.
(() => {
  const pick = document.getElementById("local-pick");
  const view = document.getElementById("local-view");
  const explorerRoot = document.querySelector('.explorer[data-explorer="local"]');
  const unhideBtn = document.querySelector("[data-local-unhide]");
  const closeBtn = document.querySelector("[data-local-close]");

  // ---------- 이 브라우저에 기억: 연 폴더(핸들) · 연결 끊은 항목 (IndexedDB) ----------
  function idb(mode, fn) {
    return new Promise((resolve, reject) => {
      const open = indexedDB.open("marknote-local", 1);
      open.onupgradeneeded = () => open.result.createObjectStore("kv");
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const tx = open.result.transaction("kv", mode);
        const req = fn(tx.objectStore("kv"));
        tx.oncomplete = () => resolve(req.result);
        tx.onerror = () => reject(tx.error);
      };
    });
  }
  const kvGet = (key) => idb("readonly", (s) => s.get(key)).catch(() => undefined);
  const kvSet = (key, value) => idb("readwrite", (s) => s.put(value, key)).catch(console.error);
  const kvDel = (key) => idb("readwrite", (s) => s.delete(key)).catch(console.error);

  let root = null; // FileSystemDirectoryHandle
  let hidden = new Set(); // 연결 끊은 경로 ("회사/회의록.md", 폴더는 "회사/2026")
  let entries = new Map(); // 경로 → { kind, name, handle, dir(부모 폴더 핸들), parent(부모 경로) }
  let current = null; // 열린 노트 { path, handle, dir, pageId, blocks: [{id, content}] }
  let nextId = 1;
  let writeQueue = Promise.resolve();
  const hiddenKey = () => `hidden:${root.name}`;
  const saveHidden = () => kvSet(hiddenKey(), [...hidden]);

  // ---------- 경로 · 이름 ----------
  const UNTITLED = "제목 없음";
  const dirname = (path) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");
  const basename = (path) => path.slice(path.lastIndexOf("/") + 1);
  const titleOf = (path) => basename(path).replace(/\.md$/i, "");
  const join = (dir, name) => (dir ? `${dir}/${name}` : name);
  const hashFor = (path, kind) => `#/${path.split("/").map(encodeURIComponent).join("/")}${kind === "folder" && path ? "/" : ""}`;
  const safeName = (title) => (title || "").replace(/[\\/:*?"<>|\u0000-\u001f]/g, "").replace(/^\.+/, "").trim().slice(0, 200) || UNTITLED;
  function isHidden(path) {
    for (const h of hidden) if (path === h || path.startsWith(`${h}/`)) return true;
    return false;
  }
  function resolvePath(base, rel) {
    const parts = base ? base.split("/") : [];
    for (const part of rel.split("/")) {
      if (part === "..") parts.pop();
      else if (part && part !== ".") parts.push(part);
    }
    return parts.join("/");
  }

  // ---------- 폴더 훑기 ----------
  async function scan() {
    const found = new Map();
    async function walk(dir, path) {
      for await (const [name, handle] of dir.entries()) {
        if (name.startsWith(".")) continue;
        const child = join(path, name);
        if (handle.kind === "directory") {
          if (name.toLowerCase() === "images") continue; // 그림 폴더
          found.set(child, { kind: "folder", name, handle, dir, parent: path });
          await walk(handle, child);
        } else if (/\.md$/i.test(name)) {
          found.set(child, { kind: "note", name, handle, dir, parent: path });
        }
      }
    }
    await walk(root, "");
    entries = found;
  }

  const byName = (a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name, "ko") : a.kind === "folder" ? -1 : 1);
  const childrenOf = (path) =>
    [...entries.entries()]
      .filter(([p, e]) => e.parent === path && !isHidden(p))
      .map(([p, e]) => ({ path: p, ...e }))
      .sort(byName);

  // ---------- 탐색기 드라이버 (explorer.js) — key = 연 폴더 기준 경로 ----------
  const conflict = () => notFound("같은 폴더에 같은 이름이 이미 있습니다.", 409);
  async function dirAt(path) {
    let dir = root;
    for (const part of path ? path.split("/") : []) dir = await dir.getDirectoryHandle(part);
    return dir;
  }
  async function exists(dir, name) {
    for await (const key of dir.keys()) if (key.toLowerCase() === name.toLowerCase()) return true;
    return false;
  }
  async function uniqueName(dir, stem, ext) {
    let name = `${stem}${ext}`;
    for (let n = 2; await exists(dir, name); n++) name = `${stem} (${n})${ext}`;
    return name;
  }
  // 파일·폴더째 복사 (move() 가 없는 브라우저의 옮기기에도 씀)
  async function copyInto(handle, destDir, name) {
    if (handle.kind === "file") {
      const fresh = await destDir.getFileHandle(name, { create: true });
      const w = await fresh.createWritable();
      await w.write(await handle.getFile());
      await w.close();
      return;
    }
    const sub = await destDir.getDirectoryHandle(name, { create: true });
    for await (const [childName, child] of handle.entries()) await copyInto(child, sub, childName);
  }
  // 옮기기·이름 바꾸기: path → destPath 폴더 안 name. 돌려줌: 새 경로
  async function moveEntry(path, destPath, name) {
    const entry = entries.get(path);
    if (!entry) throw notFound("찾을 수 없습니다. 새로고침해 보세요.", 404);
    const destDir = await dirAt(destPath);
    const sameDir = entry.parent === destPath;
    if (!(sameDir && name.toLowerCase() === entry.name.toLowerCase()) && (await exists(destDir, name))) throw conflict();
    await writeQueue;
    let moved = false;
    if (typeof entry.handle.move === "function") {
      try {
        await (sameDir ? entry.handle.move(name) : entry.handle.move(destDir, name));
        moved = true;
      } catch (err) {
        if (err.name !== "NotSupportedError" && err.name !== "TypeError") throw err;
      }
    }
    if (!moved) {
      await copyInto(entry.handle, destDir, name);
      await entry.dir.removeEntry(entry.name, { recursive: true });
    }
    const fresh = join(destPath, name);
    const old = [...hidden];
    const renamed = old.map((h) => (h === path || h.startsWith(`${path}/`) ? fresh + h.slice(path.length) : h));
    if (renamed.some((h, i) => h !== old[i])) {
      hidden = new Set(renamed);
      await saveHidden();
    }
    if (current && (current.path === path || current.path.startsWith(`${path}/`))) current.path = fresh + current.path.slice(path.length);
    return fresh;
  }

  const localDriver = {
    id: "local",
    openKey: "marknote.local.openNodes",
    trashLabel: "휴지통으로 (연결만 끊기)",
    trashDone: (label) => `${label} 연결을 끊었습니다. PC 의 파일은 그대로 있습니다.`,
    async list() {
      if (!root) return [];
      await scan();
      const build = (path) =>
        childrenOf(path).map((item) => ({
          key: item.path,
          kind: item.kind,
          name: item.kind === "note" ? titleOf(item.path) : item.name,
          path: item.kind === "folder" ? `${item.path}/` : item.path,
          abs: `${root.name}/${item.path}`,
          children: item.kind === "folder" ? build(item.path) : undefined,
        }));
      return build("");
    },
    activeKey: () => current?.path ?? null,
    open(item, { newTab = false } = {}) {
      const hash = hashFor(item.key, item.kind);
      if (newTab) return window.open(`/local${hash}`, "_blank");
      location.hash = hash;
    },
    async create(parentKey, kind, title) {
      if (!root) throw notFound("먼저 폴더를 여세요.");
      const dir = await dirAt(parentKey || "");
      const name = `${safeName(title)}${kind === "note" ? ".md" : ""}`;
      if (await exists(dir, name)) throw conflict();
      if (kind === "folder") await dir.getDirectoryHandle(name, { create: true });
      else await dir.getFileHandle(name, { create: true });
      await scan();
      return join(parentKey || "", name);
    },
    async rename(key, title) {
      const entry = entries.get(key);
      return moveEntry(key, entry?.parent ?? "", `${safeName(title)}${entry?.kind === "note" ? ".md" : ""}`);
    },
    async move(keys, parentKey) {
      const made = [];
      for (const key of keys) made.push(await moveEntry(key, parentKey || "", basename(key)));
      return made;
    },
    async copy(keys, parentKey) {
      const dir = await dirAt(parentKey || "");
      const made = [];
      for (const key of keys) {
        const entry = entries.get(key);
        const ext = entry.kind === "note" ? ".md" : "";
        const name = await uniqueName(dir, ext ? entry.name.slice(0, -ext.length) : entry.name, ext);
        await copyInto(entry.handle, dir, name);
        made.push(join(parentKey || "", name));
      }
      return made;
    },
    // 휴지통 = 연결만 끊기 (PC 의 파일은 그대로). 되돌리기 = 다시 연결
    async trash(keys) {
      keys.forEach((k) => hidden.add(k));
      await saveHidden();
      unhideBtn.hidden = !hidden.size;
      return keys;
    },
    async untrash(keys) {
      keys.forEach((k) => hidden.delete(k));
      await saveHidden();
      unhideBtn.hidden = !hidden.size;
      return keys;
    },
    // 열린 노트가 연결 끊김 → 빈 화면, 옮겨짐 → 새 경로로 다시 열기
    activeAffected(effect, was, before, moved) {
      if (effect === "gone") {
        leave();
        location.hash = "#/";
        return showEmpty();
      }
      const fresh = was === before ? moved : moved + was.slice(before.length);
      history.replaceState(null, "", hashFor(fresh, "note"));
      return show(true);
    },
    emptyMenu: () => [
      { label: "연결 끊은 항목 다시 보기", icon: "undo", disabled: !hidden.size, action: unhideAll },
      { label: "다른 폴더 열기", icon: "folder", action: openPicker },
      { label: "이 폴더 연결 끊기", icon: "x", disabled: !root, action: closeRoot },
    ],
  };
  const explorer = (window.explorer = window.createExplorer(explorerRoot, localDriver));
  const refreshTree = () => explorer.refresh().then(() => explorer.syncActive({ reveal: false }));

  // ---------- 마크다운 → 화면 (서버로 보내지 않고 브라우저에서) ----------
  const SECRET_BOX_RE = /^[ ]{0,3}:::[ \t]*비공개[ \t]*$[\s\S]*?(?:^[ ]{0,3}:::[ \t]*$|(?![\s\S]))/gm;
  const SECRET_INLINE_RE = /\{\{[\s\S]*?(?:\}\}|(?=\n[ \t]*\n)|(?![\s\S]))/g;
  const SECRET_TITLE = "비공개 — 눌러서 보기 (AI 에게는 보내지 않음)";
  const FENCE_RE = /^\s{0,3}(```|~~~)/;
  const BOX_OPEN_RE = /^\s{0,3}:::\s*비공개\s*$/;
  const BOX_CLOSE_RE = /^\s{0,3}:::\s*$/;

  // 빈 줄 기준으로 블록 나누기 (코드블록·비공개 상자 안의 빈 줄은 나누지 않음) — 서버 split_markdown 과 같은 규칙
  function splitMarkdown(text) {
    const chunks = [];
    let current = [];
    let inFence = false;
    let inSecret = false;
    for (const line of String(text || "").replace(/\r\n/g, "\n").split("\n")) {
      if (inSecret) {
        inSecret = !BOX_CLOSE_RE.test(line);
        current.push(line.trimEnd());
        continue;
      }
      if (FENCE_RE.test(line)) inFence = !inFence;
      else if (!inFence && BOX_OPEN_RE.test(line)) inSecret = true;
      if (!inFence && !line.trim()) {
        if (current.length) chunks.push(current.join("\n"));
        current = [];
        continue;
      }
      current.push(line.trimEnd());
    }
    if (current.length) chunks.push(current.join("\n"));
    return chunks;
  }
  const joinMarkdown = (contents) => contents.filter((c) => c.trim()).join("\n\n");

  const clean = (html) => window.DOMPurify.sanitize(html, { ADD_ATTR: ["target"] });
  const imageUrls = new Map(); // 경로 → blob: 주소

  async function imageUrl(path) {
    if (imageUrls.has(path)) return imageUrls.get(path);
    let dir = root;
    const parts = path.split("/");
    for (const part of parts.slice(0, -1)) dir = await dir.getDirectoryHandle(part);
    const file = await (await dir.getFileHandle(parts.at(-1))).getFile();
    const url = URL.createObjectURL(file);
    imageUrls.set(path, url);
    return url;
  }

  async function renderMarkdown(content, notePath) {
    if (!content.trim()) return "";
    const secrets = [];
    const stash = (html) => {
      secrets.push(html);
      return `MNSECRET${secrets.length - 1}X`;
    };
    let text = content.replace(SECRET_BOX_RE, (raw) => {
      const body = raw.split("\n").slice(1).filter((l, i, all) => !(i === all.length - 1 && BOX_CLOSE_RE.test(l))).join("\n");
      return `\n\n${stash(
        `<div class="secret secret-box" tabindex="0" title="${SECRET_TITLE}"><div class="secret-label">${icon("lock")}<span>비공개 · AI 에게 보내지 않음</span><span class="secret-hint">눌러서 보기</span></div>` +
          `<div class="secret-body">${clean(window.marked.parse(body, { gfm: true, breaks: true }))}</div></div>`
      )}\n\n`;
    });
    text = text.replace(SECRET_INLINE_RE, (raw) => {
      const body = raw.slice(2).replace(/\}\}$/, "");
      return stash(`<span class="secret" tabindex="0" title="${SECRET_TITLE}">${icon("lock")}<span class="secret-text">${clean(window.marked.parseInline(body))}</span></span>`);
    });
    const box = document.createElement("template");
    box.innerHTML = clean(window.marked.parse(text, { gfm: true, breaks: true }));
    const doc = box.content;
    // 비공개 자리표시 → 흐린 표시 (안의 HTML 은 위에서 이미 정화)
    const walker = document.createTreeWalker(doc, NodeFilter.SHOW_TEXT);
    const holders = [];
    while (walker.nextNode()) if (/MNSECRET\d+X/.test(walker.currentNode.nodeValue)) holders.push(walker.currentNode);
    holders.forEach((node) => {
      const span = document.createElement("span");
      span.innerHTML = node.nodeValue.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]).replace(/MNSECRET(\d+)X/g, (_, i) => secrets[Number(i)]);
      const parent = node.parentNode;
      if (parent.nodeName === "P" && parent.childNodes.length === 1 && span.querySelector(".secret-box")) parent.replaceWith(...span.childNodes);
      else node.replaceWith(...span.childNodes);
    });
    // 체크박스 (서버 표시와 같은 class)
    doc.querySelectorAll('li > input[type="checkbox"], li > p > input[type="checkbox"]').forEach((cb) => {
      cb.removeAttribute("disabled");
      cb.className = "task-check";
      cb.closest("li").classList.add("task");
    });
    // 노트끼리 상대 링크 → 그 노트 화면, 그림 → PC 파일
    const base = dirname(notePath);
    doc.querySelectorAll("a[href]").forEach((a) => {
      const href = a.getAttribute("href");
      if (/^[a-z]+:|^#|^\//i.test(href)) return;
      const target = resolvePath(base, decodeURIComponent(href.split("#")[0]));
      const entry = entries.get(target) || entries.get(target.replace(/\/$/, ""));
      a.setAttribute("href", entry ? hashFor(target.replace(/\/$/, ""), entry.kind) : href);
      if (!entry) {
        a.classList.add("broken-link");
        a.title = "없는 노트";
      }
    });
    for (const img of doc.querySelectorAll("img[src]")) {
      const src = img.getAttribute("src");
      if (/^[a-z]+:|^\//i.test(src)) continue;
      try {
        img.src = await imageUrl(resolvePath(base, decodeURIComponent(src)));
      } catch {
        img.alt = `그림을 찾을 수 없음: ${src}`;
      }
    }
    const out = document.createElement("div");
    out.appendChild(doc);
    return out.innerHTML;
  }

  // ---------- 열린 노트 + 편집기가 부르는 요청 (window.pageApi) ----------

  const notFound = (message, status = 400) => Object.assign(new Error(message), { status });
  const blockDict = async (note, b) => ({ id: b.id, content: b.content, html: await renderMarkdown(b.content, note.path) });
  const pageDict = (note) => {
    const title = titleOf(note.path);
    return { id: note.pageId, title, display_title: title || UNTITLED, status: "active", kind: "note", local: true };
  };

  function saveNote(note) {
    const markdown = joinMarkdown(note.blocks.map((b) => b.content));
    writeQueue = writeQueue.then(async () => {
      const w = await note.handle.createWritable();
      await w.write(markdown ? `${markdown}\n` : "");
      await w.close();
    });
    return writeQueue;
  }

  async function renameNote(note, title) {
    const name = `${safeName(title)}.md`;
    if (name === basename(note.path)) return;
    try {
      await note.dir.getFileHandle(name);
      throw notFound("같은 폴더에 같은 이름의 노트가 이미 있습니다.", 409);
    } catch (err) {
      if (err.status === 409) throw err;
    }
    await writeQueue;
    if (typeof note.handle.move === "function") {
      await note.handle.move(name);
    } else {
      // move() 가 없는 브라우저: 새 이름으로 같은 내용을 쓰고 옛 파일 정리 (이름 바꾸기)
      const fresh = await note.dir.getFileHandle(name, { create: true });
      const w = await fresh.createWritable();
      await w.write(await (await note.handle.getFile()).text());
      await w.close();
      await note.dir.removeEntry(basename(note.path));
      note.handle = fresh;
    }
    const oldPath = note.path;
    note.path = join(dirname(oldPath), name);
    if (hidden.delete(oldPath)) saveHidden();
    await scan();
    note.handle = entries.get(note.path)?.handle || note.handle;
    history.replaceState(null, "", hashFor(note.path, "note"));
    await refreshTree();
    view.querySelectorAll("[data-local-path]").forEach((n) => (n.textContent = note.path));
    renderBreadcrumbs(note.path);
  }

  async function saveImage(note, file) {
    const images = await note.dir.getDirectoryHandle("images", { create: true });
    const dot = file.name.lastIndexOf(".");
    const stem = safeName(dot > 0 ? file.name.slice(0, dot) : file.name);
    const ext = dot > 0 ? file.name.slice(dot) : "";
    let name = `${stem}${ext}`;
    for (let n = 2; ; n++) {
      try {
        await images.getFileHandle(name);
        name = `${stem}-${n}${ext}`;
      } catch {
        break;
      }
    }
    const handle = await images.getFileHandle(name, { create: true });
    const w = await handle.createWritable();
    await w.write(file);
    await w.close();
    return { name: stem, url: `images/${encodeURIComponent(name)}` };
  }

  window.localPageApi = async (method, url, body) => {
    const note = current;
    if (!note) throw notFound("열린 노트가 없습니다.");
    const path = url.split("?")[0];
    const find = (id) => {
      const index = note.blocks.findIndex((b) => b.id === Number(id));
      if (index < 0) throw notFound("블록을 찾을 수 없습니다. 다시 열어 주세요.", 404);
      return index;
    };
    let m;
    if (method === "GET" && /\/blocks$/.test(path)) {
      return { page: pageDict(note), blocks: await Promise.all(note.blocks.map((b) => blockDict(note, b))) };
    }
    if (method === "GET" && /\/source$/.test(path)) return { markdown: joinMarkdown(note.blocks.map((b) => b.content)) };
    if (method === "PUT" && /\/source$/.test(path)) {
      note.blocks = splitMarkdown(body.markdown).map((content) => ({ id: nextId++, content }));
      await saveNote(note);
      return { ok: true };
    }
    if (method === "POST" && /\/blocks\/reorder$/.test(path)) {
      const order = body.order.map(Number);
      note.blocks.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
      await saveNote(note);
      return { ok: true };
    }
    if (method === "POST" && /^\/api\/pages\/\d+\/blocks$/.test(path)) {
      const block = { id: nextId++, content: String(body.content || "") };
      let index = note.blocks.length;
      if (body.after_id != null) index = find(body.after_id) + 1;
      else if (body.before_id != null) index = find(body.before_id);
      note.blocks.splice(index, 0, block);
      await saveNote(note);
      return { block: await blockDict(note, block) };
    }
    if ((m = path.match(/^\/api\/blocks\/(\d+)$/))) {
      const index = find(m[1]);
      if (method === "DELETE") {
        note.blocks.splice(index, 1);
        await saveNote(note);
        return { ok: true };
      }
      const content = String(body.content || "");
      const parts = body.split === false ? [content] : splitMarkdown(content);
      const list = (parts.length ? parts : [""]).map((c, i) => (i === 0 ? Object.assign(note.blocks[index], { content: c }) : { id: nextId++, content: c }));
      note.blocks.splice(index, 1, ...list);
      await saveNote(note);
      return { blocks: await Promise.all(list.map((b) => blockDict(note, b))) };
    }
    if (method === "PATCH" && /^\/api\/pages\/\d+$/.test(path)) {
      if (body.title !== undefined) await renameNote(note, body.title);
      return { page: pageDict(note), path: note.path, abs_path: null, url: null };
    }
    if (method === "POST" && path === "/api/uploads") return saveImage(note, body.get("file"));
    if (/\/note-edits/.test(path)) return { edits: [] }; // AI 수정 강조는 서버 노트에만
    throw notFound("내 PC 노트에서는 쓸 수 없는 기능입니다.");
  };

  // ---------- 화면 ----------
  function route() {
    const raw = location.hash.replace(/^#\/?/, "");
    const folder = raw === "" || raw.endsWith("/");
    let path = "";
    try {
      path = raw.split("/").filter(Boolean).map(decodeURIComponent).join("/");
    } catch {
      path = "";
    }
    return { path, folder };
  }

  function renderBreadcrumbs(path) {
    const nav = view.querySelector(".breadcrumbs");
    if (!nav) return;
    nav.replaceChildren();
    const rootLink = document.createElement("a");
    rootLink.href = "#/";
    rootLink.innerHTML = `<span class="crumb-icon">${icon("monitor")}</span><span></span>`;
    rootLink.lastChild.textContent = root.name;
    nav.appendChild(rootLink);
    const parts = path ? path.split("/") : [];
    parts.forEach((part, i) => {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "/";
      nav.appendChild(sep);
      const sub = parts.slice(0, i + 1).join("/");
      const last = i === parts.length - 1;
      const el = document.createElement(last ? "span" : "a");
      el.className = last ? "current" : "";
      if (!last) el.href = hashFor(sub, "folder");
      el.textContent = last ? titleOf(part) : part;
      nav.appendChild(el);
    });
  }

  function leave() {
    window.leavePage?.();
    delete window.pageApi;
    document.querySelectorAll("script[data-local-script]").forEach((s) => s.remove());
    current = null;
  }

  async function show(force = false) {
    if (!root) return;
    try {
      await window.flushEditor?.();
    } catch (err) {
      console.error(err);
    }
    const { path, folder } = route();
    const entry = entries.get(path);
    if (path && (!entry || isHidden(path) || (entry.kind === "folder") !== folder)) {
      // 없거나 연결 끊은 항목 → 남아 있는 상위 폴더로
      let up = dirname(path);
      while (up && (!entries.has(up) || isHidden(up))) up = dirname(up);
      if (entry && !isHidden(path)) location.replace(hashFor(path, entry.kind));
      else {
        window.toast("찾는 노트가 없어 상위 폴더로 이동했습니다.");
        location.replace(hashFor(up, "folder"));
      }
      return;
    }
    if (folder) {
      // 폴더 화면은 없다 → 탐색기에서 펼쳐 보여 주고, 열린 노트는 그대로
      if (path) explorer.reveal(path);
      if (!current) showEmpty();
      return;
    }
    if (!force && current?.path === path) return; // 이름을 바꾼 뒤 주소만 바뀐 경우
    leave();
    await showNote(path, entry);
    explorer.syncActive();
  }

  function showEmpty() {
    view.replaceChildren(document.getElementById("local-empty-template").content.cloneNode(true));
    view.querySelector(".local-empty-root").textContent = root.name;
    document.title = `${root.name} · 내 PC · MarkNote`;
    explorer.syncActive();
  }

  async function showNote(path, entry) {
    const text = await (await entry.handle.getFile()).text();
    const note = { path, handle: entry.handle, dir: entry.dir, pageId: nextId++, blocks: splitMarkdown(text).map((content) => ({ id: nextId++, content })) };
    current = note;
    window.pageApi = window.localPageApi;
    view.replaceChildren(document.getElementById("local-page-template").content.cloneNode(true));
    view.dataset.folder = dirname(path);
    view.querySelector("#page-title").value = titleOf(path);
    view.querySelector("[data-local-path]").textContent = path;
    renderBreadcrumbs(path);
    const data = document.createElement("script");
    data.type = "application/json";
    data.id = "page-data";
    data.textContent = JSON.stringify({ page: pageDict(note), blocks: await Promise.all(note.blocks.map((b) => blockDict(note, b))) });
    view.appendChild(data);
    document.title = `${titleOf(path) || UNTITLED} · 내 PC · MarkNote`;
    const script = document.createElement("script");
    script.src = "/static/js/editor.js";
    script.dataset.localScript = "";
    document.body.appendChild(script);
  }

  // ---------- 연결 끊은 항목 다시 보기 · 폴더 연결 끊기 ----------
  async function unhideAll() {
    hidden.clear();
    await saveHidden();
    unhideBtn.hidden = true;
    await refreshTree();
    window.toast("연결을 끊었던 항목을 모두 다시 보여 줍니다.");
  }

  async function closeRoot() {
    if (!confirm(`“${root.name}” 폴더와의 연결을 끊을까요? PC 의 파일은 그대로 남습니다.`)) return;
    leave();
    await kvDel("root");
    root = null;
    location.hash = "";
    startPicker();
  }

  // ---------- AI 도우미 (chat.js): 물을 때만 지금 노트를 보내고, 고친 노트를 받아 PC 파일에 반영 ----------
  // 비공개 부분은 보내기 전에 ⟦🔒번호⟧ 로 바꾸고, 답에서 원래대로 되돌린다 (서버도 원래 내용을 모름)
  function redact(markdown) {
    const secrets = [];
    const hide = (raw) => {
      secrets.push(raw);
      return `⟦🔒${secrets.length - 1}⟧`;
    };
    return { text: markdown.replace(SECRET_BOX_RE, hide).replace(SECRET_INLINE_RE, hide), secrets };
  }
  let lastSecrets = [];
  window.localNoteForAI = () => {
    if (!current) return { title: "", markdown: "", path: "" };
    const { text, secrets } = redact(joinMarkdown(current.blocks.map((b) => b.content)));
    lastSecrets = secrets;
    return { title: titleOf(current.path), markdown: text, path: current.path };
  };
  // 답에 <marknote-note> … </marknote-note> 가 있으면 그 내용으로 노트를 바꾼다. 돌려줌: { text(보여 줄 답), undo() } 또는 null
  window.localApplyAi = async (answer) => {
    const m = answer.match(/<marknote-note>\n?([\s\S]*?)\n?<\/marknote-note>/);
    if (!m || !current) return null;
    const used = [...m[1].matchAll(/⟦🔒(\d+)⟧/g)].map((x) => Number(x[1]));
    const missing = lastSecrets.map((_, i) => i).filter((i) => !used.includes(i));
    if (missing.length || used.some((i) => i >= lastSecrets.length)) {
      throw new Error(`AI 답이 비공개 부분 ${missing.length || "일부"}개를 지우거나 바꿔서 노트에 반영하지 않았습니다.`);
    }
    const note = current;
    const before = joinMarkdown(note.blocks.map((b) => b.content));
    const after = m[1].replace(/⟦🔒(\d+)⟧/g, (_, i) => lastSecrets[Number(i)]);
    const put = async (markdown) => {
      note.blocks = splitMarkdown(markdown).map((content) => ({ id: nextId++, content }));
      await saveNote(note);
      if (current === note) await window.editorReload?.();
    };
    await put(after);
    const entry = { label: "AI 수정", undo: () => put(before), redo: () => put(after) };
    window.undoManager.push(entry);
    return { text: answer.replace(m[0], "").trim() || "노트를 고쳤습니다.", undo: () => put(before), redo: () => put(after) };
  };

  // ---------- 버튼 (만들기·옮기기·휴지통 등은 탐색기 명령) ----------
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-local-trash], [data-local-open], [data-local-reopen], [data-local-unhide], [data-local-close]");
    if (!btn) return;
    e.preventDefault();
    const run = async () => {
      if (btn.hasAttribute("data-local-trash")) await (current && explorer.commands.trash([current.path]));
      else if (btn.hasAttribute("data-local-open")) await openPicker();
      else if (btn.hasAttribute("data-local-reopen")) await reopen();
      else if (btn.hasAttribute("data-local-unhide")) await unhideAll();
      else if (btn.hasAttribute("data-local-close")) await closeRoot();
    };
    run().catch((err) => alert(err.message));
  });

  // ---------- 폴더 열기 ----------
  async function useRoot(handle) {
    root = handle;
    hidden = new Set((await kvGet(hiddenKey())) || []);
    current = null;
    view.replaceChildren();
    pick.hidden = true;
    closeBtn.hidden = false;
    unhideBtn.hidden = !hidden.size;
    explorerRoot.querySelector(".explorer-label").textContent = root.name;
    await explorer.refresh();
    await show();
  }

  async function openPicker() {
    let handle;
    try {
      handle = await window.showDirectoryPicker({ id: "marknote-local", mode: "readwrite" });
    } catch (err) {
      if (err.name === "AbortError") return; // 고르기 취소
      throw err;
    }
    await kvSet("root", handle);
    location.hash = "";
    await useRoot(handle);
  }

  let saved = null;
  async function reopen() {
    if ((await saved.requestPermission({ mode: "readwrite" })) === "granted") await useRoot(saved);
  }

  function startPicker() {
    view.replaceChildren();
    explorer.load([]);
    explorerRoot.querySelector(".explorer-label").textContent = "내 PC";
    closeBtn.hidden = true;
    unhideBtn.hidden = true;
    pick.hidden = false;
    const reopenBtn = pick.querySelector("[data-local-reopen]");
    reopenBtn.hidden = !saved;
    if (saved) reopenBtn.querySelector("span").textContent = `“${saved.name}” 다시 열기`;
  }

  async function start() {
    if (!("showDirectoryPicker" in window)) {
      pick.hidden = false;
      pick.querySelector("[data-local-open]").disabled = true;
      pick.querySelector("[data-local-unsupported]").hidden = false;
      return;
    }
    saved = (await kvGet("root")) || null;
    if (saved && (await saved.queryPermission({ mode: "readwrite" })) === "granted") return useRoot(saved);
    startPicker();
  }

  window.addEventListener("hashchange", () => show().catch((err) => alert(err.message)));
  // 다른 프로그램에서 파일을 바꿨을 수 있으니 창으로 돌아오면 목록을 다시 읽는다 (열린 노트는 그대로)
  window.addEventListener("focus", () => root && refreshTree().catch(console.error));
  start().catch((err) => alert(err.message));
})();
