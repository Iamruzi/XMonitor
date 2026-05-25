const state = {
  token: localStorage.getItem("monitorAdminToken") || "",
  config: null,
  targets: [],
  groups: [],
  events: [],
  resolvedUser: null,
  eventPage: 1,
  eventPageSize: 12,
  nextPollAt: null,
  lastConfigRefreshAt: 0,
  configRefreshInFlight: false,
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

function headers() {
  const result = { "Content-Type": "application/json" };
  if (state.token) result["X-Admin-Token"] = state.token;
  return result;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...headers(), ...(options.headers || {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_) {
      // Keep default error text.
    }
    throw new Error(detail);
  }
  return response.json();
}

function setStatus(message, isError = false) {
  const line = $("statusLine");
  if (!line) return;
  line.textContent = message || "";
  line.classList.toggle("error", isError);
}

function setLoginStatus(message, isError = false) {
  $("loginStatus").textContent = message || "";
  $("loginStatus").classList.toggle("error", isError);
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

function groupNames() {
  return [...new Set([
    ...state.groups.map((group) => group.name),
    ...state.targets.map((target) => target.group_name),
  ].filter(Boolean))].sort();
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
  if (notification.wxpusherConfigured) channels.push("WxPusher");
  if (notification.barkConfigured) channels.push("Bark");
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
    </td>
    <td>
      <div class="row-actions">
        <button data-action="save" class="primary" type="button">保存</button>
        <button data-action="poll" type="button">检查</button>
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
  tr.querySelector('[data-action="delete"]').addEventListener("click", async () => {
    if (!confirm(`删除 @${target.handle} 的监控？`)) return;
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

function recipientRow({ id = "", title = "", primary = false } = {}) {
  const row = document.createElement("div");
  row.className = "editable-row";
  row.innerHTML = `
    <input data-field="id" type="text" value="${escapeHtml(id)}" placeholder="聊天 ID" ${primary ? "data-primary='true'" : ""} />
    <input data-field="title" type="text" value="${escapeHtml(title)}" placeholder="${primary ? "主聊天" : "备注"}" />
    <button data-action="remove" type="button" ${primary ? "disabled" : ""}>删除</button>
  `;
  row.querySelector('[data-action="remove"]').addEventListener("click", () => row.remove());
  return row;
}

function wxpusherRow(uid = "") {
  const row = document.createElement("div");
  row.className = "editable-row two";
  row.innerHTML = `
    <input data-field="uid" type="text" value="${escapeHtml(uid)}" placeholder="UID_xxx" />
    <button data-action="remove" type="button">删除</button>
  `;
  row.querySelector('[data-action="remove"]').addEventListener("click", () => row.remove());
  return row;
}

function barkDeviceRow(deviceKey = "") {
  const row = document.createElement("div");
  row.className = "editable-row two";
  row.innerHTML = `
    <input data-field="device_key" type="text" value="${escapeHtml(deviceKey)}" placeholder="Bark 设备码" />
    <button data-action="remove" type="button">删除</button>
  `;
  row.querySelector('[data-action="remove"]').addEventListener("click", () => row.remove());
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
  });
  row.querySelector('[data-action="clear"]').addEventListener("click", async () => {
    if (!confirm(`清空分组「${group.name}」？相关用户会变成未分组，但不会删除用户。`)) return;
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
  $("barkServerUrl").value = notification.barkServerUrl || "https://api.day.app";
  $("barkGroup").value = notification.barkGroup || "XMonitor";
  $("barkLevel").value = notification.barkLevel || "active";
  $("barkSound").value = notification.barkSound || "";
  $("barkCall").checked = Boolean(notification.barkCall);
  $("barkVolume").value = Number(notification.barkVolume ?? 5);

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
  await loadTargets();
  await loadGroups();
  await loadEvents();
}

async function loginWithToken(token) {
  state.token = token;
  localStorage.setItem("monitorAdminToken", state.token);
  await refreshAll();
  showApp();
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = $("loginPassword").value.trim();
  if (!token) return;
  try {
    setLoginStatus("正在验证...");
    await loginWithToken(token);
    setLoginStatus("");
  } catch (error) {
    localStorage.removeItem("monitorAdminToken");
    state.token = "";
    setLoginStatus(error.message || "登录失败", true);
  }
});

$("logout").addEventListener("click", () => {
  localStorage.removeItem("monitorAdminToken");
  state.token = "";
  showLogin();
});

$("openSettings").addEventListener("click", () => $("settingsDrawer").classList.remove("hidden"));
$("closeSettings").addEventListener("click", () => $("settingsDrawer").classList.add("hidden"));
$("closeSettingsBackdrop").addEventListener("click", () => $("settingsDrawer").classList.add("hidden"));

$("addTelegramRecipient").addEventListener("click", () => $("telegramRecipients").appendChild(recipientRow()));
$("addWxpusherRecipient").addEventListener("click", () => $("wxpusherRecipients").appendChild(wxpusherRow()));
$("addBarkDeviceKey").addEventListener("click", () => $("barkDeviceKeys").appendChild(barkDeviceRow()));

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
  } catch (error) {
    state.resolvedUser = null;
    $("resolvePreview").textContent = error.message;
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
        wxpusher_app_token: $("wxpusherToken").value.trim() || null,
        wxpusher_uids: collectWxpusherUids(),
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

$("eventPrev").addEventListener("click", () => {
  state.eventPage = Math.max(state.eventPage - 1, 1);
  renderEvents();
});

$("eventNext").addEventListener("click", () => {
  state.eventPage += 1;
  renderEvents();
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
  setInterval(updateCountdown, 1000);
  if (!state.token) {
    showLogin();
    return;
  }
  try {
    await loginWithToken(state.token);
  } catch (_) {
    localStorage.removeItem("monitorAdminToken");
    state.token = "";
    showLogin();
  }
}

boot();
