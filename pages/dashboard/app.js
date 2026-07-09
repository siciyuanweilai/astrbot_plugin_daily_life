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
  LIFE_DECISION_KIND_LABELS,
  PLACE_TYPE_LABELS,
  PREFERENCE_CATEGORY_LABELS,
  RHYTHM_LIFECYCLE_LABELS,
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

import { createConfigPanel } from "./ui/config.js?v=20260709-life-generation";
import { createDashboardEffects } from "./ui/effects.js?v=20260709-life-effects";
import { createLifeSelectControls } from "./ui/selects.js?v=20260709-life-settings-fast";

const NOTICE_HIDE_MS = 4200;
const STATUS_WAIT_SECONDS = 25;
const STATUS_RETRY_DELAY_MS = 2000;
const MEMO_CAROUSEL_MS = 6500;
const EMOJI_AUTO_REFRESH_MS = 10000;
const EMOJI_IMPORT_MAX_MB = 20;
const EMOJI_IMPORT_MAX_BYTES = EMOJI_IMPORT_MAX_MB * 1024 * 1024;
const EMOJI_BACKUP_MAX_MB = 200;
const EMOJI_BACKUP_MAX_BYTES = EMOJI_BACKUP_MAX_MB * 1024 * 1024;
const EMOJI_ANIMATED_PREVIEW_STAGGER_MS = 80;
const EMOJI_PAGE_SIZE = 30;
const MEMO_EMPTY_TEXT = "暂无备忘录";
const CURRENT_ACTIVITY_EMPTY_TEXT = "暂无当前活动";
const METER_EMPTY_TEXT = "暂无数据";
const TIMELINE_TIME_EMPTY_TEXT = "未定";
const TODAY_FACT_EMPTY_TEXT = {
  weatherText: "暂无天气",
  themeText: "暂无主题",
  todayWeekPlan: "暂无周计划",
  moodColorText: "暂无心情色彩",
  scheduleTypeText: "暂无日程类型",
  scheduleToneText: "暂无日程基调",
  scheduleIntentText: "暂无活动倾向",
  currentOutfitText: "暂无穿搭",
  outfitDecisionText: "暂无判断",
};
const FACT_CARD_ORDER = [
  "weather",
  "theme",
  "week",
  "mood",
  "schedule-type",
  "schedule-tone",
  "schedule-intent",
  "outfit",
  "outfit-decision",
  "memo",
];

const HEALTH_CHECK_KEYS = [
  "episodes",
  "evidence",
  "focus",
  "feedback",
  "emotion_arcs",
  "physiological_rhythm_logs",
  "terms",
  "boundaries",
  "memory_rows",
];

const HERO_COPY = {
  dashboard: {
    eyebrow: "Daily Life · 把今天装进生活手帐",
    title: "日常生活工作台",
    subtitle: "今日、时间轴、状态和记忆分格摆好，像一张柔软又清楚的少女生活桌面。",
  },
  emoji: {
    eyebrow: "Emoji Pocket · 把情绪收进贴纸夹",
    title: "表情管理",
    subtitle: "收藏、识图、导入和启停都放在一处，让表情在合适的时候自然出现。",
  },
  settings: {
    eyebrow: "Soft Settings · 把规则整理成抽屉",
    title: "运行规则",
    subtitle: "调整聊天表达、生活节奏、媒体能力和记忆边界，让角色按你的习惯运行。",
  },
};

const state = {
  view: "dashboard",
  status: null,
  emojiItems: [],
  emojiStats: {},
  emojiFilter: "all",
  emojiPage: 1,
  emojiLoaded: false,
  emojiLoading: false,
  emojiRefreshTimer: 0,
  emojiDetailId: 0,
  emojiManageMode: false,
  emojiSelectedIds: new Set(),
  emojiPreviewCache: new Map(),
  emojiAnimatedPreviewObserver: null,
  emojiAnimatedPreviewSeq: 0,
  memoryTab: "world",
  worldTab: "life_decisions",
  experienceTab: "relationships",
  noticeTimer: 0,
  busy: false,
  configSchema: {},
  config: {},
  providers: [],
  configSectionKey: "",
  configDirty: false,
  configLoaded: false,
  configLoading: false,
  configSaveTimer: 0,
  configSaving: false,
  configSaveQueued: false,
  configDirtySince: 0,
  configChangeSeq: 0,
  configVersion: 0,
  configLoadFrame: 0,
  configLoadTimer: 0,
  timelineEditing: false,
  timelineDraft: [],
  memoCarouselIndex: 0,
  memoCarouselKey: "",
  memoCarouselTimer: 0,
  clockTimer: 0,
  todayFactsLayoutTimer: 0,
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
  lifeDriftLayer: byId("lifeDriftLayer"),
  cursorTrailLayer: byId("cursorTrailLayer"),
  heroEyebrow: byId("heroEyebrow"),
  heroTitle: byId("heroTitle"),
  heroSubtitle: byId("heroSubtitle"),
  dashboardView: byId("dashboardView"),
  emojiView: byId("emojiView"),
  settingsView: byId("settingsView"),
  viewButtons: all(".view-button"),
  actionGroups: all("[data-action-view]"),
  memoryTabs: all("[data-memory-tab]"),
  memoryPanels: all("[data-memory-panel]"),
  resetDayButton: byId("resetDayButton"),
  refreshStateButton: byId("refreshStateButton"),
  targetDate: byId("targetDate"),
  todayFacts: byId("todayFacts"),
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
  experienceTabs: all("[data-experience-tab]"),
  worldList: byId("worldList"),
  lifecycleList: byId("lifecycleList"),
  experienceList: byId("experienceList"),
  emojiSummary: byId("emojiSummary"),
  emojiFilter: byId("emojiFilter"),
  emojiImportButton: byId("emojiImportButton"),
  emojiBackupButton: byId("emojiBackupButton"),
  emojiRestoreButton: byId("emojiRestoreButton"),
  emojiRestoreFile: byId("emojiRestoreFile"),
  emojiImportDialog: byId("emojiImportDialog"),
  emojiImportClose: byId("emojiImportClose"),
  emojiImportFile: byId("emojiImportFile"),
  emojiImportFileButton: byId("emojiImportFileButton"),
  emojiManageButton: byId("emojiManageButton"),
  emojiSelectedSummary: byId("emojiSelectedSummary"),
  emojiBulkEnableButton: byId("emojiBulkEnableButton"),
  emojiBulkDisableButton: byId("emojiBulkDisableButton"),
  emojiBulkDeleteButton: byId("emojiBulkDeleteButton"),
  emojiCancelManageButton: byId("emojiCancelManageButton"),
  emojiStats: byId("emojiStats"),
  emojiPager: byId("emojiPager"),
  emojiPrevPage: byId("emojiPrevPage"),
  emojiPageInfo: byId("emojiPageInfo"),
  emojiNextPage: byId("emojiNextPage"),
  emojiList: byId("emojiList"),
  emojiDetailDialog: byId("emojiDetailDialog"),
  emojiDetailTitle: byId("emojiDetailTitle"),
  emojiDetailBody: byId("emojiDetailBody"),
  emojiDetailClose: byId("emojiDetailClose"),
  configNav: byId("configNav"),
  configSectionTitle: byId("configSectionTitle"),
  configSectionHint: byId("configSectionHint"),
  configFieldList: byId("configFieldList"),
};

const dashboardEffects = createDashboardEffects({
  lifeDriftLayer: el.lifeDriftLayer,
  cursorTrailLayer: el.cursorTrailLayer,
});
const lifeSelectControls = createLifeSelectControls();

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

function shouldAutoRefreshEmoji() {
  return state.view === "emoji" && bridge && state.bridgeReady && !document.hidden;
}

function stopEmojiAutoRefresh() {
  window.clearTimeout(state.emojiRefreshTimer);
  state.emojiRefreshTimer = 0;
}

function scheduleEmojiAutoRefresh(delayMs = EMOJI_AUTO_REFRESH_MS) {
  stopEmojiAutoRefresh();
  if (!shouldAutoRefreshEmoji()) return;
  state.emojiRefreshTimer = window.setTimeout(async () => {
    if (!shouldAutoRefreshEmoji()) return;
    await loadEmojiAssets({ quiet: true });
    scheduleEmojiAutoRefresh();
  }, Math.max(0, delayMs));
}

function bindAutoRefreshEvents() {
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      flushConfigAutosave();
      stopEmojiAutoRefresh();
      return;
    }
    if (!document.hidden && state.view === "dashboard" && bridge && state.bridgeReady) {
      loadStatus({ quiet: true });
      scheduleStatusWatch(0);
    } else if (!document.hidden && state.view === "emoji" && bridge && state.bridgeReady) {
      loadEmojiAssets({ quiet: true });
      scheduleEmojiAutoRefresh();
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

function currentViewElement() {
  if (state.view === "settings") return el.settingsView;
  if (state.view === "emoji") return el.emojiView;
  return el.dashboardView;
}

function collectFormControls(scope, controls) {
  if (!scope) return;
  if (scope.matches?.("button, input, select, textarea")) controls.add(scope);
  scope.querySelectorAll?.("button, input, select, textarea").forEach((item) => controls.add(item));
}

function setBusy(value) {
  state.busy = Boolean(value);
  const controls = new Set(el.viewButtons);
  collectFormControls(currentViewElement(), controls);
  el.actionGroups.forEach((group) => {
    if (!group.hidden) collectFormControls(group, controls);
  });
  controls.forEach((item) => {
    item.disabled = state.busy || item.dataset.lockDisabled === "true";
  });
  lifeSelectControls.syncSelects(currentViewElement());
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

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result || "")));
    reader.addEventListener("error", () => reject(reader.error || new Error("文件读取失败")));
    reader.readAsDataURL(file);
  });
}

function downloadDataUrl(filename, dataUrl) {
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = clean(filename, "daily_life_emoji_backup.zip");
  document.body.append(link);
  link.click();
  link.remove();
}

function typedLabel(value, labels, fallbackLabels = []) {
  const raw = text(value).trim();
  if (!raw) return "";
  for (const table of [labels, ...fallbackLabels]) {
    if (!table) continue;
    const translated = table[raw] || table[raw.toLowerCase()];
    if (translated) return translated;
  }
  return clean(raw, raw);
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

function relationshipDisplayName(item = {}) {
  return clean(item.display_name || item.subjective_name || item.name || item.alias || "", "");
}

const RELATIONSHIP_REFERENCE_PREFIXES = ["profile", "relationship", "group_profile", "群友档案", "关系"];

function addRelationshipName(index, key, label) {
  const raw = text(key).trim();
  const name = text(label).trim();
  if (!raw || !name || raw === name) return;
  index.set(raw, name);
  const parts = raw.split(/[:：]/).map((part) => part.trim()).filter(Boolean);
  const id = parts.length > 1 ? parts[parts.length - 1] : raw;
  if (id && id !== name) index.set(id, name);
  RELATIONSHIP_REFERENCE_PREFIXES.forEach((prefix) => {
    index.set(`${prefix}:${id}`, name);
  });
}

function addGroupScopeName(index, key, label) {
  const raw = text(key).trim();
  const name = text(label).trim();
  if (!raw || !name || raw === name) return;
  if (!index.has(raw)) index.set(raw, name);
  const parts = raw.split(/[:：]/).map((part) => part.trim()).filter(Boolean);
  const id = parts.length > 1 ? parts[parts.length - 1] : raw;
  if (id && id !== name && !index.has(id)) index.set(id, name);
}

export function relationshipNameIndex(status = {}) {
  const index = new Map();
  objectItems(status.world?.relationships).forEach((item) => {
    const label = relationshipDisplayName(item);
    if (!label) return;
    ["id", "user_id", "profile_id", "target_scope"].forEach((key) => addRelationshipName(index, item[key], label));
    objectItems(item.contacts).forEach((contact) => {
      ["user_id", "target_scope", "profile_id"].forEach((key) => addRelationshipName(index, contact[key], label));
    });
  });
  objectItems(status.world?.group_environments).forEach((item) => {
    const label = clean(item.group_name, "");
    if (!label) return;
    ["group_id", "session_id"].forEach((key) => addGroupScopeName(index, item[key], label));
  });
  return index;
}

function resolveRelationshipReference(value, relationshipNames = new Map()) {
  const raw = text(value).trim();
  if (!raw) return "";
  const direct = relationshipNames.get(raw);
  if (direct) return direct;
  const parts = raw.split(/[:：]/).map((part) => part.trim()).filter(Boolean);
  if (parts.length > 1) {
    const id = parts[parts.length - 1];
    const byId = relationshipNames.get(id) || relationshipNames.get(`${parts[0]}:${id}`);
    if (byId) return byId;
  }
  return "";
}

export function relationshipScopeLabel(value, relationshipNames = new Map()) {
  const raw = text(value).trim();
  if (!raw) return "";
  const resolved = resolveRelationshipReference(raw, relationshipNames);
  if (resolved) return resolved;
  return clean(raw, "");
}

export function relationshipReferenceText(value, relationshipNames = new Map()) {
  const raw = text(value).trim();
  if (!raw) return "";
  const resolved = relationshipNames.get(raw);
  if (resolved) return resolved;
  const prefixPattern = RELATIONSHIP_REFERENCE_PREFIXES
    .map((prefix) => prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  const pattern = new RegExp(`(^|[\\s,，:：;；、|｜()（）\\[\\]{}<>《》])(${prefixPattern})[:：]([^\\s,，:：;；、|｜()（）\\[\\]{}<>《》]+)`, "g");
  const body = raw.replace(pattern, (match, lead, prefix, id) => {
    const name = resolveRelationshipReference(`${prefix}:${id}`, relationshipNames);
    return name ? `${lead}${name}` : match;
  });
  return body === raw ? clean(body, "") : body;
}

function relationshipTextResolver(status = {}) {
  const names = relationshipNameIndex(status);
  return {
    names,
    scope: (value) => relationshipScopeLabel(value, names),
    text: (value) => relationshipReferenceText(value, names),
  };
}

function relationshipRecordLines(items, relationshipText) {
  return items.map((item) => (
    Array.isArray(item)
      ? [item[0], relationshipText(item[1])]
      : relationshipText(item)
  ));
}

function renderHeroCopy(view = state.view) {
  const copy = HERO_COPY[view] || HERO_COPY.dashboard;
  if (el.heroEyebrow) el.heroEyebrow.textContent = copy.eyebrow;
  if (el.heroTitle) el.heroTitle.textContent = copy.title;
  if (el.heroSubtitle) el.heroSubtitle.textContent = copy.subtitle;
}

function cancelDeferredConfigLoad() {
  if (state.configLoadFrame) {
    window.cancelAnimationFrame(state.configLoadFrame);
    state.configLoadFrame = 0;
  }
  if (state.configLoadTimer) {
    window.clearTimeout(state.configLoadTimer);
    state.configLoadTimer = 0;
  }
}

function deferConfigLoadForSettings() {
  if (!bridge || state.configLoaded || state.configLoading || state.configLoadFrame || state.configLoadTimer) return;
  state.configLoadFrame = window.requestAnimationFrame(() => {
    state.configLoadFrame = 0;
    state.configLoadTimer = window.setTimeout(() => {
      state.configLoadTimer = 0;
      if (state.view === "settings" && !state.configLoaded && bridge) {
        loadConfig({ quiet: true, busy: false });
      }
    }, 0);
  });
}

function setView(view) {
  if (state.view === "settings" && view !== "settings") flushConfigAutosave();
  state.view = view === "settings" || view === "emoji" ? view : "dashboard";
  if (state.view !== "settings") cancelDeferredConfigLoad();
  renderHeroCopy(state.view);
  el.dashboardView.hidden = state.view !== "dashboard";
  el.emojiView.hidden = state.view !== "emoji";
  el.settingsView.hidden = state.view !== "settings";
  el.viewButtons.forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
  el.actionGroups.forEach((group) => {
    group.hidden = group.dataset.actionView !== state.view;
  });
  if (state.view !== "emoji") {
    resetEmojiManageState();
    closeEmojiDetail();
    closeEmojiImport();
    stopEmojiAutoRefresh();
  }
  if (state.view === "emoji" && bridge && state.bridgeReady) {
    loadEmojiAssets({ quiet: state.emojiLoaded });
    scheduleEmojiAutoRefresh();
  } else if (state.view === "settings" && !state.configLoaded && bridge) {
    deferConfigLoadForSettings();
  } else if (state.view === "dashboard" && bridge && state.bridgeReady) {
    loadStatus({ quiet: true });
    scheduleStatusWatch(0);
  }
  lifeSelectControls.syncSelects(currentViewElement());
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
  top.append(node("span", "", label), node("span", "", percent === null ? METER_EMPTY_TEXT : `${Math.round(percent)}/100`));
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
  const rhythm = lifeState.physiological_rhythm || {};
  const bodyCondition = rhythm.body_condition || {};
  const optionalCycle = rhythm.optional_cycle || {};
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
    "生理节律",
    [
      clean(rhythm.energy_curve, ""),
      bodyCondition.label ? `身体：${clean(bodyCondition.label, "")}${bodyCondition.intensity !== undefined ? ` ${Number(bodyCondition.intensity || 0)}/100` : ""}` : "",
      Array.isArray(rhythm.recovery_actions) && rhythm.recovery_actions.length ? `恢复：${rhythm.recovery_actions.map((item) => clean(item, "")).filter(Boolean).join("、")}` : "",
      rhythm.social_battery !== undefined ? `社交电量：${Number(rhythm.social_battery || 0)}/100` : "",
      clean(rhythm.attention_state, ""),
      clean(rhythm.summary, ""),
    ].filter(Boolean).join(" · ")
  );
  if (optionalCycle.enabled) {
    appendInfoBox(
      "可选周期",
      [
        clean(optionalCycle.label, "可选周期"),
        optionalCycle.intensity !== undefined ? `${Number(optionalCycle.intensity || 0)}/100` : "",
        clean(optionalCycle.source, ""),
      ].filter(Boolean).join(" · ")
    );
  }
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
      li.append(node("div", "time", clean(item.time, TIMELINE_TIME_EMPTY_TEXT)));
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

function memoDisplayText(status = {}) {
  const items = memoCarouselItems(status);
  if (!items.length) return MEMO_EMPTY_TEXT;
  const index = Math.min(Math.max(Number(state.memoCarouselIndex || 0), 0), items.length - 1);
  return clean(items[index].display_text, MEMO_EMPTY_TEXT);
}

function memoCarouselItems(status = {}) {
  const memo = status.memo && typeof status.memo === "object" ? status.memo : {};
  return (Array.isArray(memo.items) ? memo.items : []).filter((item) => (
    item && typeof item === "object" && clean(item.display_text, "")
  ));
}

function memoCarouselKey(items = []) {
  return items.map((item) => [
    clean(item.date, ""),
    clean(item.scope, ""),
    clean(item.text, ""),
    clean(item.display_text, ""),
  ].join("\u0001")).join("\u0002");
}

function stopMemoCarousel() {
  if (!state.memoCarouselTimer) return;
  window.clearInterval(state.memoCarouselTimer);
  state.memoCarouselTimer = 0;
}

function syncMemoCarousel(status = {}) {
  const items = memoCarouselItems(status);
  const key = memoCarouselKey(items);
  if (key !== state.memoCarouselKey) {
    state.memoCarouselKey = key;
    state.memoCarouselIndex = 0;
  }
  if (items.length <= 1) {
    stopMemoCarousel();
    return;
  }
  if (state.memoCarouselTimer) return;
  state.memoCarouselTimer = window.setInterval(() => {
    const liveStatus = state.status || {};
    const liveItems = memoCarouselItems(liveStatus);
    if (liveItems.length <= 1) {
      state.memoCarouselIndex = 0;
      stopMemoCarousel();
    } else {
      state.memoCarouselIndex = (Number(state.memoCarouselIndex || 0) + 1) % liveItems.length;
    }
    if (el.memoText) el.memoText.textContent = memoDisplayText(liveStatus);
  }, MEMO_CAROUSEL_MS);
}

function renderMemo(status = {}) {
  syncMemoCarousel(status);
  el.memoText.textContent = memoDisplayText(status);
}

function renderEmptyTodayFacts() {
  Object.entries(TODAY_FACT_EMPTY_TEXT).forEach(([key, value]) => {
    if (el[key]) el[key].textContent = value;
  });
}

function renderDay(status) {
  const day = status.day;
  syncClock(status);
  el.nowText.textContent = "";
  el.nowText.hidden = true;
  renderTargetDateTime();
  if (!day) {
    el.currentActivity.textContent = "暂无日常生活数据";
    renderEmptyTodayFacts();
    renderMemo(status);
    el.timelineList.replaceChildren(empty("暂无时间轴"));
    setTimelineButtons(false);
    renderMeters({}, status);
    renderStateLogs({});
    return;
  }

  el.weatherText.textContent = clean(day.weather, TODAY_FACT_EMPTY_TEXT.weatherText);
  const meta = day.meta || {};
  el.themeText.textContent = clean(meta.theme, TODAY_FACT_EMPTY_TEXT.themeText);
  renderTodayWeekPlan(status.week_plan || {});
  el.moodColorText.textContent = clean(moodColorText(meta.mood), TODAY_FACT_EMPTY_TEXT.moodColorText);
  el.scheduleTypeText.textContent = clean(scheduleTypeText(meta.schedule_type), TODAY_FACT_EMPTY_TEXT.scheduleTypeText);
  el.scheduleToneText.textContent = clean(enumLabel(meta.life_mode, SCHEDULE_TONE_LABELS), TODAY_FACT_EMPTY_TEXT.scheduleToneText);
  renderRealtimeDayFacts();
  renderFactPair(el.currentOutfitText, currentOutfitDisplayText(day, meta), TODAY_FACT_EMPTY_TEXT.currentOutfitText);
  renderFactPair(el.outfitDecisionText, outfitDecisionText(meta), TODAY_FACT_EMPTY_TEXT.outfitDecisionText);
  renderMemo(status);
  scheduleTodayFactsLayout();
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
    : CURRENT_ACTIVITY_EMPTY_TEXT;
  el.scheduleIntentText.textContent = clean(currentScheduleIntentText(day, clock), TODAY_FACT_EMPTY_TEXT.scheduleIntentText);
}

function renderTodayWeekPlan(week = {}) {
  const theme = clean(stripLeadingEmoji(week.theme), "");
  const hint = clean(stripLeadingEmoji(week.today_hint), "");
  const suggested = clean(stripLeadingEmoji(week.today_suggested), "");
  if (!theme && !hint && !suggested) {
    el.todayWeekPlan.textContent = TODAY_FACT_EMPTY_TEXT.todayWeekPlan;
    return;
  }
  const lines = [];
  if (theme) lines.push(todayWeekRow("主题", theme));
  if (hint) lines.push(todayWeekRow("提示", hint));
  if (suggested) lines.push(todayWeekRow("建议", suggested, "muted"));
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

function renderFactPair(target, value, emptyText = "暂无内容") {
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
    target.textContent = emptyText;
    return;
  }
  wrap.replaceChildren(...parts);
  target.replaceChildren(wrap);
}

function layoutTodayFacts() {
  const root = el.todayFacts;
  if (!root || typeof root.querySelectorAll !== "function") return;
  const columns = Array.from(root.querySelectorAll(".facts-column"));
  if (columns.length < 2) return;
  const cards = new Map(
    Array.from(root.querySelectorAll("[data-fact-card]"))
      .map((card) => [card.dataset.factCard, card])
      .filter(([key, card]) => key && card)
  );
  const leftSize = Math.ceil(FACT_CARD_ORDER.length / 2);
  const left = FACT_CARD_ORDER.slice(0, leftSize).map((key) => cards.get(key)).filter(Boolean);
  const right = FACT_CARD_ORDER.slice(leftSize).map((key) => cards.get(key)).filter(Boolean);
  columns[0].replaceChildren(...left);
  columns[1].replaceChildren(...right);
}

function scheduleTodayFactsLayout() {
  window.clearTimeout(state.todayFactsLayoutTimer);
  state.todayFactsLayoutTimer = window.setTimeout(layoutTodayFacts, 0);
}

function worldEmptyText(tab) {
  const labels = {
    relationships: "暂无关系记录",
    summaries: "暂无会话记录",
    group_environments: "暂无群聊环境记录",
    message_visibility: "暂无留意记录",
    action_decisions: "暂无裁定记录",
    life_decisions: "暂无生活观察记录",
    places: "暂无地点记录",
    events: "暂无事件记录",
  };
  return labels[tab] || "暂无记录";
}

function observationRecord(titleText, metaText, lines = [], className = "") {
  const record = node("div", `record ${className}`.trim());
  const title = node("div", "record-title");
  title.append(node("span", "", clean(titleText, "生活观察")), node("span", "muted", clean(metaText, "")));
  const seen = new Set();
  const uniqueLines = [];
  lines.forEach((line) => {
    const body = Array.isArray(line)
      ? [clean(line[0], ""), clean(line[1], "")]
      : clean(line, "");
    const key = Array.isArray(body) ? body.join("\u0000") : body;
    if (!key || seen.has(key) || (Array.isArray(body) && (!body[0] || !body[1]))) return;
    seen.add(key);
    uniqueLines.push(body);
  });
  record.append(title, recordLines(uniqueLines));
  return record;
}

function lifeObservationRecords(status) {
  const observatory = status.observatory || {};
  const relationshipText = relationshipTextResolver(status).text;
  const records = [];

  const decision = observatory.today_decision && typeof observatory.today_decision === "object"
    ? observatory.today_decision
    : {};
  if (decision.decision || decision.reason || decision.evidence) {
    const influenceSources = Array.isArray(decision.influence_sources)
      ? decision.influence_sources.map((item) => relationshipText(item)).filter(Boolean).join(" · ")
      : "";
    records.push(
      observationRecord(
        "今日决策摘要",
        typedLabel(decision.kind, LIFE_DECISION_KIND_LABELS) || clean(decision.date),
        [
          ["决策", relationshipText(decision.decision)],
          ["原因", relationshipText(decision.reason)],
          ["依据", relationshipText(decision.evidence)],
          ["来源", influenceSources],
          ["安排", relationshipText(decision.outcome)],
        ]
      )
    );
  }

  return records;
}

function renderWorld(status) {
  const activeTab = text(state.worldTab || "relationships").trim() || "relationships";
  const relationship = relationshipTextResolver(status);
  const relationshipText = relationship.text;
  el.worldTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.worldTab === activeTab));
  if (activeTab === "life_decisions") {
    const records = lifeObservationRecords(status);
    el.worldList.replaceChildren(...(records.length ? records : [empty(worldEmptyText(activeTab))]));
    return;
  }
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
        const story = relationshipText(item.relationship_story);
        const body = [
          subjective && subjective !== displayName ? `主观称呼：${subjective}` : "",
          tags,
          story,
          relationshipText(point?.content || latest?.content) || `最近：${clean(item.last_seen)}`,
        ].filter(Boolean).join(" · ");
        record.append(title, node("div", "record-body", body));
      } else if (activeTab === "summaries") {
        title.append(node("span", "", relationshipText(item.brief || item.long_summary)), node("span", "muted", clean(item.date)));
        record.append(title, node("div", "record-body", relationshipText(item.long_summary || item.brief)));
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
        record.append(title, node("div", "record-body", `${relationshipText(item.topic || item.summary) || "暂无话题"} ${flags.length ? `(${flags.join("、")})` : ""} · ${scores}`));
      } else if (activeTab === "message_visibility") {
        const meta = [
          enumLabel(item.visibility || "seen", VISIBILITY_LABELS),
          item.attention_level || item.attention_level === 0 ? `注意 ${Number(item.attention_level || 0)}` : "",
          enumLabel(item.freshness, FRESHNESS_LABELS),
          item.psychological_freshness || item.psychological_freshness === 0 ? `心理 ${Number(item.psychological_freshness || 0)}` : "",
          item.reactivated_from_id ? `激活 #${item.reactivated_from_id}` : "",
        ].filter(Boolean).join(" · ");
        title.append(node("span", "", relationship.scope(item.sender_name || item.sender_profile_id) || "未知"), node("span", "muted", meta));
        const reactivation = relationshipText(item.reactivation_hint);
        record.append(title, node("div", "record-body", `${relationshipText(item.reason) || "无留意说明"}${reactivation ? ` · 再激活：${reactivation}` : ""}`));
      } else if (activeTab === "action_decisions") {
        const meta = [enumLabel(item.scene_type, SCENE_TYPE_LABELS), enumLabel(item.understanding, UNDERSTANDING_LABELS), item.deep_analysis ? "深析" : ""].filter(Boolean).join(" · ");
        title.append(node("span", "", enumLabel(item.action, ACTION_LABELS) || "未定"), node("span", "muted", meta || `${Math.round(Number(item.confidence || 0) * 100)}%`));
        record.append(title, node("div", "record-body", relationshipText(item.reason) || "无裁定说明"));
      } else if (activeTab === "places") {
        title.append(node("span", "", clean(item.name)), node("span", "muted", `${item.visits || 0} 次`));
        record.append(title, node("div", "record-body", relationshipText(item.hint) || enumLabel(item.type, PLACE_TYPE_LABELS)));
      } else {
        title.append(node("span", "", relationshipText(item.summary)), node("span", "muted", clean(item.date)));
        const people = Array.isArray(item.people) && item.people.length ? ` · ${item.people.map((person) => relationship.scope(person) || clean(person, "")).filter(Boolean).join("、")}` : "";
        record.append(title, node("div", "record-body", `${relationshipText(item.place) || "未记录地点"}${people}`));
      }
      return record;
    })
  );
}

function renderLifecycle(status) {
  const lifecycle = status.lifecycle || {};
  const relationshipText = relationshipTextResolver(status).text;
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
    record.append(title, node("div", "record-body", relationshipText(item.summary)));
    records.push(record);
  });
  preferences.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", relationshipText(item.content)), node("span", "muted", enumLabel(item.category, PREFERENCE_CATEGORY_LABELS)));
    const evidence = evidenceText(item.evidence, item.source);
    record.append(title, node("div", "record-body", `权重 ${Number(item.weight || 0).toFixed(1)} · ${relationshipText(evidence)}`));
    records.push(record);
  });
  events.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", relationshipText(item.title)), node("span", "muted", enumLabel(item.status, EVENT_STATUS_LABELS)));
    record.append(title, node("div", "record-body", relationshipText(item.effect || item.detail)));
    records.push(record);
  });
  el.lifecycleList.replaceChildren(...records);
  if (!records.length) {
    el.lifecycleList.replaceChildren(empty("暂无可展示的生活演化记录"));
  }
}

function experienceEmptyText(tab) {
  const labels = {
    relationships: "暂无关系记录",
    behavior: "暂无行为记录",
    language: "暂无语言记录",
    evidence: "暂无证据记录",
    feedback: "暂无反馈记录",
  };
  return labels[tab] || "暂无体验层记录";
}

function healthCheckRows(checks = []) {
  const byKey = new Map();
  checks.forEach((item) => {
    if (!item || typeof item !== "object") return;
    const key = clean(item.key, "");
    if (key) byKey.set(key, item);
  });
  const orderedKeys = [...HEALTH_CHECK_KEYS];
  checks.forEach((item) => {
    const key = clean(item && item.key, "");
    if (key && !orderedKeys.includes(key)) orderedKeys.push(key);
  });
  return orderedKeys.map((key) => {
    const item = byKey.get(key) || {};
    const count = Number(item.count || 0);
    return {
      key,
      label: clean(item.label || enumLabel(key, HEALTH_CHECK_LABELS)),
      count: Number.isFinite(count) ? count : 0,
    };
  });
}

function healthExperienceRecord(health = {}) {
  const checks = Array.isArray(health.checks) ? health.checks : [];
  const summary = clean(health.summary, "");
  if (!summary && !checks.length) return null;
  const rows = healthCheckRows(checks);
  const record = node("div", "record health-record");
  const title = node("div", "record-title");
  title.append(node("span", "", "健康检查"), node("span", "muted", `${Number(health.score || 0)} 分`));
  const body = node("div", "record-body");
  if (summary) body.append(node("p", "health-summary", summary));
  if (rows.length) {
    const list = node("div", "health-check-list");
    rows.forEach((item) => {
      const row = node("div", "health-check-item");
      row.append(
        node("span", "health-check-label", item.label),
        node("span", "health-check-count", item.count)
      );
      list.append(row);
    });
    body.append(list);
  }
  record.append(title, body);
  return record;
}

function experienceGroups(status) {
  const experience = status.experience || {};
  const episodes = objectItems(experience.episodes);
  const visibleEpisodes = visibleLifeEpisodes(episodes);
  const evidence = objectItems(experience.evidence);
  const visibleEvidence = visibleExperienceEvidence(evidence, episodes);
  const feedback = uniqueExperienceFeedback(experience.feedback);
  const emotionArcs = objectItems(experience.emotion_arcs);
  const rhythmLogs = objectItems(experience.physiological_rhythm_logs);
  const rhythmTrend = experience.physiological_rhythm_trend && typeof experience.physiological_rhythm_trend === "object"
    ? experience.physiological_rhythm_trend
    : {};
  const focusTargets = objectItems(experience.focus_targets);
  const terms = objectItems(experience.terms);
  const longTermMemories = objectItems(experience.long_term_memories);
  const memoryClusters = objectItems(experience.memory_clusters);
  const memoryEntities = objectItems(experience.memory_entities);
  const memoryConflicts = objectItems(experience.memory_conflicts);
  const health = experience.health && typeof experience.health === "object" ? experience.health : {};
  const relationshipNames = relationshipNameIndex(status);
  const relationshipText = (value) => relationshipReferenceText(value, relationshipNames);
  const groups = {
    relationships: [],
    behavior: [],
    language: [],
    evidence: [],
    feedback: [],
  };

  const healthRecord = healthExperienceRecord(health);
  if (healthRecord) groups.evidence.push(healthRecord);

  if (rhythmLogs.length || rhythmTrend.summary) {
    const record = node("div", "record");
    const title = node("div", "record-title");
    const subtitle = [
      rhythmTrend.average_body_intensity !== undefined ? `身体 ${Number(rhythmTrend.average_body_intensity || 0)}/100` : "",
      rhythmTrend.average_social_battery !== undefined ? `社交 ${Number(rhythmTrend.average_social_battery || 0)}/100` : "",
    ].filter(Boolean).join(" · ");
    title.append(node("span", "", "生理节律趋势"), node("span", "muted", subtitle || "近期"));
    const recentLines = rhythmLogs.slice(0, 3).map((item) => {
      const marker = enumLabel(item.lifecycle_kind, RHYTHM_LIFECYCLE_LABELS);
      const body = [
        clean(item.summary || item.body_label || item.energy_curve, ""),
        item.body_intensity !== undefined ? `身体负荷：${Number(item.body_intensity || 0)}/100` : "",
        item.social_battery !== undefined ? `社交电量：${Number(item.social_battery || 0)}/100` : "",
      ].filter(Boolean).join("；");
      return body ? [marker || "近期", body] : "";
    }).filter(Boolean);
    record.append(title, recordLines([
      clean(rhythmTrend.summary, ""),
      ...recentLines,
    ]));
    groups.behavior.push(record);
  }

  emotionArcs.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(
      node("span", "", clean(item.label, "情绪脉络")),
      node("span", "muted", `强度 ${Number(item.intensity || 0)}/100`)
    );
    record.append(title, recordLines([
      `正负向：${Number(item.valence || 0)} · 唤醒度：${Number(item.arousal || 0)} · 稳定度：${Number(item.stability || 0)}`,
      item.trigger ? ["触发", relationshipText(item.trigger)] : "",
      item.evidence ? ["依据", relationshipText(item.evidence)] : "",
      item.influence ? ["影响", relationshipText(item.influence)] : "",
      item.expires_at ? `有效期：${clean(item.expires_at)}` : "",
    ]));
    groups.behavior.push(record);
  });

  visibleEpisodes.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    const badge = [enumLabel(item.kind, EPISODE_KIND_LABELS), enumLabel(item.status, EPISODE_STATUS_LABELS), item.protected ? "已保护" : ""].filter(Boolean).join(" · ");
    title.append(node("span", "", clean(item.title)), node("span", "muted", badge || clean(item.date)));
    const people = Array.isArray(item.related_people) && item.related_people.length
      ? ["人物", item.related_people.map((person) => relationshipScopeLabel(person, relationshipNames) || clean(person, "")).filter(Boolean).join("、")]
      : "";
    record.append(title, recordLines(relationshipRecordLines([...lifeEpisodeLines(item), people].filter(Boolean), relationshipText)));
    groups.behavior.push(record);
  });

  focusTargets.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", relationshipScopeLabel(item.label || item.target_id, relationshipNames) || clean(item.label || item.target_id)), node("span", "muted", `关注 ${Number(item.priority || 0)}`));
    const scope = relationshipScopeLabel(item.scope, relationshipNames) || enumLabel(item.scope, SCOPE_LABELS) || enumLabel(item.target_type, TARGET_TYPE_LABELS);
    record.append(title, recordLines([relationshipText(item.reason) || "近期自然多留意", scope ? ["范围", scope] : ""]));
    groups.behavior.push(record);
  });

  feedback.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", enumLabel(item.action, ACTION_LABELS) || clean(item.scene || "行为反馈")), node("span", "muted", `${enumLabel(item.result, FEEDBACK_RESULT_LABELS)} ${Number(item.score || 0)}`));
    const scene = enumLabel(item.scene, SCENE_TYPE_LABELS) || enumLabel(item.source, SOURCE_LABELS);
    record.append(title, recordLines([relationshipText(item.feedback || item.reason) || "暂无反馈说明", scene ? `场景：${scene}` : ""]));
    groups.feedback.push(record);
  });

  terms.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", clean(item.term)), node("span", "muted", enumLabel(item.scope, SCOPE_LABELS) || clean(item.last_seen)));
    record.append(title, recordLines([clean(item.meaning), item.evidence ? ["证据", relationshipText(item.evidence)] : ""]));
    groups.language.push(record);
  });

  memoryClusters.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", relationshipScopeLabel(item.title, relationshipNames) || clean(item.title, "经历聚合")), node("span", "muted", `${Number(item.memory_count || 0)} 条`));
    record.append(title, recordLines([
      relationshipText(item.summary),
      clean([item.first_date, item.last_date].filter(Boolean).join(" 至 ")),
      item.scope ? ["范围", relationshipScopeLabel(item.scope, relationshipNames)] : "",
    ]));
    groups.relationships.push(record);
  });

  memoryEntities.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", relationshipScopeLabel(item.name, relationshipNames) || clean(item.name, "记忆实体")), node("span", "muted", `${clean(item.entity_type, "topic")} · ${Number(item.mention_count || 0)} 次`));
    record.append(title, recordLines([
      item.last_seen ? `最近出现：${item.last_seen}` : "",
      item.scope ? ["范围", relationshipScopeLabel(item.scope, relationshipNames)] : "",
    ]));
    groups.relationships.push(record);
  });

  memoryConflicts.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", "记忆张力"), node("span", "muted", clean(item.conflict_type, "待判断")));
    record.append(title, recordLines([relationshipText(item.summary), relationshipText(item.resolution)]));
    groups.relationships.push(record);
  });

  longTermMemories.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    const scope = relationshipScopeLabel(item.scope, relationshipNames);
    title.append(
      node("span", "", relationshipScopeLabel(item.title || item.scope, relationshipNames) || clean(item.title || item.category, "长期记忆")),
      node("span", "muted", scope || clean(item.category))
    );
    record.append(title, recordLines([
      relationshipText(item.content),
      scope ? ["范围", scope] : "",
      item.expires_at ? `有效期：${item.expires_at}` : "",
    ]));
    groups.relationships.push(record);
  });

  visibleEvidence.slice(0, 3).forEach((item, index) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(
      node("span", "", evidenceTargetTitle(item, index + 1)),
      node("span", "muted", enumLabel(item.evidence_type, EVIDENCE_TYPE_LABELS))
    );
    record.append(title, recordLines([relationshipText(item.summary)]));
    groups.evidence.push(record);
  });

  return groups;
}

function renderExperience(status) {
  if (!el.experienceList) return;
  const groups = experienceGroups(status);
  const activeTab = Object.prototype.hasOwnProperty.call(groups, state.experienceTab)
    ? state.experienceTab
    : "relationships";
  state.experienceTab = activeTab;
  el.experienceTabs.forEach((tab) => {
    const active = tab.dataset.experienceTab === activeTab;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  const total = Object.values(groups).reduce((sum, records) => sum + records.length, 0);
  if (!total) {
    el.experienceList.replaceChildren(empty("暂无体验层记录"));
    return;
  }
  const records = groups[activeTab] || [];
  el.experienceList.replaceChildren(...(records.length ? records : [empty(experienceEmptyText(activeTab))]));
}

const EMOJI_SOURCE_LABELS = {
  manual: "手动导入",
  review: "识图收集",
  trusted: "平台表情",
  plain_image: "普通图片",
};

const EMOJI_STATUS_LABELS = {
  ready: "可用",
  pending: "待识别",
  reviewing: "待确认",
  rejected: "已拒绝",
  missing: "缺失",
  failed: "失败",
  disabled: "已停用",
};

const EMOJI_TYPE_LABELS = {
  emoji: "表情",
  sticker: "贴纸",
  reaction: "反应",
  meme: "梗图",
  other: "其他",
  image: "图片",
};

function emojiSourceLabel(item = {}) {
  return enumLabel(item.source_kind, EMOJI_SOURCE_LABELS) || clean(item.source_kind, "未知来源");
}

function emojiStatusLabel(item = {}) {
  const status = enumLabel(item.status, EMOJI_STATUS_LABELS) || clean(item.status, "未知");
  if (item.status === "ready" && !item.sendable) return `${status} · 停用`;
  return status;
}

function emojiStatusMark(item = {}) {
  return item.status === "ready" && item.sendable ? "✔️" : "❌";
}

function emojiTypeLabel(item = {}) {
  return enumLabel(item.asset_type, EMOJI_TYPE_LABELS) || clean(item.asset_type, "未分类");
}

function emojiCompactMeta(item = {}) {
  return [emojiSourceLabel(item), emojiTypeLabel(item), `${Number(item.used_count || 0)} 次`]
    .filter(Boolean)
    .join(" · ");
}

function emojiItemById(id) {
  const targetId = Number(id || 0);
  if (targetId <= 0) return null;
  return objectItems(state.emojiItems).find((item) => Number(item.id || 0) === targetId) || null;
}

function emojiSelectedIds() {
  return Array.from(state.emojiSelectedIds || [])
    .map((id) => Number(id || 0))
    .filter((id) => id > 0);
}

function pruneEmojiSelection() {
  const liveIds = new Set(objectItems(state.emojiItems).map((item) => Number(item.id || 0)).filter((id) => id > 0));
  state.emojiSelectedIds = new Set(emojiSelectedIds().filter((id) => liveIds.has(id)));
}

function setEmojiSelected(id, selected) {
  if (!state.emojiManageMode) return;
  const emojiId = Number(id || 0);
  if (emojiId <= 0) return;
  resetEmojiBulkDeleteButton();
  if (selected) {
    state.emojiSelectedIds.add(emojiId);
  } else {
    state.emojiSelectedIds.delete(emojiId);
  }
  renderEmojiManagement();
}

function toggleEmojiSelected(id) {
  const emojiId = Number(id || 0);
  if (emojiId <= 0) return;
  setEmojiSelected(emojiId, !state.emojiSelectedIds.has(emojiId));
}

function beginEmojiManage() {
  state.emojiManageMode = true;
  renderEmojiManagement();
}

function resetEmojiManageState() {
  resetEmojiBulkDeleteButton();
  state.emojiSelectedIds.clear();
  state.emojiManageMode = false;
}

function cancelEmojiManage() {
  resetEmojiManageState();
  renderEmojiManagement();
}

function setEmojiBulkButton(button, managing, selectedCount) {
  if (!button) return;
  button.hidden = !managing;
  button.dataset.lockDisabled = managing && selectedCount ? "false" : "true";
  button.disabled = state.busy || !managing || !selectedCount;
}

function renderEmojiSelectionTools() {
  const selected = emojiSelectedIds();
  const managing = Boolean(state.emojiManageMode);
  if (el.emojiManageButton) {
    el.emojiManageButton.classList.toggle("is-active", managing);
    el.emojiManageButton.disabled = state.busy;
  }
  if (el.emojiSelectedSummary) {
    el.emojiSelectedSummary.hidden = !managing;
    el.emojiSelectedSummary.textContent = `已选 ${selected.length} 条`;
  }
  setEmojiBulkButton(el.emojiBulkEnableButton, managing, selected.length);
  setEmojiBulkButton(el.emojiBulkDisableButton, managing, selected.length);
  if (el.emojiBulkDeleteButton) {
    setEmojiBulkButton(el.emojiBulkDeleteButton, managing, selected.length);
    if ((!managing || !selected.length) && el.emojiBulkDeleteButton.dataset.confirmDelete === "true") {
      resetEmojiBulkDeleteButton();
    }
  }
  if (el.emojiCancelManageButton) {
    el.emojiCancelManageButton.hidden = !managing;
    el.emojiCancelManageButton.disabled = state.busy || !managing;
  }
}

function filteredEmojiItems() {
  const filter = text(state.emojiFilter || "all");
  const items = objectItems(state.emojiItems);
  if (filter === "ready") return items.filter((item) => item.status === "ready");
  if (filter === "sendable") return items.filter((item) => item.sendable);
  if (filter === "manual") return items.filter((item) => item.source_kind === "manual");
  if (filter === "review") return items.filter((item) => item.source_kind === "review");
  if (filter === "trusted") return items.filter((item) => item.source_kind === "trusted");
  if (filter === "missing") return items.filter((item) => item.status === "missing" || !item.is_cached);
  return items;
}

function emojiPageWindow(items = []) {
  const pageSize = EMOJI_PAGE_SIZE;
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const rawPage = Number(state.emojiPage || 1);
  const page = Math.min(Math.max(1, Number.isFinite(rawPage) ? Math.trunc(rawPage) : 1), pageCount);
  const start = total ? (page - 1) * pageSize : 0;
  const end = Math.min(start + pageSize, total);
  state.emojiPage = page;
  return {
    page,
    pageCount,
    start,
    end,
    total,
    items: items.slice(start, end),
  };
}

function setEmojiPageButton(button, disabled) {
  if (!button) return;
  button.dataset.lockDisabled = disabled ? "true" : "false";
  button.disabled = state.busy || disabled;
}

function renderEmojiPager(pageInfo) {
  if (!el.emojiPager) return;
  const hasItems = Number(pageInfo?.total || 0) > 0;
  el.emojiPager.hidden = !hasItems;
  if (!hasItems) return;
  setEmojiPageButton(el.emojiPrevPage, pageInfo.page <= 1);
  setEmojiPageButton(el.emojiNextPage, pageInfo.page >= pageInfo.pageCount);
  if (el.emojiPageInfo) {
    el.emojiPageInfo.textContent = "第 " + pageInfo.page + " / " + pageInfo.pageCount + " 页";
  }
}

function isAnimatedEmojiItem(item = {}) {
  if (item.is_animated) return true;
  return String(item.file_name || item.file_path || "").toLowerCase().split(/[?#]/, 1)[0].endsWith(".gif");
}

function resetEmojiAnimatedPreviews() {
  if (state.emojiAnimatedPreviewObserver) {
    state.emojiAnimatedPreviewObserver.disconnect();
  }
  state.emojiAnimatedPreviewSeq = 0;
}

function emojiAnimatedPreviewObserver() {
  if (!("IntersectionObserver" in window)) return null;
  if (!state.emojiAnimatedPreviewObserver) {
    state.emojiAnimatedPreviewObserver = new IntersectionObserver(handleEmojiAnimatedPreviewVisibility, {
      root: null,
      rootMargin: "160px 0px",
      threshold: 0.05,
    });
  }
  return state.emojiAnimatedPreviewObserver;
}

function observeEmojiAnimatedPreview(thumb, img, emojiId) {
  const id = Number(emojiId || 0);
  if (!thumb || !img || id <= 0) return;
  thumb.dataset.emojiId = String(id);
  thumb.emojiPreviewImage = img;
  img.dataset.emojiPreviewVisible = "false";
  const observer = emojiAnimatedPreviewObserver();
  if (!observer) {
    img.dataset.emojiPreviewVisible = "true";
    scheduleEmojiAnimatedPreview(img, id);
    return;
  }
  observer.observe(thumb);
}

function handleEmojiAnimatedPreviewVisibility(entries) {
  entries.forEach((entry) => {
    const thumb = entry.target;
    const img = thumb?.emojiPreviewImage;
    const id = Number(thumb?.dataset?.emojiId || img?.dataset?.emojiId || 0);
    if (!img || id <= 0) return;
    if (entry.isIntersecting) {
      img.dataset.emojiPreviewVisible = "true";
      scheduleEmojiAnimatedPreview(img, id);
      return;
    }
    img.dataset.emojiPreviewVisible = "false";
    delete img.dataset.emojiAnimatedLoaded;
    loadEmojiPreview(img, id, { still: true });
  });
}

function scheduleEmojiAnimatedPreview(img, emojiId) {
  const id = Number(emojiId || 0);
  if (!img || id <= 0 || img.dataset.emojiPreviewVisible !== "true") return;
  if (img.dataset.emojiAnimatedLoading === "true" || img.dataset.emojiAnimatedLoaded === "true") return;
  const cached = state.emojiPreviewCache.get(`${id}:full`);
  if (cached) {
    img.src = cached;
    img.dataset.emojiAnimatedLoaded = "true";
    return;
  }
  img.dataset.emojiAnimatedLoading = "true";
  const delay = Math.min((state.emojiAnimatedPreviewSeq || 0) * EMOJI_ANIMATED_PREVIEW_STAGGER_MS, 1200);
  state.emojiAnimatedPreviewSeq = (state.emojiAnimatedPreviewSeq || 0) + 1;
  window.setTimeout(async () => {
    if (!img.isConnected || img.dataset.emojiPreviewVisible !== "true") {
      delete img.dataset.emojiAnimatedLoading;
      return;
    }
    try {
      await loadEmojiPreview(img, id, {
        still: false,
        shouldApply: (target) => target.dataset.emojiPreviewVisible === "true",
      });
      if (img.isConnected && img.dataset.emojiPreviewVisible === "true") {
        img.dataset.emojiAnimatedLoaded = "true";
      }
    } finally {
      delete img.dataset.emojiAnimatedLoading;
    }
  }, delay);
}

function renderEmojiStats() {
  if (!el.emojiStats) return;
  const stats = state.emojiStats || {};
  const entries = [
    ["总数", stats.total],
    ["可用", stats.ready],
    ["可发送", stats.sendable],
    ["导入", stats.manual],
    ["识图", stats.review],
    ["平台", stats.trusted],
    ["缺失", stats.missing],
  ];
  el.emojiStats.replaceChildren(
    ...entries.map(([label, value]) => {
      const card = node("div", "emoji-stat");
      card.append(node("span", "emoji-stat-label", label), node("strong", "", Number(value || 0)));
      return card;
    })
  );
}

function emojiRecord(item = {}) {
  const record = node("article", "emoji-record");
  const emojiId = Number(item.id || 0);
  const selected = state.emojiSelectedIds.has(emojiId);
  const managing = Boolean(state.emojiManageMode);
  record.classList.toggle("is-selected", managing && selected);
  record.classList.toggle("is-managing", managing);
  const selector = node("label", "emoji-select");
  if (managing) {
    selector.addEventListener("click", (event) => event.stopPropagation());
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selected;
    if (typeof checkbox.setAttribute === "function") {
      checkbox.setAttribute("aria-label", `选择${clean(item.label || item.description || item.short_hash, "表情素材")}`);
    }
    checkbox.addEventListener("change", () => setEmojiSelected(emojiId, checkbox.checked));
    selector.append(checkbox);
  }
  const thumb = node("div", "emoji-thumb");
  thumb.tabIndex = 0;
  thumb.addEventListener("click", () => {
    if (state.emojiManageMode) {
      toggleEmojiSelected(emojiId);
    } else {
      openEmojiDetail(emojiId);
    }
  });
  thumb.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    if (state.emojiManageMode) {
      toggleEmojiSelected(emojiId);
    } else {
      openEmojiDetail(emojiId);
    }
  });
  if (typeof thumb.setAttribute === "function") {
    thumb.setAttribute("role", "button");
    thumb.setAttribute("aria-label", `${managing ? "选择" : "查看"}${clean(item.label || item.description || item.short_hash, "表情素材")}${managing ? "" : "详情"}`);
  }
  const preview = document.createElement("img");
  preview.alt = clean(item.label, "表情预览");
  preview.loading = "lazy";
  preview.decoding = "async";
  preview.dataset.emojiId = text(item.id);
  if (!item.preview_available) {
    thumb.append(node("span", "emoji-thumb-empty", "无预览"));
  } else {
    thumb.classList.add("is-loading");
    preview.addEventListener("load", () => thumb.classList.remove("is-loading", "is-error"));
    preview.addEventListener("error", () => {
      thumb.classList.remove("is-loading");
      thumb.classList.add("is-error");
    });
    thumb.append(preview);
    loadEmojiPreview(preview, item.id, { still: true });
    if (isAnimatedEmojiItem(item)) {
      observeEmojiAnimatedPreview(thumb, preview, item.id);
    }
  }
  if (managing) thumb.append(selector);

  const body = node("div", "emoji-record-body");
  const title = node("div", "emoji-record-title");
  const titleText = clean(item.label || item.description || item.short_hash, "未命名表情");
  const label = node("span", "emoji-record-label", titleText);
  label.title = titleText;
  const status = node("span", "emoji-status", emojiStatusMark(item));
  status.title = emojiStatusLabel(item);
  title.append(label, status);
  const meta = node("div", "emoji-record-meta", emojiCompactMeta(item));

  body.append(title, meta);
  record.append(thumb, body);
  return record;
}

function renderEmojiDetailDialog() {
  if (!el.emojiDetailDialog || !el.emojiDetailBody || !el.emojiDetailTitle) return;
  if (!state.emojiDetailId) {
    el.emojiDetailDialog.hidden = true;
    if (typeof el.emojiDetailDialog.setAttribute === "function") {
      el.emojiDetailDialog.setAttribute("aria-hidden", "true");
    }
    el.emojiDetailBody.replaceChildren();
    return;
  }
  const item = emojiItemById(state.emojiDetailId);
  if (!item) {
    closeEmojiDetail();
    return;
  }

  const titleText = clean(item.label || item.description || item.short_hash, "未命名表情");
  el.emojiDetailTitle.textContent = titleText;

  const previewWrap = node("div", "emoji-detail-preview");
  if (!item.preview_available) {
    previewWrap.append(node("span", "emoji-thumb-empty", "无预览"));
  } else {
    const preview = document.createElement("img");
    preview.alt = titleText;
    preview.loading = "lazy";
    preview.decoding = "async";
    preview.dataset.emojiId = text(item.id);
    preview.addEventListener("load", () => previewWrap.classList.remove("is-loading", "is-error"));
    preview.addEventListener("error", () => {
      previewWrap.classList.remove("is-loading");
      previewWrap.classList.add("is-error");
    });
    previewWrap.classList.add("is-loading");
    previewWrap.append(preview);
    loadEmojiPreview(preview, item.id, { still: false });
  }

  const tags = Array.isArray(item.emotions) && item.emotions.length ? item.emotions.join("、") : "";
  const detailLines = [
    ["状态", emojiStatusLabel(item)],
    ["来源", emojiSourceLabel(item)],
    ["类型", emojiTypeLabel(item)],
    ["情绪", tags],
    ["用途", clean(item.description, "")],
    ["使用", `${Number(item.used_count || 0)} 次${item.last_used_at ? ` · 最近 ${clean(item.last_used_at)}` : ""}`],
    ["文件", clean(item.file_name, item.is_remote ? "远程图片" : "本地缓存")],
  ].filter((line) => text(line[1]).trim());

  const content = node("div", "emoji-detail-content");
  const actions = node("div", "emoji-detail-actions");
  const toggleButton = node("button", "", item.sendable ? "停用" : "启用");
  toggleButton.type = "button";
  toggleButton.addEventListener("click", () => setEmojiSendable(item.id, !item.sendable));
  const deleteButton = node("button", "danger", "删除");
  deleteButton.type = "button";
  deleteButton.addEventListener("click", () => confirmEmojiDelete(deleteButton, item.id));
  actions.append(toggleButton, deleteButton);
  content.append(recordLines(detailLines), actions);

  el.emojiDetailBody.replaceChildren(previewWrap, content);
  el.emojiDetailDialog.hidden = false;
  if (typeof el.emojiDetailDialog.setAttribute === "function") {
    el.emojiDetailDialog.setAttribute("aria-hidden", "false");
  }
}

function openEmojiDetail(id) {
  state.emojiDetailId = Number(id || 0);
  renderEmojiDetailDialog();
}

function closeEmojiDetail() {
  state.emojiDetailId = 0;
  renderEmojiDetailDialog();
}

function openEmojiImport() {
  if (!el.emojiImportDialog) return;
  el.emojiImportDialog.hidden = false;
  if (typeof el.emojiImportDialog.setAttribute === "function") {
    el.emojiImportDialog.setAttribute("aria-hidden", "false");
  }
  window.setTimeout(() => el.emojiImportFileButton?.focus(), 0);
}

function closeEmojiImport() {
  if (!el.emojiImportDialog) return;
  el.emojiImportDialog.hidden = true;
  if (typeof el.emojiImportDialog.setAttribute === "function") {
    el.emojiImportDialog.setAttribute("aria-hidden", "true");
  }
  if (el.emojiImportFile) el.emojiImportFile.value = "";
}

function renderEmojiManagement() {
  if (!el.emojiList) return;
  pruneEmojiSelection();
  renderEmojiStats();
  renderEmojiSelectionTools();
  const items = filteredEmojiItems();
  const total = Number(state.emojiStats?.total || 0);
  const pageInfo = emojiPageWindow(items);
  renderEmojiPager(pageInfo);
  if (el.emojiSummary) {
    if (!total) {
      el.emojiSummary.textContent = "暂无表情素材";
    } else if (!items.length) {
      el.emojiSummary.textContent = `暂无符合条件的表情素材，共 ${total} 个`;
    } else if (items.length === total) {
      el.emojiSummary.textContent = `显示 ${pageInfo.start + 1}-${pageInfo.end} 个，共 ${total} 个表情素材`;
    } else {
      el.emojiSummary.textContent = `显示 ${pageInfo.start + 1}-${pageInfo.end} 个，共 ${items.length} 个符合条件；总数 ${total} 个`;
    }
  }
  resetEmojiAnimatedPreviews();
  el.emojiList.replaceChildren(...(pageInfo.items.length ? pageInfo.items.map(emojiRecord) : [empty("暂无符合条件的表情素材")]));
  renderEmojiDetailDialog();
}

async function loadEmojiPreview(img, emojiId, { still = false, shouldApply = null } = {}) {
  const id = Number(emojiId || 0);
  if (!img || id <= 0) return;
  const cacheKey = `${id}:${still ? "still" : "full"}`;
  const cached = state.emojiPreviewCache.get(cacheKey);
  if (cached) {
    if (!shouldApply || shouldApply(img)) img.src = cached;
    return;
  }
  try {
    const result = await apiPost("page/emoji/preview", { id, still });
    const src = result.data_url || result.url || "";
    if (!src) throw new Error("表情预览为空");
    state.emojiPreviewCache.set(cacheKey, src);
    if (img.isConnected && (!shouldApply || shouldApply(img))) img.src = src;
  } catch (_error) {
    img.removeAttribute("src");
    img.closest(".emoji-thumb, .emoji-detail-preview")?.classList.add("is-error");
  }
}

function applyEmojiPayload(payload = {}) {
  state.emojiItems = objectItems(payload.items);
  state.emojiStats = payload.stats && typeof payload.stats === "object" ? payload.stats : {};
  pruneEmojiSelection();
  state.emojiLoaded = true;
  renderEmojiManagement();
}

async function loadEmojiAssets({ quiet = false } = {}) {
  if (state.emojiLoading) return;
  state.emojiLoading = true;
  try {
    applyEmojiPayload(await apiGet("page/emoji/list", { _ts: Date.now() }));
    if (!quiet) setNotice("");
  } catch (error) {
    if (!quiet) setNotice(error.message || "表情素材加载失败", "error");
  } finally {
    state.emojiLoading = false;
  }
}

function isEmojiImportFile(file) {
  if (!file) return false;
  if (String(file.type || "").startsWith("image/")) return true;
  const suffix = String(file.name || "").split(".").pop().toLowerCase();
  return ["png", "jpg", "jpeg", "webp", "gif", "bmp"].includes(suffix);
}

function isEmojiBackupFile(file) {
  if (!file) return false;
  if (["application/zip", "application/x-zip-compressed"].includes(String(file.type || "").toLowerCase())) return true;
  return String(file.name || "").toLowerCase().endsWith(".zip");
}

async function importEmojiPayloads(payloads, successLabel) {
  const items = Array.isArray(payloads) ? payloads.filter(Boolean) : [];
  if (!items.length) {
    setNotice("没有可导入的表情素材", "error");
    return;
  }
  setBusy(true);
  stopEmojiAutoRefresh();
  try {
    let latest = null;
    for (const payload of items) {
      latest = await apiPost("page/emoji/import", payload, {
        timeoutMs: 60000,
        timeoutMessage: "表情导入耗时较久，请稍后查看",
      });
    }
    if (latest) applyEmojiPayload(latest);
    closeEmojiImport();
    setNotice(successLabel || `已导入 ${items.length} 个表情素材`, "success");
  } catch (error) {
    setNotice(error.message || "表情导入失败", "error");
  } finally {
    setBusy(false);
    scheduleEmojiAutoRefresh();
  }
}

async function importEmojiFiles(files) {
  const selected = Array.from(files || []);
  if (!selected.length) return;
  const payloads = [];
  let skippedUnsupported = 0;
  let skippedTooLarge = 0;
  setBusy(true);
  try {
    for (const file of selected) {
      if (!isEmojiImportFile(file)) {
        skippedUnsupported += 1;
        continue;
      }
      if (Number(file.size || 0) > EMOJI_IMPORT_MAX_BYTES) {
        skippedTooLarge += 1;
        continue;
      }
      payloads.push({
        image: await fileToDataUrl(file),
        filename: file.name || "",
      });
    }
  } catch (error) {
    setNotice(error.message || "图片读取失败", "error");
    return;
  } finally {
    setBusy(false);
  }
  if (!payloads.length && (skippedUnsupported || skippedTooLarge)) {
    const reasons = [];
    if (skippedTooLarge) reasons.push(`${skippedTooLarge} 个超过 ${EMOJI_IMPORT_MAX_MB} MB`);
    if (skippedUnsupported) reasons.push(`${skippedUnsupported} 个格式不支持`);
    setNotice(`没有可导入的表情素材：${reasons.join("，")}`, "error");
    return;
  }
  const skipped = skippedUnsupported + skippedTooLarge;
  await importEmojiPayloads(
    payloads,
    skipped ? `已导入 ${payloads.length} 个表情素材，跳过 ${skipped} 个` : `已导入 ${payloads.length} 个表情素材`
  );
}

async function backupEmojiAssets() {
  setBusy(true);
  stopEmojiAutoRefresh();
  try {
    const result = await apiPost("page/emoji/backup", {}, {
      timeoutMs: 120000,
      timeoutMessage: "表情备份耗时较久，请稍后再试",
    });
    if (!result.data_url) throw new Error("表情备份文件为空");
    downloadDataUrl(result.filename || "daily_life_emoji_backup.zip", result.data_url);
    setNotice(`已备份 ${Number(result.count || 0)} 个表情素材`, "success");
  } catch (error) {
    setNotice(error.message || "表情备份失败", "error");
  } finally {
    setBusy(false);
    scheduleEmojiAutoRefresh();
  }
}

async function restoreEmojiBackupFile(file) {
  if (!file) return;
  if (!isEmojiBackupFile(file)) {
    setNotice("请选择 ZIP 表情备份文件", "error");
    return;
  }
  if (Number(file.size || 0) > EMOJI_BACKUP_MAX_BYTES) {
    setNotice(`表情备份文件不能超过 ${EMOJI_BACKUP_MAX_MB} MB`, "error");
    return;
  }
  setBusy(true);
  stopEmojiAutoRefresh();
  try {
    const result = await apiPost("page/emoji/restore", {
      archive: await fileToDataUrl(file),
      filename: file.name || "",
    }, {
      timeoutMs: 120000,
      timeoutMessage: "表情还原耗时较久，请稍后查看",
    });
    state.emojiPreviewCache.clear();
    applyEmojiPayload(result);
    const skipped = Number(result.skipped_records || 0);
    setNotice(
      skipped
        ? `已还原 ${Number(result.restored || 0)} 个表情素材，跳过 ${skipped} 个`
        : `已还原 ${Number(result.restored || 0)} 个表情素材`,
      "success"
    );
  } catch (error) {
    setNotice(error.message || "表情还原失败", "error");
  } finally {
    setBusy(false);
    scheduleEmojiAutoRefresh();
  }
}

function emojiTargetIds(ids) {
  return Array.from(new Set((Array.isArray(ids) ? ids : [ids])
    .map((id) => Number(id || 0))
    .filter((id) => id > 0)));
}

async function setEmojiSendable(ids, sendable) {
  const targets = emojiTargetIds(ids);
  if (!targets.length) return;
  setBusy(true);
  try {
    const payload = targets.length === 1 ? { id: targets[0], sendable } : { ids: targets, sendable };
    const result = await apiPost("page/emoji/sendable", payload);
    applyEmojiPayload(result);
    if (targets.length > 1) {
      setNotice(sendable ? `已启用 ${targets.length} 个表情素材` : `已停用 ${targets.length} 个表情素材`, "success");
    } else {
      setNotice(sendable ? "表情已启用" : "表情已停用", "success");
    }
  } catch (error) {
    setNotice(error.message || "表情状态保存失败", "error");
  } finally {
    setBusy(false);
    renderEmojiSelectionTools();
  }
}

function setSelectedEmojiSendable(sendable) {
  const ids = emojiSelectedIds();
  if (!ids.length) return;
  setEmojiSendable(ids, sendable);
}

function confirmEmojiDelete(button, id) {
  if (button.dataset.confirmDelete === "true") {
    window.clearTimeout(Number(button.dataset.confirmTimer || 0));
    delete button.dataset.confirmDelete;
    delete button.dataset.confirmTimer;
    deleteEmojiAssets([id]);
    return;
  }
  stopEmojiAutoRefresh();
  button.dataset.confirmDelete = "true";
  button.classList.add("is-confirming");
  button.textContent = "确认删除";
  const timer = window.setTimeout(() => {
    if (!button.isConnected) return;
    delete button.dataset.confirmDelete;
    delete button.dataset.confirmTimer;
    button.classList.remove("is-confirming");
    button.textContent = "删除";
    scheduleEmojiAutoRefresh();
  }, 3200);
  button.dataset.confirmTimer = String(timer);
  setNotice("再次点击确认删除表情素材");
}

function resetEmojiBulkDeleteButton() {
  const button = el.emojiBulkDeleteButton;
  if (!button) return;
  window.clearTimeout(Number(button.dataset.confirmTimer || 0));
  delete button.dataset.confirmDelete;
  delete button.dataset.confirmTimer;
  button.classList.remove("is-confirming");
  button.textContent = "删除选中";
}

function confirmEmojiBulkDelete() {
  const ids = emojiSelectedIds();
  const button = el.emojiBulkDeleteButton;
  if (!button || !ids.length) return;
  if (button.dataset.confirmDelete === "true") {
    resetEmojiBulkDeleteButton();
    deleteEmojiAssets(ids);
    return;
  }
  stopEmojiAutoRefresh();
  button.dataset.confirmDelete = "true";
  button.classList.add("is-confirming");
  button.textContent = `确认删除 ${ids.length} 个`;
  const timer = window.setTimeout(() => {
    resetEmojiBulkDeleteButton();
    scheduleEmojiAutoRefresh();
  }, 3200);
  button.dataset.confirmTimer = String(timer);
  setNotice(`再次点击确认删除 ${ids.length} 个表情素材`);
}

async function deleteEmojiAssets(ids) {
  const targets = emojiTargetIds(ids);
  if (!targets.length) return;
  setBusy(true);
  try {
    const result = await apiPost("page/emoji/delete", { ids: targets });
    targets.forEach((id) => {
      state.emojiPreviewCache.delete(`${id}:still`);
      state.emojiPreviewCache.delete(`${id}:full`);
      state.emojiSelectedIds.delete(id);
    });
    if (targets.includes(Number(state.emojiDetailId || 0))) {
      state.emojiDetailId = 0;
    }
    resetEmojiManageState();
    applyEmojiPayload(result);
    setNotice(targets.length > 1 ? `已删除 ${targets.length} 个表情素材` : "表情素材已删除", "success");
  } catch (error) {
    setNotice(error.message || "表情素材删除失败", "error");
  } finally {
    resetEmojiBulkDeleteButton();
    setBusy(false);
    scheduleEmojiAutoRefresh();
  }
}

function renderDashboard() {
  const status = state.status || {};
  renderDay(status);
  renderWorld(status);
  renderLifecycle(status);
  renderExperience(status);
  renderMemoryPanel();
}

function renderMemoryPanel() {
  const allowedTabs = new Set(["world", "experience", "lifecycle"]);
  const activeTab = allowedTabs.has(state.memoryTab) ? state.memoryTab : "world";
  state.memoryTab = activeTab;
  el.memoryTabs.forEach((tab) => {
    const active = tab.dataset.memoryTab === activeTab;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  el.memoryPanels.forEach((panel) => {
    panel.hidden = panel.dataset.memoryPanel !== activeTab;
  });
}

const configPanel = createConfigPanel({
  state,
  el,
  node,
  empty,
  setBusy,
  setNotice,
  loadStatus,
  syncSelectControls: (scope) => lifeSelectControls.refresh(scope),
});
const { loadConfig, renderConfig, flushConfigAutosave } = configPanel;

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
  window.addEventListener("resize", scheduleTodayFactsLayout);
  el.viewButtons.forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  el.resetDayButton.addEventListener("click", () => resetDay(""));
  el.refreshStateButton?.addEventListener("click", refreshState);
  el.timelineEditButton.addEventListener("click", beginTimelineEdit);
  el.timelineAddButton.addEventListener("click", addTimelineItem);
  el.timelineCancelButton.addEventListener("click", cancelTimelineEdit);
  el.timelineSaveButton.addEventListener("click", saveTimeline);
  el.memoryTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      state.memoryTab = tab.dataset.memoryTab || "world";
      renderMemoryPanel();
    });
  });
  el.emojiFilter?.addEventListener("change", () => {
    state.emojiFilter = el.emojiFilter.value || "all";
    state.emojiPage = 1;
    renderEmojiManagement();
  });
  el.emojiPrevPage?.addEventListener("click", () => {
    state.emojiPage = Math.max(1, Number(state.emojiPage || 1) - 1);
    renderEmojiManagement();
  });
  el.emojiNextPage?.addEventListener("click", () => {
    state.emojiPage = Number(state.emojiPage || 1) + 1;
    renderEmojiManagement();
  });
  el.emojiImportButton?.addEventListener("click", openEmojiImport);
  el.emojiBackupButton?.addEventListener("click", backupEmojiAssets);
  el.emojiRestoreButton?.addEventListener("click", () => el.emojiRestoreFile?.click());
  el.emojiRestoreFile?.addEventListener("change", () => {
    const file = Array.from(el.emojiRestoreFile?.files || [])[0];
    if (el.emojiRestoreFile) el.emojiRestoreFile.value = "";
    restoreEmojiBackupFile(file);
  });
  el.emojiImportClose?.addEventListener("click", closeEmojiImport);
  el.emojiImportDialog?.addEventListener("click", (event) => {
    if (event.target === el.emojiImportDialog) closeEmojiImport();
  });
  el.emojiImportFileButton?.addEventListener("click", () => el.emojiImportFile?.click());
  el.emojiImportFile?.addEventListener("change", () => {
    const files = Array.from(el.emojiImportFile?.files || []);
    if (el.emojiImportFile) el.emojiImportFile.value = "";
    importEmojiFiles(files);
  });
  el.emojiManageButton?.addEventListener("click", beginEmojiManage);
  el.emojiCancelManageButton?.addEventListener("click", cancelEmojiManage);
  el.emojiBulkEnableButton?.addEventListener("click", () => setSelectedEmojiSendable(true));
  el.emojiBulkDisableButton?.addEventListener("click", () => setSelectedEmojiSendable(false));
  el.emojiBulkDeleteButton?.addEventListener("click", confirmEmojiBulkDelete);
  el.emojiDetailClose?.addEventListener("click", closeEmojiDetail);
  el.emojiDetailDialog?.addEventListener("click", (event) => {
    if (event.target === el.emojiDetailDialog) closeEmojiDetail();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (state.emojiDetailId) closeEmojiDetail();
    if (el.emojiImportDialog && !el.emojiImportDialog.hidden) closeEmojiImport();
  });
  el.settingsView?.addEventListener("focusout", (event) => {
    if (event.relatedTarget && el.settingsView.contains(event.relatedTarget)) return;
    window.setTimeout(() => {
      if (!el.settingsView?.contains(document.activeElement)) flushConfigAutosave();
    }, 0);
  });
  el.worldTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      state.worldTab = tab.dataset.worldTab;
      renderWorld(state.status || {});
    });
  });
  el.experienceTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      state.experienceTab = tab.dataset.experienceTab;
      renderExperience(state.status || {});
    });
  });
}

async function init() {
  bindEvents();
  lifeSelectControls.init();
  dashboardEffects.initLifeDrift();
  dashboardEffects.initCursorTrail();
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
