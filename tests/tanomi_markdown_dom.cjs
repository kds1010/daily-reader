const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

// Minimal DOM contract: no HTML injection sink is available to the renderer.
class Node {
  constructor(tag = "#text", text = "") {
    this.tagName = tag;
    this.children = [];
    this.value = text;
    this.style = {};
    this.attributes = {};
    this.classList = { add: (name) => { this.className = `${this.className || ""} ${name}`; } };
  }
  append(...nodes) { this.children.push(...nodes.map((n) => typeof n === "string" ? new Node("#text", n) : n)); }
  prepend(node) { this.children.unshift(node); }
  set textContent(text) { this.children = []; this.value = text; }
  get textContent() { return this.value + this.children.map((n) => n.textContent).join(""); }
  set innerHTML(_) { throw Error("untrusted HTML sink"); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener() {}
}
global.document = { createElement: (tag) => new Node(tag), createTextNode: (text) => new Node("#text", text) };
global.DOMParser = class {
  parseFromString(entity) {
    assert.match(entity, /^&(?:#[0-9]+|#x[0-9a-f]+|[a-z][a-z0-9]+);$/i);
    const named = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&colon;": ":" };
    const value = entity.startsWith("&#x") ? String.fromCodePoint(parseInt(entity.slice(3), 16))
      : entity.startsWith("&#") ? String.fromCodePoint(parseInt(entity.slice(2), 10)) : named[entity] || entity;
    return { body: { textContent: value } };
  }
};
global.marked = require("../site/vendor/marked-15.0.12.js");
const markdown = require("../site/markdown.js");
function all(node, tag) { return [ ...(node.tagName === tag ? [node] : []), ...node.children.flatMap((n) => all(n, tag)) ]; }
function visibleText(node) { return node.tagName === "br" ? "\n" : node.value + node.children.map(visibleText).join(""); }

const source = "# 日本語\n\n本文 **太字**と*斜体*、`a < b &amp; c`。\n次の行。\n\n3. 親\n   - 子\n4. 次\n\n> 引用\n\n```js\n<script>literal</script>\n```\n\n| 左 | 右 |\n|:---|---:|\n| A | |\n\n[参照](https://example.com/?a=1&amp;b=2) &amp; &#65;\\*literal\\*";
const rendered = markdown.render(source);
assert.equal(all(rendered, "h1")[0].textContent, "日本語");
assert.equal(all(rendered, "strong")[0].textContent, "太字");
assert.equal(all(rendered, "em")[0].textContent, "斜体");
assert.equal(all(rendered, "ol")[0].start, 3);
assert.equal(all(all(rendered, "ol")[0], "ul").length, 1);
assert.equal(all(rendered, "blockquote")[0].textContent, "引用");
assert.equal(all(rendered, "pre")[0].textContent, "<script>literal</script>");
assert.equal(all(rendered, "td").length, 2);
assert.equal(all(rendered, "td")[1].textContent, "");
assert.equal(all(rendered, "td")[1].style.textAlign, "right");
assert.equal(all(rendered, "a")[0].href, "https://example.com/?a=1&b=2");
assert.ok(rendered.textContent.includes("& A*literal*"));
assert.ok(all(rendered, "code")[0].textContent.includes("&amp;"));
assert.ok(rendered.textContent.includes("次の行"));

const hostile = markdown.render('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n![代替](https://example.com/a.png) ![](https://example.com/b.png) [危険](javascript:alert(1)) [危険2](javascript&colon;alert(1)) [data](data:text/html,x) [相対](/api/tanomi/tasks)');
for (const tag of ["script", "img", "iframe", "a"]) assert.equal(all(hostile, tag).length, 0);
assert.ok(hostile.textContent.includes("<script>alert(1)</script>"));
assert.ok(hostile.textContent.includes("代替 画像"));
for (const url of ["javascript:alert(1)", "data:text/html,x", "file:///tmp/a", "//example.com", "/api/tasks", "https://user:pass@example.com"]) assert.equal(markdown.safeURL(url), null);
assert.equal(markdown.safeURL("HTTPS://example.com/path"), "https://example.com/path");
assert.equal(markdown.render("**未完 [途中](").textContent, "**未完 [途中](");
assert.equal(all(markdown.render("```\n未完コード\n次行"), "pre")[0].textContent, "未完コード\n次行");
assert.equal(markdown.render("").textContent, "");
const shortLines = Array.from({ length: 12 }, (_, i) => `行${i}`).join("\n");
assert.equal(visibleText(markdown.render(shortLines)), shortLines);
const long = "本文".repeat(10000);
assert.equal(markdown.render(long).textContent, long);
const first = markdown.render(source);
all(first, "h1")[0].textContent = "changed";
assert.equal(all(markdown.render(source), "h1")[0].textContent, "日本語");
const originalMarked = global.marked;
global.marked = { lexer: () => { throw Error("parse failure"); } };
assert.equal(markdown.render("fallback **原文**").textContent, "fallback **原文**");
global.marked = originalMarked;

// Exercise the actual card integration, including error fallback and follow-up form.
const app = fs.readFileSync(path.join(__dirname, "../site/app.js"), "utf8");
const cardCode = app.slice(app.indexOf("function renderTanomiJob("), app.indexOf("async function tanomiResponseError("));
const context = { document, TanomiMarkdown: markdown, openTanomiJobs: new Set(["task"]), Set };
vm.createContext(context);
vm.runInContext(cardCode, context);
const card = context.renderTanomiJob({ id: "task", status: "done", prompt: "**依頼**", result: "# 結果", session_id: "session" });
assert.equal(card.open, true);
assert.equal(all(card, "h1")[0].textContent, "結果");
assert.equal(all(card, "form").length, 1);
assert.equal(all(card, "textarea")[0].maxLength, 100000);
const error = context.renderTanomiJob({ id: "error", status: "error", error: "**raw**\n<error>" });
assert.ok(error.textContent.includes("**raw**\n<error>"));

async function checkPolling() {
  let tasks = [{ id: "poll", status: "done", result: "```\nlong code\n```" }];
  let replacements = 0;
  const jobs = new Node("div");
  jobs.contains = () => false;
  jobs.replaceChildren = (...nodes) => { replacements++; jobs.children = nodes; };
  const polling = {
    ...context,
    renderedTanomiTasks: null,
    window: { getSelection: () => ({ isCollapsed: true }) },
    elements: {
      tanomiRepository: { replaceChildren() {} }, tanomiJobs: jobs,
      tanomiStatus: new Node(), tanomiHealth: new Node(),
      tanomiForm: { dataset: {}, querySelectorAll: () => [] },
    },
    fetchWithTimeout: async (url) => ({
      ok: !url.endsWith("/config"),
      json: async () => url.includes("/tasks?") ? { tasks }
        : url.endsWith("/health") ? { ok: true } : [],
    }),
  };
  vm.createContext(polling);
  vm.runInContext(app.slice(app.indexOf("function renderTanomiJob("), app.indexOf("function updateTanomiOptionsSummary(")), polling);
  await polling.loadTanomiTasks();
  assert.equal(polling.elements.tanomiHealth.textContent, "接続中");
  all(jobs, "pre")[0].scrollLeft = 50;
  await polling.loadTanomiTasks();
  assert.equal(replacements, 1);
  assert.equal(all(jobs, "pre")[0].scrollLeft, 50);
  tasks = [{ ...tasks[0], result: "# updated" }];
  await polling.loadTanomiTasks();
  assert.equal(replacements, 2);
  assert.equal(all(jobs, "h1")[0].textContent, "updated");
  console.log("tanomi Markdown DOM and card checks passed");
}
checkPolling().catch((error) => { console.error(error); process.exitCode = 1; });
