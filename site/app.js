function loadStoredSet(key) {
  try {
    const value = JSON.parse(localStorage.getItem(`daily-reader:${key}`) || "[]");
    return new Set(Array.isArray(value) ? value.filter((item) => typeof item === "string") : []);
  } catch {
    localStorage.removeItem(`daily-reader:${key}`);
    return new Set();
  }
}

const state = {
  articles: [],
  category: "すべて",
  query: "",
  sort: "newest",
  savedOnly: false,
  saved: loadStoredSet("saved"),
  read: loadStoredSet("read"),
  hidden: loadStoredSet("hidden"),
};

const elements = {
  articles: document.querySelector("#articles"),
  categories: document.querySelector("#categories"),
  digestItems: document.querySelector("#digest-items"),
  gadgetDigest: document.querySelector("#gadget-digest"),
  gadgetDigestItems: document.querySelector("#gadget-digest-items"),
  empty: document.querySelector("#empty"),
  highlightItems: document.querySelector("#highlight-items"),
  highlights: document.querySelector("#highlights"),
  highlightsHeading: document.querySelector("#highlights-heading"),
  highlightsOverview: document.querySelector("#highlights-overview"),
  officialDigest: document.querySelector("#official-digest"),
  refresh: document.querySelector("#refresh"),
  resultCount: document.querySelector("#result-count"),
  savedOnly: document.querySelector("#saved-only"),
  search: document.querySelector("#search"),
  sort: document.querySelector("#sort"),
  status: document.querySelector("#status"),
  template: document.querySelector("#article-template"),
  techPicks: document.querySelector("#tech-picks"),
  techPickItems: document.querySelector("#tech-pick-items"),
};

function recordRead(article, surface) {
  const body = JSON.stringify({ article_id: article.id, surface });
  if (navigator.sendBeacon) {
    navigator.sendBeacon("./api/read", new Blob([body], { type: "application/json" }));
    return;
  }
  fetch("./api/read", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    keepalive: true,
  }).catch(() => {});
}

function hideArticle(article, surface) {
  state.hidden.add(article.id);
  persist("hidden", state.hidden);
  document.querySelectorAll(`[data-article-id="${CSS.escape(article.id)}"]`).forEach((item) => {
    item.remove();
  });
  fetch("./api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ article_id: article.id, surface }),
  }).catch(() => {});
  if (state.articles.length) {
    renderArticles();
  }
}

function makeNotInterestedButton(article, surface) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "not-interested-button";
  button.textContent = "表示したくない";
  button.setAttribute("aria-label", `「${article.title}」を表示したくない`);
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    hideArticle(article, surface);
  });
  return button;
}

function wrapFeedbackItem(content, article, surface, className = "feedback-item") {
  const wrapper = document.createElement("div");
  wrapper.className = className;
  wrapper.dataset.articleId = article.id;
  const actions = document.createElement("div");
  actions.className = "feedback-actions";
  actions.append(makeNotInterestedButton(article, surface));
  wrapper.append(content, actions);
  return wrapper;
}

async function loadFeedback() {
  try {
    const response = await fetch("./api/feedback", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    state.hidden = new Set(payload.hidden_article_ids || []);
    persist("hidden", state.hidden);
  } catch {
    // Keep the device-local copy when the local server is temporarily unavailable.
  }
}

function trackLink(link, article, surface) {
  link.addEventListener("click", () => recordRead(article, surface));
}

function makeDigestLink(article, surface) {
  const link = document.createElement("a");
  link.href = article.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  trackLink(link, article, surface);
  const title = document.createElement("strong");
  title.textContent = article.title;
  const metadata = document.createElement("small");
  metadata.textContent = `${article.source}・${formatReleaseDate(article.published_at)}`;
  link.append(title, metadata);
  return wrapFeedbackItem(link, article, surface, "digest-link-item");
}

async function loadHighlights() {
  try {
    const response = await fetch("./data/highlights.json", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    elements.highlightsHeading.textContent = payload.headline;
    elements.highlightsOverview.textContent = payload.overview;
    const fragment = document.createDocumentFragment();
    for (const field of payload.field_highlights || []) {
      const group = document.createElement("section");
      group.className = "highlight-field";
      const fieldName = document.createElement("h3");
      fieldName.textContent = field.field;
      const fieldSummary = document.createElement("p");
      fieldSummary.textContent = field.summary;
      const items = document.createElement("div");
      items.className = "highlight-field-items";
      for (const [itemIndex, item] of field.items.entries()) {
        if (state.hidden.has(item.article.id)) {
          continue;
        }
        const link = document.createElement("a");
        link.className = "highlight-item";
        if (itemIndex === 0) {
          link.classList.add("highlight-item-featured");
        }
        link.href = item.article.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        trackLink(link, item.article, "field_highlight");
        const title = document.createElement("strong");
        title.textContent = item.label;
        const reason = document.createElement("span");
        reason.textContent = item.reason;
        if (item.article.image_url) {
          const image = document.createElement("img");
          image.className = "highlight-image";
          image.src = item.article.image_url;
          image.alt = item.article.title;
          image.loading = "lazy";
          image.decoding = "async";
          image.referrerPolicy = "no-referrer";
          image.addEventListener("error", () => image.remove());
          link.append(image);
        }
        const content = document.createElement("div");
        content.className = "highlight-item-content";
        content.append(title);
        if (
          field.field === "データマネジメント・エンジニアリング書籍" ||
          field.field === "生成AI活用・テクニック" ||
          field.field === "CLI・ターミナル生産性" ||
          field.field === "子育て" ||
          field.field === "横浜イベント" ||
          field.field === "街の新店"
        ) {
          const eventDate = document.createElement("time");
          eventDate.dateTime = item.article.published_at;
          let dateLabel = "開催日";
          if (field.field === "データマネジメント・エンジニアリング書籍") {
            dateLabel = "発売日";
          } else if (
            field.field === "生成AI活用・テクニック" ||
            field.field === "CLI・ターミナル生産性"
          ) {
            dateLabel = "更新日";
          } else if (field.field === "街の新店") {
            dateLabel = "公開日";
          }
          eventDate.textContent = `${dateLabel} ${formatReleaseDate(item.article.published_at)}`;
          content.append(eventDate);
        }
        content.append(reason);
        link.append(content);
        items.append(wrapFeedbackItem(link, item.article, "field_highlight", "highlight-item-wrapper"));
      }
      group.append(fieldName, fieldSummary, items);
      fragment.append(group);
    }
    elements.highlightItems.replaceChildren(fragment);
    elements.highlights.hidden = false;
    const digestFragment = document.createDocumentFragment();
    for (const digest of payload.official_digest || []) {
      const details = document.createElement("details");
      details.className = "digest-card";
      details.open = true;
      const summary = document.createElement("summary");
      const product = document.createElement("strong");
      product.textContent = digest.product;
      const description = document.createElement("span");
      description.textContent = digest.summary;
      summary.append(product, description);
      const changes = document.createElement("ul");
      for (const change of digest.changes) {
        const item = document.createElement("li");
        item.textContent = change;
        changes.append(item);
      }
      const links = document.createElement("div");
      links.className = "digest-links";
      digest.articles.forEach((article) => {
        if (!state.hidden.has(article.id)) {
          links.append(makeDigestLink(article, "official_digest"));
        }
      });
      details.append(summary, changes, links);
      digestFragment.append(details);
    }
    elements.digestItems.replaceChildren(digestFragment);
    elements.officialDigest.hidden = !payload.official_digest?.length;

    const gadgetFragment = document.createDocumentFragment();
    for (const digest of payload.gadget_digest || []) {
      const details = document.createElement("details");
      details.className = "digest-card";
      details.open = true;
      const summary = document.createElement("summary");
      const theme = document.createElement("strong");
      theme.textContent = digest.theme;
      const description = document.createElement("span");
      description.textContent = digest.summary;
      summary.append(theme, description);
      const benefits = document.createElement("ul");
      for (const benefit of digest.benefits) {
        const item = document.createElement("li");
        item.textContent = benefit;
        benefits.append(item);
      }
      const links = document.createElement("div");
      links.className = "digest-links";
      digest.articles.forEach((article) => {
        if (!state.hidden.has(article.id)) {
          links.append(makeDigestLink(article, "gadget_digest"));
        }
      });
      details.append(summary, benefits, links);
      gadgetFragment.append(details);
    }
    elements.gadgetDigestItems.replaceChildren(gadgetFragment);
    elements.gadgetDigest.hidden = !payload.gadget_digest?.length;

    const techFragment = document.createDocumentFragment();
    for (const pick of payload.tech_picks || []) {
      if (state.hidden.has(pick.article.id)) {
        continue;
      }
      const link = document.createElement("a");
      link.className = "tech-pick";
      link.href = pick.article.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      trackLink(link, pick.article, "tech_pick");
      const title = document.createElement("strong");
      title.textContent = pick.label;
      const insight = document.createElement("span");
      insight.textContent = pick.insight;
      const reason = document.createElement("small");
      reason.textContent = pick.why_read;
      link.append(title, insight, reason);
      techFragment.append(wrapFeedbackItem(link, pick.article, "tech_pick"));
    }
    elements.techPickItems.replaceChildren(techFragment);
    elements.techPicks.hidden = !payload.tech_picks?.length;
  } catch {
    elements.highlights.hidden = true;
  }
}

function persist(key, values) {
  try {
    localStorage.setItem(`daily-reader:${key}`, JSON.stringify([...values]));
  } catch {
    // Rendering should continue even when Safari storage is unavailable or full.
  }
}

function formatDate(value) {
  const date = new Date(value);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return new Intl.DateTimeFormat("ja-JP", { hour: "2-digit", minute: "2-digit" }).format(date);
  }
  return new Intl.DateTimeFormat("ja-JP", { month: "numeric", day: "numeric" }).format(date);
}

function formatReleaseDate(value) {
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).format(new Date(value));
}

function filteredArticles() {
  const query = state.query.toLocaleLowerCase("ja");
  return state.articles
    .filter((article) => state.category === "すべて" || article.category === state.category)
    .filter((article) => !state.hidden.has(article.id))
    .filter((article) => !state.savedOnly || state.saved.has(article.id))
    .filter((article) => {
      const searchable = `${article.title} ${article.summary} ${article.source}`.toLocaleLowerCase("ja");
      return !query || searchable.includes(query);
    })
    .sort((left, right) => {
      if (state.sort === "score" && right.score !== left.score) {
        return right.score - left.score;
      }
      return new Date(right.published_at) - new Date(left.published_at);
    });
}

function renderCategories() {
  const categories = ["すべて", ...new Set(state.articles.map((article) => article.category))];
  elements.categories.replaceChildren();
  for (const category of categories) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `chip${category === state.category ? " active" : ""}`;
    button.textContent = category;
    button.addEventListener("click", () => {
      state.category = category;
      renderCategories();
      renderArticles();
    });
    elements.categories.append(button);
  }
}

function renderArticles() {
  const articles = filteredArticles();
  const fragment = document.createDocumentFragment();
  for (const article of articles) {
    const card = elements.template.content.firstElementChild.cloneNode(true);
    card.dataset.articleId = article.id;
    card.classList.toggle("read", state.read.has(article.id));
    card.querySelector(".category").textContent = article.category;
    card.querySelector(".source").textContent = article.source;
    const time = card.querySelector("time");
    time.dateTime = article.published_at;
    time.textContent = formatDate(article.published_at);
    const link = card.querySelector(".article-link");
    link.href = article.url;
    link.querySelector("h3").textContent = article.title;
    link.querySelector(".summary").textContent = article.summary;
    link.addEventListener("click", () => {
      recordRead(article, "article_feed");
      state.read.add(article.id);
      persist("read", state.read);
      card.classList.add("read");
    });
    card.querySelector(".score").textContent = article.score > 0 ? String(article.score) : "";
    const saveButton = card.querySelector(".save-button");
    const isSaved = state.saved.has(article.id);
    saveButton.classList.toggle("saved", isSaved);
    saveButton.textContent = isSaved ? "保存済み" : "あとで読む";
    saveButton.addEventListener("click", () => {
      if (state.saved.has(article.id)) {
        state.saved.delete(article.id);
      } else {
        state.saved.add(article.id);
      }
      persist("saved", state.saved);
      renderArticles();
    });
    card
      .querySelector(".not-interested-button")
      .addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        hideArticle(article, "article_feed");
      });
    fragment.append(card);
  }
  elements.articles.replaceChildren(fragment);
  elements.resultCount.textContent = `${articles.length}件`;
  elements.empty.hidden = articles.length > 0;
}

async function loadArticles() {
  elements.status.textContent = "ニュースを読み込んでいます…";
  elements.refresh.disabled = true;
  try {
    const response = await fetch("./data/articles.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    state.articles = payload.articles;
    const generatedAt = new Intl.DateTimeFormat("ja-JP", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(payload.generated_at));
    const errorNote = payload.errors.length ? `・取得失敗 ${payload.errors.length}件` : "";
    elements.status.textContent = `${generatedAt} 更新${errorNote}`;
    renderCategories();
    renderArticles();
  } catch (error) {
    elements.status.textContent = `読み込みに失敗しました：${error.message}`;
  } finally {
    elements.refresh.disabled = false;
  }
}

elements.search.addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  renderArticles();
});
elements.sort.addEventListener("change", (event) => {
  state.sort = event.target.value;
  renderArticles();
});
elements.savedOnly.addEventListener("change", (event) => {
  state.savedOnly = event.target.checked;
  renderArticles();
});
elements.refresh.addEventListener("click", loadArticles);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("./sw.js"));
}

loadArticles();
loadHighlights();
loadFeedback().then(() => {
  document.querySelectorAll("[data-article-id]").forEach((item) => {
    if (state.hidden.has(item.dataset.articleId)) {
      item.remove();
    }
  });
  if (state.articles.length) {
    renderArticles();
  }
});
