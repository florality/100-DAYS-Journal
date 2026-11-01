const app = document.getElementById("app");
const sheetRoot = document.getElementById("sheet-root");
const toastRoot = document.getElementById("toast-root");

const VERSION_LABELS = { min: "最小版", standard: "标准版", adventure: "冒险版" };
const MOOD_LABELS = { 1: "很沉", 2: "有点低", 3: "平的", 4: "轻一点", 5: "有光" };
const ENERGY_OPTIONS = [
  { value: 1, label: "省电" },
  { value: 3, label: "一般" },
  { value: 4, label: "还行" },
];
const COLORS = ["#D8A47F", "#B8C6B0", "#A9C4D4", "#D6A5A0", "#C7A5A0", "#A8ADA4"];
const CATEGORY_OPTIONS = ["自定义", "安静", "身体", "创造", "观察", "城市", "连接", "照顾", "勇气"];

const state = {
  boot: null,
  view: "today",
  intent: "",
  energy: Number(localStorage.getItem("yibai.energy") || 3),
  suggestions: null,
  loadingSuggestions: false,
  suggestionSeq: 0,
  browseTasks: [],
  browseLoading: false,
  memories: null,
  memoriesLoaded: false,
  memoriesLoading: false,
  result: null,
  sheet: null,
  authRequired: false,
  authenticated: false,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/login") {
      state.authRequired = true;
      state.authenticated = false;
      state.boot = null;
      renderLogin(true);
    }
    let message = "刚才没有成功，再试一次好吗？";
    try {
      const data = await response.json();
      if (data.detail) message = typeof data.detail === "string" ? data.detail : message;
    } catch (_) {
      // Keep the warm fallback.
    }
    throw new Error(message);
  }
  return response.json();
}

function showToast(message) {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  toastRoot.appendChild(toast);
  window.setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(6px)";
    window.setTimeout(() => toast.remove(), 220);
  }, 3400);
}

function formatDate(value, long = false) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  if (long) {
    return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
  }
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function dayNumber(createdAt) {
  const start = new Date(createdAt).getTime();
  if (Number.isNaN(start)) return 1;
  return Math.max(1, Math.floor((Date.now() - start) / 86400000) + 1);
}

function greeting() {
  const hour = new Date().getHours();
  if (hour < 5) return "夜深了";
  if (hour < 11) return "早上好";
  if (hour < 14) return "中午好";
  if (hour < 18) return "下午好";
  if (hour < 23) return "晚上好";
  return "夜深了";
}

function timeOf(task, version) {
  return task?.minutes?.[version] ?? task?.minutes?.standard ?? 10;
}

function socialLabel(task) {
  if (task.social === "solo") return "一个人也可以";
  if (task.social === "any") return "按你的方式来";
  return "可以轻轻连接";
}

function findTask(taskId) {
  const id = Number(taskId);
  const pools = [
    state.suggestions || [],
    state.boot?.suggestions || [],
    state.boot?.active?.task ? [state.boot.active.task] : [],
    state.browseTasks || [],
    state.result?.recommendations || [],
    state.memories?.map((memory) => memory.task) || [],
    state.boot?.memories?.map((memory) => memory.task) || [],
  ];
  for (const pool of pools) {
    const found = pool.find((task) => Number(task.id) === id);
    if (found) return found;
  }
  return null;
}

function findMemory(entryId) {
  const id = Number(entryId);
  const pools = [state.memories || [], state.boot?.memories || [], state.result?.memory ? [state.result.memory] : []];
  for (const pool of pools) {
    const found = pool.find((memory) => Number(memory.entry_id) === id);
    if (found) return found;
  }
  return null;
}

async function reloadBootstrap() {
  state.boot = await api("/api/bootstrap");
  state.suggestions = null;
  if (state.boot.active?.intent) {
    state.intent = state.boot.active.intent;
  }
}

async function init() {
  try {
    const session = await api("/api/session");
    state.authRequired = Boolean(session.auth_required);
    state.authenticated = Boolean(session.authenticated);
    if (state.authRequired && !state.authenticated) {
      renderLogin(Boolean(session.configured));
      return;
    }
    await reloadBootstrap();
    if (!ENERGY_OPTIONS.some((option) => option.value === state.energy)) state.energy = 3;
    renderApp();
  } catch (error) {
    app.innerHTML = `
      <div class="loading-screen">
        <span class="loading-mark">!</span>
        <p>${escapeHtml(error.message)}</p>
        <button class="primary-button" type="button" data-action="retry-boot">再试一次</button>
      </div>`;
  }
}

function renderLogin(configured = true) {
  state.sheet = null;
  sheetRoot.classList.remove("open");
  sheetRoot.innerHTML = "";
  document.body.classList.remove("sheet-open");
  app.innerHTML = `
    <main class="auth-shell">
      <div class="auth-brand">
        <span class="brand-mark" aria-hidden="true"></span>
        <span>
          <span class="brand-name">一百个今天</span>
          <span class="brand-sub">one hundred todays</span>
        </span>
      </div>
      <section class="auth-card">
        <div class="eyebrow">你的私人小册子</div>
        <h1>这里只留给你<br /><em>一个人看。</em></h1>
        ${
          configured
            ? `<p>输入访问密码，才能看到已经留下的日子。</p>
               <form data-form="login">
                 <div class="field">
                   <label for="access-password">访问密码</label>
                   <input id="access-password" name="password" type="password" autocomplete="current-password" required autofocus />
                 </div>
                 <button class="primary-button auth-submit" type="submit">打开我的小册子</button>
               </form>`
            : `<p>线上环境还没有配置访问密码。请在 Vercel 项目的环境变量里设置 <code>APP_PASSWORD</code>，然后重新部署。</p>`
        }
        <p class="auth-note">密码只用来保护你的记录，不会用于任何分析。</p>
      </section>
    </main>`;
}

function renderApp() {
  if (!state.boot) return;
  const view = state.view;
  const content =
    view === "memories"
      ? renderMemoriesView()
      : view === "settings"
        ? renderSettingsView()
        : view === "result"
          ? renderResultView()
          : renderTodayView();
  app.innerHTML = `
    ${renderTopbar()}
    <main class="page">${content}</main>
    ${renderBottomNav()}`;
  document.body.classList.toggle("sheet-open", Boolean(state.sheet));
}

function renderTopbar() {
  const profile = state.boot.profile;
  const completed = state.boot.stats.completed;
  return `
    <header class="topbar">
      <button class="brand" type="button" data-action="view" data-view="today" aria-label="回到今天">
        <span class="brand-mark" aria-hidden="true"></span>
        <span>
          <span class="brand-name">一百个今天</span>
          <span class="brand-sub">one hundred todays</span>
        </span>
      </button>
      <nav class="topnav" aria-label="主导航">
        ${topNavButton("today", "今天")}
        ${topNavButton("memories", "记忆")}
        ${topNavButton("settings", "设置")}
      </nav>
      <div class="progress-pill" aria-label="已完成 ${completed} 件，共 ${profile.target_count} 件">
        <span>已留下</span>
        <strong>${completed}</strong>
        <span>/ ${profile.target_count}</span>
      </div>
    </header>`;
}

function topNavButton(view, label) {
  const active = state.view === view || (state.view === "result" && view === "today");
  return `<button class="nav-link ${active ? "active" : ""}" type="button" data-action="view" data-view="${view}">${label}</button>`;
}

function renderBottomNav() {
  return `
    <nav class="bottom-nav" aria-label="主导航">
      ${bottomNavButton("today", "今", "今天")}
      ${bottomNavButton("memories", "忆", "记忆")}
      ${bottomNavButton("settings", "我", "设置")}
    </nav>`;
}

function bottomNavButton(view, icon, label) {
  const active = state.view === view || (state.view === "result" && view === "today");
  return `
    <button class="bottom-link ${active ? "active" : ""}" type="button" data-action="view" data-view="${view}">
      <span class="bottom-icon" aria-hidden="true">${icon}</span>
      <span>${label}</span>
    </button>`;
}

function renderTodayView() {
  const boot = state.boot;
  const profile = boot.profile;
  const stats = boot.stats;
  const active = boot.active;
  const tasks = state.suggestions ?? boot.suggestions ?? [];
  const memories = boot.memories || [];
  const completed = stats.completed;
  const days = dayNumber(profile.created_at);
  const dotCount = Math.max(1, Math.min(profile.target_count, 100));
  let dots = "";
  for (let index = 0; index < dotCount; index += 1) {
    const className = index < completed ? "done" : index === completed ? "next" : "";
    dots += `<span class="dot ${className}" aria-hidden="true"></span>`;
  }

  return `
    <section class="home-grid">
      <div class="hero">
        <div class="eyebrow">${greeting()} · 第 ${days} 天</div>
        <h1>今天，<br /><em>不用过得很好。</em></h1>
        <p class="hero-sub">从一件愿意做的小事开始。做完之后，把它留在这里；不需要照片，也不需要一段漂亮的总结。</p>
        <div class="hero-foot">
          <p class="hero-note">你不需要把每一件都做完。一百只是一个容器，用来装下一些本来会被忘记的日子。</p>
          <span class="hero-manifesto">先和自己待一会儿</span>
        </div>
      </div>
      <aside class="progress-panel" aria-label="收集进度">
        <div>
          <h2>${profile.target_count} 个今天</h2>
          <p>${completed ? `你已经留下了 ${completed} 个今天。` : "第一颗点，等你来点亮。"}</p>
        </div>
        <div class="dots">${dots}</div>
        <div class="progress-legend">
          <span><strong>${completed}</strong> / ${profile.target_count}</span>
          <span>${stats.remaining} 个位置</span>
        </div>
      </aside>
    </section>

    ${active ? renderActiveCard(active) : ""}

    <section class="section">
      <div class="section-head">
        <div>
          <h2>此刻，你想怎么和自己待一会儿？</h2>
          <p>选一个接近现在的状态就好，不用选“应该”的那个。</p>
        </div>
        <button class="text-button" type="button" data-action="open-browse">全部小事 →</button>
      </div>
      <div class="intent-grid">
        ${boot.intents.map((intent) => renderIntent(intent)).join("")}
      </div>
      <div class="energy-row">
        <span class="energy-label">今天的电量</span>
        <div class="energy-options">
          ${ENERGY_OPTIONS.map(
            (option) => `
              <button class="energy-chip ${state.energy === option.value ? "active" : ""}" type="button" data-action="energy" data-value="${option.value}">
                ${option.label}
              </button>`,
          ).join("")}
        </div>
        <span class="energy-note">电量低时，我们会只给最小版本。</span>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <h2>${state.intent ? "这几件，可能适合现在" : "从这几件开始"}</h2>
          <p>${state.intent ? "推荐只是一个方向，不喜欢就换一个。" : "第一次由你来选，后面再慢慢认识彼此。"}</p>
        </div>
        <button class="text-button" type="button" data-action="refresh-suggestions" ${state.loadingSuggestions ? "disabled" : ""}>${state.loadingSuggestions ? "正在换…" : "换一组"}</button>
      </div>
      ${state.loadingSuggestions ? renderSuggestionLoading() : renderTaskGrid(tasks)}
      <div class="home-actions">
        <button class="ghost-button" type="button" data-action="open-custom">自己写一件</button>
        <button class="secondary-button" type="button" data-action="open-browse">看看更多</button>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <h2>被留下的日子</h2>
          <p>${memories.length ? "它们不需要证明什么，只是曾经发生过。" : "第一件完成后，这里会开始有东西。"}</p>
        </div>
        <button class="text-button" type="button" data-action="view" data-view="memories">去记忆墙 →</button>
      </div>
      ${memories.length ? `<div class="memory-strip">${memories.map(renderMiniMemory).join("")}</div>` : renderMemoryHint()}
    </section>`;
}

function renderIntent(intent) {
  const active = state.intent === intent.id;
  return `
    <button class="intent-card ${active ? "active" : ""}" type="button" data-action="intent" data-id="${intent.id}" aria-pressed="${active}">
      <span class="intent-emoji" aria-hidden="true">${intent.emoji}</span>
      <span class="intent-label">${escapeHtml(intent.label)}</span>
      <span class="intent-short">${escapeHtml(intent.short)}</span>
    </button>`;
}

function renderActiveCard(active) {
  const task = active.task;
  const version = active.version || "standard";
  return `
    <section class="active-card" aria-label="正在进行的这件事">
      <div>
        <div class="active-ribbon">有一件正在等你回来</div>
        <h2>${escapeHtml(task.title)}</h2>
        <p>${escapeHtml(task.versions[version])} · 约 ${timeOf(task, version)} 分钟</p>
      </div>
      <div class="active-actions">
        <button class="primary-button" type="button" data-action="open-complete">我回来了，记下它</button>
        <button class="text-button" type="button" data-action="skip-active">今天先不做</button>
      </div>
    </section>`;
}

function renderSuggestionLoading() {
  return `<div class="suggestions-loading"><span class="loading-mark small" aria-hidden="true">○</span><p>正在找一件不需要太费力的事…</p></div>`;
}

function renderTaskGrid(tasks) {
  if (!tasks.length) {
    return `
      <div class="empty-state compact">
        <span class="empty-mark">○</span>
        <h2>这里暂时空着</h2>
        <p>可以自己写一件，或者点击“换一组”看看别的小事。</p>
        <button class="primary-button" type="button" data-action="open-custom">自己写一件</button>
      </div>`;
  }
  return `<div class="task-grid">${tasks.slice(0, 6).map(renderTaskCard).join("")}</div>`;
}

function renderTaskCard(task) {
  const version = task.suggested_version || "standard";
  return `
    <article class="task-card" style="--task-color:${escapeAttr(task.color)}">
      <span class="task-wash" aria-hidden="true"></span>
      <div class="task-kicker">
        <span>${escapeHtml(task.category)}</span>
        <span>${escapeHtml(VERSION_LABELS[version])}</span>
      </div>
      <h3 class="task-title">${escapeHtml(task.title)}</h3>
      <p class="task-summary">${escapeHtml(task.summary)}</p>
      ${task.reason ? `<p class="task-reason">${escapeHtml(task.reason)}</p>` : ""}
      <div class="task-footer">
        <div class="meta">
          <span>约 ${timeOf(task, version)} 分钟</span>
          <span>${task.cost ? "花一点钱" : "不花钱"}</span>
        </div>
        <button class="task-action" type="button" data-action="open-task" data-task-id="${task.id}" data-version="${version}">看看</button>
      </div>
    </article>`;
}

function renderMemoryHint() {
  return `
    <div class="memory-hint">
      <span class="memory-hint-mark">○</span>
      <p>做完一件小事，给它留一句话、一张照片，或只是一个颜色。</p>
    </div>`;
}

function renderMiniMemory(memory) {
  return `
    <button class="mini-memory" type="button" data-action="open-memory" data-entry-id="${memory.entry_id}">
      <span class="mini-memory-color" style="--memory-color:${escapeAttr(memory.color)}" aria-hidden="true"></span>
      <span class="mini-memory-date">${formatDate(memory.completed_at)}</span>
      <span class="mini-memory-title">${escapeHtml(memory.task.title)}</span>
      <span class="mini-memory-note">${escapeHtml(memory.note || "这一天被记住了。")}</span>
    </button>`;
}

function renderResultView() {
  const result = state.result;
  if (!result) return renderTodayView();
  const memory = result.memory;
  const recommendations = result.recommendations || [];
  return `
    <section class="result-head">
      <div class="eyebrow">第 ${state.boot.stats.completed} 个今天</div>
      <h1>这一天，<br /><em>被你留住了。</em></h1>
      <p>不需要它有多特别。你愿意停下来记录，它就已经有了位置。</p>
    </section>
    ${renderResultMemory(memory)}
    <div class="result-actions">
      <button class="primary-button" type="button" data-action="view" data-view="today">回到今天</button>
      <button class="ghost-button" type="button" data-action="view" data-view="memories">看看记忆</button>
    </div>
    <section class="recommendation-section">
      <div class="section-head">
        <div>
          <h2>接下来，要不要试试这几件？</h2>
          <p>它们来自你刚刚留下的反馈。不是任务，只是几个方向。</p>
        </div>
      </div>
      <div class="recommendation-list">
        ${recommendations.length ? recommendations.map(renderRecommendationRow).join("") : `<div class="empty-state compact"><p>今天先到这里也很好。下次再慢慢挑。</p></div>`}
      </div>
    </section>`;
}

function renderResultMemory(memory) {
  return `
    <article class="result-memory">
      ${memory.photo_url ? `<img class="result-photo" src="${escapeAttr(memory.photo_url)}" alt="${escapeAttr(memory.task.title)}" />` : `<div class="result-color" style="--memory-color:${escapeAttr(memory.color)}" aria-hidden="true"></div>`}
      <div class="result-memory-body">
        <div class="memory-date">${formatDate(memory.completed_at, true)} · ${escapeHtml(memory.task.category)}</div>
        <h2>${escapeHtml(memory.task.title)}</h2>
        <p class="result-note ${memory.note ? "" : "empty"}">${escapeHtml(memory.note || "没有写下什么，但这一天被记住了。")}</p>
        <div class="result-meta">
          ${memory.mood_after ? `<span>余温：${MOOD_LABELS[memory.mood_after]}</span>` : ""}
          ${memory.repeat_wish ? `<span>还想再做</span>` : ""}
          <span class="private-mark">${memory.is_private ? "只给自己看" : "愿意分享"}</span>
        </div>
      </div>
    </article>`;
}

function renderRecommendationRow(task) {
  return `
    <div class="recommendation-row" style="--task-color:${escapeAttr(task.color)}">
      <span class="recommendation-swatch" aria-hidden="true"></span>
      <div>
        <span class="recommendation-title">${escapeHtml(task.title)}</span>
        <span class="recommendation-reason">${escapeHtml(task.reason || task.summary)}</span>
      </div>
      <button class="task-action" type="button" data-action="open-task" data-task-id="${task.id}" data-version="${task.suggested_version || "standard"}">选这件</button>
    </div>`;
}

function renderMemoriesView() {
  const memories = state.memories || [];
  const count = state.memoriesLoaded ? memories.length : state.boot.stats.completed;
  return `
    <section class="memory-page-head">
      <div>
        <div class="eyebrow">你的私人档案</div>
        <h1>被留下的<br /><em>日子。</em></h1>
        <p>这里不需要排版得像一份漂亮的履历。它们只是你曾经待过的地方。</p>
      </div>
      <div class="memory-count"><strong>${count}</strong> 个今天</div>
    </section>
    ${state.memoriesLoading ? `<div class="suggestions-loading"><span class="loading-mark small">○</span><p>正在把记忆铺开…</p></div>` : ""}
    ${!state.memoriesLoading && state.memoriesLoaded && memories.length ? `<div class="memory-grid">${memories.map(renderMemoryCard).join("")}</div>` : ""}
    ${!state.memoriesLoading && state.memoriesLoaded && !memories.length ? renderEmptyMemories() : ""}`;
}

function renderEmptyMemories() {
  return `
    <div class="empty-state">
      <span class="empty-mark">○</span>
      <h2>还没有被留下的日子</h2>
      <p>从一件很小的事开始。完成之后，你可以放一张照片、写一句话，或者只选一个颜色。</p>
      <div class="settings-actions">
        <button class="primary-button" type="button" data-action="view" data-view="today">从今天开始</button>
        <button class="ghost-button" type="button" data-action="seed-demo">看看示例记忆</button>
      </div>
    </div>`;
}

function renderMemoryCard(memory) {
  return `
    <button class="memory-card" type="button" data-action="open-memory" data-entry-id="${memory.entry_id}">
      ${memory.photo_url ? `<img class="memory-photo" src="${escapeAttr(memory.photo_url)}" alt="${escapeAttr(memory.task.title)}" loading="lazy" />` : `<span class="memory-color" style="--memory-color:${escapeAttr(memory.color)}" aria-hidden="true"></span>`}
      <span class="memory-body">
        <span class="memory-date">${formatDate(memory.completed_at, true)}</span>
        <span class="memory-title">${escapeHtml(memory.task.title)}</span>
        <span class="memory-note">${escapeHtml(memory.note || "没有写下什么，但这一天被记住了。")}</span>
        <span class="memory-footer">
          <span>${escapeHtml(memory.task.category)}</span>
          <span class="private-mark">${memory.is_private ? "只给自己看" : "愿意分享"}</span>
        </span>
      </span>
    </button>`;
}

function renderSettingsView() {
  const profile = state.boot.profile;
  const stats = state.boot.stats;
  return `
    <section class="settings-head">
      <div class="eyebrow">关于这本小册子</div>
      <h1>按你的方式<br /><em>慢慢来。</em></h1>
      <p>目标、时间和记录都属于你。它们可以随时改变，已经留下的日子不会因此消失。</p>
    </section>
    <form class="settings-grid" data-form="settings">
      <section class="settings-card">
        <h2>你的小册子</h2>
        <div class="field">
          <label for="profile-name">怎么称呼你</label>
          <input id="profile-name" name="name" value="${escapeAttr(profile.name)}" maxlength="24" />
        </div>
        <div class="field">
          <label for="target-count">想收集多少个今天</label>
          <select id="target-count" name="target_count">
            ${[30, 50, 100, 200].map((count) => `<option value="${count}" ${profile.target_count === count ? "selected" : ""}>${count} 个</option>`).join("")}
          </select>
        </div>
        <div class="field">
          <label for="duration-days">大概用多久</label>
          <select id="duration-days" name="duration_days">
            ${[[30, "30 天"], [90, "3 个月"], [365, "1 年"], [0, "不限时间"]].map(([value, label]) => `<option value="${value}" ${profile.duration_days === value ? "selected" : ""}>${label}</option>`).join("")}
          </select>
        </div>
        <p class="field-note">目标改小也不会抹掉已经完成的记录。你可以随时重新扩回来。</p>
        <div class="settings-actions">
          <button class="primary-button" type="submit">保存设置</button>
        </div>
      </section>
      <section class="settings-card">
        <h2>现在的样子</h2>
        <div class="settings-stat"><strong>${stats.completed}</strong><span>个今天被留下</span></div>
        <div class="settings-stat"><strong>${stats.categories.length}</strong><span>种不同的生活入口</span></div>
        <div class="settings-stat"><strong>${stats.remaining}</strong><span>个空位还在等你</span></div>
        <p class="field-note">没有连续打卡，也没有中断惩罚。这里只负责把日子放好。</p>
      </section>
      <section class="settings-card wide">
        <h2>试用数据</h2>
        <p class="field-note">本地试用时记录保存在 SQLite，线上版本保存在 Postgres；线上照片存 Vercel Blob。照片会压缩并移除拍摄位置等 EXIF 信息，默认只给自己看。</p>
        <div class="settings-actions">
          ${stats.completed === 0 ? `<button class="ghost-button" type="button" data-action="seed-demo">载入示例记忆</button>` : ""}
          <button class="danger-button" type="button" data-action="reset-data">清空所有记录，重新开始</button>
        </div>
      </section>
      ${
        state.authRequired
          ? `<section class="settings-card wide">
               <h2>访问安全</h2>
               <p class="field-note">线上版本用访问密码保护。退出后，需要重新输入密码才能看到这本小册子。</p>
               <div class="settings-actions">
                 <button class="ghost-button" type="button" data-action="logout">退出登录</button>
               </div>
             </section>`
          : ""
      }
      <section class="settings-card wide safety-note-card">
        <h2>一个温柔的边界</h2>
        <p class="safety-note">这个产品是记录和陪伴工具，不是心理治疗，也不能替代专业帮助。如果你正处在危机中，请联系当地紧急服务、心理援助热线，或一个你信任的人。不要一个人扛着。</p>
      </section>
    </form>`;
}

function renderSheet() {
  if (!state.sheet) {
    sheetRoot.classList.remove("open");
    sheetRoot.innerHTML = "";
    document.body.classList.remove("sheet-open");
    return;
  }
  sheetRoot.classList.add("open");
  document.body.classList.add("sheet-open");
  let content = "";
  if (state.sheet.type === "task") content = renderTaskSheet();
  if (state.sheet.type === "browse") content = renderBrowseSheet();
  if (state.sheet.type === "custom") content = renderCustomSheet();
  if (state.sheet.type === "complete") content = renderCompleteSheet();
  if (state.sheet.type === "memory") content = renderMemorySheet();
  sheetRoot.innerHTML = `<button class="sheet-backdrop" type="button" data-action="close-sheet" aria-label="关闭"></button>${content}`;
  const focusTarget = sheetRoot.querySelector(".sheet button, .sheet input, .sheet textarea");
  if (focusTarget) window.setTimeout(() => focusTarget.focus({ preventScroll: true }), 100);
}

function renderSheetHead(kicker, title) {
  return `
    <div class="sheet-head">
      <div>
        <p class="sheet-kicker">${escapeHtml(kicker)}</p>
        <h2>${escapeHtml(title)}</h2>
      </div>
      <button class="sheet-close" type="button" data-action="close-sheet" aria-label="关闭">×</button>
    </div>`;
}

function renderTaskSheet() {
  const sheet = state.sheet;
  const task = sheet.task;
  const version = sheet.version || "standard";
  return `
    <section class="sheet" role="dialog" aria-modal="true" aria-label="${escapeAttr(task.title)}">
      ${renderSheetHead(task.category, task.title)}
      <div class="sheet-body">
        <p class="detail-summary">${escapeHtml(task.summary)}</p>
        ${sheet.reason ? `<p class="detail-reason">${escapeHtml(sheet.reason)}</p>` : ""}
        <div class="detail-meta">
          <span class="meta-chip">${escapeHtml(task.category)}</span>
          <span class="meta-chip">约 ${timeOf(task, version)} 分钟</span>
          <span class="meta-chip">${task.cost ? "可以花一点钱" : "不花钱"}</span>
          <span class="meta-chip">${socialLabel(task)}</span>
        </div>
        <p class="version-label">选一个今天的版本</p>
        <div class="version-list">
          ${["min", "standard", "adventure"].map((key) => `
            <button class="version-option ${version === key ? "active" : ""}" type="button" data-action="choose-version" data-version="${key}">
              <span class="version-dot" aria-hidden="true"></span>
              <span>
                <span class="version-name">${VERSION_LABELS[key]}</span>
                <span class="version-action">${escapeHtml(task.versions[key])}</span>
              </span>
              <span class="version-time">${timeOf(task, key)} 分钟</span>
            </button>`).join("")}
        </div>
        <p class="safety-note">做之前不用准备得完美。累了就选最小版，今天先不做也算照顾自己。</p>
      </div>
      <div class="sheet-footer">
        <span class="sheet-footer-note">选择后，它会安静地出现在你的今天里。</span>
        <button class="primary-button" type="button" data-action="confirm-select">就是这件</button>
      </div>
    </section>`;
}

function renderBrowseSheet() {
  const tasks = state.browseTasks;
  return `
    <section class="sheet" role="dialog" aria-modal="true" aria-label="挑一件小事">
      ${renderSheetHead("不用挑最正确的", "挑一件你愿意做的")}
      <div class="sheet-body">
        <p class="detail-summary">不一定要做最有用的事。选一个现在看到它，身体没有立刻想逃开的就好。</p>
        ${state.browseLoading ? `<div class="suggestions-loading"><span class="loading-mark small">○</span><p>正在把小事摆出来…</p></div>` : `<div class="browse-list">${tasks.map(renderBrowseItem).join("")}</div>`}
      </div>
      <div class="sheet-footer">
        <button class="ghost-button" type="button" data-action="open-custom">自己写一件</button>
        <button class="secondary-button" type="button" data-action="refresh-browse" ${state.browseLoading ? "disabled" : ""}>${state.browseLoading ? "正在换…" : "换一组"}</button>
      </div>
    </section>`;
}

function renderBrowseItem(task) {
  return `
    <button class="browse-item" type="button" data-action="open-task" data-task-id="${task.id}" data-version="${task.suggested_version || "standard"}">
      <span class="browse-swatch" style="--task-color:${escapeAttr(task.color)}" aria-hidden="true"></span>
      <span>
        <span class="browse-title">${escapeHtml(task.title)}</span>
        <span class="browse-sub">${escapeHtml(task.category)} · 约 ${timeOf(task, task.suggested_version || "standard")} 分钟</span>
      </span>
      <span class="browse-arrow" aria-hidden="true">→</span>
    </button>`;
}

function renderCustomSheet() {
  return `
    <section class="sheet" role="dialog" aria-modal="true" aria-label="自己写一件小事">
      ${renderSheetHead("由你来出题", "自己写一件")}
      <form data-form="custom">
        <div class="sheet-body">
          <div class="field">
            <label for="custom-title">想做什么</label>
            <input id="custom-title" name="title" placeholder="比如：给自己买一束花" maxlength="60" required />
          </div>
          <div class="field">
            <label for="custom-summary">给它一句说明</label>
            <input id="custom-summary" name="summary" placeholder="一句就够，可以之后再说" maxlength="160" />
          </div>
          <div class="field">
            <label for="custom-category">它更像哪一类</label>
            <select id="custom-category" name="category">
              ${CATEGORY_OPTIONS.map((category) => `<option value="${category}">${category}</option>`).join("")}
            </select>
          </div>
          <div class="field">
            <label for="custom-minutes">大概需要多久</label>
            <select id="custom-minutes" name="minutes_standard">
              ${[[5, "5 分钟"], [15, "15 分钟"], [30, "30 分钟"], [60, "1 小时"], [120, "2 小时"]].map(([value, label]) => `<option value="${value}" ${value === 15 ? "selected" : ""}>${label}</option>`).join("")}
            </select>
          </div>
          <p class="safety-note">不需要写得像一个目标。它可以是今天突然想做的一件小事。</p>
        </div>
        <div class="sheet-footer">
          <span class="sheet-footer-note">写下来，就不会那么容易溜走。</span>
          <button class="primary-button" type="submit">写好了</button>
        </div>
      </form>
    </section>`;
}

function renderCompleteSheet() {
  const active = state.sheet.active;
  const task = active.task;
  return `
    <section class="sheet" role="dialog" aria-modal="true" aria-label="把这一天收好">
      ${renderSheetHead("把这一天收好", task.title)}
      <form data-form="complete">
        <div class="sheet-body">
          <label class="photo-drop" for="photo-input">
            ${state.sheet.photoData ? `<img class="photo-preview" src="${escapeAttr(state.sheet.photoData)}" alt="已选择的照片预览" /><button class="photo-clear" type="button" data-action="clear-photo">移除照片</button>` : `<span class="photo-placeholder"><strong>放一张照片</strong>不拍也可以。照片会压缩并移除位置信息。</span>`}
            <input id="photo-input" name="photo" type="file" accept="image/*" />
          </label>
          <div class="form-block">
            <p class="prompt-hint">${escapeHtml(task.prompt)}</p>
            <div class="field">
              <label for="entry-note">留一句话 <span>不想写也可以</span></label>
              <textarea id="entry-note" name="note" placeholder="今天发生了什么？只有一个词也可以。" maxlength="1200"></textarea>
            </div>
          </div>
          <div class="form-block">
            <div class="form-block-head"><label>今天的余温</label><span>可选</span></div>
            <div class="mood-grid">
              ${[1, 2, 3, 4, 5].map((mood) => `
                <button class="mood-chip ${state.sheet.mood === mood ? "active" : ""}" type="button" data-action="mood" data-value="${mood}">
                  <span class="mood-dot" aria-hidden="true"></span>
                  <span>${MOOD_LABELS[mood]}</span>
                </button>`).join("")}
            </div>
          </div>
          <div class="form-block">
            <div class="form-block-head"><label>给这件事选一个颜色</label><span>会出现在记忆卡上</span></div>
            <div class="color-grid">
              ${COLORS.map((color) => `<button class="color-swatch ${state.sheet.color === color ? "active" : ""}" type="button" style="--swatch:${color}" data-action="color" data-value="${color}" aria-label="选择颜色 ${color}"></button>`).join("")}
            </div>
          </div>
          <div class="toggle-row">
            <div class="toggle-copy"><strong>还想再做一次</strong><span>只是记下来，不会变成任务</span></div>
            <label class="switch"><input type="checkbox" name="repeat" /><span class="switch-track"></span></label>
          </div>
          <div class="toggle-row">
            <div class="toggle-copy"><strong>只给自己看</strong><span>默认私密；之后可以再决定是否分享</span></div>
            <label class="switch"><input type="checkbox" name="private" checked /><span class="switch-track"></span></label>
          </div>
        </div>
        <div class="sheet-footer">
          <span class="sheet-footer-note">没有照片、没有文字，也可以留下一个颜色。</span>
          <button class="primary-button" type="submit">把这一天收好</button>
        </div>
      </form>
    </section>`;
}

function renderMemorySheet() {
  const memory = state.sheet.memory;
  return `
    <section class="sheet" role="dialog" aria-modal="true" aria-label="${escapeAttr(memory.task.title)}">
      ${renderSheetHead(memory.task.category, memory.task.title)}
      <div class="sheet-body">
        ${memory.photo_url ? `<img class="detail-photo" src="${escapeAttr(memory.photo_url)}" alt="${escapeAttr(memory.task.title)}" />` : `<div class="detail-color" style="--memory-color:${escapeAttr(memory.color)}" aria-hidden="true"></div>`}
        <p class="detail-summary">${formatDate(memory.completed_at, true)} · ${escapeHtml(memory.task.category)}</p>
        <p class="result-note ${memory.note ? "" : "empty"}">${escapeHtml(memory.note || "没有写下什么，但这一天被记住了。")}</p>
        <div class="result-meta">
          ${memory.mood_after ? `<span>余温：${MOOD_LABELS[memory.mood_after]}</span>` : ""}
          ${memory.repeat_wish ? `<span>还想再做</span>` : ""}
          <span class="private-mark">${memory.is_private ? "只给自己看" : "愿意分享"}</span>
        </div>
      </div>
      <div class="sheet-footer">
        <button class="ghost-button" type="button" data-action="view" data-view="memories">回到记忆墙</button>
        <button class="primary-button" type="button" data-action="close-sheet">看完啦</button>
      </div>
    </section>`;
}

async function switchView(view) {
  if (view === "result" && !state.result) view = "today";
  state.view = view;
  if (view === "memories" && !state.memoriesLoaded && !state.memoriesLoading) {
    loadMemories();
  }
  renderApp();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadMemories() {
  state.memoriesLoading = true;
  renderApp();
  try {
    const data = await api("/api/memories?limit=200");
    state.memories = data.memories;
    state.memoriesLoaded = true;
  } catch (error) {
    showToast(error.message);
  } finally {
    state.memoriesLoading = false;
    if (state.view === "memories") renderApp();
  }
}

async function refreshSuggestions() {
  if (state.loadingSuggestions) return;
  const seq = ++state.suggestionSeq;
  const current = state.suggestions ?? state.boot.suggestions ?? [];
  const exclude = current.map((task) => task.id).join(",");
  state.loadingSuggestions = true;
  state.suggestions = [];
  renderApp();
  try {
    const query = new URLSearchParams({
      intent: state.intent,
      energy: String(state.energy),
      limit: "12",
      refresh: "1",
      exclude,
    });
    const data = await api(`/api/tasks?${query.toString()}`);
    if (seq !== state.suggestionSeq) return;
    const activeId = state.boot.active?.task?.id;
    const currentIds = new Set(current.map((task) => task.id));
    const eligible = (data.tasks || []).filter((task) => task.id !== activeId);
    const fresh = eligible.filter((task) => !currentIds.has(task.id));
    const rest = eligible.filter((task) => !fresh.includes(task));
    state.suggestions = [...fresh, ...rest].slice(0, 3);
  } catch (error) {
    if (seq === state.suggestionSeq) showToast(error.message);
  } finally {
    if (seq === state.suggestionSeq) {
      state.loadingSuggestions = false;
      if (state.view === "today") renderApp();
    }
  }
}

async function applyIntent(intent) {
  state.intent = intent;
  await refreshSuggestions();
}

async function applyEnergy(value) {
  state.energy = Number(value);
  localStorage.setItem("yibai.energy", String(state.energy));
  await refreshSuggestions();
}

async function openTask(taskId, extra = {}) {
  let task = findTask(taskId);
  if (!task) {
    try {
      task = await api(`/api/tasks/${taskId}`);
    } catch (error) {
      showToast(error.message);
      return;
    }
  }
  state.sheet = {
    type: "task",
    task,
    version: extra.version || task.suggested_version || "standard",
    intent: extra.intent ?? state.intent,
    reason: extra.reason ?? task.reason ?? "",
  };
  renderSheet();
}

function openMemory(entryId) {
  const memory = findMemory(entryId);
  if (!memory) return;
  state.sheet = { type: "memory", memory };
  renderSheet();
}

async function openBrowse() {
  state.sheet = { type: "browse" };
  state.browseTasks = [];
  renderSheet();
  await loadBrowse(false);
}

async function loadBrowse(refresh) {
  state.browseLoading = true;
  renderSheet();
  try {
    const query = new URLSearchParams({
      intent: state.intent,
      energy: String(state.energy),
      limit: "24",
      refresh: refresh ? "1" : "0",
    });
    if (refresh && state.browseTasks.length) {
      query.set("exclude", state.browseTasks.map((task) => task.id).join(","));
    }
    const data = await api(`/api/tasks?${query.toString()}`);
    const currentIds = new Set(state.browseTasks.map((task) => task.id));
    const fresh = refresh ? (data.tasks || []).filter((task) => !currentIds.has(task.id)) : data.tasks;
    state.browseTasks = fresh?.length ? fresh : data.tasks;
  } catch (error) {
    showToast(error.message);
  } finally {
    state.browseLoading = false;
    if (state.sheet?.type === "browse") renderSheet();
  }
}

function openCustom() {
  state.sheet = { type: "custom" };
  renderSheet();
}

function openComplete() {
  const active = state.boot.active;
  if (!active) {
    showToast("先挑一件小事吧。");
    return;
  }
  state.sheet = {
    type: "complete",
    active,
    photoData: null,
    mood: null,
    color: active.task.color || COLORS[0],
  };
  renderSheet();
}

function closeSheet() {
  state.sheet = null;
  renderSheet();
}

function updatePhotoUI() {
  const drop = document.querySelector(".photo-drop");
  if (!drop) return;
  if (state.sheet.photoData) {
    drop.innerHTML = `
      <img class="photo-preview" src="${escapeAttr(state.sheet.photoData)}" alt="已选择的照片预览" />
      <button class="photo-clear" type="button" data-action="clear-photo">移除照片</button>
      <input id="photo-input" name="photo" type="file" accept="image/*" />`;
  } else {
    drop.innerHTML = `
      <span class="photo-placeholder"><strong>放一张照片</strong>不拍也可以。照片会压缩并移除位置信息。</span>
      <input id="photo-input" name="photo" type="file" accept="image/*" />`;
  }
}

async function selectCurrentTask(button) {
  const sheet = state.sheet;
  if (!sheet || sheet.type !== "task") return;
  if (button) {
    button.disabled = true;
    button.textContent = "放好了…";
  }
  try {
    const data = await api("/api/select", {
      method: "POST",
      body: JSON.stringify({
        task_id: sheet.task.id,
        version: sheet.version || "standard",
        intent: sheet.intent || state.intent || "",
        reason: sheet.reason || "",
      }),
    });
    state.boot.active = data.active;
    const currentSuggestions = state.suggestions ?? state.boot.suggestions ?? [];
    state.suggestions = currentSuggestions.filter((task) => task.id !== sheet.task.id).slice(0, 3);
    state.boot.suggestions = state.suggestions;
    closeSheet();
    state.view = "today";
    renderApp();
    window.scrollTo({ top: 0, behavior: "smooth" });
    showToast("它已经在你的今天里了。去做吧，回来时我还在这里。");
  } catch (error) {
    showToast(error.message);
    if (button) {
      button.disabled = false;
      button.textContent = "就是这件";
    }
  }
}

async function skipActive() {
  const active = state.boot.active;
  if (!active) return;
  try {
    const data = await api(`/api/skip/${active.user_task_id}`, { method: "POST" });
    state.boot.active = null;
    state.suggestions = (data.recommendations || []).slice(0, 3);
    renderApp();
    showToast("那就先放下。不需要为今天解释什么。");
  } catch (error) {
    showToast(error.message);
  }
}

async function submitLogin(form) {
  const submit = form.querySelector('button[type="submit"]');
  if (submit) {
    submit.disabled = true;
    submit.textContent = "正在打开…";
  }
  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ password: form.elements.password.value }),
    });
    state.authenticated = true;
    state.authRequired = true;
    await reloadBootstrap();
    state.view = "today";
    renderApp();
    showToast("欢迎回来。");
  } catch (error) {
    showToast(error.message);
    if (submit) {
      submit.disabled = false;
      submit.textContent = "打开我的小册子";
    }
  }
}

async function submitComplete(form) {
  const sheet = state.sheet;
  if (!sheet || sheet.type !== "complete") return;
  const submit = form.querySelector('button[type="submit"]');
  const note = form.elements.note?.value || "";
  const repeatWish = Boolean(form.elements.repeat?.checked);
  const isPrivate = Boolean(form.elements.private?.checked);
  if (submit) {
    submit.disabled = true;
    submit.textContent = "正在收好…";
  }
  try {
    const data = await api("/api/complete", {
      method: "POST",
      body: JSON.stringify({
        user_task_id: sheet.active.user_task_id,
        note,
        photo_data: sheet.photoData,
        mood_after: sheet.mood,
        color: sheet.color,
        repeat_wish: repeatWish,
        is_private: isPrivate,
        energy: state.energy,
      }),
    });
    state.result = data;
    state.boot.active = null;
    state.boot.stats = data.stats;
    state.boot.memories = [data.memory, ...(state.boot.memories || [])].slice(0, 4);
    state.boot.suggestions = data.recommendations || [];
    state.memories = null;
    state.memoriesLoaded = false;
    state.suggestions = null;
    closeSheet();
    state.view = "result";
    renderApp();
    window.scrollTo({ top: 0, behavior: "smooth" });
    showToast("这一天被你收好了。");
  } catch (error) {
    showToast(error.message);
    if (submit) {
      submit.disabled = false;
      submit.textContent = "把这一天收好";
    }
  }
}

async function submitCustom(form) {
  const submit = form.querySelector('button[type="submit"]');
  if (submit) {
    submit.disabled = true;
    submit.textContent = "正在写下…";
  }
  try {
    const data = await api("/api/custom-task", {
      method: "POST",
      body: JSON.stringify({
        title: form.elements.title.value,
        summary: form.elements.summary.value,
        category: form.elements.category.value,
        minutes_standard: Number(form.elements.minutes_standard.value),
      }),
    });
    state.sheet = {
      type: "task",
      task: data.task,
      version: "standard",
      intent: state.intent,
      reason: "这是你自己写下的事。按你的方式完成它，不用把它变成另一个目标。",
    };
    renderSheet();
    showToast("写好了。要不要现在就去做？");
  } catch (error) {
    showToast(error.message);
    if (submit) {
      submit.disabled = false;
      submit.textContent = "写好了";
    }
  }
}

async function submitSettings(form) {
  const submit = form.querySelector('button[type="submit"]');
  if (submit) {
    submit.disabled = true;
    submit.textContent = "保存中…";
  }
  try {
    const data = await api("/api/profile", {
      method: "POST",
      body: JSON.stringify({
        name: form.elements.name.value,
        target_count: Number(form.elements.target_count.value),
        duration_days: Number(form.elements.duration_days.value),
      }),
    });
    state.boot.profile = data.profile;
    state.boot.stats = data.stats;
    renderApp();
    showToast("设置已经更新。你可以随时再改。");
  } catch (error) {
    showToast(error.message);
  } finally {
    if (submit) {
      submit.disabled = false;
      submit.textContent = "保存设置";
    }
  }
}

async function seedDemo() {
  try {
    const data = await api("/api/demo/seed", { method: "POST" });
    await reloadBootstrap();
    state.memories = null;
    state.memoriesLoaded = false;
    state.view = "memories";
    renderApp();
    await loadMemories();
    showToast(data.seeded ? "这是一些示例记忆。你可以随时清空它们。" : "已经有记忆了。");
  } catch (error) {
    showToast(error.message);
  }
}

async function logout() {
  try {
    await api("/api/logout", { method: "POST" });
  } catch (_) {
    // The local session is cleared even if the request fails.
  }
  state.authenticated = false;
  state.authRequired = true;
  state.boot = null;
  state.sheet = null;
  renderLogin(true);
  showToast("已经退出。");
}

async function resetData() {
  if (!window.confirm("确定要清空所有记录、照片和设置吗？这个动作不能撤回。")) return;
  try {
    await api("/api/demo/reset", { method: "POST" });
    state.result = null;
    state.memories = null;
    state.memoriesLoaded = false;
    state.memoriesLoading = false;
    state.intent = "";
    state.energy = 3;
    localStorage.removeItem("yibai.intent");
    localStorage.removeItem("yibai.energy");
    await reloadBootstrap();
    state.view = "today";
    renderApp();
    showToast("已经清空了。可以重新开始，也可以先休息一下。");
  } catch (error) {
    showToast(error.message);
  }
}

document.addEventListener("click", async (event) => {
  const actionTarget = event.target.closest("[data-action]");
  if (!actionTarget) return;
  const action = actionTarget.dataset.action;
  if (action === "clear-photo") {
    event.preventDefault();
    event.stopPropagation();
    if (state.sheet) {
      state.sheet.photoData = null;
      updatePhotoUI();
    }
    return;
  }
  if (action === "view") {
    closeSheet();
    await switchView(actionTarget.dataset.view);
    return;
  }
  if (action === "intent") {
    await applyIntent(actionTarget.dataset.id);
    return;
  }
  if (action === "energy") {
    await applyEnergy(actionTarget.dataset.value);
    return;
  }
  if (action === "refresh-suggestions") {
    if (!state.loadingSuggestions) await refreshSuggestions();
    return;
  }
  if (action === "open-task") {
    await openTask(actionTarget.dataset.taskId, {
      version: actionTarget.dataset.version,
      reason: actionTarget.dataset.reason,
      intent: actionTarget.dataset.intent,
    });
    return;
  }
  if (action === "open-browse") {
    await openBrowse();
    return;
  }
  if (action === "refresh-browse") {
    if (!state.browseLoading) await loadBrowse(true);
    return;
  }
  if (action === "open-custom") {
    openCustom();
    return;
  }
  if (action === "open-complete") {
    openComplete();
    return;
  }
  if (action === "open-memory") {
    openMemory(actionTarget.dataset.entryId);
    return;
  }
  if (action === "choose-version") {
    if (state.sheet?.type === "task") {
      state.sheet.version = actionTarget.dataset.version;
      renderSheet();
    }
    return;
  }
  if (action === "confirm-select") {
    await selectCurrentTask(actionTarget);
    return;
  }
  if (action === "skip-active") {
    await skipActive();
    return;
  }
  if (action === "mood") {
    if (state.sheet?.type === "complete") {
      state.sheet.mood = Number(actionTarget.dataset.value);
      document.querySelectorAll(".mood-chip").forEach((button) => {
        button.classList.toggle("active", Number(button.dataset.value) === state.sheet.mood);
      });
    }
    return;
  }
  if (action === "color") {
    if (state.sheet?.type === "complete") {
      state.sheet.color = actionTarget.dataset.value;
      document.querySelectorAll(".color-swatch").forEach((button) => {
        button.classList.toggle("active", button.dataset.value === state.sheet.color);
      });
    }
    return;
  }
  if (action === "close-sheet") {
    closeSheet();
    return;
  }
  if (action === "seed-demo") {
    await seedDemo();
    return;
  }
  if (action === "reset-data") {
    await resetData();
    return;
  }
  if (action === "logout") {
    await logout();
    return;
  }
  if (action === "retry-boot") {
    init();
  }
});

async function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("这张图片没有读出来，再试一次吧。"));
    reader.readAsDataURL(file);
  });
}

async function preparePhoto(file) {
  if (!file.type.startsWith("image/")) {
    throw new Error("请选择一张图片。");
  }
  if (file.size > 15 * 1024 * 1024) {
    throw new Error("图片超过 15MB，换一张小一点的吧。");
  }
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("浏览器无法处理这张图片");
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    if (typeof bitmap.close === "function") bitmap.close();
    const dataUrl = canvas.toDataURL("image/jpeg", 0.82);
    if (dataUrl.length > 3.8 * 1024 * 1024) {
      throw new Error("照片压缩后还是有点大，请换一张小一点的。");
    }
    return dataUrl;
  } catch (error) {
    if (file.size > 3 * 1024 * 1024) {
      throw error instanceof Error ? error : new Error("这张图片暂时无法处理");
    }
    return fileToDataUrl(file);
  }
}

document.addEventListener("change", async (event) => {
  if (event.target.id === "photo-input") {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const dataUrl = await preparePhoto(file);
      if (!state.sheet || state.sheet.type !== "complete") return;
      state.sheet.photoData = dataUrl;
      updatePhotoUI();
    } catch (error) {
      showToast(error.message || "这张图片暂时无法处理，请换一张试试。");
      event.target.value = "";
    }
  }
});

document.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (form.dataset.form === "login") await submitLogin(form);
  if (form.dataset.form === "complete") await submitComplete(form);
  if (form.dataset.form === "custom") await submitCustom(form);
  if (form.dataset.form === "settings") await submitSettings(form);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.sheet) closeSheet();
});

init();
