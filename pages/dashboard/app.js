import {
  ACTION_LABELS,
  ATMOSPHERE_LABELS,
  BOT_WATCH_STATE_LABELS,
  CURRENT_SLEEP_LABELS,
  EPISODE_KIND_LABELS,
  EPISODE_STATUS_LABELS,
  EVENT_STATUS_LABELS,
  EVIDENCE_TYPE_LABELS,
  FEEDBACK_RESULT_LABELS,
  FRESHNESS_LABELS,
  HEALTH_CHECK_LABELS,
  INTERRUPT_LEVEL_LABELS,
  PLACE_TYPE_LABELS,
  PREFERENCE_CATEGORY_LABELS,
  SCHEDULE_INTENT_LABELS,
  SCHEDULE_TONE_LABELS,
  SCENE_TYPE_LABELS,
  SCOPE_LABELS,
  SOURCE_LABELS,
  TARGET_TYPE_LABELS,
  UNDERSTANDING_LABELS,
  VISIBILITY_LABELS,
} from "./shared/labels.js";

import {
  clean,
  currentOutfitDisplayText,
  enumLabel,
  evidenceTargetTitle,
  evidenceText,
  labeledTemplateName,
  lifeEpisodeLines,
  moodColorText,
  outfitDecisionText,
  recordLines,
  scheduleTypeText,
  stateLogText,
  text,
  visibleExperienceEvidence,
  visibleLifeEpisodes
} from "./shared/display.js";

import {
  clampPercent,
  clampRange,
  clone,
  firstClockMinutes,
  formatClock,
  formatDate,
  parseStatusNow,
  parseTimeMinutes
} from "./shared/utils.js";

import {
  apiGet,
  apiPost,
  bridge,
  GENERATION_TIMEOUT_MS,
  withTimeout
} from "./api/api.js";

import { createConfigPanel } from "./ui/config.js?v=20260621-reference-preview-cache2";
import { createWorkshopPanel } from "./ui/workshop.js";

const NOTICE_HIDE_MS = 4200;
const STATUS_WAIT_SECONDS = 25;
const STATUS_RETRY_DELAY_MS = 2000;

const state = {
  view: "dashboard",
  status: null,
  worldTab: "relationships",
  noticeTimer: 0,
  busy: false,
  configSchema: {},
  config: {},
  providers: [],
  configSectionKey: "",
  configDirty: false,
  configLoaded: false,
  configSaveTimer: 0,
  configSaving: false,
  configSaveQueued: false,
  configSaveStatus: "idle",
  configVersion: 0,
  templateDraft: null,
  templateDraftId: "",
  catalogCategory: "",
  catalogDraft: null,
  hairDraft: null,
  timelineEditing: false,
  timelineDraft: [],
  clockTimer: 0,
  statusWatchTimer: 0,
  statusWatchActive: false,
  bridgeReady: false,
  clockSourceNow: "",
  clockEpochMs: 0,
  clockClientMs: 0,
};

const byId = (id) => document.getElementById(id);
const all = (selector) => Array.from(document.querySelectorAll(selector));
const el = {
  notice: byId("notice"),
  dashboardView: byId("dashboardView"),
  settingsView: byId("settingsView"),
  viewButtons: all(".view-button"),
  actionGroups: all("[data-action-view]"),
  resetDayButton: byId("resetDayButton"),
  refreshStateButton: byId("refreshStateButton"),
  targetDate: byId("targetDate"),
  weatherText: byId("weatherText"),
  themeText: byId("themeText"),
  todayWeekPlan: byId("todayWeekPlan"),
  moodColorText: byId("moodColorText"),
  scheduleTypeText: byId("scheduleTypeText"),
  scheduleToneText: byId("scheduleToneText"),
  scheduleIntentText: byId("scheduleIntentText"),
  currentOutfitText: byId("currentOutfitText"),
  outfitDecisionText: byId("outfitDecisionText"),
  memoText: byId("memoText"),
  nowText: byId("nowText"),
  currentActivity: byId("currentActivity"),
  stateMeters: byId("stateMeters"),
  timelineList: byId("timelineList"),
  timelineAddButton: byId("timelineAddButton"),
  timelineEditButton: byId("timelineEditButton"),
  timelineCancelButton: byId("timelineCancelButton"),
  timelineSaveButton: byId("timelineSaveButton"),
  stateLogList: byId("stateLogList"),
  worldTabs: all("[data-world-tab]"),
  worldList: byId("worldList"),
  lifecycleList: byId("lifecycleList"),
  experienceList: byId("experienceList"),
  configDirtyBadge: byId("configDirtyBadge"),
  configNav: byId("configNav"),
  configSectionTitle: byId("configSectionTitle"),
  configSectionHint: byId("configSectionHint"),
  configFieldList: byId("configFieldList"),
  weekTemplateSelect: byId("weekTemplateSelect"),
  weekForm: byId("weekForm"),
  weekGoalsInput: byId("weekGoalsInput"),
  weekWebButton: byId("weekWebButton"),
  templateEditorSelect: byId("templateEditorSelect"),
  templateNewButton: byId("templateNewButton"),
  templateCopyButton: byId("templateCopyButton"),
  templateSaveButton: byId("templateSaveButton"),
  templateEditorHint: byId("templateEditorHint"),
  templateIdInput: byId("templateIdInput"),
  templateNameInput: byId("templateNameInput"),
  templateEmojiInput: byId("templateEmojiInput"),
  templateWeightInput: byId("templateWeightInput"),
  templateCooldownInput: byId("templateCooldownInput"),
  templateEnabledInput: byId("templateEnabledInput"),
  templateDescriptionInput: byId("templateDescriptionInput"),
  templateGoalsInput: byId("templateGoalsInput"),
  templateHintsInput: byId("templateHintsInput"),
  templateWeekdayInput: byId("templateWeekdayInput"),
  templateWeekendInput: byId("templateWeekendInput"),
  templateTagsInput: byId("templateTagsInput"),
  templateForm: byId("templateForm"),
  templateText: byId("templateText"),
  templateWebButton: byId("templateWebButton"),
  templateList: byId("templateList"),
  catalogCategorySelect: byId("catalogCategorySelect"),
  catalogNewButton: byId("catalogNewButton"),
  catalogCopyButton: byId("catalogCopyButton"),
  catalogSaveButton: byId("catalogSaveButton"),
  catalogEditorHint: byId("catalogEditorHint"),
  catalogTextInput: byId("catalogTextInput"),
  catalogEnabledInput: byId("catalogEnabledInput"),
  catalogForm: byId("catalogForm"),
  catalogText: byId("catalogText"),
  catalogWebButton: byId("catalogWebButton"),
  catalogList: byId("catalogList"),
  hairStyleSelect: byId("hairStyleSelect"),
  hairNewButton: byId("hairNewButton"),
  hairCopyButton: byId("hairCopyButton"),
  hairSaveButton: byId("hairSaveButton"),
  hairEditorHint: byId("hairEditorHint"),
  hairNameInput: byId("hairNameInput"),
  hairEnabledInput: byId("hairEnabledInput"),
  hairOptionsInput: byId("hairOptionsInput"),
  hairForm: byId("hairForm"),
  hairText: byId("hairText"),
  hairWebButton: byId("hairWebButton"),
  hairList: byId("hairList"),
};

function syncClock(status = {}) {
  const sourceNow = text(status.now).trim();
  if (!sourceNow || state.clockSourceNow === sourceNow) return;
  const parsed = parseStatusNow(sourceNow);
  if (!parsed) return;
  state.clockSourceNow = sourceNow;
  state.clockEpochMs = parsed.getTime();
  state.clockClientMs = Date.now();
}

function currentClockDate() {
  if (!state.clockEpochMs || !state.clockClientMs) return new Date();
  return new Date(state.clockEpochMs + Date.now() - state.clockClientMs);
}

function targetDateText(status = {}) {
  return clean(status.target_date || status.day?.date || formatDate(currentClockDate()), "");
}

function renderTargetDateTime() {
  if (!el.targetDate) return;
  const clock = currentClockDate();
  el.targetDate.textContent = `${targetDateText(state.status || {})} ${formatClock(clock)}`.trim();
  renderRealtimeDayFacts(clock);
}

function stripLeadingEmoji(value) {
  return text(value).replace(/^[\p{Extended_Pictographic}\uFE0F\s]+/u, "").trim();
}

function startClock() {
  renderTargetDateTime();
  if (state.clockTimer) return;
  state.clockTimer = window.setInterval(renderTargetDateTime, 1000);
}

function statusVersion() {
  const version = Number(state.status?.status_version || 0);
  return Number.isFinite(version) ? Math.max(0, Math.trunc(version)) : 0;
}

function applyStatus(nextStatus, { render = true } = {}) {
  if (!nextStatus || typeof nextStatus !== "object") return;
  state.status = nextStatus;
  if (render && state.view === "dashboard") renderDashboard();
}

function shouldWatchStatus() {
  return !document.hidden && state.view === "dashboard";
}

function scheduleStatusWatch(delayMs = 0) {
  window.clearTimeout(state.statusWatchTimer);
  if (!state.statusWatchActive || !bridge || !state.bridgeReady || !shouldWatchStatus()) return;
  state.statusWatchTimer = window.setTimeout(watchStatusOnce, Math.max(0, delayMs));
}

async function watchStatusOnce() {
  if (!state.statusWatchActive || !bridge || !state.bridgeReady) return;
  if (!shouldWatchStatus()) return;
  try {
    const data = await apiGet("page/status/wait", {
      since: statusVersion(),
      timeout: STATUS_WAIT_SECONDS,
      _ts: Date.now(),
    });
    if (data.changed) applyStatus(data);
    scheduleStatusWatch(0);
  } catch (_error) {
    scheduleStatusWatch(STATUS_RETRY_DELAY_MS);
  }
}

function startStatusAutoRefresh() {
  if (state.statusWatchActive || !bridge || !state.bridgeReady) return;
  state.statusWatchActive = true;
  scheduleStatusWatch(0);
}

function bindAutoRefreshEvents() {
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.view === "dashboard" && bridge && state.bridgeReady) {
      loadStatus({ quiet: true });
      scheduleStatusWatch(0);
    }
  });
}

function setNotice(message, tone = "info") {
  window.clearTimeout(state.noticeTimer);
  state.noticeTimer = 0;
  const body = text(message).trim();
  if (!body) {
    el.notice.hidden = true;
    el.notice.textContent = "";
    el.notice.className = "notice";
    return;
  }
  el.notice.hidden = false;
  el.notice.textContent = body;
  el.notice.className = "notice";
  void el.notice.offsetWidth;
  el.notice.className = `notice ${tone}`;
  state.noticeTimer = window.setTimeout(() => setNotice(""), NOTICE_HIDE_MS);
}

function setBusy(value) {
  state.busy = Boolean(value);
  document.querySelectorAll("button, input, select, textarea").forEach((item) => {
    item.disabled = state.busy || item.dataset.lockDisabled === "true";
  });
}

function node(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = text(content);
  return element;
}

function empty(label) {
  return node("div", "empty", label);
}

function objectItems(items) {
  return (Array.isArray(items) ? items : []).filter((item) => item && typeof item === "object");
}

function uniqueExperienceFeedback(items) {
  const result = [];
  const seen = new Set();
  objectItems(items).forEach((item) => {
    const marker = [
      text(item.scene).trim(),
      text(item.action).trim(),
      text(item.feedback).trim(),
      text(item.result).trim(),
    ].join("\n");
    if (seen.has(marker)) return;
    seen.add(marker);
    result.push(item);
  });
  return result;
}

function setView(view) {
  state.view = view === "settings" ? "settings" : "dashboard";
  el.dashboardView.hidden = state.view !== "dashboard";
  el.settingsView.hidden = state.view !== "settings";
  el.viewButtons.forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
  el.actionGroups.forEach((group) => {
    group.hidden = group.dataset.actionView !== state.view;
  });
  if (state.view === "settings" && !state.configLoaded && bridge) {
    loadConfig();
  } else if (state.view === "dashboard" && bridge && state.bridgeReady) {
    loadStatus({ quiet: true });
    scheduleStatusWatch(0);
  }
}

function timelineProgress(day = {}, status = {}) {
  const timeline = Array.isArray(day.timeline) ? day.timeline : [];
  if (!timeline.length) return null;
  const first = parseTimeMinutes(timeline[0]?.time);
  const last = parseTimeMinutes(timeline[timeline.length - 1]?.time);
  const parsedNow = parseStatusNow(status.now);
  const nowMinutes = parsedNow
    ? parsedNow.getHours() * 60 + parsedNow.getMinutes()
    : firstClockMinutes(status.now);
  if (first === null || last === null || nowMinutes === null) return null;
  const span = Math.max(1, last - first);
  return clampPercent(((nowMinutes - first) / span) * 100);
}

function appendMeter(label, value, options = {}) {
  const percent = clampPercent(value);
  const item = node("div", "meter");
  const top = node("div", "meter-top");
  top.append(node("span", "", label), node("span", "", percent === null ? "--" : `${Math.round(percent)}/100`));
  const track = node("div", "track");
  const bar = node("div", `bar${options.tone ? ` ${options.tone}` : ""}`);
  bar.style.width = `${percent ?? 0}%`;
  track.append(bar);
  item.append(top, track);
  el.stateMeters.append(item);
}

function appendInfoBox(label, value) {
  const body = clean(value, "");
  if (!body) return;
  const item = node("div", "meter info-meter");
  item.append(node("div", "meter-top", label), node("p", "paragraph", body));
  el.stateMeters.append(item);
}

function renderMeters(day = {}, status = {}) {
  el.stateMeters.replaceChildren();
  const lifeState = day.state || {};
  const sleep = lifeState.sleep || {};
  const meta = day.meta || {};
  const items = [
    ["体力", lifeState.energy],
    ["心情值", lifeState.mood_score],
    ["忙碌", lifeState.busyness],
    ["社交意愿", lifeState.social],
    ["睡眠质量", sleep.quality],
    ["压力感", lifeState.stress],
    ["专注度", lifeState.focus],
    ["困倦度", lifeState.sleepiness],
    ["外出意愿", lifeState.outgoing],
    ["情绪稳定", lifeState.emotional_stability],
    ["互动意愿", lifeState.interaction_capacity],
    ["无聊值", lifeState.boredom],
    ["摸鱼值", lifeState.fishing],
    ["注意力开放", lifeState.attention_openness],
  ];
  for (const [label, value] of items) {
    appendMeter(label, value);
  }

  const debt = clampRange(meta.sleep_debt, 0, 10);
  if (debt !== null) {
    appendMeter("睡眠债", debt * 10, { tone: "warn" });
  }
  if (meta.energy_carryover) {
    appendMeter("体力延续", meta.energy_carryover);
  }
  const progress = timelineProgress(day, status);
  if (progress !== null) {
    appendMeter("日程进度", progress);
  }

  const mood = clean(lifeState.mood, "");
  appendInfoBox("心情", mood);
  appendInfoBox("睡眠影响", sleep.summary);
  appendInfoBox("当前睡眠", enumLabel(sleep.depth, CURRENT_SLEEP_LABELS));
  appendInfoBox(
    "注意力状态",
    [
      enumLabel(lifeState.watch_state, BOT_WATCH_STATE_LABELS),
      enumLabel(lifeState.interrupt_level, INTERRUPT_LEVEL_LABELS),
      clean(lifeState.interrupt_reason, ""),
    ].filter(Boolean).join(" · ")
  );
}

function cloneTimeline(timeline = []) {
  return (Array.isArray(timeline) ? timeline : []).map((item) => ({
    time: clean(item.time, ""),
    activity: clean(item.activity, ""),
    status: clean(item.status, ""),
  }));
}

function setTimelineButtons(hasDay) {
  el.timelineEditButton.hidden = !hasDay || state.timelineEditing;
  el.timelineAddButton.hidden = !hasDay || !state.timelineEditing;
  el.timelineCancelButton.hidden = !hasDay || !state.timelineEditing;
  el.timelineSaveButton.hidden = !hasDay || !state.timelineEditing;
}

function renderTimelineDisplay(timeline) {
  if (!timeline.length) {
    el.timelineList.replaceChildren(empty("暂无时间轴"));
    return;
  }
  el.timelineList.replaceChildren(
    ...timeline.map((item) => {
      const li = node("li", "timeline-item");
      li.append(node("div", "time", clean(item.time, "--")));
      const body = node("div");
      body.append(node("div", "timeline-activity", clean(item.activity)));
      if (item.status) body.append(node("div", "status", item.status));
      li.append(body);
      return li;
    })
  );
}

function timelineEditorRow(item, index) {
  const li = node("li", "timeline-item timeline-edit-row");
  const time = document.createElement("input");
  time.type = "time";
  time.value = clean(item.time, "");
  time.dataset.timelineField = "time";
  time.dataset.index = String(index);
  const body = node("div", "timeline-edit-fields");
  const activity = document.createElement("textarea");
  activity.rows = 2;
  activity.value = clean(item.activity, "");
  activity.placeholder = "活动";
  activity.dataset.timelineField = "activity";
  activity.dataset.index = String(index);
  const status = document.createElement("input");
  status.type = "text";
  status.value = clean(item.status, "");
  status.placeholder = "状态";
  status.dataset.timelineField = "status";
  status.dataset.index = String(index);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "删除";
  remove.className = "danger";
  remove.addEventListener("click", () => {
    updateTimelineDraftFromInputs();
    state.timelineDraft.splice(index, 1);
    renderTimelineEditor();
  });
  body.append(activity, status, remove);
  li.append(time, body);
  return li;
}

function updateTimelineDraftFromInputs() {
  const next = cloneTimeline(state.timelineDraft);
  el.timelineList.querySelectorAll("[data-timeline-field]").forEach((input) => {
    const index = Number(input.dataset.index);
    const field = input.dataset.timelineField;
    if (!Number.isInteger(index) || !next[index] || !field) return;
    next[index][field] = input.value.trim();
  });
  state.timelineDraft = next;
}

function renderTimelineEditor() {
  const draft = cloneTimeline(state.timelineDraft);
  if (!draft.length) {
    el.timelineList.replaceChildren(empty("暂无时间轴，可点击新增"));
    return;
  }
  el.timelineList.replaceChildren(...draft.map((item, index) => timelineEditorRow(item, index)));
}

function renderStateLogs(day = {}) {
  const logs = Array.isArray(day.state_log) ? day.state_log : [];
  if (!logs.length) {
    el.stateLogList.replaceChildren(empty("暂无状态变化记录"));
    return;
  }
  el.stateLogList.replaceChildren(
    ...logs.slice(-10).reverse().map((entry) => {
      const record = node("div", "record");
      record.append(node("div", "record-body", clean(stateLogText(entry))));
      return record;
    })
  );
}

function renderDay(status) {
  const day = status.day;
  syncClock(status);
  el.nowText.textContent = "";
  el.nowText.hidden = true;
  renderTargetDateTime();
  if (!day) {
    el.currentActivity.textContent = "暂无日常生活数据";
    el.weatherText.textContent = "--";
    el.themeText.textContent = "--";
    el.todayWeekPlan.textContent = "--";
    el.moodColorText.textContent = "--";
    el.scheduleTypeText.textContent = "--";
    el.scheduleToneText.textContent = "--";
    el.scheduleIntentText.textContent = "--";
    el.currentOutfitText.textContent = "--";
    el.outfitDecisionText.textContent = "--";
    el.memoText.textContent = "--";
    el.timelineList.replaceChildren(empty("暂无时间轴"));
    setTimelineButtons(false);
    renderMeters({}, status);
    renderStateLogs({});
    return;
  }

  el.weatherText.textContent = clean(day.weather);
  const meta = day.meta || {};
  el.themeText.textContent = clean(meta.theme);
  renderTodayWeekPlan(status.week_plan || {});
  el.moodColorText.textContent = clean(moodColorText(meta.mood));
  el.scheduleTypeText.textContent = clean(scheduleTypeText(meta.schedule_type));
  el.scheduleToneText.textContent = clean(enumLabel(meta.life_mode, SCHEDULE_TONE_LABELS));
  renderRealtimeDayFacts();
  renderFactPair(el.currentOutfitText, currentOutfitDisplayText(day, meta));
  renderFactPair(el.outfitDecisionText, outfitDecisionText(meta));
  el.memoText.textContent = clean(day.memo);
  renderMeters(day, status);

  const timeline = Array.isArray(day.timeline) ? day.timeline : [];
  setTimelineButtons(true);
  if (state.timelineEditing) renderTimelineEditor();
  else renderTimelineDisplay(timeline);
  renderStateLogs(day);
}

function renderRealtimeDayFacts(clock = currentClockDate()) {
  const day = state.status?.day;
  if (!day) return;
  const current = currentTimelinePair(day, clock, { carryExtendedNight: false }).current;
  el.currentActivity.textContent = current
    ? `${clean(current.time, "")} ${clean(current.activity, "")}`.trim()
    : "--";
  el.scheduleIntentText.textContent = clean(currentScheduleIntentText(day, clock));
}

function renderTodayWeekPlan(week = {}) {
  const theme = clean(stripLeadingEmoji(week.theme), "");
  const hint = clean(stripLeadingEmoji(week.today_hint), "");
  const suggested = clean(stripLeadingEmoji(week.today_suggested), "");
  if (!theme && !hint && !suggested) {
    el.todayWeekPlan.textContent = "--";
    return;
  }
  const lines = [];
  if (theme) lines.push(todayWeekRow("主题", theme));
  if (hint) lines.push(todayWeekRow("进度", hint));
  if (suggested) lines.push(todayWeekRow("目标", suggested, "muted"));
  const card = node("div", "today-week-card", "");
  card.replaceChildren(...lines);
  el.todayWeekPlan.replaceChildren(card);
}

export function currentTimelinePair(day = {}, clock = currentClockDate(), options = {}) {
  const timeline = Array.isArray(day.timeline) ? day.timeline : [];
  if (!timeline.length) return { current: null, next: null };
  let nowMinutes = clock.getHours() * 60 + clock.getMinutes();
  const dateText = text(day.date).trim();
  if (options.carryExtendedNight !== false && (day.extended_night || (dateText && formatDate(clock) > dateText))) {
    nowMinutes += 24 * 60;
  }
  const items = timeline
    .map((item) => ({ minutes: parseTimeMinutes(item?.time), item }))
    .filter((entry) => entry.minutes !== null)
    .sort((left, right) => left.minutes - right.minutes);
  let current = null;
  let next = null;
  for (const entry of items) {
    if (entry.minutes <= nowMinutes) {
      current = entry.item;
      continue;
    }
    next = entry.item;
    break;
  }
  return { current, next };
}

export function currentScheduleIntentText(day = {}, clock = currentClockDate()) {
  const lifeState = day.state || {};
  const sleepDepth = text(lifeState.sleep?.depth).trim();
  const hour = clock.getHours();
  const outgoing = clampRange(lifeState.outgoing, 0, 100);
  const social = clampRange(lifeState.social, 0, 100);
  const busyness = clampRange(lifeState.busyness, 0, 100);
  const focus = clampRange(lifeState.focus, 0, 100);
  const interaction = clampRange(lifeState.interaction_capacity, 0, 100);
  const sleepiness = clampRange(lifeState.sleepiness, 0, 100);
  const energy = clampRange(lifeState.energy, 0, 100);
  const { current, next } = currentTimelinePair(day, clock);
  const dateText = text(day.date).trim();
  const extendedNight = Boolean(day.extended_night || (dateText && formatDate(clock) > dateText));
  const beforeFirstItem = Boolean(!current && next);
  const afterLastItem = Boolean(current && !next);
  const lateNight = hour >= 23 || hour < 7;

  if (sleepDepth === "deep_sleep" || sleepDepth === "light_sleep") return "睡眠";
  if (sleepiness !== null && sleepiness >= 70 && energy !== null && energy <= 35) return "睡眠";
  if (lateNight || extendedNight) {
    if (beforeFirstItem || (extendedNight && afterLastItem)) return "居家";
    if ((sleepiness !== null && sleepiness >= 55) || (energy !== null && energy <= 45) || (outgoing !== null && outgoing <= 45)) {
      return "居家";
    }
    if (lateNight && sleepDepth === "light_rest") return "居家";
  }
  if (busyness !== null && busyness >= 65 && focus !== null && focus >= 55) return "工作/学习";
  if (social !== null && social >= 65 && interaction !== null && interaction >= 55) return "社交";
  if (outgoing !== null && outgoing >= 65 && energy !== null && energy >= 55) return "外出";
  if (outgoing !== null && outgoing <= 35 && (sleepiness === null || sleepiness >= 35)) return "居家";
  if (busyness !== null && busyness <= 35 && outgoing !== null && outgoing <= 45) return "居家";
  if (sleepiness !== null && sleepiness >= 65) return "睡眠";
  if (energy !== null && energy >= 70 && outgoing !== null && outgoing >= 55) return "外出";
  return "居家";
}

function todayWeekRow(label, value, className = "") {
  const row = node("div", `today-week-line ${className}`.trim(), "");
  row.replaceChildren(node("span", "today-week-label", label), document.createTextNode(value));
  return row;
}

function renderFactPair(target, value) {
  if (!target) return;
  const wrap = node("div", "today-week-line wrap");
  const data = value && typeof value === "object" ? value : {};
  const parts = [];
  if (data.style) {
    parts.push(node("span", "today-week-label", data.style));
  }
  if (data.outfit) {
    parts.push(document.createTextNode(`${parts.length ? " " : ""}${data.outfit}`));
  }
  if (data.decision) {
    parts.push(node("span", "today-week-label", data.decision));
  }
  if (data.reason) {
    parts.push(document.createTextNode(`${parts.length ? " " : ""}${data.reason}`));
  }

  if (!parts.length) {
    target.textContent = "--";
    return;
  }
  wrap.replaceChildren(...parts);
  target.replaceChildren(wrap);
}

function renderWeek(status) {
  const templates = Array.isArray(status.templates) ? status.templates : [];
  const selected = el.weekTemplateSelect?.value || "random";
  const randomOption = document.createElement("option");
  randomOption.value = "random";
  randomOption.textContent = "随机模板";
  if (el.weekTemplateSelect) {
    el.weekTemplateSelect.replaceChildren(
      randomOption,
      ...templates.map((template) => {
        const option = document.createElement("option");
        option.value = text(template.template_id);
        option.textContent = labeledTemplateName(template);
        return option;
      })
    );
    if (templates.some((item) => item.template_id === selected)) {
      el.weekTemplateSelect.value = selected;
    } else {
      el.weekTemplateSelect.value = "random";
    }
  }
}

function worldEmptyText(tab) {
  const labels = {
    relationships: "暂无关系记录",
    summaries: "暂无会话记录",
    group_environments: "暂无群聊环境记录",
    message_visibility: "暂无留意记录",
    action_decisions: "暂无裁定记录",
    places: "暂无地点记录",
    events: "暂无事件记录",
  };
  return labels[tab] || "暂无记录";
}

function renderWorld(status) {
  const activeTab = text(state.worldTab || "relationships").trim() || "relationships";
  el.worldTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.worldTab === activeTab));
  const world = status.world || {};
  const items = Array.isArray(world[activeTab]) ? world[activeTab] : [];
  if (!items.length) {
    el.worldList.replaceChildren(empty(worldEmptyText(activeTab)));
    return;
  }

  el.worldList.replaceChildren(
    ...items.map((item) => {
      item = item && typeof item === "object" ? item : {};
      const record = node("div", "record");
      const title = node("div", "record-title");
      if (activeTab === "relationships") {
        const subjective = clean(item.subjective_name, "");
        const displayName = clean(item.display_name || item.name || item.alias || item.id, "未知");
        title.append(
          node("span", "", displayName),
          node("span", "muted", `${item.interactions || 0} 次`)
        );
        const latest = Array.isArray(item.notes) ? item.notes[item.notes.length - 1] : null;
        const point = Array.isArray(item.memory_points) ? item.memory_points[item.memory_points.length - 1] : null;
        const tags = Array.isArray(item.subjective_tags) && item.subjective_tags.length
          ? `标签：${item.subjective_tags.join("、")}`
          : "";
        const story = clean(item.relationship_story, "");
        const body = [
          subjective && subjective !== displayName ? `主观称呼：${subjective}` : "",
          tags,
          story,
          point?.content || latest?.content || `最近：${clean(item.last_seen)}`,
        ].filter(Boolean).join(" · ");
        record.append(title, node("div", "record-body", body));
      } else if (activeTab === "summaries") {
        title.append(node("span", "", clean(item.brief || item.long_summary)), node("span", "muted", clean(item.date)));
        record.append(title, node("div", "record-body", clean(item.long_summary || item.brief)));
      } else if (activeTab === "group_environments") {
        const group = clean(item.group_name || item.group_id, "未命名群聊");
        const flags = [item.is_multithread ? "多线程" : "", item.is_spam ? "刷屏" : "", item.is_repetition ? "复读" : "", item.is_discussing_bot ? "提到我" : ""].filter(Boolean);
        const meta = [
          enumLabel(item.atmosphere, ATMOSPHERE_LABELS),
          enumLabel(item.bot_watch_state, BOT_WATCH_STATE_LABELS),
          item.deep_analysis_needed ? "需深析" : "",
        ].filter(Boolean).join(" · ");
        title.append(node("span", "", group), node("span", "muted", clean(meta, "未知氛围")));
        const scores = `参与 ${Number(item.participation_desire || 0)} · 复杂 ${Number(item.complexity_score || 0)} · 理解 ${Number(item.understanding_confidence || 0)}`;
        record.append(title, node("div", "record-body", `${clean(item.topic || item.summary, "暂无话题")} ${flags.length ? `(${flags.join("、")})` : ""} · ${scores}`));
      } else if (activeTab === "message_visibility") {
        const meta = [
          enumLabel(item.visibility || "seen", VISIBILITY_LABELS),
          item.attention_level || item.attention_level === 0 ? `注意 ${Number(item.attention_level || 0)}` : "",
          enumLabel(item.freshness, FRESHNESS_LABELS),
          item.psychological_freshness || item.psychological_freshness === 0 ? `心理 ${Number(item.psychological_freshness || 0)}` : "",
          item.reactivated_from_id ? `激活 #${item.reactivated_from_id}` : "",
        ].filter(Boolean).join(" · ");
        title.append(node("span", "", clean(item.sender_name || item.sender_profile_id, "未知")), node("span", "muted", meta));
        const reactivation = clean(item.reactivation_hint);
        record.append(title, node("div", "record-body", `${clean(item.reason, "无留意说明")}${reactivation ? ` · 再激活：${reactivation}` : ""}`));
      } else if (activeTab === "action_decisions") {
        const meta = [enumLabel(item.scene_type, SCENE_TYPE_LABELS), enumLabel(item.understanding, UNDERSTANDING_LABELS), item.deep_analysis ? "深析" : ""].filter(Boolean).join(" · ");
        title.append(node("span", "", enumLabel(item.action, ACTION_LABELS) || "未定"), node("span", "muted", meta || `${Math.round(Number(item.confidence || 0) * 100)}%`));
        record.append(title, node("div", "record-body", clean(item.reason, "无裁定说明")));
      } else if (activeTab === "places") {
        title.append(node("span", "", clean(item.name)), node("span", "muted", `${item.visits || 0} 次`));
        record.append(title, node("div", "record-body", clean(item.hint || enumLabel(item.type, PLACE_TYPE_LABELS))));
      } else {
        title.append(node("span", "", clean(item.summary)), node("span", "muted", clean(item.date)));
        const people = Array.isArray(item.people) && item.people.length ? ` · ${item.people.join("、")}` : "";
        record.append(title, node("div", "record-body", `${clean(item.place, "未记录地点")}${people}`));
      }
      return record;
    })
  );
}

function renderLifecycle(status) {
  const lifecycle = status.lifecycle || {};
  const reviews = objectItems(lifecycle.reviews);
  const preferences = objectItems(lifecycle.preferences);
  const events = objectItems(lifecycle.life_events);
  const total = reviews.length + preferences.length + events.length;
  if (!total) {
    el.lifecycleList.replaceChildren(empty("暂无生活演化记录"));
    return;
  }
  const records = [];
  reviews.slice(0, 2).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", `复盘 ${clean(item.date)}`), node("span", "muted", "复盘"));
    record.append(title, node("div", "record-body", clean(item.summary)));
    records.push(record);
  });
  preferences.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", clean(item.content)), node("span", "muted", enumLabel(item.category, PREFERENCE_CATEGORY_LABELS)));
    const evidence = evidenceText(item.evidence, item.source);
    record.append(title, node("div", "record-body", `权重 ${Number(item.weight || 0).toFixed(1)} · ${clean(evidence)}`));
    records.push(record);
  });
  events.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", clean(item.title)), node("span", "muted", enumLabel(item.status, EVENT_STATUS_LABELS)));
    record.append(title, node("div", "record-body", clean(item.effect || item.detail)));
    records.push(record);
  });
  el.lifecycleList.replaceChildren(...records);
  if (!records.length) {
    el.lifecycleList.replaceChildren(empty("暂无可展示的生活演化记录"));
  }
}

function renderExperience(status) {
  if (!el.experienceList) return;
  const experience = status.experience || {};
  const episodes = objectItems(experience.episodes);
  const visibleEpisodes = visibleLifeEpisodes(episodes);
  const evidence = objectItems(experience.evidence);
  const feedback = uniqueExperienceFeedback(experience.feedback);
  const focusTargets = objectItems(experience.focus_targets);
  const terms = objectItems(experience.terms);
  const health = experience.health && typeof experience.health === "object" ? experience.health : {};
  const visibleEvidence = visibleExperienceEvidence(evidence, episodes);
  const total = visibleEpisodes.length + visibleEvidence.length + feedback.length + focusTargets.length + terms.length;
  if (!total && !health.summary) {
    el.experienceList.replaceChildren(empty("暂无体验层记录"));
    return;
  }

  const records = [];
  if (health.summary) {
    const record = node("div", "record health-record");
    const title = node("div", "record-title");
    title.append(node("span", "", "健康检查"), node("span", "muted", `${Number(health.score || 0)} 分`));
    const checks = Array.isArray(health.checks)
      ? health.checks.map((item) => [
        clean(item.label || enumLabel(item.key, HEALTH_CHECK_LABELS)),
        `${item.ok ? "可用" : "待积累"}(${Number(item.count || 0)})`,
      ])
      : [];
    record.append(title, recordLines([clean(health.summary), ...checks]));
    records.push(record);
  }

  visibleEpisodes.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    const badge = [enumLabel(item.kind, EPISODE_KIND_LABELS), enumLabel(item.status, EPISODE_STATUS_LABELS), item.protected ? "已保护" : ""].filter(Boolean).join(" · ");
    title.append(node("span", "", clean(item.title)), node("span", "muted", badge || clean(item.date)));
    const people = Array.isArray(item.related_people) && item.related_people.length ? `人物：${item.related_people.join("、")}` : "";
    record.append(title, recordLines([...lifeEpisodeLines(item), people].filter(Boolean)));
    records.push(record);
  });

  focusTargets.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", clean(item.label || item.target_id)), node("span", "muted", `关注 ${Number(item.priority || 0)}`));
    const scope = enumLabel(item.scope, SCOPE_LABELS) || enumLabel(item.target_type, TARGET_TYPE_LABELS);
    record.append(title, recordLines([clean(item.reason, "近期自然多留意"), scope ? `范围：${scope}` : ""]));
    records.push(record);
  });

  feedback.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", enumLabel(item.action, ACTION_LABELS) || clean(item.scene || "行为反馈")), node("span", "muted", `${enumLabel(item.result, FEEDBACK_RESULT_LABELS)} ${Number(item.score || 0)}`));
    const scene = enumLabel(item.scene, SCENE_TYPE_LABELS) || enumLabel(item.source, SOURCE_LABELS);
    record.append(title, recordLines([clean(item.feedback || item.reason, "暂无反馈说明"), scene ? `场景：${scene}` : ""]));
    records.push(record);
  });

  terms.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", clean(item.term)), node("span", "muted", enumLabel(item.scope, SCOPE_LABELS) || clean(item.last_seen)));
    record.append(title, recordLines([clean(item.meaning), item.evidence ? `证据：${item.evidence}` : ""]));
    records.push(record);
  });

  visibleEvidence.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(
      node("span", "", evidenceTargetTitle(item)),
      node("span", "muted", enumLabel(item.evidence_type, EVIDENCE_TYPE_LABELS))
    );
    record.append(title, recordLines([item.summary]));
    records.push(record);
  });

  el.experienceList.replaceChildren(...records);
  if (!records.length) {
    el.experienceList.replaceChildren(empty("暂无可展示的体验层记录"));
  }
}

function renderDashboard() {
  const status = state.status || {};
  renderDay(status);
  renderWeek(status);
  renderWorld(status);
  renderLifecycle(status);
  renderExperience(status);
  workshopPanel.renderTemplates(status);
  workshopPanel.renderCatalog(status);
}

const configPanel = createConfigPanel({
  state,
  el,
  node,
  empty,
  setBusy,
  setNotice,
  loadStatus,
});
const { loadConfig, renderConfig } = configPanel;

const workshopPanel = createWorkshopPanel({
  state,
  el,
  node,
  empty,
  setNotice,
  runAction,
});

async function loadStatus({ quiet = false } = {}) {
  try {
    applyStatus(await apiGet("page/status", { _ts: Date.now() }));
    if (!quiet) setNotice("");
  } catch (error) {
    setNotice(error.message || "状态加载失败", "error");
  }
}

async function runAction(action, successMessage) {
  setBusy(true);
  try {
    const result = await action();
    const rendered = applyActionStatus(result);
    if (!rendered) await loadStatus({ quiet: true });
    setNotice(successMessage, "success");
    return result;
  } catch (error) {
    setNotice(error.message || "操作失败", "error");
    return null;
  } finally {
    setBusy(false);
  }
}

function applyActionStatus(result) {
  if (!result || typeof result !== "object") return false;
  if (result.status && typeof result.status === "object") {
    state.status = result.status;
    state.timelineEditing = false;
    state.timelineDraft = [];
    renderDashboard();
    return true;
  }
  if (result.day && state.status && typeof state.status === "object") {
    state.status = {
      ...state.status,
      day: result.day,
      target_date: result.day.date || state.status.target_date,
    };
    state.timelineEditing = false;
    state.timelineDraft = [];
    renderDashboard();
    return true;
  }
  return false;
}

async function resetDay(extra = "", useWeb = false) {
  const payload = { extra };
  if (useWeb) {
    payload.use_web = true;
  }
  await runAction(
    () => apiPost(
      "page/action/reset-day",
      payload,
      { timeoutMs: GENERATION_TIMEOUT_MS, timeoutMessage: "重生成耗时较久，请稍后刷新面板查看结果" }
    ),
    useWeb ? "已联网填充今日生活背景" : "今日生活背景已重生成"
  );
}

async function refreshState() {
  await runAction(
    () => apiPost(
      "page/action/refresh-state",
      {},
      { timeoutMs: GENERATION_TIMEOUT_MS, timeoutMessage: "状态刷新耗时较久，请稍后查看面板" }
    ),
    "实时状态已刷新"
  );
}

async function generateWeek(goals = "", useWeb = false) {
  const templateId = el.weekTemplateSelect.value || "random";
  await runAction(
    () => apiPost(
      "page/action/generate-week",
      { template_id: templateId, goals, use_web: useWeb },
      { timeoutMs: GENERATION_TIMEOUT_MS, timeoutMessage: "周计划生成耗时较久，请稍后刷新面板查看结果" }
    ),
    useWeb ? "已联网填充周计划" : "周计划已生成"
  );
}

function beginTimelineEdit() {
  const day = state.status?.day;
  if (!day) return;
  state.timelineEditing = true;
  state.timelineDraft = cloneTimeline(day.timeline);
  renderDashboard();
}

function cancelTimelineEdit() {
  state.timelineEditing = false;
  state.timelineDraft = [];
  renderDashboard();
}

function addTimelineItem() {
  updateTimelineDraftFromInputs();
  state.timelineDraft.push({ time: "12:00", activity: "", status: "" });
  renderTimelineEditor();
}

async function saveTimeline() {
  updateTimelineDraftFromInputs();
  const timeline = cloneTimeline(state.timelineDraft).filter((item) => item.time || item.activity || item.status);
  const missing = timeline.find((item) => !item.time || !item.activity);
  if (missing) {
    setNotice("时间轴每一项都需要时间和活动", "error");
    return;
  }
  const date = state.status?.day?.date || state.status?.target_date || "";
  const result = await runAction(
    () => apiPost("page/timeline/save", { date, timeline }),
    "时间轴已保存"
  );
  if (result?.day) {
    state.timelineEditing = false;
    state.timelineDraft = [];
    renderDashboard();
  }
}

function bindEvents() {
  bindAutoRefreshEvents();
  el.viewButtons.forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  el.resetDayButton.addEventListener("click", () => resetDay(""));
  el.refreshStateButton?.addEventListener("click", refreshState);
  el.timelineEditButton.addEventListener("click", beginTimelineEdit);
  el.timelineAddButton.addEventListener("click", addTimelineItem);
  el.timelineCancelButton.addEventListener("click", cancelTimelineEdit);
  el.timelineSaveButton.addEventListener("click", saveTimeline);
  workshopPanel.bindEvents();
  el.weekForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    generateWeek(el.weekGoalsInput.value.trim());
  });
  el.weekWebButton?.addEventListener("click", () => generateWeek(el.weekGoalsInput.value.trim(), true));
  el.worldTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      state.worldTab = tab.dataset.worldTab;
      renderWorld(state.status || {});
    });
  });
}

async function init() {
  bindEvents();
  setView("dashboard");
  startClock();
  if (!bridge) {
    setNotice("没有检测到页面桥接，请从网页管理后台的插件页面进入。", "error");
    renderDashboard();
    renderConfig();
    return;
  }
  try {
    await withTimeout(bridge.ready(), "桥接初始化超时");
    state.bridgeReady = true;
    await loadStatus();
    startStatusAutoRefresh();
  } catch (error) {
    setNotice(error.message || "桥接初始化失败", "error");
  }
}

init();
