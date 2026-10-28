// 프레젠테이션 목록: 첫 슬라이드 축소판, 카드 우클릭 메뉴
(() => {
  const cards = JSON.parse(document.getElementById("deck-cards").textContent);
  const byId = new Map(cards.map((c) => [c.id, c]));

  document.querySelectorAll(".deck-card").forEach((card) => {
    const info = byId.get(Number(card.dataset.deckId));
    const holder = card.querySelector(".deck-card-thumb");
    if (info?.first) holder.appendChild(DeckRender.renderThumb(info.first, holder.clientWidth || 240));
  });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-deck-show]");
    if (btn) location.href = `/d/${btn.dataset.deckShow}/show`;
  });

  const clickIn = (card, action) => () => card.querySelector(`[data-action="${action}"]`)?.click();

  window.contextMenuProviders?.push((e) => {
    const card = e.target.closest(".deck-card");
    if (!card) return null;
    const id = Number(card.dataset.deckId);
    const status = card.dataset.status;
    const items = [
      { label: "열기", icon: "open", action: () => (location.href = `/d/${id}`) },
      { label: "새 탭에서 열기", icon: "external", action: () => window.open(`/d/${id}`, "_blank") },
      { label: "슬라이드 쇼", icon: "play", hint: "F5", action: () => (location.href = `/d/${id}/show`) },
    ];
    if (status === "active") {
      items.push(
        "sep",
        {
          label: "이름 바꾸기",
          icon: "type",
          action: async () => {
            const current = byId.get(id)?.title || "";
            const title = prompt("프레젠테이션 이름", current);
            if (title === null) return;
            await api("PATCH", `/api/decks/${id}`, { title });
            location.reload();
          },
        },
        "sep",
        { label: "보관", icon: "archive", action: clickIn(card, "deck-archive") },
        { label: "휴지통으로 이동", icon: "trash", danger: true, action: clickIn(card, "deck-trash") }
      );
    } else {
      items.push("sep", { label: "복원", icon: "undo", action: clickIn(card, "deck-restore") });
      if (status === "trashed") items.push({ label: "영구 삭제", icon: "file-x", danger: true, action: clickIn(card, "deck-delete") });
      else items.push({ label: "휴지통으로 이동", icon: "trash", danger: true, action: clickIn(card, "deck-trash") });
    }
    return items;
  });
})();
