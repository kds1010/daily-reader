/* Markdown is tokenized locally; untrusted content never enters innerHTML. */
const TanomiMarkdown = (() => {
  const cache = new Map();
  let cachedCharacters = 0;
  const MAX_CACHE_CHARACTERS = 500000;

  function decodeEntities(value) {
    // The HTML parser sees only a single character entity, never source markup.
    return value.replace(/&(?:#[0-9]+|#x[0-9a-f]+|[a-z][a-z0-9]+);/gi, (entity) =>
      new DOMParser().parseFromString(entity, "text/html").body.textContent);
  }

  function safeURL(value) {
    try {
      const url = new URL(decodeEntities(value));
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password
        ? url.href : null;
    } catch { return null; }
  }

  function element(tag, children = []) {
    const node = document.createElement(tag);
    node.append(...children);
    return node;
  }

  function renderTokens(tokens) {
    return tokens.flatMap((token) => {
      const inline = () => renderTokens(token.tokens || []);
      switch (token.type) {
        case "space": return [];
        case "heading": return [element(`h${Math.min(6, Math.max(1, token.depth))}`, inline())];
        case "paragraph": return [element("p", inline())];
        case "text": return token.tokens ? inline() : [document.createTextNode(decodeEntities(token.text))];
        case "escape": return [document.createTextNode(token.text)];
        case "strong": return [element("strong", inline())];
        case "em": return [element("em", inline())];
        case "del": return [element("del", inline())];
        case "codespan": return [element("code", [token.text])];
        case "code": return [element("pre", [element("code", [token.text])])];
        case "br": return [element("br")];
        case "hr": return [element("hr")];
        case "blockquote": return [element("blockquote", inline())];
        case "list": {
          const list = element(token.ordered ? "ol" : "ul");
          if (token.ordered && Number.isSafeInteger(token.start)) list.start = token.start;
          for (const item of token.items) {
            const row = element("li", renderTokens(item.tokens));
            if (item.task) row.prepend(document.createTextNode(item.checked ? "☑ " : "☐ "));
            list.append(row);
          }
          return [list];
        }
        case "link": {
          const url = safeURL(token.href);
          if (!url) return inline();
          const link = element("a", inline());
          link.href = url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          return [link];
        }
        case "image": return [document.createTextNode(decodeEntities(token.text) || "画像")];
        case "table": {
          const makeRow = (cells, tag) => element("tr", cells.map((cell, index) => {
            const node = element(tag, renderTokens(cell.tokens));
            if (tag === "th") node.scope = "col";
            if (["left", "center", "right"].includes(token.align[index])) {
              node.style.textAlign = token.align[index];
            }
            return node;
          }));
          const table = element("table", [
            element("thead", [makeRow(token.header, "th")]),
            element("tbody", token.rows.map((row) => makeRow(row, "td"))),
          ]);
          const scroll = element("div", [table]);
          scroll.className = "markdown-table-scroll";
          scroll.tabIndex = 0;
          scroll.setAttribute("role", "region");
          scroll.setAttribute("aria-label", "表（横スクロールできます）");
          return [scroll];
        }
        // HTML and any unsupported tokens are visible, inert text.
        default: return [document.createTextNode(token.raw || token.text || "")];
      }
    });
  }

  function render(source) {
    const text = String(source ?? "");
    const container = element("div");
    container.className = "tanomi-markdown";
    try {
      let tokens = cache.get(text);
      if (!tokens) {
        tokens = marked.lexer(text, { gfm: true, breaks: true });
        if (text.length <= MAX_CACHE_CHARACTERS) {
          while (cache.size >= 50 || cachedCharacters + text.length > MAX_CACHE_CHARACTERS) {
            const oldest = cache.keys().next().value;
            cachedCharacters -= oldest.length;
            cache.delete(oldest);
          }
          cache.set(text, tokens);
          cachedCharacters += text.length;
        }
      }
      container.append(...renderTokens(tokens));
    } catch {
      container.textContent = text;
      container.classList.add("markdown-plain");
    }
    return container;
  }

  return { render, safeURL };
})();

if (typeof module !== "undefined") module.exports = TanomiMarkdown;
