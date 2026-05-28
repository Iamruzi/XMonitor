const state = {
  token: localStorage.getItem("monitorAdminToken") || "",
  config: null,
  targets: [],
  groups: [],
  events: [],
  insights: null,
  pollQueue: null,
  resolvedUser: null,
  activeView: "overview",
  selectedGroup: "",
  radarMode: "projects",
  eventPage: 1,
  eventPageSize: 12,
  nextPollAt: null,
  lastConfigRefreshAt: 0,
  configRefreshInFlight: false,
  toastVersion: 0,
};

const $ = (id) => document.getElementById(id);

const EVENT_LABELS = {
  all: "全部",
  tweet: "原创发推",
  retweet: "转推",
  reply: "回复",
  following: "新增关注",
  test: "测试通知",
};

class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function headers() {
  const result = { "Content-Type": "application/json" };
  if (state.token) {
    try {
      result["X-Admin-Token"] = encodeURIComponent(state.token);
    } catch (_) {
      throw new ApiError("管理密钥包含无效字符，请重新输入", 401);
    }
  }
  return result;
}

async function api(path, options = {}) {
  const { timeoutMs = 15000, headers: optionHeaders = {}, ...fetchOptions } = options;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      ...fetchOptions,
      signal: fetchOptions.signal || controller.signal,
      headers: { ...headers(), ...optionHeaders },
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch (_) {
        // Keep default error text.
      }
      throw new ApiError(detail, response.status);
    }
    return response.json();
  } catch (error) {
    if (error.name === "AbortError") {
      throw new ApiError("请求超时，请稍后重试", 0);
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

function isInvalidTokenError(error) {
  return Boolean(error && error.status === 401);
}

function clearStoredToken() {
  localStorage.removeItem("monitorAdminToken");
  state.token = "";
}

function setStatus(message, isError = false) {
  const line = $("statusLine");
  if (line) {
    line.textContent = message || "";
    line.classList.toggle("error", isError);
  }
  showToast(message, isError);
}

function setLoginStatus(message, isError = false) {
  $("loginStatus").textContent = message || "";
  $("loginStatus").classList.toggle("error", isError);
  showToast(message, isError);
}

function showToast(message, isError = false) {
  if (!message) return;
  const stack = $("toastStack");
  if (!stack) return;
  state.toastVersion += 1;
  const toast = document.createElement("div");
  toast.className = `toast ${isError ? "error" : ""}`;
  toast.setAttribute("role", isError ? "alert" : "status");

  const title = document.createElement("strong");
  title.textContent = isError ? "操作失败" : "操作反馈";
  const body = document.createElement("span");
  body.textContent = String(message);
  toast.append(title, body);

  const dismiss = () => {
    toast.classList.add("leaving");
    window.setTimeout(() => toast.remove(), 180);
  };
  toast.addEventListener("click", dismiss);
  stack.appendChild(toast);
  while (stack.children.length > 4) {
    stack.firstElementChild?.remove();
  }
  window.requestAnimationFrame(() => toast.classList.add("show"));
  window.setTimeout(dismiss, isError ? 5200 : 3200);
}

function toastButtonFeedback(button) {
  const text = (button.dataset.toast || button.textContent || button.title || "").trim();
  if (!text) return;
  const previousToastVersion = state.toastVersion;
  window.setTimeout(() => {
    if (state.toastVersion !== previousToastVersion) return;
    showToast(`${text} 已触发`);
  }, 80);
}

function showApp() {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
}

function showLogin() {
  $("appView").classList.add("hidden");
  $("loginView").classList.remove("hidden");
}

function fmt(value) {
  if (!value) return "从未检查";
  return String(value).replace("T", " ").replace("Z", "");
}

function fmtCompact(value) {
  if (!value) return "未记录";
  return fmt(value).slice(0, 16);
}

function isDue(value) {
  if (!value) return false;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) && parsed <= Date.now();
}

function numberText(value) {
  return Number(value || 0).toLocaleString();
}

function clip(text, limit = 160) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `${normalized.slice(0, limit - 3)}...` : normalized;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function boolText(value) {
  return value ? "开启" : "暂停";
}

function targetDisplay(target) {
  const displayName = target.display_name || target.target_name || "";
  const handle = target.handle || target.target_handle || "unknown";
  if (displayName && displayName !== handle && displayName !== `@${handle}`) {
    return `${displayName}（@${handle}）`;
  }
  return `@${handle}`;
}

function targetIdentity(target) {
  const parts = [];
  const groupName = target.group_name || target.target_group_name || "";
  const remarkName = target.remark_name || target.target_remark_name || "";
  if (groupName) parts.push(groupName);
  if (remarkName) parts.push(remarkName);
  parts.push(targetDisplay(target));
  return parts.join("｜");
}

function taskTypeText(value) {
  if (value === "following") return "关注";
  if (value === "tweets") return "推文";
  return "任务";
}

function taskStateText(task) {
  if (task.status === "running") return "运行中";
  if (task.lastError) return `异常后等待 ${fmtCompact(task.runAfter)}`;
  if (isDue(task.runAfter)) return "待执行";
  return `下次 ${fmtCompact(task.runAfter)}`;
}

function pollTaskLine(tasks) {
  if (!tasks || !tasks.length) return "调度队列待同步";
  return tasks
    .map((task) => `${taskTypeText(task.taskType)} ${taskStateText(task)}`)
    .join(" · ");
}

function groupNames() {
  return [...new Set([
    ...state.groups.map((group) => group.name),
    ...state.targets.map((target) => target.group_name),
  ].filter(Boolean))].sort();
}

function setView(view) {
  state.activeView = view;
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  ["overview", "groups", "projects"].forEach((name) => {
    const pane = $(`${name}View`);
    if (pane) pane.classList.toggle("hidden", name !== view);
  });
}

function pollDelayText(value) {
  if (value === null || value === undefined) return "等待首轮";
  return `${value} 秒`;
}

async function refreshConfigAfterCountdown() {
  if (!state.token || state.configRefreshInFlight || Date.now() - state.lastConfigRefreshAt < 5000) return;
  state.configRefreshInFlight = true;
  state.lastConfigRefreshAt = Date.now();
  try {
    await loadConfig();
  } catch (_) {
    // Keep countdown display stable when a transient request fails.
  } finally {
    state.configRefreshInFlight = false;
  }
}

function updateCountdown() {
  if (!state.nextPollAt) {
    $("nextPoll").textContent = pollDelayText(state.config?.nextPollDelaySeconds);
    return;
  }
  const remaining = Math.max(Math.ceil((new Date(state.nextPollAt).getTime() - Date.now()) / 1000), 0);
  if (remaining <= 0) {
    $("nextPoll").textContent = "正在检查...";
    refreshConfigAfterCountdown();
    return;
  }
  $("nextPoll").textContent = `${remaining} 秒`;
}

function behaviorPayloadFromRow(row) {
  return {
    group_name: row.querySelector('[data-field="group_name"]').value,
    remark_name: row.querySelector('[data-field="remark_name"]').value,
    enabled: row.querySelector('[data-field="enabled"]').checked,
    monitor_tweets: row.querySelector('[data-field="monitor_tweets"]').checked,
    monitor_retweets: row.querySelector('[data-field="monitor_retweets"]').checked,
    monitor_replies: row.querySelector('[data-field="monitor_replies"]').checked,
    monitor_following: row.querySelector('[data-field="monitor_following"]').checked,
    tweet_fetch_count: Number(row.querySelector('[data-field="tweet_fetch_count"]').value || 10),
    following_fetch_count: Number(row.querySelector('[data-field="following_fetch_count"]').value || 40),
  };
}

async function loadConfig() {
  state.config = await api("/api/config");
  const stats = state.config.stats || {};
  $("metricTargets").textContent = stats.targets || 0;
  $("metricEnabled").textContent = stats.enabledTargets || 0;
  $("metricEvents").textContent = stats.events || 0;

  const notification = state.config.notification || {};
  const channels = [];
  if (notification.telegramConfigured) channels.push("Telegram");
  if (notification.wxpusherConfigured) channels.push(notification.wxpusherEnabled ? "WxPusher" : "WxPusher(暂停)");
  if (notification.barkConfigured) channels.push(notification.barkEnabled ? "Bark" : "Bark(暂停)");
  $("metricTelegram").textContent = channels.length ? channels.join(" + ") : "未配置";

  const minPoll = state.config.pollIntervalMinSeconds || state.config.pollIntervalSeconds || 300;
  const maxPoll = state.config.pollIntervalMaxSeconds || state.config.pollIntervalSeconds || 300;
  $("pollWindow").textContent = `${minPoll}-${maxPoll} 秒随机`;
  $("pollMinSeconds").value = minPoll;
  $("pollMaxSeconds").value = maxPoll;
  $("pollBackoffSeconds").value = state.config.pollBackoffMaxSeconds || Math.max(maxPoll, 1800);
  state.nextPollAt = state.config.nextPollDelaySeconds === null || state.config.nextPollDelaySeconds === undefined
    ? null
    : Date.now() + Number(state.config.nextPollDelaySeconds) * 1000;
  updateCountdown();

  $("targetCount").textContent = `${stats.targets || 0} 个`;
  renderSettings(notification);
}

function targetRow(target) {
  const tr = document.createElement("tr");
  tr.dataset.targetId = target.id;
  tr.innerHTML = `
    <td>
      <strong>${escapeHtml(target.display_name || target.handle)}</strong>
      ${target.last_error ? `<small class="error">${escapeHtml(clip(target.last_error, 90))}</small>` : ""}
    </td>
    <td><input class="mini-text" data-field="group_name" type="text" list="groupOptions" value="${escapeHtml(target.group_name || "")}" placeholder="分组" /></td>
    <td><input class="mini-text" data-field="remark_name" type="text" value="${escapeHtml(target.remark_name || "")}" placeholder="备注名" /></td>
    <td><a href="https://x.com/${escapeHtml(target.handle)}" target="_blank" rel="noreferrer">@${escapeHtml(target.handle)}</a></td>
    <td><label class="mini-check"><input data-field="enabled" type="checkbox" ${target.enabled ? "checked" : ""} />${boolText(target.enabled)}</label></td>
    <td><input data-field="monitor_tweets" type="checkbox" ${target.monitor_tweets ? "checked" : ""} /></td>
    <td><input data-field="monitor_retweets" type="checkbox" ${target.monitor_retweets ? "checked" : ""} /></td>
    <td><input data-field="monitor_replies" type="checkbox" ${target.monitor_replies ? "checked" : ""} /></td>
    <td><input data-field="monitor_following" type="checkbox" ${target.monitor_following ? "checked" : ""} /></td>
    <td><input class="mini-number" data-field="tweet_fetch_count" type="number" min="1" max="200" value="${Number(target.tweet_fetch_count || 10)}" /></td>
    <td><input class="mini-number" data-field="following_fetch_count" type="number" min="1" max="200" value="${Number(target.following_fetch_count || 40)}" /></td>
    <td>
      <span>${escapeHtml(fmt(target.last_checked_at))}</span>
      <small>推文${target.tweets_initialized ? "已记录" : "待记录"} · 关注${target.following_initialized ? "已记录" : "待记录"}</small>
      <small class="poll-task-line">${escapeHtml(pollTaskLine(target.pollTasks || []))}</small>
    </td>
    <td>
      <div class="row-actions">
        <button data-action="save" class="primary" type="button">保存</button>
        <button data-action="poll" type="button">检查</button>
        <button data-action="backfill" type="button" title="按首次补齐数量重新采集关注，不推送历史事件">补齐</button>
        <button data-action="delete" class="danger" type="button">删除</button>
      </div>
    </td>
  `;
  tr.querySelector('[data-action="save"]').addEventListener("click", async () => {
    try {
      await api(`/api/targets/${target.id}`, {
        method: "PATCH",
        body: JSON.stringify(behaviorPayloadFromRow(tr)),
      });
      await refreshAll();
      setStatus(`已保存 @${target.handle}`);
    } catch (error) {
      setStatus(error.message, true);
    }
  });
  tr.querySelector('[data-action="poll"]').addEventListener("click", async () => {
    try {
      setStatus(`正在检查 @${target.handle}...`);
      await api(`/api/targets/${target.id}/poll`, { method: "POST" });
      await refreshAll();
      setStatus(`已检查 @${target.handle}`);
    } catch (error) {
      setStatus(error.message, true);
    }
  });
  tr.querySelector('[data-action="backfill"]').addEventListener("click", async () => {
    try {
      setStatus(`正在补齐 @${target.handle} 的关注关系...`);
      const payload = await api(`/api/targets/${target.id}/following/backfill`, { method: "POST" });
      const data = payload.data || {};
      if (data.error) throw new Error(data.error);
      setStatus(
        `已补齐 @${data.handle || target.handle}：拉取 ${Number(data.fetchedFollowing || 0)} 个，` +
        `新增 ${Number(data.backfilledFollowing || 0)} 个，共同 ${Number(data.sharedMatches || 0)} 个，` +
        `项目 ${Number(data.projectMatches || 0)} 个`
      );
      await refreshAll();
    } catch (error) {
      setStatus(error.message, true);
    }
  });
  tr.querySelector('[data-action="delete"]').addEventListener("click", async () => {
    if (!confirm(`删除 @${target.handle} 的监控？`)) {
      showToast("已取消删除");
      return;
    }
    try {
      await api(`/api/targets/${target.id}`, { method: "DELETE" });
      await refreshAll();
      setStatus(`已删除 @${target.handle}`);
    } catch (error) {
      setStatus(error.message, true);
    }
  });
  return tr;
}

async function loadTargets() {
  const payload = await api("/api/targets");
  state.targets = payload.data || [];
  const body = $("targetsTableBody");
  body.innerHTML = "";
  if (!state.targets.length) {
    body.innerHTML = '<tr><td colspan="13" class="empty">还没有监控用户</td></tr>';
  } else {
    state.targets.forEach((target) => body.appendChild(targetRow(target)));
  }
  renderTargetFilter();
  renderGroupFilter();
}

function renderTargetFilter() {
  const select = $("eventTargetFilter");
  const current = select.value || "all";
  select.innerHTML = '<option value="all">全部用户</option>';
  state.targets.forEach((target) => {
    const option = document.createElement("option");
    option.value = target.handle;
    option.textContent = targetIdentity(target);
    select.appendChild(option);
  });
  select.value = [...select.options].some((option) => option.value === current) ? current : "all";
}

function renderGroupFilter() {
  const select = $("eventGroupFilter");
  const current = select.value || "all";
  const groups = groupNames();
  select.innerHTML = '<option value="all">全部分组</option>';
  groups.forEach((groupName) => {
    const option = document.createElement("option");
    option.value = groupName;
    option.textContent = groupName;
    select.appendChild(option);
  });
  select.value = [...select.options].some((option) => option.value === current) ? current : "all";
  renderGroupOptions();
}

function renderGroupOptions() {
  const datalist = $("groupOptions");
  if (!datalist) return;
  datalist.innerHTML = "";
  groupNames().forEach((groupName) => {
    const option = document.createElement("option");
    option.value = groupName;
    datalist.appendChild(option);
  });
}

function renderTypeFilter() {
  const select = $("eventTypeFilter");
  const current = select.value || "all";
  select.innerHTML = "";
  Object.entries(EVENT_LABELS).forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  });
  select.value = [...select.options].some((option) => option.value === current) ? current : "all";
}

function eventMatches(event) {
  const group = $("eventGroupFilter").value;
  const target = $("eventTargetFilter").value;
  const type = $("eventTypeFilter").value;
  const notify = $("eventNotifyFilter").value;
  const keyword = $("eventSearch").value.trim().toLowerCase();
  if (group !== "all" && event.target_group_name !== group) return false;
  if (target !== "all" && event.target_handle !== target) return false;
  if (type !== "all" && event.event_type !== type) return false;
  if (notify === "notified" && !event.notified_at) return false;
  if (notify === "pending" && event.notified_at) return false;
  if (notify === "error" && !event.notification_error) return false;
  if (!keyword) return true;
  return `${event.title} ${event.body} ${targetIdentity(event)}`.toLowerCase().includes(keyword);
}

function eventCard(event) {
  const card = document.createElement("article");
  card.className = "event-card";
  const label = EVENT_LABELS[event.event_type] || "动态";
  const statusClass = event.notification_error ? "error" : event.notified_at ? "ok" : "neutral";
  const statusText = event.notification_error ? "通知异常" : event.notified_at ? "已通知" : "未通知";
  const safeUrl = escapeHtml(event.url || "");
  const source = targetIdentity(event);
  card.innerHTML = `
    <div class="event-top">
      <span class="type-pill ${event.event_type}">${label}</span>
      <span class="event-state ${statusClass}">${statusText}</span>
    </div>
    <div class="event-title">${escapeHtml(event.title)}</div>
    <div class="event-meta">${escapeHtml(source)} · ${escapeHtml(fmt(event.detected_at))}</div>
    <p class="event-body">${escapeHtml(clip(event.body, 280))}</p>
    ${event.url ? `<p class="event-meta"><a href="${safeUrl}" target="_blank" rel="noreferrer">${safeUrl}</a></p>` : ""}
    ${event.notification_error ? `<div class="event-meta error">${escapeHtml(event.notification_error)}</div>` : ""}
  `;
  return card;
}

function renderEvents() {
  const events = state.events.filter(eventMatches);
  const totalPages = Math.max(Math.ceil(events.length / state.eventPageSize), 1);
  state.eventPage = Math.min(Math.max(state.eventPage, 1), totalPages);
  const start = (state.eventPage - 1) * state.eventPageSize;
  const pageItems = events.slice(start, start + state.eventPageSize);
  $("eventCount").textContent = `${events.length} / ${state.events.length} 条`;
  const list = $("eventsList");
  list.innerHTML = "";
  if (!pageItems.length) {
    list.innerHTML = '<div class="event-card empty">没有匹配的事件</div>';
  } else {
    pageItems.forEach((event) => list.appendChild(eventCard(event)));
  }
  $("eventPageInfo").textContent = `第 ${state.eventPage} / ${totalPages} 页`;
  $("eventPrev").disabled = state.eventPage <= 1;
  $("eventNext").disabled = state.eventPage >= totalPages;
}

async function loadEvents() {
  const payload = await api("/api/events?limit=300");
  state.events = payload.data || [];
  renderTypeFilter();
  renderEvents();
}

function renderPollTasks() {
  const data = state.pollQueue || { summary: {}, tasks: [] };
  const summary = data.summary || {};
  const tasks = data.tasks || [];
  $("metricPollDue").textContent = numberText(summary.due || 0);
  $("metricPollGuard").textContent = `${numberText(summary.running || 0)} / ${numberText(summary.errors || 0)}`;
  $("pollQueueCount").textContent = `${numberText(summary.total || 0)} 个任务`;
  const list = $("pollTaskList");
  list.innerHTML = "";
  if (!tasks.length) {
    list.innerHTML = '<div class="task-item"><strong>暂无调度任务</strong><span>等待后台同步监控用户</span></div>';
    return;
  }
  tasks.slice(0, 8).forEach((task) => {
    const item = document.createElement("div");
    item.className = `task-item ${task.status === "running" ? "running" : ""} ${task.lastError ? "error" : ""}`;
    const target = task.targetLabel || targetIdentity({
      group_name: task.targetGroupName,
      remark_name: task.targetRemarkName,
      display_name: task.targetDisplayName,
      handle: task.targetHandle,
    });
    item.innerHTML = `
      <strong>${escapeHtml(taskTypeText(task.taskType))} · ${escapeHtml(target)}</strong>
      <span>${escapeHtml(taskStateText(task))}</span>
      ${task.lastError ? `<span>${escapeHtml(clip(task.lastError, 80))}</span>` : ""}
    `;
    list.appendChild(item);
  });
}

async function loadPollTasks() {
  const payload = await api("/api/poll-tasks?limit=12");
  state.pollQueue = payload.data || { summary: {}, tasks: [] };
  renderPollTasks();
}

function targetBriefLabel(target) {
  const handle = target.handle || "unknown";
  const display = target.displayName || handle;
  const group = target.groupName || "未分组";
  const prefix = [group, target.remarkName].filter(Boolean).join("｜");
  return `${prefix ? `${prefix}｜` : ""}${display}（@${handle}）`;
}

function evidenceRows(followedBy) {
  return (followedBy || []).map((target) => {
    const handle = target.handle || "unknown";
    const display = target.displayName || handle;
    const group = target.groupName || "未分组";
    const remark = target.remarkName || "无备注";
    const firstSeen = fmt(target.firstSeenAt);
    return `
      <div class="evidence-row">
        <span class="evidence-group">分组 ${escapeHtml(group)}</span>
        <span class="evidence-remark">备注 ${escapeHtml(remark)}</span>
        <strong>${escapeHtml(display)}</strong>
        <a href="https://x.com/${escapeHtml(handle)}" target="_blank" rel="noreferrer">@${escapeHtml(handle)}</a>
        <time title="${escapeHtml(firstSeen)}">关注 ${escapeHtml(firstSeen)}</time>
      </div>
    `;
  }).join("");
}

function signalTags(project) {
  return (project.discoverySignals || []).slice(0, 5).map((signal) => (
    `<span class="project-tag signal">${escapeHtml(signal)}</span>`
  )).join("");
}

function hunterSignalTags(candidate) {
  return (candidate.hunterSignals || []).slice(0, 5).map((signal) => (
    `<span class="project-tag hunter">${escapeHtml(signal)}</span>`
  )).join("");
}

function timeValue(value) {
  if (!value) return 0;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : 0;
}

function projectLatestFollowTime(project) {
  return Math.max(
    timeValue(project.lastSeenAt),
    ...(project.followedBy || []).map((target) => timeValue(target.firstSeenAt))
  );
}

function projectEarliestFollowTime(project) {
  const values = [
    timeValue(project.firstSeenAt),
    ...(project.followedBy || []).map((target) => timeValue(target.firstSeenAt)),
  ].filter(Boolean);
  return values.length ? Math.min(...values) : 0;
}

function projectHeat(project) {
  const common = Number(project.commonCount || 0);
  if (!project.isProject) {
    return {
      className: "",
      level: "",
      commonText: `共同 ${common} 人`,
      fireText: "",
      show: false,
    };
  }
  if (common <= 2) {
    return {
      className: "attention",
      level: "注意",
      commonText: `共同 ${common} 人`,
      fireText: "",
      show: true,
    };
  }
  if (common <= 5) {
    const fireText = "🔥".repeat(common - 2);
    return {
      className: "hot",
      level: "火爆",
      commonText: `${fireText} 共同 ${common} 人`,
      fireText,
      show: true,
    };
  }
  return {
    className: "super",
    level: "超级火爆",
    commonText: `🔥 ${common} 人共同关注`,
    fireText: `🔥 ${common}`,
    show: true,
  };
}

function sortedProjectTimeline(followedBy) {
  return [...(followedBy || [])].sort((a, b) => {
    const diff = timeValue(a.firstSeenAt) - timeValue(b.firstSeenAt);
    if (diff) return diff;
    return String(a.handle || "").localeCompare(String(b.handle || ""));
  });
}

function projectTimeline(project) {
  if (!project.isProject) return "";
  const timeline = sortedProjectTimeline(project.followedBy);
  if (!timeline.length) return "";
  const earliest = projectEarliestFollowTime(project);
  const latest = projectLatestFollowTime(project);
  const earliestText = earliest ? fmt(new Date(earliest).toISOString().replace(".000Z", "Z")) : "";
  const latestText = latest ? fmt(new Date(latest).toISOString().replace(".000Z", "Z")) : "";
  const meta = earliest && latest
    ? `最早 ${earliestText} · 最近 ${latestText}`
    : "按发现关注的时间排序";
  return `
    <div class="project-timeline">
      <div class="timeline-head">
        <strong>关注时间线</strong>
        <span>${escapeHtml(meta)}</span>
      </div>
      <div class="timeline-items">
        ${timeline.map((target, index) => {
          const firstSeen = fmt(target.firstSeenAt);
          const label = targetBriefLabel(target);
          const group = target.groupName || "未分组";
          const remark = target.remarkName || "无备注";
          return `
            <div class="timeline-item">
              <i>${index + 1}</i>
              <time title="${escapeHtml(firstSeen)}">${escapeHtml(firstSeen)}</time>
              <span>${escapeHtml(group)} · ${escapeHtml(remark)} · ${escapeHtml(label)}</span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

function projectEvidenceText(project) {
  return (project.followedBy || []).map((target) => [
    target.groupName || "未分组",
    target.remarkName || "",
    target.displayName || "",
    target.handle || "",
    target.firstSeenAt || "",
  ].join(" ")).join(" ");
}

function hunterMatches(candidate) {
  const keyword = $("projectSearch").value.trim().toLowerCase();
  if (!keyword) return true;
  const followedBy = (candidate.followedBy || []).map(targetBriefLabel).join(" ");
  return [
    candidate.name,
    candidate.handle,
    candidate.summary,
    candidate.reason,
    candidate.hunterConfidence,
    candidate.recommendation,
    (candidate.hunterSignals || []).join(" "),
    (candidate.hunterMatchedTerms || []).join(" "),
    followedBy,
    projectEvidenceText(candidate),
  ].join(" ").toLowerCase().includes(keyword);
}

function ageText(days) {
  if (days === null || days === undefined || days === "") return "";
  const value = Number(days);
  if (!Number.isFinite(value)) return "";
  if (value < 30) return `${value} 天账号`;
  if (value < 365) return `${Math.floor(value / 30)} 个月账号`;
  return `${Math.floor(value / 365)} 年账号`;
}

function miniProjectItem(project) {
  const item = document.createElement("article");
  item.className = `mini-project ${project.isProject ? "project" : ""}`;
  item.innerHTML = `
    <div>
      <strong>${escapeHtml(project.name || project.handle)}</strong>
      <span>@${escapeHtml(project.handle)} · 共同 ${Number(project.commonCount || 0)} 人</span>
    </div>
    <span class="project-tag">${escapeHtml(project.category || "账号")}</span>
  `;
  item.addEventListener("click", () => {
    setView("projects");
    $("projectSearch").value = project.handle || project.name || "";
    renderProjects();
  });
  return item;
}

function groupCard(group) {
  const card = document.createElement("article");
  card.className = `group-card ${state.selectedGroup === group.name ? "active" : ""}`;
  card.tabIndex = 0;
  card.innerHTML = `
    <div class="group-card-top">
      <strong>${escapeHtml(group.name)}</strong>
      <span>${Number(group.enabledCount || 0)} / ${Number(group.targetCount || 0)} 运行</span>
    </div>
    <div class="group-card-metrics">
      <span>采集 ${numberText(group.followingAccounts)} 个关注</span>
      <span>共同 ${numberText(group.sharedAccounts)} 个</span>
      <span>项目 ${numberText(group.projectAccounts)} 个</span>
    </div>
  `;
  const select = () => {
    state.selectedGroup = group.name;
    renderGroupCards();
  };
  card.addEventListener("click", select);
  card.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    select();
  });
  return card;
}

function renderGroupCards() {
  const list = $("groupCards");
  if (!list) return;
  const groups = state.insights?.groups || [];
  $("groupInsightCount").textContent = `${groups.length} 组`;
  if (!state.selectedGroup || !groups.some((group) => group.name === state.selectedGroup)) {
    state.selectedGroup = groups[0]?.name || "";
  }
  list.innerHTML = "";
  if (!groups.length) {
    list.innerHTML = '<div class="group-empty">还没有可展示的分组</div>';
    renderGroupDetail();
    return;
  }
  groups.forEach((group) => list.appendChild(groupCard(group)));
  renderGroupDetail();
}

function renderGroupDetail() {
  const groups = state.insights?.groups || [];
  const group = groups.find((item) => item.name === state.selectedGroup);
  if (!group) {
    $("groupDetailTitle").textContent = "选择分组";
    $("groupDetailMeta").textContent = "查看这个分组监控的用户和热门关注对象";
    $("groupDetailMembers").innerHTML = '<div class="group-empty">暂无分组数据</div>';
    $("groupDetailProjects").innerHTML = '<div class="group-empty">暂无共同关注</div>';
    return;
  }
  $("groupDetailTitle").textContent = group.name;
  $("groupDetailMeta").textContent =
    `${Number(group.targetCount || 0)} 个用户 · ${Number(group.followingAccounts || 0)} 个已采集关注 · ` +
    `${Number(group.sharedAccounts || 0)} 个共同关注`;

  const members = $("groupDetailMembers");
  members.innerHTML = "";
  if (!group.targets?.length) {
    members.innerHTML = '<div class="group-empty">这个分组还没有监控用户</div>';
  } else {
    group.targets.forEach((target) => {
      const item = document.createElement("article");
      item.className = "member-item";
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(targetBriefLabel(target))}</strong>
          <span>${target.followingInitialized ? "关注已建立基线" : "关注待建立基线"} · ${escapeHtml(fmt(target.lastCheckedAt))}</span>
        </div>
        <a href="https://x.com/${escapeHtml(target.handle)}" target="_blank" rel="noreferrer">@${escapeHtml(target.handle)}</a>
      `;
      members.appendChild(item);
    });
  }

  const projects = $("groupDetailProjects");
  projects.innerHTML = "";
  if (!group.topProjects?.length) {
    projects.innerHTML = '<div class="group-empty">这个分组还没有被多人共同关注的账号</div>';
  } else {
    group.topProjects.forEach((project) => projects.appendChild(miniProjectItem(project)));
  }
}

function renderInsightMetrics() {
  const summary = state.insights?.summary || {};
  $("insightFollowedAccounts").textContent = numberText(summary.followedAccounts);
  $("insightProfiledAccounts").textContent = numberText(summary.profiledAccounts);
  $("insightSharedAccounts").textContent = numberText(summary.sharedAccounts);
  $("insightProjectAccounts").textContent = numberText(summary.projectAccounts);
  $("insightHunterCandidates").textContent = numberText(summary.hunterCandidates);
}

function renderProjectGroupFilter() {
  const select = $("projectGroupFilter");
  if (!select) return;
  const current = select.value || "";
  select.innerHTML = '<option value="">全部分组</option>';
  (state.insights?.groups || [])
    .filter((group) => group.name && group.name !== "未分组")
    .forEach((group) => {
      const option = document.createElement("option");
      option.value = group.name;
      option.textContent = `${group.name}（${Number(group.targetCount || 0)}）`;
      select.appendChild(option);
    });
  select.value = [...select.options].some((option) => option.value === current) ? current : "";
}

function projectMatches(project) {
  if ($("projectOnly").checked && !project.isProject) return false;
  const keyword = $("projectSearch").value.trim().toLowerCase();
  if (!keyword) return true;
  const followedBy = (project.followedBy || []).map(targetBriefLabel).join(" ");
  return [
    project.name,
    project.handle,
    project.summary,
    project.reason,
    project.category,
    project.followerStage,
    (project.discoverySignals || []).join(" "),
    followedBy,
    projectEvidenceText(project),
  ].join(" ").toLowerCase().includes(keyword);
}

function sortProjects(projects) {
  const mode = $("projectSort")?.value || "heat";
  return [...projects].sort((a, b) => {
    if (mode === "timeline") {
      return (
        projectLatestFollowTime(b) - projectLatestFollowTime(a) ||
        Number(b.commonCount || 0) - Number(a.commonCount || 0) ||
        Number(Boolean(b.isProject)) - Number(Boolean(a.isProject)) ||
        String(a.handle || "").localeCompare(String(b.handle || ""))
      );
    }
    return (
      Number(b.commonCount || 0) - Number(a.commonCount || 0) ||
      Number(Boolean(b.isProject)) - Number(Boolean(a.isProject)) ||
      Number(b.earlyScore || 0) - Number(a.earlyScore || 0) ||
      projectLatestFollowTime(b) - projectLatestFollowTime(a) ||
      String(a.handle || "").localeCompare(String(b.handle || ""))
    );
  });
}

function sortHunters(candidates) {
  return [...candidates].sort((a, b) => (
    Number(b.hunterScore || 0) - Number(a.hunterScore || 0) ||
    Number(b.commonCount || 0) - Number(a.commonCount || 0) ||
    Number(b.groupCount || 0) - Number(a.groupCount || 0) ||
    projectLatestFollowTime(b) - projectLatestFollowTime(a) ||
    String(a.handle || "").localeCompare(String(b.handle || ""))
  ));
}

function projectCard(project) {
  const card = document.createElement("article");
  const heat = projectHeat(project);
  card.className = `project-card ${heat.className}`;
  const profileUrl = `https://x.com/${encodeURIComponent(project.handle || "")}`;
  const followedBy = project.followedBy || [];
  const accountAge = ageText(project.accountAgeDays);
  card.innerHTML = `
    <div class="project-card-head">
      <div>
        <strong>${escapeHtml(project.name || project.handle)}</strong>
        <a href="${profileUrl}" target="_blank" rel="noreferrer">@${escapeHtml(project.handle)}</a>
      </div>
      <span class="common-badge ${heat.className}">${escapeHtml(heat.commonText)}</span>
    </div>
    <div class="project-tags">
      <span class="project-tag ${project.isProject ? "hot" : ""}">${escapeHtml(project.category || "账号")}</span>
      ${heat.show ? `<span class="project-tag heat ${heat.className}">${escapeHtml(heat.level)}</span>` : ""}
      ${Number(project.commonCount || 0) > 5 ? '<span class="project-tag heat super">更多关注</span>' : ""}
      ${project.verified ? '<span class="project-tag">已认证</span>' : ""}
      <span class="project-tag">粉丝 ${numberText(project.followers)}</span>
      <span class="project-tag">早期分 ${Number(project.earlyScore || 0)}</span>
      ${project.followerStage ? `<span class="project-tag">${escapeHtml(project.followerStage)}</span>` : ""}
      ${accountAge ? `<span class="project-tag">${escapeHtml(accountAge)}</span>` : ""}
      ${signalTags(project)}
    </div>
    <p>${escapeHtml(project.summary || "")}</p>
    <div class="project-reason">${escapeHtml(project.reason || "")}</div>
    ${projectTimeline(project)}
    <div class="evidence-list">${evidenceRows(followedBy)}</div>
    ${project.url ? `<a class="project-url" href="${escapeHtml(project.url)}" target="_blank" rel="noreferrer">${escapeHtml(project.url)}</a>` : ""}
  `;
  return card;
}

function hunterCard(candidate) {
  const card = document.createElement("article");
  card.className = "project-card hunter-card";
  const profileUrl = `https://x.com/${encodeURIComponent(candidate.handle || "")}`;
  const followedBy = candidate.followedBy || [];
  card.innerHTML = `
    <div class="project-card-head">
      <div>
        <strong>${escapeHtml(candidate.name || candidate.handle)}</strong>
        <a href="${profileUrl}" target="_blank" rel="noreferrer">@${escapeHtml(candidate.handle)}</a>
      </div>
      <span class="common-badge hunter">猎手分 ${Number(candidate.hunterScore || 0)}</span>
    </div>
    <div class="project-tags">
      <span class="project-tag hunter">${escapeHtml(candidate.hunterConfidence || "观察")}</span>
      <span class="project-tag">共同 ${Number(candidate.commonCount || 0)} 人</span>
      <span class="project-tag">跨组 ${Number(candidate.groupCount || 0)} 个</span>
      <span class="project-tag">粉丝 ${numberText(candidate.followers)}</span>
      ${candidate.following ? `<span class="project-tag">关注 ${numberText(candidate.following)}</span>` : ""}
      ${hunterSignalTags(candidate)}
    </div>
    <p>${escapeHtml(candidate.summary || "")}</p>
    <div class="project-reason">${escapeHtml(candidate.recommendation || "建议先影子观察")}</div>
    ${projectTimeline(candidate)}
    <div class="evidence-list">${evidenceRows(followedBy)}</div>
    ${candidate.url ? `<a class="project-url" href="${escapeHtml(candidate.url)}" target="_blank" rel="noreferrer">${escapeHtml(candidate.url)}</a>` : ""}
  `;
  return card;
}

function renderProjects() {
  const list = $("projectsList");
  if (!list) return;
  const mode = state.radarMode || "projects";
  const source = mode === "hunters"
    ? (state.insights?.hunterCandidates || [])
    : mode === "all"
      ? (state.insights?.accounts || [])
      : (state.insights?.projectCandidates || state.insights?.projects || []);
  const items = mode === "hunters"
    ? sortHunters(source.filter(hunterMatches))
    : sortProjects(source.filter(projectMatches));
  $("projectCount").textContent = `${items.length} / ${source.length} 个`;
  $("projectOnly").disabled = mode === "hunters";
  document.querySelectorAll("[data-radar-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.radarMode === mode);
  });
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = mode === "hunters"
      ? '<div class="project-empty">还没有满足评分的猎手候选</div>'
      : '<div class="project-empty">没有满足共同关注人数的项目候选</div>';
    return;
  }
  items.forEach((item) => list.appendChild(mode === "hunters" ? hunterCard(item) : projectCard(item)));
}

async function loadInsights() {
  const params = new URLSearchParams({
    min_common: String(Math.max(Number($("projectMinCommon")?.value || 2), 2)),
    limit: "200",
    compact: "1",
  });
  const group = $("projectGroupFilter")?.value || "";
  if (group) params.set("group", group);
  const payload = await api(`/api/following-insights?${params.toString()}`, { timeoutMs: 60000 });
  state.insights = payload.data || {
    summary: {},
    groups: [],
    accounts: [],
    projects: [],
    projectCandidates: [],
    hunterCandidates: [],
  };
  renderInsightMetrics();
  renderProjectGroupFilter();
  renderGroupCards();
  renderProjects();
}

function recipientRow({ id = "", title = "", primary = false } = {}) {
  const row = document.createElement("div");
  row.className = "editable-row";
  row.innerHTML = `
    <input data-field="id" type="text" value="${escapeHtml(id)}" placeholder="聊天 ID" ${primary ? "data-primary='true'" : ""} />
    <input data-field="title" type="text" value="${escapeHtml(title)}" placeholder="${primary ? "主聊天" : "备注"}" />
    <button data-action="remove" type="button" ${primary ? "disabled" : ""}>删除</button>
  `;
  row.querySelector('[data-action="remove"]').addEventListener("click", () => {
    row.remove();
    showToast("已删除 Telegram 接收聊天");
  });
  return row;
}

function wxpusherRow(uid = "") {
  const row = document.createElement("div");
  row.className = "editable-row two";
  row.innerHTML = `
    <input data-field="uid" type="text" value="${escapeHtml(uid)}" placeholder="UID_xxx" />
    <button data-action="remove" type="button">删除</button>
  `;
  row.querySelector('[data-action="remove"]').addEventListener("click", () => {
    row.remove();
    showToast("已删除 WxPusher UID");
  });
  return row;
}

function barkDeviceRow(deviceKey = "") {
  const row = document.createElement("div");
  row.className = "editable-row two";
  row.innerHTML = `
    <input data-field="device_key" type="text" value="${escapeHtml(deviceKey)}" placeholder="Bark 设备码" />
    <button data-action="remove" type="button">删除</button>
  `;
  row.querySelector('[data-action="remove"]').addEventListener("click", () => {
    row.remove();
    showToast("已删除 Bark 设备码");
  });
  return row;
}

function groupRow(group) {
  const row = document.createElement("div");
  row.className = "group-row";
  row.innerHTML = `
    <div class="group-summary">
      <strong>${escapeHtml(group.name)}</strong>
      <span>${Number(group.count || 0)} 个用户 · 运行中 ${Number(group.enabledCount || 0)} 个</span>
    </div>
    <input data-field="name" type="text" value="${escapeHtml(group.name)}" />
    <button data-action="rename" class="primary" type="button">改名</button>
    <button data-action="view" type="button">筛选</button>
    <button data-action="clear" class="danger" type="button">清空</button>
  `;
  row.querySelector('[data-action="rename"]').addEventListener("click", async () => {
    const nextName = row.querySelector('[data-field="name"]').value.trim();
    if (!nextName || nextName === group.name) return;
    try {
      await api("/api/groups/rename", {
        method: "POST",
        body: JSON.stringify({ old_name: group.name, new_name: nextName }),
      });
      await refreshAll();
      setStatus(`已把分组「${group.name}」改为「${nextName}」`);
    } catch (error) {
      setStatus(error.message, true);
    }
  });
  row.querySelector('[data-field="name"]').addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    row.querySelector('[data-action="rename"]').click();
  });
  row.querySelector('[data-action="view"]').addEventListener("click", () => {
    $("eventGroupFilter").value = group.name;
    state.eventPage = 1;
    renderEvents();
    $("settingsDrawer").classList.add("hidden");
    showToast(`已筛选分组「${group.name}」`);
  });
  row.querySelector('[data-action="clear"]').addEventListener("click", async () => {
    if (!confirm(`清空分组「${group.name}」？相关用户会变成未分组，但不会删除用户。`)) {
      showToast("已取消清空分组");
      return;
    }
    try {
      await api("/api/groups/clear", {
        method: "POST",
        body: JSON.stringify({ group_name: group.name }),
      });
      await refreshAll();
      setStatus(`已清空分组「${group.name}」`);
    } catch (error) {
      setStatus(error.message, true);
    }
  });
  return row;
}

function renderGroupManager() {
  const list = $("groupManager");
  if (!list) return;
  renderGroupOptions();
  list.innerHTML = "";
  if (!state.groups.length) {
    list.innerHTML = '<div class="group-empty">还没有分组。添加或编辑监控用户时填写“分组”即可创建。</div>';
    return;
  }
  state.groups.forEach((group) => list.appendChild(groupRow(group)));
}

function renderSettings(notification) {
  $("telegramProxy").value = notification.telegramProxy || "";
  $("telegramToken").placeholder = notification.telegramBotTokenSaved
    ? `已保存：${notification.telegramBotTokenPreview}`
    : "";
  $("wxpusherToken").placeholder = notification.wxpusherAppTokenSaved
    ? `已保存：${notification.wxpusherAppTokenPreview}`
    : "";
  $("wxpusherEnabled").checked = notification.wxpusherEnabled !== false;
  $("wxpusherHotFilterEnabled").checked = Boolean(notification.wxpusherHotFilterEnabled);
  $("wxpusherHotFilterMinCommon").value = Number(notification.wxpusherHotFilterMinCommon || 2);
  $("barkEnabled").checked = notification.barkEnabled !== false;
  $("barkServerUrl").value = notification.barkServerUrl || "https://api.day.app";
  $("barkGroup").value = notification.barkGroup || "XMonitor";
  $("barkLevel").value = notification.barkLevel || "active";
  $("barkSound").value = notification.barkSound || "";
  $("barkCall").checked = Boolean(notification.barkCall);
  $("barkVolume").value = Number(notification.barkVolume ?? 5);
  $("barkHotFilterEnabled").checked = Boolean(notification.barkHotFilterEnabled);
  $("barkHotFilterMinCommon").value = Number(notification.barkHotFilterMinCommon || 2);

  const telegramList = $("telegramRecipients");
  telegramList.innerHTML = "";
  telegramList.appendChild(recipientRow({ id: notification.telegramChatId || "", title: "主聊天", primary: true }));
  (notification.telegramAuthorizedChats || []).forEach((chat) => telegramList.appendChild(recipientRow(chat)));

  const wxList = $("wxpusherRecipients");
  wxList.innerHTML = "";
  (notification.wxpusherUids || []).forEach((uid) => wxList.appendChild(wxpusherRow(uid)));
  if (!notification.wxpusherUids || !notification.wxpusherUids.length) wxList.appendChild(wxpusherRow());

  const barkList = $("barkDeviceKeys");
  barkList.innerHTML = "";
  (notification.barkDeviceKeys || []).forEach((deviceKey) => barkList.appendChild(barkDeviceRow(deviceKey)));
  if (!notification.barkDeviceKeys || !notification.barkDeviceKeys.length) barkList.appendChild(barkDeviceRow());
}

async function loadGroups() {
  const payload = await api("/api/groups");
  state.groups = payload.data || [];
  renderGroupManager();
  renderGroupFilter();
}

function collectTelegramRecipients() {
  const rows = [...$("telegramRecipients").querySelectorAll(".editable-row")];
  const primaryRow = rows[0];
  const primaryChatId = primaryRow?.querySelector('[data-field="id"]').value.trim() || "";
  const authorized = rows.slice(1)
    .map((row) => ({
      id: row.querySelector('[data-field="id"]').value.trim(),
      title: row.querySelector('[data-field="title"]').value.trim(),
    }))
    .filter((item) => item.id);
  return { primaryChatId, authorized };
}

function collectWxpusherUids() {
  return [...$("wxpusherRecipients").querySelectorAll('[data-field="uid"]')]
    .map((input) => input.value.trim())
    .filter(Boolean);
}

function collectBarkDeviceKeys() {
  const keys = [...$("barkDeviceKeys").querySelectorAll('[data-field="device_key"]')]
    .map((input) => input.value.trim())
    .filter(Boolean);
  const savedCount = Number(state.config?.notification?.barkDeviceKeyCount || 0);
  if (!keys.length && savedCount > 0) return null;
  return keys;
}

function parseCsvLine(line) {
  const cells = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
      continue;
    }
    if (!quoted && (char === "," || char === "\t" || char === "，")) {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += char;
  }
  cells.push(current.trim());
  return cells;
}

function parseBooleanCell(value, fallback = true) {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) return fallback;
  if (["1", "true", "yes", "on", "开", "开启", "是"].includes(normalized)) return true;
  if (["0", "false", "no", "off", "关", "关闭", "否", "暂停"].includes(normalized)) return false;
  return fallback;
}

function parseImportText(text) {
  const lines = String(text || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const targets = [];
  lines.forEach((line, index) => {
    const hasDelimiter = /[,，\t]/.test(line);
    const cells = hasDelimiter ? parseCsvLine(line) : line.split(/\s+/);
    const first = (cells[0] || "").trim().replace(/^\uFEFF/, "");
    if (!first || first.startsWith("#")) return;
    if (index === 0 && ["handle", "用户名", "用户"].includes(first.toLowerCase())) return;
    targets.push({
      handle: first,
      group_name: cells[1] || "",
      remark_name: hasDelimiter ? (cells[2] || "") : cells.slice(2).join(" "),
      enabled: parseBooleanCell(cells[3], true),
      monitor_tweets: parseBooleanCell(cells[4], true),
      monitor_retweets: parseBooleanCell(cells[5], true),
      monitor_replies: parseBooleanCell(cells[6], true),
      monitor_following: parseBooleanCell(cells[7], true),
      tweet_fetch_count: Number(cells[8] || 10),
      following_fetch_count: Number(cells[9] || 40),
    });
  });
  return targets;
}

function csvCell(value) {
  const text = String(value ?? "");
  if (!/[",\r\n]/.test(text)) return text;
  return `"${text.replaceAll('"', '""')}"`;
}

function exportTargetsCsv() {
  const headers = [
    "handle",
    "group_name",
    "remark_name",
    "enabled",
    "monitor_tweets",
    "monitor_retweets",
    "monitor_replies",
    "monitor_following",
    "tweet_fetch_count",
    "following_fetch_count",
  ];
  const rows = state.targets.map((target) => headers.map((key) => csvCell(target[key])).join(","));
  const csv = `\uFEFF${headers.join(",")}\n${rows.join("\n")}`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `x-monitor-targets-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function refreshAll() {
  await loadConfig();
  await Promise.all([
    loadPollTasks(),
    loadTargets(),
    loadGroups(),
    loadEvents(),
    loadInsights(),
  ]);
  renderTargetFilter();
  renderGroupFilter();
  renderEvents();
}

async function verifyLoginToken() {
  await api("/api/auth/check", { timeoutMs: 6000 });
}

async function refreshAfterLogin(restore) {
  try {
    await refreshAll();
    setStatus(restore ? "已恢复登录" : "登录成功");
  } catch (error) {
    if (isInvalidTokenError(error)) {
      clearStoredToken();
      showLogin();
      throw error;
    }
    setStatus(`登录已保留，数据刷新失败：${error.message || "请求失败"}`, true);
  }
}

async function loginWithToken(token, options = {}) {
  const restore = Boolean(options.restore);
  state.token = token;
  localStorage.setItem("monitorAdminToken", state.token);
  await verifyLoginToken();
  showApp();
  setLoginStatus("");
  setStatus(restore ? "已恢复登录，正在刷新数据..." : "登录成功，正在刷新数据...");
  await refreshAfterLogin(restore);
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = $("loginPassword").value.trim();
  if (!token) return;
  try {
    setLoginStatus("正在验证...");
    await loginWithToken(token);
  } catch (error) {
    if (isInvalidTokenError(error)) {
      clearStoredToken();
    }
    setLoginStatus(error.message || "登录失败", true);
  }
});

$("logout").addEventListener("click", () => {
  clearStoredToken();
  showLogin();
  showToast("已退出登录");
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || button.disabled) return;
  toastButtonFeedback(button);
}, true);

$("openSettings").addEventListener("click", () => {
  $("settingsDrawer").classList.remove("hidden");
  showToast("已打开设置");
});
$("closeSettings").addEventListener("click", () => {
  $("settingsDrawer").classList.add("hidden");
  showToast("已关闭设置");
});
$("closeSettingsBackdrop").addEventListener("click", () => $("settingsDrawer").classList.add("hidden"));

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    setView(button.dataset.view || "overview");
    showToast(`已切换到${button.textContent.trim()}`);
  });
});

document.querySelectorAll("[data-radar-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    state.radarMode = button.dataset.radarMode || "projects";
    renderProjects();
    showToast(`已切换到${button.textContent.trim()}`);
  });
});

$("groupDetailEvents").addEventListener("click", () => {
  const group = state.selectedGroup;
  setView("overview");
  if (group && group !== "未分组") {
    $("eventGroupFilter").value = group;
  } else {
    $("eventGroupFilter").value = "all";
  }
  state.eventPage = 1;
  renderEvents();
  showToast("已切换到事件中心");
});

$("addTelegramRecipient").addEventListener("click", () => {
  $("telegramRecipients").appendChild(recipientRow());
  showToast("已新增 Telegram 接收聊天");
});
$("addWxpusherRecipient").addEventListener("click", () => {
  $("wxpusherRecipients").appendChild(wxpusherRow());
  showToast("已新增 WxPusher UID");
});
$("addBarkDeviceKey").addEventListener("click", () => {
  $("barkDeviceKeys").appendChild(barkDeviceRow());
  showToast("已新增 Bark 设备码");
});

$("addGroup").addEventListener("click", async () => {
  const name = $("newGroupInput").value.trim();
  if (!name) return;
  try {
    await api("/api/groups", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    $("newGroupInput").value = "";
    await loadGroups();
    renderGroupFilter();
    setStatus(`已添加分组「${name}」`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

$("newGroupInput").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  $("addGroup").click();
});

$("savePollSettings").addEventListener("click", async () => {
  try {
    await api("/api/poll-settings", {
      method: "POST",
      body: JSON.stringify({
        poll_interval_min_seconds: Number($("pollMinSeconds").value || 180),
        poll_interval_max_seconds: Number($("pollMaxSeconds").value || 300),
        poll_backoff_max_seconds: Number($("pollBackoffSeconds").value || 1800),
      }),
    });
    await loadConfig();
    setStatus("后台检查间隔已保存");
  } catch (error) {
    setStatus(error.message, true);
  }
});

$("importTargets").addEventListener("click", async () => {
  const targets = parseImportText($("bulkImportText").value);
  if (!targets.length) {
    $("bulkImportResult").textContent = "没有可导入的用户";
    setStatus("没有可导入的用户", true);
    return;
  }
  try {
    const result = await api("/api/targets/import", {
      method: "POST",
      body: JSON.stringify({
        targets,
        update_existing: $("importUpdateExisting").checked,
      }),
    });
    await refreshAll();
    const errorText = result.errors?.length ? `，失败 ${result.errors.length} 条` : "";
    $("bulkImportResult").textContent = `导入完成：新增 ${result.created}，更新 ${result.updated}，跳过 ${result.skipped}${errorText}`;
    if (!result.errors?.length) $("bulkImportText").value = "";
    setStatus($("bulkImportResult").textContent, Boolean(result.errors?.length));
  } catch (error) {
    $("bulkImportResult").textContent = error.message;
    setStatus(error.message, true);
  }
});

$("exportTargets").addEventListener("click", () => {
  exportTargetsCsv();
  setStatus("已导出监控用户 CSV");
});

$("resolveUser").addEventListener("click", async () => {
  const query = $("handleInput").value.trim();
  if (!query) return;
  try {
    $("resolvePreview").textContent = "正在识别用户...";
    const payload = await api("/api/users/resolve", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    state.resolvedUser = payload.data;
    $("handleInput").value = payload.data.handle;
    $("resolvePreview").innerHTML = `
      已识别：<strong>${escapeHtml(payload.data.displayName)}</strong>
      <span>@${escapeHtml(payload.data.handle)}</span>
      <span>粉丝 ${Number(payload.data.followers || 0).toLocaleString()}</span>
    `;
    setStatus(`已识别 @${payload.data.handle}`);
  } catch (error) {
    state.resolvedUser = null;
    $("resolvePreview").textContent = error.message;
    setStatus(error.message, true);
  }
});

$("addTargetForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const handle = $("handleInput").value.trim();
  if (!handle) return;
  try {
    await api("/api/targets", {
      method: "POST",
      body: JSON.stringify({
        handle,
        group_name: $("groupInput").value.trim(),
        remark_name: $("remarkInput").value.trim(),
        monitor_tweets: true,
        monitor_retweets: true,
        monitor_replies: true,
        monitor_following: true,
      }),
    });
    $("handleInput").value = "";
    $("remarkInput").value = "";
    $("resolvePreview").textContent = "还未识别用户";
    await refreshAll();
    setStatus(`已添加 @${handle}`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

$("notificationForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const telegram = collectTelegramRecipients();
  try {
    await api("/api/notification-settings", {
      method: "POST",
      body: JSON.stringify({
        telegram_bot_token: $("telegramToken").value.trim() || null,
        telegram_chat_id: telegram.primaryChatId,
        telegram_authorized_chats: telegram.authorized,
        telegram_proxy: $("telegramProxy").value.trim(),
        wxpusher_enabled: $("wxpusherEnabled").checked,
        wxpusher_hot_filter_enabled: $("wxpusherHotFilterEnabled").checked,
        wxpusher_hot_filter_min_common: Number($("wxpusherHotFilterMinCommon").value || 2),
        wxpusher_app_token: $("wxpusherToken").value.trim() || null,
        wxpusher_uids: collectWxpusherUids(),
        bark_enabled: $("barkEnabled").checked,
        bark_hot_filter_enabled: $("barkHotFilterEnabled").checked,
        bark_hot_filter_min_common: Number($("barkHotFilterMinCommon").value || 2),
        bark_server_url: $("barkServerUrl").value.trim(),
        bark_device_keys: collectBarkDeviceKeys(),
        bark_level: $("barkLevel").value,
        bark_sound: $("barkSound").value.trim(),
        bark_group: $("barkGroup").value.trim(),
        bark_call: $("barkCall").checked,
        bark_volume: Number($("barkVolume").value || 5),
      }),
    });
    $("telegramToken").value = "";
    $("wxpusherToken").value = "";
    await loadConfig();
    setStatus("设置已保存");
  } catch (error) {
    setStatus(error.message, true);
  }
});

async function testNotification(channel, label) {
  try {
    setStatus(`正在发送${label}测试通知...`);
    await api("/api/notification-settings/test", {
      method: "POST",
      body: JSON.stringify({ channel }),
    });
    setStatus(`${label}测试通知已发送`);
  } catch (error) {
    setStatus(error.message, true);
  }
}

$("testAll").addEventListener("click", () => testNotification("all", "全部渠道"));
$("testTelegram").addEventListener("click", () => testNotification("telegram", "Telegram"));
$("testWxpusher").addEventListener("click", () => testNotification("wxpusher", "WxPusher"));
$("testBark").addEventListener("click", () => testNotification("bark", "Bark"));

["eventGroupFilter", "eventTargetFilter", "eventTypeFilter", "eventNotifyFilter", "eventSearch"].forEach((id) => {
  $(id).addEventListener("input", () => {
    state.eventPage = 1;
    renderEvents();
  });
});

$("projectGroupFilter").addEventListener("change", async () => {
  try {
    await loadInsights();
  } catch (error) {
    setStatus(error.message, true);
  }
});

$("projectMinCommon").addEventListener("change", async () => {
  try {
    await loadInsights();
  } catch (error) {
    setStatus(error.message, true);
  }
});

["projectSearch", "projectOnly"].forEach((id) => {
  $(id).addEventListener("input", renderProjects);
});

$("projectSort").addEventListener("change", () => {
  renderProjects();
  showToast(`已切换为${$("projectSort").selectedOptions[0]?.textContent || "当前"}排序`);
});

$("eventPrev").addEventListener("click", () => {
  state.eventPage = Math.max(state.eventPage - 1, 1);
  renderEvents();
  showToast(`已切换到第 ${state.eventPage} 页`);
});

$("eventNext").addEventListener("click", () => {
  state.eventPage += 1;
  renderEvents();
  showToast(`已切换到第 ${state.eventPage} 页`);
});

$("pollAll").addEventListener("click", async () => {
  try {
    setStatus("正在检查全部用户...");
    await api("/api/poll/run", { method: "POST" });
    await refreshAll();
    setStatus("全部用户检查完成");
  } catch (error) {
    setStatus(error.message, true);
  }
});

$("refresh").addEventListener("click", async () => {
  try {
    await refreshAll();
    setStatus("页面已刷新");
  } catch (error) {
    setStatus(error.message, true);
  }
});

async function boot() {
  renderTypeFilter();
  setView(state.activeView);
  setInterval(updateCountdown, 1000);
  if (!state.token) {
    showLogin();
    return;
  }
  try {
    await loginWithToken(state.token, { restore: true });
  } catch (error) {
    if (isInvalidTokenError(error)) {
      clearStoredToken();
      showLogin();
      setLoginStatus(error.message || "登录已失效，请重新输入", true);
      return;
    }
    showApp();
    setStatus(`自动登录暂时无法验证：${error.message || "请求失败"}。已保留本机登录，可稍后刷新。`, true);
  }
}

boot();
