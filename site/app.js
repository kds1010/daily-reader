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
  newsStatus: "ニュースを読み込んでいます…",
  emailStatus: "メールを読み込んでいます…",
  todayStatus: "今日の予定を読み込んでいます…",
  agentStatus: "Agentタスクを読み込んでいます…",
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
  pullToRefresh: document.querySelector("#pull-to-refresh"),
  refresh: document.querySelector("#refresh"),
  resultCount: document.querySelector("#result-count"),
  savedOnly: document.querySelector("#saved-only"),
  search: document.querySelector("#search"),
  sort: document.querySelector("#sort"),
  status: document.querySelector("#status"),
  template: document.querySelector("#article-template"),
  techPicks: document.querySelector("#tech-picks"),
  techPickItems: document.querySelector("#tech-pick-items"),
  emailAssistant: document.querySelector("#email-assistant"),
  emailEmpty: document.querySelector("#email-empty"),
  emailItems: document.querySelector("#email-items"),
  emailSyncStatus: document.querySelector("#email-sync-status"),
  emailCount: document.querySelector("#email-count"),
  emailView: document.querySelector("#email-view"),
  newsView: document.querySelector("#news-view"),
  todayView: document.querySelector("#today-view"),
  todayCount: document.querySelector("#today-count"),
  todaySummary: document.querySelector("#today-summary"),
  taskItems: document.querySelector("#task-items"),
  taskProgress: document.querySelector("#task-progress"),
  tasksEmpty: document.querySelector("#tasks-empty"),
  routineItems: document.querySelector("#routine-items"),
  routineProgress: document.querySelector("#routine-progress"),
  routinesEmpty: document.querySelector("#routines-empty"),
  taskForm: document.querySelector("#task-form"),
  healthForm: document.querySelector("#health-form"),
  healthMetrics: document.querySelector("#health-metrics"),
  healthSynced: document.querySelector("#health-synced"),
  todayEmailItems: document.querySelector("#today-email-items"),
  todayEmailEmpty: document.querySelector("#today-email-empty"),
  agentView: document.querySelector("#agent-view"),
  agentCount: document.querySelector("#agent-count"),
  agentForm: document.querySelector("#agent-form"),
  agentRepository: document.querySelector("#agent-repository"),
  agentModel: document.querySelector("#agent-model"),
  agentReasoningEffort: document.querySelector("#agent-reasoning-effort"),
  agentModelSummary: document.querySelector("#agent-model-summary"),
  agentJobs: document.querySelector("#agent-jobs"),
  agentJobsSummary: document.querySelector("#agent-jobs-summary"),
  agentEmpty: document.querySelector("#agent-empty"),
  agentArchive: document.querySelector("#agent-archive"),
  agentArchiveCount: document.querySelector("#agent-archive-count"),
  agentArchivedJobs: document.querySelector("#agent-archived-jobs"),
  tanomiForm: document.querySelector("#tanomi-form"),
  tanomiPrompt: document.querySelector("#tanomi-prompt"),
  tanomiRepository: document.querySelector("#tanomi-repository"),
  tanomiModel: document.querySelector("#tanomi-model"),
  tanomiPermission: document.querySelector("#tanomi-permission"),
  tanomiJobs: document.querySelector("#tanomi-jobs"),
  tanomiStatus: document.querySelector("#tanomi-status"),
  tanomiHealth: document.querySelector("#tanomi-health"),
  codexUsageLimits: document.querySelector("#codex-usage-limits"),
  codexUsagePlan: document.querySelector("#codex-usage-plan"),
  codexUsageStatus: document.querySelector("#codex-usage-status"),
  deploymentVersion: document.querySelector("#deployment-version"),
  deploymentDate: document.querySelector("#deployment-date"),
  screenUpdatedAt: document.querySelector("#screen-updated-at"),
};

let currentView = "agent";
const screenRefreshAgingMs = 5 * 60 * 1000;
const screenRefreshStaleMs = 10 * 60 * 1000;
let screenFreshnessTimer;

function screenRefreshFreshness(updatedAt, now = new Date()) {
  const elapsed = Math.max(0, now.getTime() - updatedAt.getTime());
  if (elapsed < screenRefreshAgingMs) return "fresh";
  if (elapsed < screenRefreshStaleMs) return "aging";
  return "stale";
}

function scheduleScreenFreshnessUpdate(updatedAt, now) {
  window.clearTimeout(screenFreshnessTimer);
  const elapsed = Math.max(0, now.getTime() - updatedAt.getTime());
  const nextBoundary = elapsed < screenRefreshAgingMs
    ? screenRefreshAgingMs
    : elapsed < screenRefreshStaleMs ? screenRefreshStaleMs : null;
  if (nextBoundary === null) return;
  screenFreshnessTimer = window.setTimeout(() => updateScreenFreshness(), Math.max(1, nextBoundary - elapsed));
}

function updateScreenFreshness(now = new Date()) {
  const updatedAt = new Date(elements.screenUpdatedAt.dateTime);
  if (Number.isNaN(updatedAt.getTime())) return;
  const freshness = screenRefreshFreshness(updatedAt, now);
  const labels = {
    fresh: "更新から5分未満",
    aging: "更新から5分以上",
    stale: "更新から10分以上",
  };
  elements.screenUpdatedAt.dataset.freshness = freshness;
  elements.screenUpdatedAt.setAttribute("aria-label", `${elements.screenUpdatedAt.textContent}（${labels[freshness]}）`);
  scheduleScreenFreshnessUpdate(updatedAt, now);
}

function updateScreenUpdatedAt(date = new Date()) {
  const formatted = new Intl.DateTimeFormat("ja-JP", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
  elements.screenUpdatedAt.dateTime = date.toISOString();
  elements.screenUpdatedAt.textContent = `画面更新 ${formatted}`;
  updateScreenFreshness(date);
}

function switchView(view) {
  currentView = view;
  elements.agentView.hidden = view !== "agent";
  elements.newsView.hidden = view !== "news";
  elements.emailView.hidden = view !== "email";
  elements.todayView.hidden = view !== "today";
  document.querySelectorAll("[data-app-view]").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.appView === view));
  });
  const labels = {
    agent: "Agentタスクを再読み込み",
    today: "今日を再読み込み",
    email: "メールを再読み込み",
    news: "ニュースを再読み込み",
  };
  const statuses = {
    agent: state.agentStatus,
    today: state.todayStatus,
    email: state.emailStatus,
    news: state.newsStatus,
  };
  elements.refresh.setAttribute("aria-label", labels[view]);
  elements.status.textContent = statuses[view];
}

const agentStatusLabels = {
  queued: "待機中",
  running: "実行中",
  blocked: "判断待ち",
  completed: "完了",
  failed: "失敗",
  cancelled: "キャンセル済み",
};

const openAgentConversations = new Set();
const openAgentJobs = new Set();
const openTanomiJobs = new Set();

const agentStatusIcons = {
  queued: "◷",
  running: "●",
  blocked: "!",
  completed: "✓",
  failed: "×",
  cancelled: "−",
};

function agentEventSpeaker(kind) {
  if (kind === "user") return "あなた";
  if (kind === "codex") return "Agent";
  if (kind === "activity") return "アクティビティ";
  return "進捗";
}

function parseAgentResult(message) {
  if (typeof message !== "string" || !message.trimStart().startsWith("{")) return null;
  try {
    const result = JSON.parse(message);
    if (!result || typeof result !== "object" || Array.isArray(result)) return null;
    if (typeof result.summary !== "string" || typeof result.state !== "string") return null;
    return result;
  } catch {
    return null;
  }
}

function parseAgentResults(message) {
  const singleResult = parseAgentResult(message);
  if (singleResult) return [singleResult];
  if (typeof message !== "string") return null;
  const lines = message.split("\n").filter((line) => line.trim());
  if (lines.length < 2) return null;
  const results = lines.map(parseAgentResult);
  return results.every(Boolean) ? results : null;
}

function appendAgentResultField(container, labelText, value) {
  if (typeof value !== "string" || !value.trim()) return;
  const field = document.createElement("section");
  field.className = "agent-result-field";
  const label = document.createElement("strong");
  label.textContent = labelText;
  const content = document.createElement("p");
  content.textContent = value;
  field.append(label, content);
  container.append(field);
}

function renderAgentMessage(messageText, parseStructured = false) {
  const results = parseStructured ? parseAgentResults(messageText) : null;
  if (!results) {
    const message = document.createElement("p");
    message.className = "agent-event-message";
    message.textContent = messageText;
    return message;
  }

  const message = document.createElement("div");
  message.className = "agent-event-message agent-result";
  for (const structured of results) {
    const turn = document.createElement("section");
    turn.className = "agent-result-turn";
    const state = document.createElement("span");
    state.className = `agent-result-state state-${structured.state}`;
    state.textContent = {
      done: "完了",
      continue: "作業を継続",
      blocked: "判断待ち",
    }[structured.state] || structured.state;
    turn.append(state);
    appendAgentResultField(turn, "報告", structured.summary);
    appendAgentResultField(turn, "次に行うこと", structured.next_action);
    if (Array.isArray(structured.verification) && structured.verification.length) {
      const verification = document.createElement("section");
      verification.className = "agent-result-field";
      const label = document.createElement("strong");
      label.textContent = "確認結果";
      const list = document.createElement("ul");
      for (const entry of structured.verification) {
        if (typeof entry !== "string" || !entry.trim()) continue;
        const item = document.createElement("li");
        item.textContent = entry;
        list.append(item);
      }
      if (list.children.length) {
        verification.append(label, list);
        turn.append(verification);
      }
    }
    message.append(turn);
  }
  return message;
}

function renderAgentEvent(event) {
  const item = document.createElement("div");
  item.className = `agent-event event-${event.kind}`;
  const meta = document.createElement("p");
  meta.className = "agent-event-meta";
  meta.textContent = `${formatAgentTime(event.created_at)}・${agentEventSpeaker(event.kind)}`;
  const message = renderAgentMessage(event.message, event.kind === "codex");
  item.append(meta, message);
  return item;
}

function formatAgentTime(value) {
  return new Intl.DateTimeFormat("ja-JP", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function renderAgentJob(job, archived = false) {
  const swipeContainer = archived ? null : document.createElement("div");
  const swipeAction = archived ? null : document.createElement("span");
  if (swipeContainer && swipeAction) {
    swipeContainer.className = "agent-swipe-container";
    swipeAction.className = "agent-swipe-action";
    swipeAction.textContent = "アーカイブ";
    swipeAction.setAttribute("aria-hidden", "true");
  }
  const card = document.createElement("details");
  card.className = `agent-job status-${job.status}`;
  card.open = openAgentJobs.has(job.id);
  card.addEventListener("toggle", () => {
    if (card.open) openAgentJobs.add(job.id);
    else openAgentJobs.delete(job.id);
  });
  const cardSummary = document.createElement("summary");
  cardSummary.className = "agent-job-overview";
  const icon = document.createElement("span");
  icon.className = "agent-status-icon";
  icon.textContent = agentStatusIcons[job.status] || "•";
  icon.setAttribute("aria-hidden", "true");
  const overviewText = document.createElement("span");
  overviewText.className = "agent-overview-text";
  const heading = document.createElement("span");
  heading.className = "agent-job-heading";
  const status = document.createElement("span");
  status.className = "agent-status";
  status.textContent = agentStatusLabels[job.status] || job.status;
  const repository = document.createElement("span");
  const repositoryLabel = job.repository_label || job.repository;
  repository.textContent = job.mode === "requirements"
    ? `${repositoryLabel}・要件深掘り`
    : repositoryLabel;
  heading.append(status, repository);
  const title = document.createElement("span");
  title.className = "agent-job-title";
  title.textContent = job.prompt;
  const phase = document.createElement("span");
  phase.className = "agent-phase";
  phase.textContent = `${job.phase}・${formatAgentTime(job.updated_at)}`;
  overviewText.append(heading, title, phase);
  const chevron = document.createElement("span");
  chevron.className = "agent-job-chevron";
  chevron.setAttribute("aria-hidden", "true");
  cardSummary.append(icon, overviewText, chevron);
  const body = document.createElement("div");
  body.className = "agent-job-body";
  card.append(cardSummary, body);
  const progressHeading = document.createElement("p");
  progressHeading.className = "agent-section-label";
  progressHeading.textContent = "現在の進捗";
  body.append(progressHeading);
  if (job.summary) {
    const summaryLabel = document.createElement("strong");
    summaryLabel.className = "agent-summary-label";
    summaryLabel.textContent = job.status === "completed" || job.follow_up
      ? "完了サマリー"
      : "現在の報告";
    const summary = document.createElement("p");
    summary.className = "agent-summary";
    summary.textContent = job.summary;
    body.append(summaryLabel, summary);
  }
  if (["queued", "running", "blocked"].includes(job.status)) {
    const live = document.createElement("section");
    live.className = "agent-live";
    live.setAttribute("aria-label", "現在の進捗");
    const liveHeading = document.createElement("div");
    liveHeading.className = "agent-live-heading";
    const pulse = document.createElement("span");
    pulse.className = "agent-live-pulse";
    pulse.setAttribute("aria-hidden", "true");
    const liveTitle = document.createElement("strong");
    liveTitle.textContent = job.status === "blocked" ? "回答を待っています" : "進捗を自動更新中";
    liveHeading.append(pulse, liveTitle);
    const recentEvents = document.createElement("div");
    recentEvents.className = "agent-events agent-live-events";
    recentEvents.replaceChildren(...(job.recent_events || []).map(renderAgentEvent));
    live.append(liveHeading, recentEvents);
    body.append(live);
  }
  const conversation = document.createElement("details");
  conversation.className = "agent-conversation";
  conversation.open = openAgentConversations.has(job.id);
  const conversationSummary = document.createElement("summary");
  const updateConversationSummary = () => {
    conversationSummary.textContent = conversation.open
      ? "やりとりを非表示"
      : "やりとりを表示";
  };
  updateConversationSummary();
  conversationSummary.className = "agent-conversation-toggle";
  const eventList = document.createElement("div");
  eventList.className = "agent-events";
  eventList.addEventListener("click", () => {
    conversation.open = false;
  });
  conversation.addEventListener("toggle", async () => {
    updateConversationSummary();
    if (conversation.open) openAgentConversations.add(job.id);
    else openAgentConversations.delete(job.id);
    if (!conversation.open || eventList.dataset.loaded) return;
    eventList.textContent = "読み込んでいます…";
    try {
      const response = await fetchWithTimeout(`./api/agent-jobs/${encodeURIComponent(job.id)}`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      eventList.replaceChildren(...payload.events.map(renderAgentEvent));
      eventList.dataset.loaded = "true";
    } catch (error) {
      eventList.textContent = `やりとりを読み込めませんでした：${error.message}`;
    }
  });
  conversation.append(conversationSummary, eventList);
  const conversationLabel = document.createElement("span");
  conversationLabel.className = "agent-section-label agent-conversation-label";
  conversationLabel.textContent = "やりとり";
  body.append(conversationLabel);
  body.append(conversation);
  const canAttach = !archived && (["queued", "running", "blocked", "completed"].includes(job.status)
    || (job.status === "failed" && job.worktree));
  if (canAttach) {
    const responseForm = document.createElement("form");
    responseForm.className = "agent-response-form";
    const instruction = document.createElement("textarea");
    instruction.required = true;
    instruction.maxLength = 10000;
    instruction.rows = 3;
    instruction.placeholder = job.status === "blocked"
      ? "必要な判断や追加情報を入力してください"
      : job.status === "completed" || job.follow_up
        ? "完了内容について確認したいことを入力してください"
        : "このタスクへの追加指示を入力してください";
    instruction.setAttribute("aria-label", "Agentへのメッセージ");
    const resume = document.createElement("button");
    resume.type = "submit";
    resume.className = "primary-button";
    resume.textContent = job.status === "completed" || job.follow_up
      ? "Agentに確認"
      : job.status === "blocked" || job.status === "failed"
        ? "送信して再開"
        : "タスクへ送信";
    responseForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      resume.disabled = true;
      try {
        await postJson("./api/agent-jobs/attach", {
          job_id: job.id,
          instruction: instruction.value,
        });
        await loadAgentJobs();
      } catch (error) {
        state.agentStatus = `メッセージを送信できませんでした：${error.message}`;
        elements.status.textContent = state.agentStatus;
        resume.disabled = false;
      }
    });
    responseForm.append(instruction, resume);
    body.append(responseForm);
  }
  if (!archived && ["queued", "running"].includes(job.status)) {
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "agent-cancel";
    cancel.textContent = "停止";
    cancel.addEventListener("click", async () => {
      cancel.disabled = true;
      try {
        await postJson("./api/agent-jobs/cancel", { job_id: job.id });
        await loadAgentJobs();
      } catch (error) {
        state.agentStatus = `停止を要求できませんでした：${error.message}`;
        elements.status.textContent = state.agentStatus;
        cancel.disabled = false;
      }
    });
    body.append(cancel);
  }
  if (archived) return card;
  const hide = document.createElement("button");
  hide.type = "button";
  hide.className = "agent-hide";
  hide.textContent = "非表示";
  hide.setAttribute("aria-label", `「${job.prompt}」をアーカイブ`);
  let hiding = false;
  const hideJob = async (swiped = false) => {
    if (hiding) return;
    hiding = true;
    hide.disabled = true;
    swipeContainer.classList.toggle("is-hiding", swiped);
    try {
      await postJson("./api/agent-jobs/hide", { job_id: job.id });
      swipeContainer.remove();
      await loadAgentJobs();
    } catch (error) {
      state.agentStatus = `タスクをアーカイブできませんでした：${error.message}`;
      elements.status.textContent = state.agentStatus;
      hide.disabled = false;
      hiding = false;
      swipeContainer.classList.remove("is-hiding");
      card.style.removeProperty("transform");
    }
  };
  hide.addEventListener("click", () => hideJob());

  let touchId = null;
  let startX = 0;
  let startY = 0;
  let offsetX = 0;
  let horizontalSwipe = false;
  let suppressClick = false;
  const findTouch = (touches, identifier) => {
    for (let index = 0; index < touches.length; index += 1) {
      const touch = touches.item(index);
      if (touch?.identifier === identifier) return touch;
    }
    return null;
  };
  card.addEventListener("click", (event) => {
    if (!suppressClick) return;
    event.preventDefault();
    event.stopPropagation();
    suppressClick = false;
  }, true);
  card.addEventListener("touchstart", (event) => {
    if (hiding || event.touches.length !== 1) return;
    if (event.target.closest("button, input, textarea, select, a, label")) return;
    const touch = event.changedTouches.item(0);
    if (!touch) return;
    touchId = touch.identifier;
    startX = touch.clientX;
    startY = touch.clientY;
    offsetX = 0;
    horizontalSwipe = false;
  }, { passive: true });
  card.addEventListener("touchmove", (event) => {
    const touch = findTouch(event.changedTouches, touchId);
    if (!touch) return;
    const deltaX = touch.clientX - startX;
    const deltaY = touch.clientY - startY;
    if (!horizontalSwipe && Math.abs(deltaX) < 8 && Math.abs(deltaY) < 8) return;
    if (!horizontalSwipe && Math.abs(deltaY) >= Math.abs(deltaX)) {
      touchId = null;
      return;
    }
    horizontalSwipe = true;
    event.preventDefault();
    offsetX = Math.max(-120, Math.min(120, deltaX));
    card.style.transform = `translateX(${offsetX}px)`;
    swipeContainer.classList.add("is-swiping");
    swipeContainer.classList.toggle("swipe-right", offsetX > 0);
  }, { passive: false });
  const finishSwipe = () => {
    touchId = null;
    swipeContainer.classList.remove("is-swiping");
    suppressClick = horizontalSwipe;
    setTimeout(() => { suppressClick = false; }, 400);
    if (horizontalSwipe && Math.abs(offsetX) >= 72) {
      swipeContainer.classList.toggle("hide-right", offsetX > 0);
      card.style.removeProperty("transform");
      hideJob(true);
      return;
    }
    card.style.removeProperty("transform");
  };
  card.addEventListener("touchend", (event) => {
    if (!findTouch(event.changedTouches, touchId)) return;
    finishSwipe();
  });
  card.addEventListener("touchcancel", (event) => {
    if (!findTouch(event.changedTouches, touchId)) return;
    touchId = null;
    swipeContainer.classList.remove("is-swiping");
    card.style.removeProperty("transform");
  });

  swipeContainer.append(swipeAction, card, hide);
  return swipeContainer;
}

function isAgentInteractionActive() {
  const activeElement = document.activeElement;
  if (activeElement === elements.agentRepository
      || activeElement === elements.agentModel
      || activeElement === elements.agentReasoningEffort
      || elements.agentJobs.contains(activeElement)
      || elements.agentArchivedJobs.contains(activeElement)) {
    return true;
  }
  const selection = window.getSelection?.();
  if (!selection || selection.isCollapsed) return false;
  return elements.agentJobs.contains(selection.anchorNode)
    || elements.agentJobs.contains(selection.focusNode)
    || elements.agentArchivedJobs.contains(selection.anchorNode)
    || elements.agentArchivedJobs.contains(selection.focusNode);
}

function updateAgentRepositories(repositories) {
  const current = [...elements.agentRepository.options].map((option) => ({
    name: option.value,
    label: option.textContent,
  }));
  if (JSON.stringify(current) === JSON.stringify(repositories)) return;
  const selected = elements.agentRepository.value;
  elements.agentRepository.replaceChildren();
  for (const repository of repositories) {
    const option = document.createElement("option");
    option.value = repository.name;
    option.textContent = repository.label;
    elements.agentRepository.append(option);
  }
  if ([...elements.agentRepository.options].some((option) => option.value === selected)) {
    elements.agentRepository.value = selected;
  }
}

function selectedAgentModelOption() {
  return [...elements.agentModel.options].find((option) => option.value === elements.agentModel.value);
}

function updateAgentModelSummary() {
  const model = selectedAgentModelOption();
  const modelLabel = model?.textContent || "モデル未選択";
  const effort = elements.agentReasoningEffort.value || "effort未選択";
  elements.agentModelSummary.textContent = `詳細（${modelLabel}・${effort}）`;
}

function updateAgentReasoningEfforts() {
  const model = selectedAgentModelOption();
  const efforts = model ? JSON.parse(model.dataset.efforts || "[]") : [];
  const current = elements.agentReasoningEffort.value;
  const nextOptions = efforts.map((effort) => ({ value: effort, label: effort }));
  const existing = [...elements.agentReasoningEffort.options].map((option) => option.value);
  if (JSON.stringify(existing) !== JSON.stringify(nextOptions.map((option) => option.value))) {
    elements.agentReasoningEffort.replaceChildren();
    for (const effort of nextOptions) {
      const option = document.createElement("option");
      option.value = effort.value;
      option.textContent = effort.label;
      elements.agentReasoningEffort.append(option);
    }
  }
  if (efforts.includes(current)) {
    elements.agentReasoningEffort.value = current;
  } else if (model && efforts.includes(model.dataset.defaultEffort)) {
    elements.agentReasoningEffort.value = model.dataset.defaultEffort;
  } else if (efforts.length) {
    elements.agentReasoningEffort.value = efforts[0];
  }
  elements.agentReasoningEffort.disabled = !efforts.length;
  updateAgentModelSummary();
}

function updateAgentModels(models, defaultModel) {
  const current = elements.agentModel.value;
  const normalized = Array.isArray(models) ? models : [];
  const existing = [...elements.agentModel.options].map((option) => ({
    value: option.value,
    label: option.textContent,
    efforts: option.dataset.efforts,
    defaultEffort: option.dataset.defaultEffort,
  }));
  const next = normalized.map((model) => ({
    value: model.slug,
    label: model.display_name || model.slug,
    efforts: model.supported_reasoning_efforts || [],
    defaultEffort: model.default_reasoning_effort || "",
  }));
  if (JSON.stringify(existing) !== JSON.stringify(next.map((model) => ({
    value: model.value,
    label: model.label,
    efforts: JSON.stringify(model.efforts),
    defaultEffort: model.defaultEffort,
  })))) {
    elements.agentModel.replaceChildren();
    for (const model of next) {
      const option = document.createElement("option");
      option.value = model.value;
      option.textContent = model.label;
      option.dataset.efforts = JSON.stringify(model.efforts);
      option.dataset.defaultEffort = model.defaultEffort;
      elements.agentModel.append(option);
    }
  }
  if ([...elements.agentModel.options].some((option) => option.value === current)) {
    elements.agentModel.value = current;
  } else if ([...elements.agentModel.options].some((option) => option.value === defaultModel)) {
    elements.agentModel.value = defaultModel;
  } else if (elements.agentModel.options.length) {
    elements.agentModel.selectedIndex = 0;
  }
  elements.agentModel.disabled = elements.agentModel.options.length === 0;
  updateAgentReasoningEfforts();
}

async function loadAgentJobs() {
  try {
    const response = await fetchWithTimeout("./api/agent-jobs", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const interactionActive = isAgentInteractionActive();
    if (!interactionActive) {
      updateAgentRepositories(payload.repositories);
      updateAgentModels(payload.models, payload.default_model);
      elements.agentJobs.replaceChildren(...payload.jobs.map((job) => renderAgentJob(job)));
      const archivedJobs = payload.archived_jobs || [];
      elements.agentArchivedJobs.replaceChildren(
        ...archivedJobs.map((job) => renderAgentJob(job, true)),
      );
      elements.agentArchiveCount.textContent = String(archivedJobs.length);
      elements.agentArchive.hidden = archivedJobs.length === 0;
    }
    elements.agentEmpty.hidden = payload.jobs.length !== 0;
    const active = payload.jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
    const blocked = payload.jobs.filter((job) => job.status === "blocked").length;
    elements.agentCount.textContent = String(active + blocked);
    elements.agentCount.hidden = active + blocked === 0;
    elements.agentJobsSummary.textContent = `実行・待機 ${active}件${blocked ? `・判断待ち ${blocked}件` : ""}`;
    state.agentStatus = active
      ? `${active}件のAgentタスクが進行中です`
      : blocked ? `${blocked}件が判断待ちです` : "Agentは待機中です";
    if (currentView === "agent") elements.status.textContent = state.agentStatus;
  } catch (error) {
    state.agentStatus = `Agentタスクを読み込めませんでした：${error.message}`;
    if (currentView === "agent") elements.status.textContent = state.agentStatus;
  }
}

function renderTanomiJob(task) {
  const card = document.createElement("details");
  card.className = `agent-job status-${({ done: "completed", error: "failed", stopped: "cancelled" }[task.status] || task.status)}`;
  card.open = openTanomiJobs.has(task.id);
  card.addEventListener("toggle", () => {
    if (card.open) openTanomiJobs.add(task.id);
    else openTanomiJobs.delete(task.id);
  });
  const overview = document.createElement("summary");
  overview.className = "agent-job-overview";
  const icon = document.createElement("span");
  icon.className = "agent-status-icon";
  icon.textContent = { queued: "…", running: "•", done: "✓", error: "!", stopped: "×" }[task.status] || "•";
  icon.setAttribute("aria-hidden", "true");
  const overviewText = document.createElement("span");
  overviewText.className = "agent-overview-text";
  const title = document.createElement("strong");
  title.className = "agent-job-title";
  title.textContent = task.title || task.prompt || task.id;
  const meta = document.createElement("span");
  meta.className = "agent-phase";
  meta.textContent = `${task.status}・${task.repo_path || task.cwd || "tanomi"}`;
  overviewText.append(title, meta);
  const chevron = document.createElement("span");
  chevron.className = "agent-job-chevron";
  chevron.setAttribute("aria-hidden", "true");
  overview.append(icon, overviewText, chevron);
  card.append(overview);
  const body = document.createElement("div");
  body.className = "agent-job-body tanomi-job-body";
  if (task.prompt) {
    const promptLabel = document.createElement("strong");
    promptLabel.className = "agent-summary-label";
    promptLabel.textContent = "依頼内容";
    const prompt = document.createElement("p");
    prompt.className = "agent-summary";
    prompt.textContent = task.prompt;
    body.append(promptLabel, prompt);
  }
  const result = task.result || task.error;
  if (result) {
    const resultLabel = document.createElement("strong");
    resultLabel.className = "agent-summary-label";
    resultLabel.textContent = task.result ? "結果" : "エラー";
    const resultText = document.createElement("p");
    resultText.className = "agent-summary";
    resultText.textContent = result;
    body.append(resultLabel, resultText);
  }
  if (["queued", "running"].includes(task.status)) {
    const stop = document.createElement("button");
    stop.type = "button";
    stop.className = "agent-cancel";
    stop.textContent = "停止";
    stop.addEventListener("click", async () => {
      stop.disabled = true;
      try { await postJson(`./api/tanomi/tasks/${encodeURIComponent(task.id)}/stop`, {}); await loadTanomiTasks(); }
      catch (error) { elements.tanomiStatus.textContent = `停止できませんでした：${error.message}`; stop.disabled = false; }
    });
    body.append(stop);
  }
  card.append(body);
  return card;
}

async function tanomiResponseError(response) {
  try {
    const payload = await response.clone().json();
    if (payload && typeof payload.error === "string" && payload.error) return payload.error;
  } catch { /* use the status below */ }
  return `HTTP ${response.status}`;
}

function setTanomiEnabled(enabled) {
  elements.tanomiForm.dataset.available = enabled ? "true" : "false";
  elements.tanomiForm.querySelectorAll("textarea, select, input, button").forEach((control) => {
    control.disabled = !enabled;
  });
}

async function loadTanomiTasks() {
  try {
    const [reposResponse, tasksResponse, healthResponse] = await Promise.all([
      fetchWithTimeout("./api/tanomi/repos", { cache: "no-store" }),
      fetchWithTimeout("./api/tanomi/tasks?limit=50", { cache: "no-store" }),
      fetchWithTimeout("./api/tanomi/health", { cache: "no-store" }),
    ]);
    const failedResponse = [reposResponse, tasksResponse, healthResponse].find((response) => !response.ok);
    if (failedResponse) throw new Error(await tanomiResponseError(failedResponse));
    const [repos, buckets, health] = await Promise.all([
      reposResponse.json(), tasksResponse.json(), healthResponse.json(),
    ]);
    if (!health || health.ok !== true) throw new Error("tanomiのヘルスチェックが失敗しました");
    elements.tanomiRepository.replaceChildren(...(Array.isArray(repos) ? repos : []).map((repo) => {
      const option = document.createElement("option");
      option.value = typeof repo === "string" ? repo : repo.path;
      option.textContent = typeof repo === "string" ? repo : (repo.label || repo.path);
      return option;
    }));
    const tasks = Array.isArray(buckets.tasks) ? buckets.tasks : [];
    const taskIDs = new Set(tasks.map((task) => task.id));
    for (const taskID of openTanomiJobs) {
      if (!taskIDs.has(taskID)) openTanomiJobs.delete(taskID);
    }
    elements.tanomiJobs.replaceChildren(...tasks.map(renderTanomiJob));
    elements.tanomiStatus.textContent = `${tasks.length}件・5秒ごとに更新`;
    elements.tanomiHealth.textContent = "接続中";
    setTanomiEnabled(true);
  } catch (error) {
    elements.tanomiStatus.textContent = `tanomiを利用できません：${error.message}`;
    elements.tanomiHealth.textContent = "接続不可";
    setTanomiEnabled(false);
  }
}

function codexWindowLabel(minutes) {
  if (minutes === 10080) return "週次";
  if (minutes && minutes % 1440 === 0) return `${minutes / 1440}日`;
  if (minutes && minutes % 60 === 0) return `${minutes / 60}時間`;
  return minutes ? `${minutes}分` : "利用枠";
}

function renderCodexLimit(name, window) {
  const item = document.createElement("div");
  item.className = "codex-limit";
  const heading = document.createElement("div");
  heading.className = "codex-limit-heading";
  const label = document.createElement("span");
  label.textContent = `${name}・${codexWindowLabel(window.windowDurationMins)}`;
  const percent = document.createElement("span");
  percent.textContent = `${window.usedPercent}% 使用`;
  heading.append(label, percent);
  const progress = document.createElement("progress");
  progress.max = 100;
  progress.value = window.usedPercent;
  progress.setAttribute("aria-label", `${label.textContent} ${percent.textContent}`);
  const meta = document.createElement("div");
  meta.className = "codex-limit-meta";
  const remaining = document.createElement("span");
  remaining.textContent = `残り ${Math.max(0, 100 - window.usedPercent)}%`;
  const reset = document.createElement("span");
  reset.textContent = window.resetsAt
    ? `リセット ${formatAgentTime(new Date(window.resetsAt * 1000).toISOString())}`
    : "リセット時刻不明";
  meta.append(remaining, reset);
  item.append(heading, progress, meta);
  return item;
}

function compareCodexLimits([leftId, left], [rightId, right]) {
  const leftRank = leftId === "codex" ? 0 : 1;
  const rightRank = rightId === "codex" ? 0 : 1;
  if (leftRank !== rightRank) return leftRank - rightRank;
  const leftName = left.limitName || leftId;
  const rightName = right.limitName || rightId;
  return leftName.localeCompare(rightName, "ja") || leftId.localeCompare(rightId, "en");
}

async function loadCodexUsage() {
  try {
    const response = await fetchWithTimeout("./api/codex-usage", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const limits = payload.rateLimitsByLimitId || {};
    const entries = Object.entries(limits)
      .sort(compareCodexLimits)
      .flatMap(([limitId, limit]) => {
        const name = limit.limitName || (limitId === "codex" ? "Codex" : limitId);
        return [limit.primary, limit.secondary]
          .filter(Boolean)
          .map((window) => renderCodexLimit(name, window));
      });
    elements.codexUsageLimits.replaceChildren(...entries);
    elements.codexUsageStatus.hidden = entries.length > 0;
    elements.codexUsageStatus.textContent = entries.length
      ? ""
      : "現在の利用枠はありません。";
    elements.codexUsagePlan.textContent = payload.rateLimits?.planType || "";
  } catch {
    elements.codexUsageLimits.replaceChildren();
    elements.codexUsagePlan.textContent = "";
    elements.codexUsageStatus.hidden = false;
    elements.codexUsageStatus.textContent = "使用状況を取得できませんでした。";
  }
}

async function fetchWithTimeout(url, options = {}, timeout = 10000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeoutId);
  }
}

function localDateString() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

async function postJson(url, payload) {
  const response = await fetchWithTimeout(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.error || detail?.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

function plannerItem(task, routine = false) {
  const row = document.createElement("article");
  row.className = `planner-item priority-${task.priority}`;
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = Boolean(task.completed_today);
  checkbox.setAttribute("aria-label", `${task.title}を完了`);
  const content = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = task.title;
  const meta = document.createElement("span");
  const recurrenceLabels = { daily: "毎日", weekdays: "平日", weekly: "毎週" };
  meta.textContent = routine
    ? recurrenceLabels[task.recurrence]
    : task.due_date ? `期限 ${task.due_date}` : "期限なし";
  content.append(title, meta);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "planner-delete";
  remove.textContent = "削除";
  remove.addEventListener("click", async () => {
    if (!window.confirm(`「${task.title}」を削除しますか？`)) return;
    await postJson("./api/tasks/delete", { task_id: task.id });
    await loadToday();
  });
  checkbox.addEventListener("change", async () => {
    checkbox.disabled = true;
    try {
      await postJson("./api/task-status", { task_id: task.id, completed: checkbox.checked });
      await loadToday();
    } catch (error) {
      checkbox.checked = !checkbox.checked;
      state.todayStatus = `更新に失敗しました：${error.message}`;
      elements.status.textContent = state.todayStatus;
    } finally {
      checkbox.disabled = false;
    }
  });
  row.append(checkbox, content, remove);
  return row;
}

function renderHealth(health) {
  const metrics = [];
  if (health?.sleep_minutes != null) metrics.push(["睡眠", `${Math.floor(health.sleep_minutes / 60)}時間${health.sleep_minutes % 60}分`]);
  if (health?.steps != null) metrics.push(["歩数", `${health.steps.toLocaleString("ja-JP")}歩`]);
  if (health?.resting_heart_rate != null) metrics.push(["安静時心拍", `${health.resting_heart_rate} bpm`]);
  if (health?.hrv_ms != null) metrics.push(["HRV", `${health.hrv_ms} ms`]);
  if (health?.respiratory_rate != null) metrics.push(["呼吸数", `${health.respiratory_rate} 回/分`]);
  elements.healthMetrics.replaceChildren();
  for (const [label, value] of metrics) {
    const item = document.createElement("div");
    const name = document.createElement("span");
    const measurement = document.createElement("strong");
    name.textContent = label;
    measurement.textContent = value;
    item.append(name, measurement);
    elements.healthMetrics.append(item);
  }
  elements.healthSynced.textContent = metrics.length ? "HealthKit同期済み" : "HealthKit未同期";
  document.querySelector("#health-fatigue").value = health?.fatigue || "";
  document.querySelector("#health-mood").value = health?.mood || "";
  document.querySelector("#health-note").value = health?.note || "";
}

async function loadToday() {
  state.todayStatus = "今日の予定を読み込んでいます…";
  if (currentView === "today") elements.status.textContent = state.todayStatus;
  try {
    const response = await fetchWithTimeout("./api/today", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    elements.taskItems.replaceChildren(...payload.tasks.map((task) => plannerItem(task)));
    elements.routineItems.replaceChildren(...payload.routines.map((task) => plannerItem(task, true)));
    elements.tasksEmpty.hidden = payload.tasks.length !== 0;
    elements.routinesEmpty.hidden = payload.routines.length !== 0;
    const routineDone = payload.routines.filter((task) => task.completed_today).length;
    elements.taskProgress.textContent = `${payload.tasks.length}件`;
    elements.routineProgress.textContent = `${routineDone}/${payload.routines.length} 完了`;
    const remaining = payload.tasks.length + payload.routines.length - routineDone;
    elements.todayCount.textContent = String(remaining);
    elements.todayCount.hidden = remaining === 0;
    elements.todaySummary.textContent = remaining ? `残り ${remaining}件です。` : "今日の項目はすべて完了しました。";
    state.todayStatus = `今日の残り ${remaining}件`;
    renderHealth(payload.health);
    if (currentView === "today") elements.status.textContent = state.todayStatus;
  } catch (error) {
    state.todayStatus = `今日の予定を読み込めませんでした：${error.message}`;
    elements.todaySummary.textContent = state.todayStatus;
    if (currentView === "today") elements.status.textContent = state.todayStatus;
  }
}

function emailStatusLabel(status) {
  return status === "awaiting_reply" ? "返信待ち" : status === "snoozed" ? "保留中" : "未対応";
}

async function updateEmailStatus(threadId, action) {
  const response = await fetchWithTimeout("./api/email-status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId, action }),
  });
  if (!response.ok) throw new Error("メール状態を更新できませんでした");
}

async function loadEmailReminders(period = "daily") {
  state.emailStatus = "メールを読み込んでいます…";
  if (currentView === "today") elements.status.textContent = state.emailStatus;
  try {
    const response = await fetchWithTimeout(`./api/email-reminders/${period}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const todayEmailFragment = document.createDocumentFragment();
    for (const email of (payload.items || []).slice(0, 5)) {
      const item = document.createElement("article");
      item.className = `planner-item email-planner-item importance-${email.importance}`;
      const marker = document.createElement("span");
      marker.className = "email-planner-marker";
      marker.textContent = "✉";
      const content = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = email.subject;
      const action = document.createElement("span");
      action.textContent = email.required_action;
      content.append(title, action);
      const link = document.createElement("a");
      link.href = email.gmail_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "開く";
      item.append(marker, content, link);
      todayEmailFragment.append(item);
    }
    elements.todayEmailItems.replaceChildren(todayEmailFragment);
    elements.todayEmailEmpty.hidden = Boolean(payload.items?.length);
  } catch (error) {
    state.emailStatus = `メールの読み込みに失敗しました：${error.message}`;
    if (currentView === "today") elements.status.textContent = state.emailStatus;
  }
}

async function loadUnreadEmails() {
  state.emailStatus = "メールを読み込んでいます…";
  if (currentView === "email") elements.status.textContent = state.emailStatus;
  try {
    const response = await fetchWithTimeout("./api/emails/unread", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const fragment = document.createDocumentFragment();
    for (const email of payload.items || []) {
      const card = document.createElement("article");
      card.className = `email-card importance-${email.importance}`;
      const meta = document.createElement("p");
      meta.className = "email-meta";
      meta.textContent = `${emailStatusLabel(email.status)}・${email.sender}`;
      const title = document.createElement("h3");
      title.textContent = email.subject;
      const reason = document.createElement("p");
      reason.textContent = `理由：${email.reason}`;
      const nextAction = document.createElement("p");
      nextAction.textContent = `次の行動：${email.required_action}${email.due_date ? `（期限 ${email.due_date}）` : ""}`;
      const controls = document.createElement("div");
      controls.className = "email-actions";
      const content = document.createElement("div");
      content.className = "email-content";
      content.hidden = true;
      const showContent = document.createElement("button");
      showContent.type = "button";
      showContent.textContent = "本文を表示";
      showContent.addEventListener("click", async () => {
        if (!content.hidden) {
          content.hidden = true;
          showContent.textContent = "本文を表示";
          return;
        }
        showContent.disabled = true;
        showContent.textContent = "本文を取得中…";
        try {
          const response = await fetchWithTimeout(
            `./api/email-content/${encodeURIComponent(email.thread_id)}`,
            { cache: "no-store" },
            30000,
          );
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const payload = await response.json();
          const messages = document.createDocumentFragment();
          for (const message of payload.messages || []) {
            const item = document.createElement("section");
            item.className = "email-message";
            const messageMeta = document.createElement("p");
            messageMeta.className = "email-meta";
            messageMeta.textContent = `${new Intl.DateTimeFormat("ja-JP", {
              month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
            }).format(new Date(message.received_at))}・${message.sender}`;
            const body = document.createElement("p");
            body.className = "email-body";
            body.textContent = message.body || "本文を取得できませんでした。";
            item.append(messageMeta, body);
            messages.append(item);
          }
          content.replaceChildren(messages);
          content.hidden = false;
          showContent.textContent = "本文を閉じる";
        } catch (error) {
          elements.status.textContent = `本文の取得に失敗しました：${error.message}`;
          showContent.textContent = "本文を再取得";
        } finally {
          showContent.disabled = false;
        }
      });
      controls.append(showContent);
      for (const [value, label] of [["read", "既読"], ["done", "対応済み"], ["snooze", "明日"], ["dismiss", "対応不要"]]) {
        if (value === "read" && payload.can_mark_read === false) continue;
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            await updateEmailStatus(email.thread_id, value);
            card.remove();
            const remaining = elements.emailItems.children.length;
            elements.emailEmpty.hidden = remaining !== 0;
            elements.emailCount.textContent = String(remaining);
            elements.emailCount.hidden = remaining === 0;
          } catch (error) {
            button.disabled = false;
            elements.status.textContent = error.message;
          }
        });
        controls.append(button);
      }
      card.append(meta, title, reason, nextAction, controls, content);
      fragment.append(card);
    }
    elements.emailItems.replaceChildren(fragment);
    elements.emailEmpty.hidden = Boolean(payload.items?.length);
    elements.emailAssistant.hidden = false;
    const count = payload.items?.length || 0;
    elements.emailCount.textContent = String(count);
    elements.emailCount.hidden = count === 0;
    const syncedAt = payload.last_sync_at
      ? new Intl.DateTimeFormat("ja-JP", {
          month: "numeric",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }).format(new Date(payload.last_sync_at))
      : "未同期";
    state.emailStatus = `${syncedAt} Gmail取得・未読メール ${count}件`;
    if (payload.sync_error) {
      state.emailStatus += payload.authorization_required
        ? "（Gmailの再認証が必要です）"
        : "（同期に失敗したため保存済みデータを表示中）";
    } else if (payload.can_mark_read === false) {
      state.emailStatus += "（既読反映にはGmailの再認証が必要です）";
    }
    elements.emailSyncStatus.textContent = payload.last_sync_at
      ? `最終取得：${syncedAt}（Gmailから${payload.synced_thread_count}スレッド取得）${payload.sync_error ? "・同期失敗" : ""}`
      : "Gmailの同期はまだ完了していません。";
    if (currentView === "email") elements.status.textContent = state.emailStatus;
  } catch (error) {
    elements.emailAssistant.hidden = true;
    state.emailStatus = error.name === "AbortError"
      ? "メールの読み込みがタイムアウトしました。Tailscale接続をご確認ください。"
      : `メールの読み込みに失敗しました：${error.message}`;
    if (currentView === "email") elements.status.textContent = state.emailStatus;
    elements.emailSyncStatus.textContent = state.emailStatus;
  }
}

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
        const selectionStatus = document.createElement("small");
        selectionStatus.className = `selection-status selection-status-${item.selection_status || "new"}`;
        selectionStatus.textContent = item.selection_status === "continued" ? "継続" : "新着";
        content.append(selectionStatus);
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
  state.newsStatus = "ニュースを読み込んでいます…";
  if (currentView === "news") elements.status.textContent = state.newsStatus;
  elements.refresh.disabled = true;
  try {
    const response = await fetchWithTimeout("./data/articles.json", { cache: "no-store" });
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
    const stats = payload.update_stats;
    const statsNote = stats
      ? `・新規 ${stats.new_articles}件（ハイライト採用 ${stats.new_articles_highlighted}件）・ハイライト新選 ${stats.new_highlights}件／継続 ${stats.kept_highlights}件`
      : "";
    state.newsStatus = `${generatedAt} 更新${statsNote}${errorNote}`;
    if (currentView === "news") elements.status.textContent = state.newsStatus;
    renderCategories();
    renderArticles();
  } catch (error) {
    state.newsStatus = error.name === "AbortError"
      ? "ニュースの読み込みがタイムアウトしました。Tailscale接続をご確認ください。"
      : `ニュースの読み込みに失敗しました：${error.message}`;
    if (currentView === "news") elements.status.textContent = state.newsStatus;
  } finally {
    elements.refresh.disabled = false;
  }
}

async function loadDeploymentInfo() {
  try {
    const response = await fetchWithTimeout("./api/deployment", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const deployedAt = new Intl.DateTimeFormat("ja-JP", {
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(payload.deployed_at));
    elements.deploymentVersion.textContent = `バージョン ${payload.version}`;
    elements.deploymentDate.textContent = `デプロイ ${deployedAt}`;
  } catch {
    elements.deploymentVersion.textContent = "デプロイ情報を取得できませんでした";
    elements.deploymentDate.textContent = "";
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
async function refreshCurrentView() {
  if (currentView === "agent") {
    await Promise.all([loadAgentJobs(), loadCodexUsage()]);
  } else if (currentView === "email") {
    await loadUnreadEmails();
  } else if (currentView === "today") {
    await Promise.all([loadToday(), loadEmailReminders()]);
  } else {
    await loadArticles();
  }
  updateScreenUpdatedAt();
}

elements.refresh.addEventListener("click", refreshCurrentView);

const pullToRefresh = {
  startX: 0,
  startY: 0,
  distance: 0,
  vertical: false,
  tracking: false,
  refreshing: false,
};
const pullRefreshDirectionThreshold = 8;
const pullRefreshThreshold = 72;
const pullRefreshMaxDistance = 112;

function resetPullToRefresh() {
  pullToRefresh.distance = 0;
  pullToRefresh.vertical = false;
  pullToRefresh.tracking = false;
  elements.pullToRefresh.classList.remove("is-visible", "is-ready");
  elements.pullToRefresh.style.removeProperty("--pull-distance");
  elements.pullToRefresh.setAttribute("aria-hidden", "true");
}

document.addEventListener("touchstart", (event) => {
  if (
    event.touches.length !== 1
    || window.scrollY > 0
    || pullToRefresh.refreshing
    || event.target.closest("button, input, textarea, select, a, label, [contenteditable='true']")
  ) return;
  pullToRefresh.startX = event.touches[0].clientX;
  pullToRefresh.startY = event.touches[0].clientY;
  pullToRefresh.tracking = true;
}, { passive: true });

document.addEventListener("touchmove", (event) => {
  if (!pullToRefresh.tracking || event.touches.length !== 1) return;
  const horizontalDistance = event.touches[0].clientX - pullToRefresh.startX;
  const rawDistance = event.touches[0].clientY - pullToRefresh.startY;
  if (!pullToRefresh.vertical) {
    if (
      Math.abs(horizontalDistance) < pullRefreshDirectionThreshold
      && Math.abs(rawDistance) < pullRefreshDirectionThreshold
    ) return;
    if (Math.abs(horizontalDistance) >= Math.abs(rawDistance)) {
      resetPullToRefresh();
      return;
    }
    pullToRefresh.vertical = true;
  }
  if (rawDistance <= 0 || window.scrollY > 0) {
    resetPullToRefresh();
    return;
  }
  event.preventDefault();
  pullToRefresh.distance = Math.min(pullRefreshMaxDistance, rawDistance * 0.55);
  const ready = pullToRefresh.distance >= pullRefreshThreshold;
  elements.pullToRefresh.style.setProperty("--pull-distance", `${pullToRefresh.distance}px`);
  elements.pullToRefresh.classList.add("is-visible");
  elements.pullToRefresh.classList.toggle("is-ready", ready);
  elements.pullToRefresh.setAttribute("aria-hidden", "false");
  elements.pullToRefresh.querySelector(".pull-to-refresh-label").textContent = ready
    ? "離して更新"
    : "引いて更新";
}, { passive: false });

document.addEventListener("touchend", async () => {
  if (!pullToRefresh.tracking) return;
  const shouldRefresh = pullToRefresh.distance >= pullRefreshThreshold;
  resetPullToRefresh();
  if (!shouldRefresh) return;
  pullToRefresh.refreshing = true;
  elements.pullToRefresh.classList.add("is-visible", "is-refreshing");
  elements.pullToRefresh.setAttribute("aria-hidden", "false");
  elements.pullToRefresh.querySelector(".pull-to-refresh-label").textContent = "更新中…";
  try {
    await refreshCurrentView();
  } finally {
    pullToRefresh.refreshing = false;
    elements.pullToRefresh.classList.remove("is-visible", "is-refreshing");
    elements.pullToRefresh.setAttribute("aria-hidden", "true");
  }
}, { passive: true });

document.addEventListener("touchcancel", resetPullToRefresh, { passive: true });
elements.agentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(elements.agentForm);
  const submits = [...elements.agentForm.querySelectorAll("button[type='submit']")];
  submits.forEach((button) => { button.disabled = true; });
  try {
    await postJson("./api/agent-jobs", {
      repository: form.get("repository"),
      prompt: form.get("prompt"),
      model: form.get("model"),
      reasoning_effort: form.get("reasoning_effort"),
      mode: "execute",
    });
    document.querySelector("#agent-prompt").value = "";
    await loadAgentJobs();
  } catch (error) {
    state.agentStatus = `タスクを開始できませんでした：${error.message}`;
    elements.status.textContent = state.agentStatus;
  } finally {
    submits.forEach((button) => { button.disabled = false; });
  }
});
elements.agentModel.addEventListener("change", updateAgentReasoningEfforts);
elements.agentReasoningEffort.addEventListener("change", updateAgentModelSummary);

elements.tanomiForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (elements.tanomiForm.dataset.available !== "true") return;
  const submit = elements.tanomiForm.querySelector("button[type='submit']");
  submit.disabled = true;
  try {
    if (elements.tanomiPermission.value === "bypassPermissions"
        && !window.confirm("任意コマンド実行を許可してtanomiタスクを開始しますか？")) return;
    await postJson("./api/tanomi/tasks", {
      prompt: elements.tanomiPrompt.value,
      repo: elements.tanomiRepository.value,
      model: elements.tanomiModel.value || "opus",
      permission_mode: elements.tanomiPermission.value,
    });
    elements.tanomiPrompt.value = "";
    await loadTanomiTasks();
  } catch (error) {
    elements.tanomiStatus.textContent = `tanomiタスクを開始できませんでした：${error.message}`;
  } finally {
    submit.disabled = false;
  }
});
elements.taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(elements.taskForm);
  const submit = elements.taskForm.querySelector("button[type='submit']");
  submit.disabled = true;
  try {
    await postJson("./api/tasks", {
      title: form.get("title"),
      due_date: form.get("due_date") || null,
      priority: Number(form.get("priority")),
      recurrence: form.get("recurrence"),
    });
    elements.taskForm.reset();
    await loadToday();
  } catch (error) {
    state.todayStatus = `追加できませんでした：${error.message}`;
    elements.status.textContent = state.todayStatus;
  } finally {
    submit.disabled = false;
  }
});
elements.healthForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(elements.healthForm);
  const submit = elements.healthForm.querySelector("button[type='submit']");
  submit.disabled = true;
  try {
    await postJson("./api/health/checkin", {
      date: localDateString(),
      fatigue: form.get("fatigue") ? Number(form.get("fatigue")) : null,
      mood: form.get("mood") ? Number(form.get("mood")) : null,
      note: form.get("note"),
    });
    await loadToday();
  } catch (error) {
    state.todayStatus = `体調を記録できませんでした：${error.message}`;
    elements.status.textContent = state.todayStatus;
  } finally {
    submit.disabled = false;
  }
});
document.querySelectorAll("[data-app-view]").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.appView));
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("./sw.js"));
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) updateScreenFreshness();
});
window.addEventListener("pageshow", () => updateScreenFreshness());

updateScreenUpdatedAt();
loadArticles();
loadHighlights();
loadEmailReminders();
loadUnreadEmails();
loadToday();
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
loadAgentJobs();
loadTanomiTasks();
loadCodexUsage();
loadDeploymentInfo();
window.setInterval(() => {
  if (currentView === "agent") loadAgentJobs();
}, 5000);
window.setInterval(() => {
  if (currentView === "agent") loadTanomiTasks();
}, 5000);
window.setInterval(() => {
  if (currentView === "agent") loadCodexUsage();
}, 60000);
switchView(currentView);
