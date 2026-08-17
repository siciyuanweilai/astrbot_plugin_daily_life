import {
  ACTION_LABELS,
  ACTION_OWNER_LABELS,
  ATMOSPHERE_LABELS,
  BOT_WATCH_STATE_LABELS,
  CURRENT_SLEEP_LABELS,
  EMOJI_SOURCE_LABELS,
  EMOJI_STATUS_LABELS,
  EMOJI_TYPE_LABELS,
  EPISODE_KIND_LABELS,
  EPISODE_STATUS_LABELS,
  EVENT_STATUS_LABELS,
  EVIDENCE_TYPE_LABELS,
  FEEDBACK_RESULT_LABELS,
  FRESHNESS_LABELS,
  INTERRUPT_LEVEL_LABELS,
  LIFE_DECISION_KIND_LABELS,
  LIFE_DOMAIN_KIND_LABELS,
  LIFE_DOMAIN_SOURCE_LABELS,
  LIFE_DOMAIN_STATUS_LABELS,
  MEAL_TYPE_LABELS,
  PLACE_TYPE_LABELS,
  PREFERENCE_CATEGORY_LABELS,
  QUANTITY_UNIT_LABELS,
  RHYTHM_LIFECYCLE_LABELS,
  SCHEDULE_INTENT_LABELS,
  SCHEDULE_TONE_LABELS,
  SCENE_TYPE_LABELS,
  SCOPE_LABELS,
  SOURCE_LABELS,
  TARGET_TYPE_LABELS,
  UNDERSTANDING_LABELS,
  VISIBILITY_LABELS,
} from "./shared/terms.js";

import {
  clean,
  cognitionPredicateText,
  cognitionSubjectText,
  cognitionValueText,
  currentOutfitDisplayText,
  emojiEmotionLabels,
  enumLabel,
  enumLabelOrReadableText,
  enumLabelStrict,
  evidenceTargetTitle,
  evidenceText,
  healthCheckRows,
  lifeEpisodeLines,
  longTermMemoryCategoryLabel,
  memoryConflictTypeLabel,
  memoryEntityTypeLabel,
  moodColorText,
  outfitDecisionText,
  recordLines,
  readableReferenceLabel,
  scheduleTypeText,
  stateLogText,
  text,
  timelineTravelText,
  visibleExperienceEvidence,
  visibleLifeEpisodes
} from "./shared/format.js";

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
  relationshipNameIndex,
  relationshipRecordLines,
  relationshipReferenceText,
  relationshipScopeLabel,
  relationshipTextResolver,
} from "./shared/relationships.js";

export {
  relationshipNameIndex,
  relationshipReferenceText,
  relationshipScopeLabel,
} from "./shared/relationships.js";

import {
  apiDownload,
  apiGet,
  apiPost,
  apiUpload,
  bridge,
  GENERATION_TIMEOUT_MS,
  userErrorMessage,
  withTimeout
} from "./api/transport.js";

import { createConfigPanel } from "./ui/settings.js";
import {
  focusDialog,
  restoreDialogFocus,
  trapDialogFocus,
} from "./ui/dialog.js";
import { createDashboardEffects } from "./ui/effects.js";
import { createLifeSelectControls } from "./ui/selects.js";

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
const CLOSET_PAGE_SIZE = 24;
const CLOSET_IMPORT_MAX_MB = 20;
const CLOSET_IMPORT_MAX_BYTES = CLOSET_IMPORT_MAX_MB * 1024 * 1024;
const CLOSET_BACKUP_MAX_MB = 500;
const CLOSET_BACKUP_MAX_BYTES = CLOSET_BACKUP_MAX_MB * 1024 * 1024;
const MEMO_EMPTY_TEXT = "暂无备忘录";
const CURRENT_ACTIVITY_EMPTY_TEXT = "暂无当前活动";
const METER_EMPTY_TEXT = "暂无数据";
const TIMELINE_TIME_EMPTY_TEXT = "未定";
const TIMELINE_EXECUTION_LABELS = {
  planned: "待进行",
  active: "进行中",
  completed: "已完成",
  skipped: "已跳过",
  cancelled: "已取消",
};
const COGNITION_STATUS_LABELS = {
  proposed: "已提出",
  pending: "待处理",
  leased: "执行中",
  completed: "已完成",
  failed: "待重试",
  dead: "已终止",
  active: "当前",
  committed: "已提交",
  rejected: "已拒绝",
  invalidated: "已失效",
  superseded: "已被替代",
  promoted: "已晋升",
  queued: "排队中",
  running: "执行中",
  retrying: "待重试",
  error: "失败",
  canceled: "已取消",
  cancelled: "已取消",
  expired: "已过期",
  open: "进行中",
};
const DURABLE_TASK_KIND_LABELS = {
  daily_refresh: "每日生活刷新",
  daily_review: "夜间生活复盘",
  private_revisit: "私聊回访检查",
  proactive_idle: "闲时主动检查",
  web_research: "网页研究报告",
};
const DECISION_STAGE_LABELS = {
  proposed: "提出候选",
  proposal: "候选裁定",
  candidate: "形成候选",
  considering: "正在考虑",
  validated: "证据通过",
  waiting: "继续等待",
  sending: "正在发送",
  commit: "发送提交",
  committed: "完成提交",
  settled: "动作结算",
  engaged: "收到互动",
  closing: "自然收束",
  cooldown: "进入冷却",
  interrupted: "已被打断",
  abandoned: "已放弃",
};
const DECISION_VALUE_LABELS = {
  reply: "发送回复",
  observe: "继续观察",
  wait: "继续等待",
  skip: "跳过",
  reject: "拒绝执行",
  accepted: "接受",
  committed: "已提交",
};
const DECISION_REASON_LABELS = {
  action_planned: "动作已纳入计划",
  channel_disabled: "发送通道不可用",
  confidence_below_threshold: "置信度未达门槛",
  context_changed_before_send: "发送前上下文已变化",
  context_changed_during_evaluation: "评估期间上下文已变化",
  continuity_audit_failed: "连续性审计未通过",
  conversation_revision_changed: "会话已经更新",
  empty_reply: "没有可发送内容",
  expression_review_failed: "表达审计未通过",
  group_anchor_missing: "群聊缺少自然承接点",
  invalid_utility_scores: "主动收益评分不完整",
  model_declined: "模型建议不发送",
  person_audit_failed: "人物事实审计未通过",
  proposal_approved: "候选裁定通过",
  review_evidence_validated: "复盘证据已核验",
  revisit_evidence_missing: "回访依据不足",
  send_failed: "发送失败",
  send_succeeded: "发送成功",
  style_rejected: "聊天表达规则未通过",
  utility_below_threshold: "主动净收益未达门槛",
};
const LIFE_ACTION_TYPE_LABELS = {
  rest: "休息",
  meal: "用餐",
  cook: "做饭",
  order_food: "点餐",
  purchase: "采购",
  move: "移动或散步",
  travel: "出行",
  work: "工作",
  study: "学习",
  chore: "家务",
  exercise: "运动",
  groom: "整理仪容",
  change_outfit: "更换穿搭",
  social: "社交活动",
  chat: "聊天互动",
  photo: "拍照",
  video: "拍摄视频",
};
const COGNITION_LAYER_LABELS = {
  transient: "短时情绪",
  daily: "日级情绪",
  relationship: "关系情绪",
};
const TODAY_FACT_EMPTY_TEXT = {
  weatherText: "暂无天气",
  themeText: "暂无主题",
  todayWeekPlan: "暂无周计划",
  moodColorText: "暂无心情色彩",
  scheduleTypeText: "暂无日程类型",
  scheduleToneText: "暂无日程基调",
  scheduleIntentText: "暂无活动状态",
  currentOutfitText: "暂无穿搭",
  outfitDecisionText: "暂无判断",
};
const FACT_CARD_COLUMNS = [
  ["weather", "theme", "week", "mood", "schedule-type", "memo"],
  ["schedule-tone", "schedule-intent", "outfit", "outfit-decision"],
];

const HERO_COPY = {
  dashboard: {
    eyebrow: "日常生活 · 把今天装进生活手帐",
    title: "日常生活工作台",
    subtitle: "今日、时间轴、状态和记忆分格摆好，像一张柔软又清楚的少女生活桌面。",
  },
  emoji: {
    eyebrow: "表情口袋 · 把情绪收进贴纸夹",
    title: "表情管理",
    subtitle: "收藏、识图、导入和启停都放在一处，让表情在合适的时候自然出现。",
  },
  closet: {
    eyebrow: "视觉衣橱 · 把喜欢的造型收进灵感册",
    title: "衣橱管理",
    subtitle: "上传、联网学习、筛选和反馈集中管理，已启用的候选会自然参与日常穿搭与发型决策。",
  },
  settings: {
    eyebrow: "运行设置 · 把规则整理成抽屉",
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
  closetItems: [],
  closetStats: {},
  closetFilter: "all",
  closetPage: 1,
  closetLoaded: false,
  closetLoading: false,
  closetDetailId: 0,
  closetManageMode: false,
  closetSelectedIds: new Set(),
  closetPreviewCache: new Map(),
  memoryTab: "world",
  worldTab: "life_decisions",
  experienceTab: "relationships",
  domainTab: "timeline",
  noticeTimer: 0,
  busy: false,
  configSchema: {},
  config: {},
  providers: [],
  relationships: [],
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
  generationRunningIds: new Set(),
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
  closetView: byId("closetView"),
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
  domainTabs: all("[data-domain-tab]"),
  domainList: byId("domainList"),
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
  closetSummary: byId("closetSummary"),
  closetFilter: byId("closetFilter"),
  closetImportButton: byId("closetImportButton"),
  closetImportFile: byId("closetImportFile"),
  closetBrowseButton: byId("closetBrowseButton"),
  closetBackupButton: byId("closetBackupButton"),
  closetRestoreButton: byId("closetRestoreButton"),
  closetRestoreFile: byId("closetRestoreFile"),
  closetManageButton: byId("closetManageButton"),
  closetSelectedSummary: byId("closetSelectedSummary"),
  closetBulkEnableButton: byId("closetBulkEnableButton"),
  closetBulkDisableButton: byId("closetBulkDisableButton"),
  closetBulkDeleteButton: byId("closetBulkDeleteButton"),
  closetCancelManageButton: byId("closetCancelManageButton"),
  closetStats: byId("closetStats"),
  closetPager: byId("closetPager"),
  closetPrevPage: byId("closetPrevPage"),
  closetPageInfo: byId("closetPageInfo"),
  closetNextPage: byId("closetNextPage"),
  closetList: byId("closetList"),
  closetDetailDialog: byId("closetDetailDialog"),
  closetDetailTitle: byId("closetDetailTitle"),
  closetDetailBody: byId("closetDetailBody"),
  closetDetailClose: byId("closetDetailClose"),
  closetBrowseDialog: byId("closetBrowseDialog"),
  closetBrowseClose: byId("closetBrowseClose"),
  closetBrowseQuery: byId("closetBrowseQuery"),
  closetBrowseKind: byId("closetBrowseKind"),
  closetBrowseCount: byId("closetBrowseCount"),
  closetBrowseNote: byId("closetBrowseNote"),
  closetBrowseSubmit: byId("closetBrowseSubmit"),
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
  if (document.hidden || state.view !== "dashboard" || state.clockTimer) return;
  state.clockTimer = window.setInterval(renderTargetDateTime, 1000);
}

function stopClock() {
  if (!state.clockTimer) return;
  window.clearInterval(state.clockTimer);
  state.clockTimer = 0;
}

function statusVersion() {
  const version = Number(state.status?.status_version || 0);
  return Number.isFinite(version) ? Math.max(0, Math.trunc(version)) : 0;
}

function applyStatus(nextStatus, { render = true } = {}) {
  if (!nextStatus || typeof nextStatus !== "object") return;
  const nextGeneration = nextStatus.daily_generation || {};
  const nextId = text(nextGeneration.operation_id).trim();
  const isRunning = nextGeneration.running === true || nextGeneration.phase === "queued";
  if (nextId && isRunning) state.generationRunningIds.add(nextId);
  const finishedTrackedOperation =
    nextId && !isRunning &&
    state.generationRunningIds.has(nextId) &&
    (nextGeneration.phase === "completed" || nextGeneration.phase === "failed");
  state.status = nextStatus;
  if (finishedTrackedOperation) {
    state.generationRunningIds.delete(nextId);
    if (!state.busy) {
      if (nextGeneration.phase === "completed") {
        setNotice("今日生活安排已生成", "success");
      } else {
        setNotice("今日生活安排生成失败", "error");
      }
    }
  }
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
      stopClock();
      stopMemoCarousel();
      return;
    }
    if (!document.hidden && state.view === "dashboard" && bridge && state.bridgeReady) {
      startClock();
      loadStatus({ quiet: true });
      scheduleStatusWatch(0);
    } else if (!document.hidden && state.view === "emoji" && bridge && state.bridgeReady) {
      loadEmojiAssets({ quiet: true });
      scheduleEmojiAutoRefresh();
    }
  });
  window.addEventListener("pagehide", () => {
    stopClock();
    stopMemoCarousel();
    stopEmojiAutoRefresh();
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

function setActionButtonBusyLabel(button, label) {
  if (!button || !label) return () => {};
  const labelNode = button.querySelector("[data-action-label]");
  if (!labelNode) return () => {};
  const originalText = labelNode.textContent;
  labelNode.textContent = label;
  button.setAttribute("aria-busy", "true");
  return () => {
    if (button.dataset.lockDisabled === "true") return;
    labelNode.textContent = originalText;
    button.removeAttribute?.("aria-busy");
  };
}

function dailyGeneration(status = {}) {
  const generation = status?.daily_generation;
  return generation && typeof generation === "object" ? generation : {};
}

function syncDailyGenerationButton(status = {}) {
  const button = el.resetDayButton;
  if (!button) return;
  const generation = dailyGeneration(status);
  const running = generation.running === true || generation.phase === "queued";
  const labelNode = button.querySelector("[data-action-label]");
  if (labelNode) labelNode.textContent = running ? "重生中…" : "重生";
  if (running) {
    button.dataset.lockDisabled = "true";
    button.setAttribute("aria-busy", "true");
  } else {
    delete button.dataset.lockDisabled;
    button.removeAttribute?.("aria-busy");
  }
  button.disabled = state.busy || running;
}

function currentViewElement() {
  if (state.view === "settings") return el.settingsView;
  if (state.view === "closet") return el.closetView;
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

function typedLabel(value, labels, fallbackLabels = []) {
  const raw = text(value).trim();
  if (!raw) return "";
  for (const table of [labels, ...fallbackLabels]) {
    if (!table) continue;
    const translated = table[raw] || table[raw.toLowerCase()];
    if (translated) return translated;
  }
  return enumLabel(raw, labels);
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
  state.view = ["settings", "emoji", "closet"].includes(view) ? view : "dashboard";
  if (state.view === "dashboard") {
    startClock();
  } else {
    stopClock();
    stopMemoCarousel();
  }
  if (state.view !== "settings") cancelDeferredConfigLoad();
  renderHeroCopy(state.view);
  el.dashboardView.hidden = state.view !== "dashboard";
  el.emojiView.hidden = state.view !== "emoji";
  el.closetView.hidden = state.view !== "closet";
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
  if (state.view !== "closet") {
    resetClosetManageState();
    closeClosetDetail();
    closeClosetBrowse();
  }
  if (state.view === "emoji" && bridge && state.bridgeReady) {
    loadEmojiAssets({ quiet: state.emojiLoaded });
    scheduleEmojiAutoRefresh();
  } else if (state.view === "closet" && bridge && state.bridgeReady) {
    loadClosetAssets({ quiet: state.closetLoaded });
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
  return (Array.isArray(timeline) ? timeline : []).map((item) => {
    const source = item && typeof item === "object" ? item : {};
    return {
      ...source,
      time: clean(source.time, ""),
      activity: clean(source.activity, ""),
      status: clean(source.status, ""),
      execution_state: clean(source.execution_state, "planned"),
      execution_reason: clean(source.execution_reason, ""),
      execution_evidence: clean(source.execution_evidence, ""),
      execution_updated_at: clean(source.execution_updated_at, ""),
    };
  });
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
    ...timeline.map((item, index) => {
      const li = node("li", "timeline-item");
      li.append(node("div", "time", clean(item.time, TIMELINE_TIME_EMPTY_TEXT)));
      const body = node("div");
      body.append(node("div", "timeline-activity", clean(item.activity)));
      const travel = timelineTravelText(item, timeline[index - 1]);
      if (travel) body.append(node("div", "timeline-travel", travel));
      if (item.status) body.append(node("div", "status", clean(item.status)));
      const executionState = clean(item.execution_state, "planned");
      const executionLabel = TIMELINE_EXECUTION_LABELS[executionState];
      if (executionLabel) {
        const execution = node(
          "div",
          `status execution-status execution-${executionState}`,
          executionLabel
        );
        const detail = [clean(item.execution_reason, ""), clean(item.execution_updated_at, "")]
          .filter(Boolean)
          .join(" · ");
        if (detail) execution.title = detail;
        body.append(execution);
      }
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

function domainRecord(title, meta = "", lines = []) {
  const record = node("div", "record domain-record");
  const head = node("div", "record-head");
  head.append(node("strong", "record-title", clean(title, "未命名记录")));
  if (meta) head.append(node("span", "muted", clean(meta)));
  record.append(head);
  const visibleLines = lines.map((line) => clean(line, "")).filter(Boolean);
  if (visibleLines.length) {
    const body = node("div", "record-lines");
    visibleLines.forEach((line) => body.append(node("div", "record-line-value", line)));
    record.append(body);
  }
  return record;
}

function renderDomainTimeline(domains = {}) {
  const items = Array.isArray(domains.timeline) ? domains.timeline : [];
  return items.map((item) => {
    const kind = enumLabelOrReadableText(item.kind, LIFE_DOMAIN_KIND_LABELS, "生活");
    const status = enumLabelOrReadableText(item.status, LIFE_DOMAIN_STATUS_LABELS, "状态未知");
    return domainRecord(
      clean(item.title, kind),
      [kind, status].filter(Boolean).join(" · "),
      [
        clean(item.occurred_at, ""),
        enumLabelOrReadableText(item.source, LIFE_DOMAIN_SOURCE_LABELS, item.source ? "其他来源" : ""),
      ]
    );
  });
}

function renderDomainFood(domains = {}) {
  const meals = (Array.isArray(domains.meals) ? domains.meals : []).map((item) => (
    domainRecord(
      clean(item.name, "用餐"),
      [
        enumLabelOrReadableText(item.meal_type, MEAL_TYPE_LABELS, "饮食"),
        enumLabelOrReadableText(item.status, LIFE_DOMAIN_STATUS_LABELS, "状态未知"),
      ].filter(Boolean).join(" · "),
      [clean(item.occurred_at || item.date, ""), clean(item.place, "")]
    )
  ));
  const recipes = (Array.isArray(domains.recipes) ? domains.recipes : []).map((item) => {
    const ingredients = (Array.isArray(item.ingredients) ? item.ingredients : [])
      .map((ingredient) => {
        const name = clean(ingredient?.name, "");
        if (!name) return "";
        const quantity = Number(ingredient?.quantity || 0);
        const unit = enumLabelOrReadableText(ingredient?.unit, QUANTITY_UNIT_LABELS, "");
        return quantity > 0 ? `${name} ${quantity}${unit}` : name;
      })
      .filter(Boolean);
    const tags = (Array.isArray(item.tags) ? item.tags : []).map((tag) => clean(tag, "")).filter(Boolean);
    return domainRecord(
      clean(item.name, "食谱"),
      ["食谱", enumLabelOrReadableText(item.meal_type, MEAL_TYPE_LABELS, "")].filter(Boolean).join(" · "),
      [
        ingredients.length ? `食材：${ingredients.join("、")}` : "",
        tags.length ? `标签：${tags.join("、")}` : "",
      ]
    );
  });
  const pantry = (Array.isArray(domains.pantry) ? domains.pantry : []).map((item) => (
    domainRecord(
      clean(item.name, "库存物品"),
      "现有库存",
      [
        `数量：${Number(item.quantity || 0)}${enumLabelOrReadableText(item.unit, QUANTITY_UNIT_LABELS, "")}`,
        item.expires_at ? `到期：${clean(item.expires_at)}` : "",
      ]
    )
  ));
  return [...meals, ...recipes, ...pantry];
}

function renderDomainChores(domains = {}) {
  const definitions = Array.isArray(domains.chores) ? domains.chores : [];
  const records = Array.isArray(domains.chore_records) ? domains.chore_records : [];
  return [
    ...definitions.map((item) => domainRecord(
      clean(item.name, "家务"),
      item.enabled ? "轮换中" : "已停用",
      [
        item.last_completed_at ? `上次：${clean(item.last_completed_at)}` : "",
        item.next_due_at ? `下次：${clean(item.next_due_at)}` : "",
        item.cadence_days ? `周期：${Number(item.cadence_days)} 天` : "",
      ]
    )),
    ...records.map((item) => domainRecord(
      clean(item.name, "家务"),
      enumLabelOrReadableText(item.status, LIFE_DOMAIN_STATUS_LABELS, "状态未知"),
      [clean(item.occurred_at, ""), item.duration_minutes ? `${Number(item.duration_minutes)} 分钟` : ""]
    )),
  ];
}

function renderDomainFitness(domains = {}) {
  const items = Array.isArray(domains.fitness) ? domains.fitness : [];
  return items.map((item) => domainRecord(
    clean(item.activity, "运动"),
    enumLabelOrReadableText(item.status, LIFE_DOMAIN_STATUS_LABELS, "状态未知"),
    [
      clean(item.occurred_at || item.date, ""),
      `${Number(item.duration_minutes || 0)} 分钟 · 强度 ${Number(item.intensity || 0)} · 负荷 ${Number(item.load_score || 0)}`,
    ]
  ));
}

function renderDomainActions(domains = {}) {
  const items = Array.isArray(domains.conversation_actions) ? domains.conversation_actions : [];
  return items.map((item) => domainRecord(
    clean(item.title, "行动项"),
    enumLabelOrReadableText(item.status, LIFE_DOMAIN_STATUS_LABELS, "状态未知"),
    [
      `负责人：${enumLabelOrReadableText(item.owner, ACTION_OWNER_LABELS, "未定")}`,
      item.due_at ? `截止：${clean(item.due_at)}` : "",
      item.source_session_label ? `来源会话：${clean(item.source_session_label)}` : "",
    ]
  ));
}

function renderDomains(status = {}) {
  const domains = status.domains && typeof status.domains === "object" ? status.domains : {};
  syncTabSelection(el.domainTabs, "domainTab", state.domainTab);
  let records = [];
  if (state.domainTab === "food") records = renderDomainFood(domains);
  else if (state.domainTab === "chores") records = renderDomainChores(domains);
  else if (state.domainTab === "fitness") records = renderDomainFitness(domains);
  else if (state.domainTab === "actions") records = renderDomainActions(domains);
  else records = renderDomainTimeline(domains);
  if (!records.length) {
    el.domainList?.replaceChildren(empty(domains.enabled === false ? "生活实况已关闭" : "暂无生活实况记录"));
    return;
  }
  el.domainList?.replaceChildren(...records.slice(0, 20));
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
  if (document.hidden || state.view !== "dashboard" || items.length <= 1) {
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
  syncDailyGenerationButton(status);
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
  const available = items.filter((entry) => {
    const executionState = clean(entry.item?.execution_state, "planned");
    return !["cancelled", "skipped", "expired"].includes(executionState);
  });
  const activeIndex = available.findIndex(
    (entry) => clean(entry.item?.execution_state, "planned") === "active"
  );
  if (activeIndex >= 0) {
    const nextEntry = available.slice(activeIndex + 1).find((entry) => {
      const executionState = clean(entry.item?.execution_state, "planned");
      return executionState !== "completed";
    });
    return {
      current: available[activeIndex].item,
      next: nextEntry?.item || null,
    };
  }
  let current = null;
  let next = null;
  for (const entry of available) {
    const executionState = clean(entry.item?.execution_state, "planned");
    if (entry.minutes <= nowMinutes) {
      if (executionState !== "completed") current = entry.item;
      continue;
    }
    if (executionState !== "completed") {
      next = entry.item;
      break;
    }
  }
  return { current, next };
}

function currentTimelineFactText(item = {}) {
  if (!item || typeof item !== "object") return "";
  const placeKind = clean(item.place_kind, "none");
  const placeScope = clean(item.place_scope, "local");
  if (placeKind === "transit") return placeScope === "travel" ? "旅行途中" : "途中";
  if (placeKind === "home") return "居家";
  if (placeScope === "travel" && ["poi", "generic"].includes(placeKind)) return "旅行中";
  if (["poi", "generic"].includes(placeKind)) return "外出中";
  if (placeKind === "online") return "线上活动";
  return "";
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
  const timelineFact = currentTimelineFactText(current);
  if (timelineFact) return timelineFact;
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
    parts.push(node("span", "today-week-label", `风格：${data.style}`));
  }
  if (data.outfit) {
    parts.push(document.createTextNode(`${parts.length ? " " : ""}${data.outfit}`));
  }
  const appearanceLine = (label, title, detail, className) => {
    if (!title && !detail) return null;
    const line = node(
      "div",
      `today-week-appearance-detail ${className}`.trim(),
      "",
    );
    line.append(
      node("span", "today-week-label", title ? `${label}：${title}` : label),
    );
    if (detail) line.append(document.createTextNode(detail));
    return line;
  };
  const appearanceLines = [
    appearanceLine("发型", data.hairStyle, data.hair, "today-week-appearance-hair"),
    appearanceLine("妆容", data.makeupStyle, data.makeup, "today-week-appearance-makeup"),
    appearanceLine("美甲", data.nailsStyle, data.nails, "today-week-appearance-nails"),
  ].filter(Boolean);
  parts.push(...appearanceLines);
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
  const [leftKeys = [], rightKeys = []] = FACT_CARD_COLUMNS;
  const left = leftKeys.map((key) => cards.get(key)).filter(Boolean);
  const right = rightKeys.map((key) => cards.get(key)).filter(Boolean);
  columns[0].replaceChildren(...left);
  columns[1].replaceChildren(...right);
}

function scheduleTodayFactsLayout() {
  window.clearTimeout(state.todayFactsLayoutTimer);
  state.todayFactsLayoutTimer = window.setTimeout(layoutTodayFacts, 0);
}

function worldEmptyText(tab) {
  const labels = {
    relationships: "暂无独立关系记忆；会话摘要会保留来源标注",
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
          ["依据", relationshipText(evidenceText(decision.evidence))],
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
  syncTabSelection(el.worldTabs, "worldTab", activeTab, el.worldList);
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
        const displayName = clean(item.display_name || item.name || item.alias, "未命名关系");
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
          item.reactivated_from_id ? "由较早消息重新激活" : "",
        ].filter(Boolean).join(" · ");
        const sender = relationship.scope(item.sender_name || item.sender_profile_id);
        title.append(node("span", "", readableReferenceLabel(sender, "未知发送者")), node("span", "muted", meta));
        const reactivation = relationshipText(item.reactivation_hint);
        record.append(title, node("div", "record-body", `${relationshipText(item.reason) || "无留意说明"}${reactivation ? ` · 再激活：${reactivation}` : ""}`));
      } else if (activeTab === "action_decisions") {
        const sender = relationship.scope(item.sender_name || item.sender_profile_id);
        const meta = [
          sender || clean(item.sender_name, ""),
          enumLabel(item.scene_type, SCENE_TYPE_LABELS),
          enumLabel(item.understanding, UNDERSTANDING_LABELS),
          item.deep_analysis ? "深析" : "",
        ].filter(Boolean).join(" · ");
        title.append(node("span", "", enumLabel(item.action, ACTION_LABELS) || "未定"), node("span", "muted", meta || `${Math.round(Number(item.confidence || 0) * 100)}%`));
        record.append(title, node("div", "record-body", relationshipText(item.reason) || "无裁定说明"));
      } else if (activeTab === "places") {
        title.append(node("span", "", clean(item.name)), node("span", "muted", `${item.visits || 0} 次`));
        record.append(title, node("div", "record-body", relationshipText(item.hint) || enumLabelOrReadableText(item.type, PLACE_TYPE_LABELS, "其他地点")));
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
  const diagnosticsEnabled = Boolean(status.config?.diagnostics_enabled);
  const relationshipResolver = relationshipTextResolver(status);
  const relationshipText = relationshipResolver.text;
  const relationshipNames = relationshipResolver.names;
  const reviews = objectItems(lifecycle.reviews);
  const reflections = diagnosticsEnabled ? objectItems(lifecycle.reflections) : [];
  const diaries = diagnosticsEnabled ? objectItems(lifecycle.grounded_diary) : [];
  const durableTasks = diagnosticsEnabled ? objectItems(lifecycle.durable_tasks) : [];
  const preferences = objectItems(lifecycle.preferences);
  const events = objectItems(lifecycle.life_events);
  const total = reviews.length + preferences.length + events.length;
  const cognitionTotal = reflections.length + diaries.length + durableTasks.length;
  const totalRecords = total + cognitionTotal;
  if (!totalRecords) {
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
  reflections.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(
      node("span", "", relationshipText(item.summary || "生活反思")),
      node("span", "muted", `${COGNITION_STATUS_LABELS[item.status] || "候选状态"} · 重要度 ${Math.round(Number(item.importance || 0) * 100)}`)
    );
    record.append(title, recordLines([
      item.assertion_subject && item.assertion_predicate
        ? `人格断言：${cognitionSubjectText(item.assertion_subject, "未指明对象", relationshipNames)} · ${cognitionPredicateText(item.assertion_predicate)} · ${cognitionValueText(item.assertion_object)}`
        : "",
      item.evidence_ids?.length ? `依据 ${item.evidence_ids.length} 条` : "",
    ]));
    records.push(record);
  });
  diaries.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", clean(item.title || "生活日记")), node("span", "muted", clean(item.date)));
    record.append(title, recordLines([
      relationshipText(item.summary),
      item.mood_label ? `心绪：${clean(item.mood_label)}` : "",
      item.evidence_ids?.length ? `有证据依据 ${item.evidence_ids.length} 条` : "",
    ]));
    records.push(record);
  });
  durableTasks.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", DURABLE_TASK_KIND_LABELS[item.kind] || "生活任务"), node("span", "muted", COGNITION_STATUS_LABELS[item.status] || "状态未知"));
    record.append(title, recordLines([
      item.available_at ? `可执行：${clean(item.available_at)}` : "",
      item.attempts ? `尝试次数：${item.attempts}/${item.max_attempts || "-"}` : "",
      item.last_error ? "最近执行未完成，已记录重试原因" : "",
    ]));
    records.push(record);
  });
  preferences.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(
      node("span", "", relationshipText(item.content)),
      node("span", "muted", enumLabelStrict(item.category, PREFERENCE_CATEGORY_LABELS, "未分类"))
    );
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
  const diagnosticsEnabled = Boolean(status.config?.diagnostics_enabled);
  const episodes = objectItems(experience.episodes);
  const temporalFacts = objectItems(experience.temporal_facts);
  const personaAssertions = diagnosticsEnabled ? objectItems(experience.persona_assertions) : [];
  const decisionTraces = diagnosticsEnabled ? objectItems(experience.decision_traces) : [];
  const actionOutcomes = diagnosticsEnabled ? objectItems(experience.action_outcomes) : [];
  const affectiveStates = objectItems(experience.affective_states);
  const visibleEpisodes = visibleLifeEpisodes(episodes);
  const evidence = objectItems(experience.evidence);
  const visibleEvidence = visibleExperienceEvidence(evidence, episodes);
  const feedback = uniqueExperienceFeedback(experience.feedback);
  const emotionArcs = diagnosticsEnabled ? objectItems(experience.emotion_arcs) : [];
  const rhythmLogs = diagnosticsEnabled ? objectItems(experience.physiological_rhythm_logs) : [];
  const rhythmTrend = experience.physiological_rhythm_trend && typeof experience.physiological_rhythm_trend === "object"
    ? experience.physiological_rhythm_trend
    : {};
  const focusTargets = diagnosticsEnabled ? objectItems(experience.focus_targets) : [];
  const terms = diagnosticsEnabled ? objectItems(experience.terms) : [];
  // 长期记忆与“世界·会话”可能来自同一份会话摘要。
  // 两处都保留，关系视图明确标出来源，方便追溯而不是把证据静默隐藏。
  const longTermMemories = objectItems(experience.long_term_memories);
  const memoryClusters = diagnosticsEnabled ? objectItems(experience.memory_clusters) : [];
  const memoryEntities = diagnosticsEnabled ? objectItems(experience.memory_entities) : [];
  const memoryConflicts = diagnosticsEnabled ? objectItems(experience.memory_conflicts) : [];
  const health = experience.health && typeof experience.health === "object" ? experience.health : {};
  const relationshipNames = relationshipNameIndex(status);
  const relationshipText = (value) => relationshipReferenceText(value, relationshipNames);
  const cognitionScopeText = (value) => {
    const raw = text(value).trim();
    if (!raw || raw === "global") return "全局生活";
    return relationshipNames.get(raw) || "当前会话";
  };
  const objectText = cognitionValueText;
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
    title.append(node("span", "", "生理节律趋势"), node("span", "muted", subtitle || "今日"));
    const recentLines = rhythmLogs.slice(0, 3).map((item) => {
      const marker = enumLabel(item.lifecycle_kind, RHYTHM_LIFECYCLE_LABELS);
      const body = [
        clean(item.summary || item.body_label || item.energy_curve, ""),
        item.body_intensity !== undefined ? `身体负荷：${Number(item.body_intensity || 0)}/100` : "",
        item.social_battery !== undefined ? `社交电量：${Number(item.social_battery || 0)}/100` : "",
      ].filter(Boolean).join("；");
      return body ? [marker || "今日", body] : "";
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
      item.evidence ? ["依据", relationshipText(evidenceText(item.evidence))] : "",
      item.influence ? ["影响", relationshipText(item.influence)] : "",
      item.expires_at ? `有效期：${clean(item.expires_at)}` : "",
    ]));
    groups.behavior.push(record);
  });

  affectiveStates.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(
      node("span", "", clean(item.label, "当前心绪")),
      node("span", "muted", COGNITION_LAYER_LABELS[item.layer] || clean(item.layer, "情绪"))
    );
    record.append(title, recordLines([
      `强度 ${Math.round(Number(item.intensity || 0) * 100)} · 正负向 ${Number(item.valence || 0).toFixed(2)} · 唤醒度 ${Number(item.arousal || 0).toFixed(2)}`,
      item.scope ? `范围：${cognitionScopeText(item.scope)}` : "",
      item.evidence?.length ? `依据 ${item.evidence.length} 条` : "",
    ]));
    groups.behavior.push(record);
  });

  actionOutcomes.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", clean(item.target || LIFE_ACTION_TYPE_LABELS[item.action_type] || "生活动作")), node("span", "muted", COGNITION_STATUS_LABELS[item.status] || "已记录"));
    record.append(title, recordLines([
      item.reason ? relationshipText(item.reason) : "",
      item.date ? `日期：${clean(item.date)}` : "",
      item.evidence?.length ? `依据 ${item.evidence.length} 条` : "",
    ]));
    groups.behavior.push(record);
  });

  decisionTraces.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", "决策轨迹"), node("span", "muted", DECISION_STAGE_LABELS[item.stage] || "内部阶段"));
    record.append(title, recordLines([
      item.decision ? `裁定：${DECISION_VALUE_LABELS[item.decision] || "其他裁定"}` : "",
      item.reason_code ? `原因：${DECISION_REASON_LABELS[item.reason_code] || "其他结构化规则"}` : "",
      item.outcome ? `结果：${relationshipText(item.outcome)}` : "",
      item.evidence?.length ? `依据 ${item.evidence.length} 条` : "",
    ]));
    groups.evidence.push(record);
  });

  temporalFacts.slice(0, 5).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(
      node("span", "", cognitionSubjectText(item.subject, "时间事实", relationshipNames)),
      node("span", "muted", COGNITION_STATUS_LABELS[item.status] || "当前")
    );
    record.append(title, recordLines([
      `${cognitionPredicateText(item.predicate)}：${objectText(item.object_value)}`,
      item.valid_from ? `生效：${clean(item.valid_from)}` : "",
      `置信度 ${Math.round(Number(item.confidence || 0) * 100)}%${item.scope ? ` · ${cognitionScopeText(item.scope)}` : ""}`,
    ]));
    groups.evidence.push(record);
  });

  personaAssertions.slice(0, 4).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", "已沉淀人格断言"), node("span", "muted", `置信度 ${Math.round(Number(item.confidence || 0) * 100)}%`));
    record.append(title, recordLines([
      `${cognitionSubjectText(item.subject, "未指明对象", relationshipNames)} · ${cognitionPredicateText(item.predicate)}：${objectText(item.object_value)}`,
      item.scope ? `范围：${cognitionScopeText(item.scope)}` : "",
    ]));
    groups.language.push(record);
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
    const focusLabel = relationshipScopeLabel(item.label || item.target_id, relationshipNames);
    title.append(node("span", "", readableReferenceLabel(focusLabel, "关注目标")), node("span", "muted", `关注 ${Number(item.priority || 0)}`));
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
    record.append(title, recordLines([clean(item.meaning), item.evidence ? ["证据", relationshipText(evidenceText(item.evidence))] : ""]));
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
    title.append(
      node("span", "", relationshipScopeLabel(item.name, relationshipNames) || clean(item.name, "记忆实体")),
      node("span", "muted", `${memoryEntityTypeLabel(item.entity_type)} · ${Number(item.mention_count || 0)} 次`)
    );
    record.append(title, recordLines([
      item.last_seen ? `最近出现：${item.last_seen}` : "",
      item.scope ? ["范围", relationshipScopeLabel(item.scope, relationshipNames)] : "",
    ]));
    groups.relationships.push(record);
  });

  memoryConflicts.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(node("span", "", "记忆张力"), node("span", "muted", memoryConflictTypeLabel(item.conflict_type)));
    record.append(title, recordLines([relationshipText(item.summary), relationshipText(item.resolution)]));
    groups.relationships.push(record);
  });

  longTermMemories.slice(0, 3).forEach((item) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    const scope = relationshipScopeLabel(item.scope, relationshipNames);
    const sourceTable = text(item.source_table).trim().toLowerCase();
    const category = text(item.category).trim().toLowerCase();
    const fromChatSummary = sourceTable === "chat_summaries" || category === "chat_summary";
    const titleText = relationshipScopeLabel(item.title || item.scope, relationshipNames)
      || clean(item.title, "")
      || longTermMemoryCategoryLabel(item.category);
    title.append(
      node("span", "", titleText),
      node("span", "muted", scope || longTermMemoryCategoryLabel(item.category))
    );
    record.append(title, recordLines([
      relationshipText(item.content),
      scope ? ["范围", scope] : "",
      fromChatSummary ? "来源：会话摘要" : "",
      item.expires_at ? `有效期：${item.expires_at}` : "",
    ]));
    groups.relationships.push(record);
  });

  visibleEvidence.slice(0, 3).forEach((item, index) => {
    const record = node("div", "record");
    const title = node("div", "record-title");
    title.append(
      node("span", "", evidenceTargetTitle(item, index + 1, relationshipNames)),
      node("span", "muted", enumLabel(item.evidence_type, EVIDENCE_TYPE_LABELS))
    );
    record.append(title, recordLines([relationshipText(evidenceText(item.summary))]));
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
  syncTabSelection(el.experienceTabs, "experienceTab", activeTab, el.experienceList);
  const total = Object.values(groups).reduce((sum, records) => sum + records.length, 0);
  if (!total) {
    el.experienceList.replaceChildren(empty("暂无体验层记录"));
    return;
  }
  const records = groups[activeTab] || [];
  el.experienceList.replaceChildren(...(records.length ? records : [empty(experienceEmptyText(activeTab))]));
}

function emojiSourceLabel(item = {}) {
  return enumLabelStrict(item.source_kind, EMOJI_SOURCE_LABELS, "未知来源");
}

function emojiStatusLabel(item = {}) {
  const status = enumLabelStrict(item.status, EMOJI_STATUS_LABELS, "未知");
  if (item.status === "ready" && !item.sendable) return `${status} · 停用`;
  return status;
}

function emojiStatusMark(item = {}) {
  return item.status === "ready" && item.sendable ? "✔️" : "❌";
}

function emojiTypeLabel(item = {}) {
  return enumLabelStrict(item.asset_type, EMOJI_TYPE_LABELS, "未分类");
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

  const tags = emojiEmotionLabels(item.emotions).join("、");
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
  focusDialog(el.emojiDetailDialog, el.emojiDetailClose);
}

function closeEmojiDetail() {
  const wasOpen = Boolean(state.emojiDetailId);
  state.emojiDetailId = 0;
  renderEmojiDetailDialog();
  if (wasOpen) restoreDialogFocus();
}

function openEmojiImport() {
  if (!el.emojiImportDialog) return;
  el.emojiImportDialog.hidden = false;
  if (typeof el.emojiImportDialog.setAttribute === "function") {
    el.emojiImportDialog.setAttribute("aria-hidden", "false");
  }
  focusDialog(el.emojiImportDialog, el.emojiImportFileButton);
}

function closeEmojiImport() {
  if (!el.emojiImportDialog) return;
  const wasOpen = !el.emojiImportDialog.hidden;
  el.emojiImportDialog.hidden = true;
  if (typeof el.emojiImportDialog.setAttribute === "function") {
    el.emojiImportDialog.setAttribute("aria-hidden", "true");
  }
  if (el.emojiImportFile) el.emojiImportFile.value = "";
  if (wasOpen) restoreDialogFocus();
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
    if (!quiet) setNotice(userErrorMessage(error, "表情素材加载失败"), "error");
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

async function uploadEmojiFiles(files, successLabel) {
  const items = Array.isArray(files) ? files.filter(Boolean) : [];
  if (!items.length) {
    setNotice("没有可导入的表情素材", "error");
    return;
  }
  setBusy(true);
  stopEmojiAutoRefresh();
  try {
    let latest = null;
    for (const file of items) {
      latest = await apiUpload("page/emoji/import", file, {
        timeoutMs: 60000,
        timeoutMessage: "表情导入耗时较久，请稍后查看",
      });
    }
    if (latest) applyEmojiPayload(latest);
    closeEmojiImport();
    setNotice(successLabel || `已导入 ${items.length} 个表情素材`, "success");
  } catch (error) {
    setNotice(userErrorMessage(error, "表情导入失败"), "error");
  } finally {
    setBusy(false);
    scheduleEmojiAutoRefresh();
  }
}

async function importEmojiFiles(files) {
  const selected = Array.from(files || []);
  if (!selected.length) return;
  const uploads = [];
  let skippedUnsupported = 0;
  let skippedTooLarge = 0;
  for (const file of selected) {
    if (!isEmojiImportFile(file)) {
      skippedUnsupported += 1;
      continue;
    }
    if (Number(file.size || 0) > EMOJI_IMPORT_MAX_BYTES) {
      skippedTooLarge += 1;
      continue;
    }
    uploads.push(file);
  }
  if (!uploads.length && (skippedUnsupported || skippedTooLarge)) {
    const reasons = [];
    if (skippedTooLarge) reasons.push(`${skippedTooLarge} 个超过 ${EMOJI_IMPORT_MAX_MB} MB`);
    if (skippedUnsupported) reasons.push(`${skippedUnsupported} 个格式不支持`);
    setNotice(`没有可导入的表情素材：${reasons.join("，")}`, "error");
    return;
  }
  const skipped = skippedUnsupported + skippedTooLarge;
  await uploadEmojiFiles(
    uploads,
    skipped ? `已导入 ${uploads.length} 个表情素材，跳过 ${skipped} 个` : `已导入 ${uploads.length} 个表情素材`
  );
}

async function backupEmojiAssets() {
  setBusy(true);
  stopEmojiAutoRefresh();
  try {
    await apiDownload("page/emoji/backup", {}, "", {
      timeoutMs: 120000,
      timeoutMessage: "表情备份耗时较久，请稍后再试",
    });
    setNotice("表情素材备份已下载", "success");
  } catch (error) {
    setNotice(userErrorMessage(error, "表情备份失败"), "error");
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
    const result = await apiUpload("page/emoji/restore", file, {
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
    setNotice(userErrorMessage(error, "表情还原失败"), "error");
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
    setNotice(userErrorMessage(error, "表情状态保存失败"), "error");
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
    setNotice(userErrorMessage(error, "表情素材删除失败"), "error");
  } finally {
    resetEmojiBulkDeleteButton();
    setBusy(false);
    scheduleEmojiAutoRefresh();
  }
}

const CLOSET_KIND_LABELS = {
  outfit: "套装",
  top: "上装",
  bottom: "下装",
  footwear: "鞋袜",
  accessory: "配饰",
  hair: "发型",
  makeup: "妆容",
  nails: "美甲",
};
const CLOSET_KIND_ORDER = [
  "outfit",
  "top",
  "bottom",
  "footwear",
  "accessory",
  "hair",
  "makeup",
  "nails",
];
const CLOSET_STATUS_LABELS = {
  active: "已启用",
  pending: "待确认",
  disabled: "已停用",
  rejected: "不采用",
};
const CLOSET_SOURCE_LABELS = {
  manual: "手动上传",
  user_image: "会话图片",
  product_image: "商品图片",
  web_image: "联网图片",
};

function closetTargetIds(ids) {
  return Array.from(new Set((Array.isArray(ids) ? ids : [ids])
    .map((id) => Number(id || 0))
    .filter((id) => id > 0)));
}

function closetSelectedIds() {
  return closetTargetIds(Array.from(state.closetSelectedIds || []));
}

function closetItem(id) {
  const itemId = Number(id || 0);
  return objectItems(state.closetItems).find((item) => Number(item.id || 0) === itemId) || null;
}

function resetClosetDeleteButton(button = el.closetBulkDeleteButton, label = "删除选中") {
  if (!button) return;
  window.clearTimeout(Number(button.dataset.confirmTimer || 0));
  delete button.dataset.confirmDelete;
  delete button.dataset.confirmTimer;
  button.classList.remove("is-confirming");
  button.textContent = label;
}

function resetClosetManageState() {
  resetClosetDeleteButton();
  state.closetSelectedIds.clear();
  state.closetManageMode = false;
}

function beginClosetManage() {
  state.closetManageMode = true;
  renderClosetManagement();
}

function cancelClosetManage() {
  resetClosetManageState();
  renderClosetManagement();
}

function toggleClosetSelected(id) {
  if (!state.closetManageMode) return;
  const itemId = Number(id || 0);
  if (itemId <= 0) return;
  resetClosetDeleteButton();
  if (state.closetSelectedIds.has(itemId)) state.closetSelectedIds.delete(itemId);
  else state.closetSelectedIds.add(itemId);
  renderClosetManagement();
}

function toggleClosetGroupSelected(ids) {
  if (!state.closetManageMode) return;
  const targets = closetTargetIds(ids);
  if (!targets.length) return;
  resetClosetDeleteButton();
  const allSelected = targets.every((id) => state.closetSelectedIds.has(id));
  targets.forEach((id) => {
    if (allSelected) state.closetSelectedIds.delete(id);
    else state.closetSelectedIds.add(id);
  });
  renderClosetManagement();
}

function pruneClosetSelection() {
  const live = new Set(objectItems(state.closetItems).map((item) => Number(item.id || 0)));
  state.closetSelectedIds = new Set(closetSelectedIds().filter((id) => live.has(id)));
}

function filteredClosetItems() {
  const filter = text(state.closetFilter || "all");
  const items = objectItems(state.closetItems);
  if (["active", "pending", "disabled"].includes(filter)) {
    return items.filter((item) => closetStatus(item) === filter);
  }
  if (Object.hasOwn(CLOSET_KIND_LABELS, filter)) return items.filter((item) => item.kind === filter);
  if (filter === "liked") return items.filter((item) => Number(item.preference_score || 0) > 0);
  if (filter === "low_confidence") return items.filter((item) => Number(item.confidence || 0) < 0.72);
  if (filter === "web") return items.filter((item) => ["web_image", "product_image"].includes(item.source_kind));
  if (filter === "local") return items.filter((item) => !["web_image", "product_image"].includes(item.source_kind));
  return items;
}

function closetGroups(items = []) {
  const groups = new Map();
  objectItems(items).forEach((item) => {
    const key = clean(item.source_group_key || item.source_image_hash, "") || `item:${Number(item.id || 0)}`;
    if (!groups.has(key)) groups.set(key, { key, items: [] });
    groups.get(key).items.push(item);
  });
  return Array.from(groups.values()).map((group) => {
    const outfit = group.items.find((item) => item.kind === "outfit");
    const preview = group.items.find((item) => item.preview_available);
    return { ...group, cover: outfit || preview || group.items[0] || {} };
  });
}

function closetPageWindow(items = []) {
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / CLOSET_PAGE_SIZE));
  const raw = Number(state.closetPage || 1);
  const page = Math.min(Math.max(1, Number.isFinite(raw) ? Math.trunc(raw) : 1), pageCount);
  const start = total ? (page - 1) * CLOSET_PAGE_SIZE : 0;
  const end = Math.min(start + CLOSET_PAGE_SIZE, total);
  state.closetPage = page;
  return { total, page, pageCount, start, end, items: items.slice(start, end) };
}

function renderClosetSelectionTools() {
  const selected = closetSelectedIds();
  const managing = Boolean(state.closetManageMode);
  if (el.closetManageButton) {
    el.closetManageButton.classList.toggle("is-active", managing);
    el.closetManageButton.disabled = state.busy;
  }
  if (el.closetSelectedSummary) {
    el.closetSelectedSummary.hidden = !managing;
    el.closetSelectedSummary.textContent = `已选 ${selected.length} 条`;
  }
  [el.closetBulkEnableButton, el.closetBulkDisableButton, el.closetBulkDeleteButton].forEach((button) => {
    if (!button) return;
    button.hidden = !managing;
    button.dataset.lockDisabled = managing && selected.length ? "false" : "true";
    button.disabled = state.busy || !managing || !selected.length;
  });
  if (el.closetCancelManageButton) {
    el.closetCancelManageButton.hidden = !managing;
    el.closetCancelManageButton.disabled = state.busy || !managing;
  }
}

function renderClosetStats() {
  if (!el.closetStats) return;
  const stats = closetComputedStats();
  const entries = [
    ["已启用", stats.active],
    ["待确认", stats.pending],
    ["套装", stats.outfit],
    ["上装", stats.top],
    ["下装", stats.bottom],
    ["鞋袜", stats.footwear],
    ["配饰", stats.accessory],
    ["发型", stats.hair],
    ["妆容", stats.makeup],
    ["美甲", stats.nails],
  ];
  el.closetStats.replaceChildren(...entries.map(([label, value]) => {
    const card = node("div", "closet-stat");
    card.append(
      node("span", "closet-stat-label", label),
      node("strong", "", Number(value || 0))
    );
    return card;
  }));
}

function closetComputedStats() {
  const items = objectItems(state.closetItems);
  const stats = {
    total: items.length,
    active: 0,
    pending: 0,
    disabled: 0,
    outfit: 0,
    top: 0,
    bottom: 0,
    footwear: 0,
    accessory: 0,
    hair: 0,
    makeup: 0,
    nails: 0,
  };
  items.forEach((item) => {
    const status = closetStatus(item);
    if (Object.hasOwn(stats, status)) stats[status] += 1;
    if (Object.hasOwn(CLOSET_KIND_LABELS, item.kind)) stats[item.kind] += 1;
  });
  return stats;
}

function closetAttributeText(item = {}, key, limit = 4) {
  const value = item.attributes?.[key];
  const values = Array.isArray(value) ? value : value ? [value] : [];
  return values.map((part) => clean(part, "")).filter(Boolean).slice(0, limit).join("、");
}

function closetBadges(item = {}) {
  const status = closetStatus(item);
  const values = [
    [CLOSET_KIND_LABELS[item.kind] || "造型", ""],
    [CLOSET_STATUS_LABELS[status] || "待确认", `is-${status}`],
  ];
  if (Number(item.preference_score || 0) > 0) values.push(["喜欢", ""]);
  return values;
}

export function closetStatus(item = {}) {
  const status = text(item.status, "pending").trim().toLowerCase();
  if (status === "archived") return "disabled";
  return Object.hasOwn(CLOSET_STATUS_LABELS, status) ? status : "pending";
}

function closetSourceText(item = {}) {
  const source = CLOSET_SOURCE_LABELS[item.source_kind] || "未知来源";
  const host = clean(item.source_host, "");
  return host ? `${source} · ${host}` : source;
}

function closetGroupBadges(items = []) {
  const counts = new Map();
  objectItems(items).forEach((item) => {
    const status = closetStatus(item);
    counts.set(status, (counts.get(status) || 0) + 1);
  });
  const badges = [];
  counts.forEach((count, status) => {
    const label = CLOSET_STATUS_LABELS[status] || "待确认";
    badges.push([
      count === items.length ? label : `${label} ${count}`,
      `is-${status}`,
    ]);
  });
  if (items.some((item) => Number(item.preference_score || 0) > 0)) {
    badges.push(["喜欢", ""]);
  }
  return badges;
}

async function loadClosetPreview(img, id) {
  const itemId = Number(id || 0);
  if (!img || itemId <= 0) return;
  const cached = state.closetPreviewCache.get(itemId);
  if (cached) {
    img.src = cached;
    return;
  }
  try {
    const result = await apiPost("page/closet/preview", { id: itemId });
    if (!result.data_url) throw new Error("衣橱预览为空");
    state.closetPreviewCache.set(itemId, result.data_url);
    if (img.isConnected) img.src = result.data_url;
  } catch (_error) {
    img.removeAttribute("src");
    img.closest(".closet-thumb, .closet-detail-preview")?.classList.add("is-error");
  }
}

function closetRecord(group = {}) {
  const items = objectItems(group.items);
  const item = group.cover || items[0] || {};
  const itemId = Number(item.id || 0);
  const itemIds = closetTargetIds(items.map((entry) => entry.id));
  const record = node("article", "closet-record");
  const selectedCount = itemIds.filter((id) => state.closetSelectedIds.has(id)).length;
  record.classList.toggle("is-selected", state.closetManageMode && selectedCount > 0);
  if (state.closetManageMode) {
    const selector = node("label", "closet-select");
    selector.addEventListener("click", (event) => event.stopPropagation());
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedCount > 0 && selectedCount === itemIds.length;
    checkbox.indeterminate = selectedCount > 0 && selectedCount < itemIds.length;
    checkbox.setAttribute("aria-label", `选择${clean(item.title || item.description, "衣橱来源图")}的全部候选`);
    checkbox.addEventListener("change", () => toggleClosetGroupSelected(itemIds));
    selector.append(checkbox);
    record.append(selector);
  }
  const thumb = node("button", "closet-thumb");
  thumb.type = "button";
  thumb.setAttribute("aria-label", `${state.closetManageMode ? "选择" : "查看"}${clean(item.title, "衣橱来源图")}`);
  thumb.addEventListener("click", () => state.closetManageMode ? toggleClosetGroupSelected(itemIds) : openClosetDetail(itemId));
  if (item.preview_available) {
    const image = document.createElement("img");
    image.alt = clean(item.title, "衣橱预览");
    image.loading = "lazy";
    image.decoding = "async";
    thumb.append(image);
    loadClosetPreview(image, itemId);
  } else {
    thumb.append(node("span", "closet-thumb-empty", "无预览"));
  }
  const body = node("div", "closet-record-body");
  const title = node("div", "closet-record-title");
  const suffix = items.length > 1 ? `#${itemId} · ${items.length} 项` : `#${itemId}`;
  title.append(node("strong", "", clean(item.title || item.description, "未命名造型")), node("span", "muted", suffix));
  const confidence = items.length
    ? Math.round(Math.min(...items.map((entry) => Number(entry.confidence || 0))) * 100)
    : 0;
  const sourceRow = node("div", "closet-record-source-row");
  sourceRow.append(
    node("span", "closet-record-source", closetSourceText(item)),
    node("span", "closet-record-confidence", `最低置信度 ${confidence}%`),
  );
  const statusBadges = node("div", "closet-badges");
  closetGroupBadges(items).forEach(([label, className]) => {
    statusBadges.append(node("span", `closet-badge ${className}`.trim(), label));
  });
  const components = node("div", "closet-components");
  items.forEach((entry) => {
    const button = node("button", `closet-component is-${closetStatus(entry)}`, CLOSET_KIND_LABELS[entry.kind] || "造型");
    button.type = "button";
    button.title = clean(entry.title || entry.description, "查看候选详情");
    button.setAttribute("aria-label", `查看${CLOSET_KIND_LABELS[entry.kind] || "造型"}：${button.title}`);
    button.addEventListener("click", () => state.closetManageMode ? toggleClosetSelected(entry.id) : openClosetDetail(entry.id));
    components.append(button);
  });
  const query = clean(item.source_query, "");
  body.append(title, sourceRow, statusBadges, components);
  if (query) body.append(node("div", "closet-record-meta", `搜索：${query}`));
  record.append(thumb, body);
  return record;
}

function renderClosetPager(pageInfo) {
  if (!el.closetPager) return;
  el.closetPager.hidden = pageInfo.total <= 0;
  if (pageInfo.total <= 0) return;
  el.closetPrevPage.disabled = state.busy || pageInfo.page <= 1;
  el.closetNextPage.disabled = state.busy || pageInfo.page >= pageInfo.pageCount;
  el.closetPageInfo.textContent = "第 " + pageInfo.page + " / " + pageInfo.pageCount + " 页";
}

function renderClosetManagement() {
  if (!el.closetList) return;
  pruneClosetSelection();
  renderClosetStats();
  renderClosetSelectionTools();
  const items = filteredClosetItems();
  const groups = closetGroups(items);
  const pageInfo = closetPageWindow(groups);
  renderClosetPager(pageInfo);
  const total = closetComputedStats().total;
  if (el.closetSummary) {
    el.closetSummary.textContent = !total
      ? "暂无衣橱素材"
      : !items.length
        ? `暂无符合条件的素材，共 ${total} 个候选`
        : `显示 ${pageInfo.start + 1}-${pageInfo.end} 组，共 ${groups.length} 组；总数 ${total} 个候选`;
  }
  el.closetList.replaceChildren(...(pageInfo.items.length ? pageInfo.items.map(closetRecord) : [empty("暂无符合条件的衣橱素材")]));
  renderClosetDetail();
}

function applyClosetPayload(payload = {}) {
  state.closetItems = objectItems(payload.items);
  state.closetStats = payload.stats && typeof payload.stats === "object" ? payload.stats : {};
  state.closetLoaded = true;
  pruneClosetSelection();
  renderClosetManagement();
}

async function loadClosetAssets({ quiet = false } = {}) {
  if (state.closetLoading) return;
  state.closetLoading = true;
  try {
    applyClosetPayload(await apiGet("page/closet/list", { _ts: Date.now() }));
    if (!quiet) setNotice("");
  } catch (error) {
    if (!quiet) setNotice(userErrorMessage(error, "衣橱素材加载失败"), "error");
  } finally {
    state.closetLoading = false;
  }
}

function closetDetailField(list, label, value, className = "") {
  const textValue = clean(value, "");
  if (!textValue) return;
  const wrapper = node("div", className);
  wrapper.append(node("dt", "", label), node("dd", "", textValue));
  list.append(wrapper);
}

function closetVisualPrompt(item = {}) {
  return clean(item.attributes?.visual_prompt || item.description, "暂无视觉提示词");
}

function closetGroupKey(item = {}) {
  return clean(item.source_group_key || item.source_image_hash, "")
    || `item:${Number(item.id || 0)}`;
}

function closetDetailItems(item = {}) {
  const key = closetGroupKey(item);
  return objectItems(state.closetItems)
    .filter((entry) => closetGroupKey(entry) === key)
    .sort((left, right) => {
      const leftOrder = CLOSET_KIND_ORDER.indexOf(left.kind);
      const rightOrder = CLOSET_KIND_ORDER.indexOf(right.kind);
      if (leftOrder !== rightOrder) {
        return (leftOrder < 0 ? CLOSET_KIND_ORDER.length : leftOrder)
          - (rightOrder < 0 ? CLOSET_KIND_ORDER.length : rightOrder);
      }
      return Number(left.id || 0) - Number(right.id || 0);
    });
}

function closetSourceLink(item = {}) {
  const value = clean(item.source_url, "");
  if (!value) return null;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    const link = node("a", "closet-source-link", "查看来源网页");
    link.href = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch (_error) {
    return null;
  }
}

function renderClosetDetail() {
  if (!el.closetDetailDialog || !el.closetDetailBody) return;
  const item = closetItem(state.closetDetailId);
  if (!item) {
    closeClosetDetail();
    return;
  }
  el.closetDetailTitle.textContent = clean(item.title, "衣橱详情");
  const preview = node("div", "closet-detail-preview");
  if (item.preview_available) {
    const image = document.createElement("img");
    image.alt = clean(item.title, "衣橱预览");
    preview.append(image);
    loadClosetPreview(image, item.id);
  } else preview.append(node("span", "closet-thumb-empty", "无预览"));
  const copy = node("div", "closet-detail-copy");
  const badges = node("div", "closet-badges");
  closetBadges(item).forEach(([label, className]) => badges.append(node("span", `closet-badge ${className}`.trim(), label)));
  const grid = document.createElement("dl");
  grid.className = "closet-detail-grid";
  closetDetailField(grid, "偏好分", Number(item.preference_score || 0).toFixed(2));
  closetDetailField(grid, "置信度", `${Math.round(Number(item.confidence || 0) * 100)}%`);
  closetDetailField(grid, "学习次数", Number(item.seen_count || 0));
  closetDetailField(grid, "使用次数", Number(item.used_count || 0));
  closetDetailField(grid, "最近使用", clean(item.last_used_at, "尚未使用"));
  closetDetailField(grid, "色彩", closetAttributeText(item, "colors"));
  closetDetailField(grid, "服饰类型", closetAttributeText(item, "garment_type"));
  closetDetailField(grid, "单品", closetAttributeText(item, "pieces"));
  closetDetailField(grid, "组成", closetAttributeText(item, "items"));
  closetDetailField(grid, "叠穿", closetAttributeText(item, "layers"));
  closetDetailField(grid, "鞋袜", closetAttributeText(item, "footwear"));
  closetDetailField(grid, "袜子", closetAttributeText(item, "socks"));
  closetDetailField(grid, "配饰", closetAttributeText(item, "accessories"));
  closetDetailField(grid, "位置", closetAttributeText(item, "placement"));
  closetDetailField(grid, "领口", closetAttributeText(item, "neckline"));
  closetDetailField(grid, "袖型", closetAttributeText(item, "sleeve"));
  closetDetailField(grid, "腰线", closetAttributeText(item, "waist"));
  closetDetailField(grid, "版型", closetAttributeText(item, "fit"));
  closetDetailField(grid, "下摆", closetAttributeText(item, "hem"));
  closetDetailField(grid, "妆效", closetAttributeText(item, "finish"));
  closetDetailField(grid, "底妆", closetAttributeText(item, "base"));
  closetDetailField(grid, "眉形", closetAttributeText(item, "brows"));
  closetDetailField(grid, "眼妆", closetAttributeText(item, "eyes"));
  closetDetailField(grid, "腮红", closetAttributeText(item, "cheeks"));
  closetDetailField(grid, "唇妆", closetAttributeText(item, "lips"));
  closetDetailField(grid, "甲型", closetAttributeText(item, "shape"));
  closetDetailField(grid, "设计", closetAttributeText(item, "designs"));
  closetDetailField(grid, "场景", closetAttributeText(item, "scenes"));
  closetDetailField(
    grid,
    "来源",
    enumLabelStrict(item.source_kind, CLOSET_SOURCE_LABELS, "未知来源")
  );
  closetDetailField(grid, "来源站点", clean(item.source_host, ""));
  closetDetailField(grid, "搜索需求", clean(item.source_query, ""));
  closetDetailField(grid, "识别批次", clean(item.source_batch_id, ""));
  const actions = node("div", "closet-detail-actions");
  const like = node("button", "", "喜欢");
  like.type = "button";
  like.addEventListener("click", () => setClosetFeedback([item.id], "prefer"));
  const dislike = node("button", "", "不喜欢");
  dislike.type = "button";
  dislike.addEventListener("click", () => setClosetFeedback([item.id], "dislike"));
  const review = node("button", "", "重新识别");
  review.type = "button";
  review.addEventListener("click", () => reviewClosetItem(item.id));
  const enabled = closetStatus(item) === "active";
  const toggle = node("button", "", enabled ? "停用" : "启用");
  toggle.type = "button";
  toggle.addEventListener("click", () => setClosetStatus(
    [item.id],
    enabled ? "disabled" : "active"
  ));
  const remove = node("button", "danger", "删除");
  remove.type = "button";
  remove.addEventListener("click", () => confirmClosetDelete(remove, [item.id], "删除"));
  actions.append(like, dislike, review, toggle, remove);
  const sourceLink = closetSourceLink(item);
  const prompts = node("div", "closet-detail-prompts");
  closetDetailItems(item).forEach((entry) => {
    const prompt = node("section", "closet-detail-prompt");
    prompt.append(
      node("h3", "closet-detail-prompt-title", CLOSET_KIND_LABELS[entry.kind] || "造型"),
      node("p", "closet-detail-description", closetVisualPrompt(entry)),
    );
    prompts.append(prompt);
  });
  copy.append(badges, prompts, grid);
  if (sourceLink) copy.append(sourceLink);
  copy.append(actions);
  el.closetDetailBody.replaceChildren(preview, copy);
}

function openClosetDetail(id) {
  const itemId = Number(id || 0);
  if (!closetItem(itemId)) return;
  state.closetDetailId = itemId;
  el.closetDetailDialog.hidden = false;
  el.closetDetailDialog.setAttribute("aria-hidden", "false");
  renderClosetDetail();
  focusDialog(el.closetDetailDialog, el.closetDetailClose);
}

function closeClosetDetail() {
  if (!el.closetDetailDialog) return;
  const wasOpen = !el.closetDetailDialog.hidden;
  state.closetDetailId = 0;
  el.closetDetailDialog.hidden = true;
  el.closetDetailDialog.setAttribute("aria-hidden", "true");
  if (wasOpen) restoreDialogFocus();
}

function openClosetBrowse() {
  if (!el.closetBrowseDialog) return;
  el.closetBrowseDialog.hidden = false;
  el.closetBrowseDialog.setAttribute("aria-hidden", "false");
  focusDialog(el.closetBrowseDialog, el.closetBrowseQuery);
}

function closeClosetBrowse() {
  if (!el.closetBrowseDialog) return;
  const wasOpen = !el.closetBrowseDialog.hidden;
  el.closetBrowseDialog.hidden = true;
  el.closetBrowseDialog.setAttribute("aria-hidden", "true");
  if (wasOpen) restoreDialogFocus();
}

async function importClosetFiles(files) {
  const selected = Array.from(files || []);
  const valid = selected.filter((file) => String(file.type || "").startsWith("image/") && Number(file.size || 0) <= CLOSET_IMPORT_MAX_BYTES);
  if (!valid.length) {
    if (selected.length) setNotice(`请选择不超过 ${CLOSET_IMPORT_MAX_MB} MB 的图片`, "error");
    return;
  }
  setBusy(true);
  try {
    let result = null;
    let succeeded = 0;
    let candidates = 0;
    const failures = [];
    for (const file of valid) {
      try {
        result = await apiUpload("page/closet/import", file, {
          timeoutMs: 180000,
          timeoutMessage: "衣橱图片识别耗时较久，请稍后查看",
        });
        succeeded += 1;
        candidates += objectItems(result.imported).length;
      } catch (error) {
        failures.push(userErrorMessage(error, "识别失败"));
      }
    }
    if (!result) throw new Error(failures[0] || "衣橱图片学习失败");
    applyClosetPayload(result);
    const skipped = selected.length - valid.length;
    const details = [`识别 ${succeeded} 张图片`, `形成 ${candidates} 个候选`];
    if (failures.length) details.push(`${failures.length} 张失败`);
    if (skipped) details.push(`${skipped} 张格式或大小不符合要求`);
    setNotice(details.join("，"), failures.length ? "warning" : "success");
  } catch (error) {
    setNotice(userErrorMessage(error, "衣橱图片学习失败"), "error");
  } finally {
    setBusy(false);
  }
}

async function browseCloset() {
  const query = clean(el.closetBrowseQuery?.value, "");
  if (!query) {
    setNotice("请输入要学习的穿搭或发型需求", "error");
    el.closetBrowseQuery?.focus();
    return;
  }
  setBusy(true);
  try {
    const result = await apiPost("page/closet/browse", {
      query,
      kind: el.closetBrowseKind?.value || "auto",
      count: Number(el.closetBrowseCount?.value || 3),
      note: clean(el.closetBrowseNote?.value, ""),
    }, { timeoutMs: 300000, timeoutMessage: "联网学习耗时较久，请稍后查看" });
    applyClosetPayload(result);
    closeClosetBrowse();
    setNotice("联网造型学习完成", "success");
  } catch (error) {
    setNotice(userErrorMessage(error, "联网学习失败"), "error");
  } finally {
    setBusy(false);
  }
}

async function setClosetStatus(ids, status) {
  const targets = closetTargetIds(ids);
  if (!targets.length) return;
  setBusy(true);
  try {
    applyClosetPayload(await apiPost("page/closet/status", { ids: targets, status }));
    setNotice(status === "active" ? "衣橱素材已启用" : "衣橱素材已停用", "success");
  } catch (error) {
    setNotice(userErrorMessage(error, "衣橱状态保存失败"), "error");
  } finally {
    setBusy(false);
  }
}

async function setClosetFeedback(ids, sentiment) {
  const targets = closetTargetIds(ids);
  if (!targets.length) return;
  setBusy(true);
  try {
    applyClosetPayload(await apiPost("page/closet/feedback", { ids: targets, sentiment }));
    setNotice(sentiment === "prefer" ? "已提高这组造型的偏好" : "已降低这组造型的偏好", "success");
  } catch (error) {
    setNotice(userErrorMessage(error, "衣橱反馈保存失败"), "error");
  } finally {
    setBusy(false);
  }
}

async function reviewClosetItem(id) {
  setBusy(true);
  try {
    applyClosetPayload(await apiPost("page/closet/review", { id }, {
      timeoutMs: 180000,
      timeoutMessage: "衣橱素材重新识别耗时较久，请稍后查看",
    }));
    setNotice("衣橱素材已重新识别", "success");
  } catch (error) {
    setNotice(userErrorMessage(error, "衣橱素材重新识别失败"), "error");
  } finally {
    setBusy(false);
  }
}

function confirmClosetDelete(button, ids, label = "删除选中") {
  const targets = closetTargetIds(ids);
  if (!button || !targets.length) return;
  if (button.dataset.confirmDelete === "true") {
    resetClosetDeleteButton(button, label);
    deleteClosetItems(targets);
    return;
  }
  button.dataset.confirmDelete = "true";
  button.classList.add("is-confirming");
  button.textContent = targets.length > 1 ? `确认删除 ${targets.length} 条` : "确认删除";
  button.dataset.confirmTimer = String(window.setTimeout(() => resetClosetDeleteButton(button, label), 3200));
  setNotice("再次点击确认删除衣橱素材");
}

async function deleteClosetItems(ids) {
  const targets = closetTargetIds(ids);
  if (!targets.length) return;
  setBusy(true);
  try {
    const result = await apiPost("page/closet/delete", { ids: targets });
    targets.forEach((id) => {
      state.closetPreviewCache.delete(id);
      state.closetSelectedIds.delete(id);
    });
    if (targets.includes(Number(state.closetDetailId || 0))) closeClosetDetail();
    resetClosetManageState();
    applyClosetPayload(result);
    setNotice(targets.length > 1 ? `已删除 ${targets.length} 条衣橱素材` : "衣橱素材已删除", "success");
  } catch (error) {
    setNotice(userErrorMessage(error, "衣橱素材删除失败"), "error");
  } finally {
    setBusy(false);
  }
}

async function backupCloset() {
  setBusy(true);
  try {
    await apiDownload("page/closet/backup", {}, "", { timeoutMs: 180000, timeoutMessage: "衣橱备份耗时较久" });
    setNotice("衣橱备份已下载", "success");
  } catch (error) {
    setNotice(userErrorMessage(error, "衣橱备份失败"), "error");
  } finally {
    setBusy(false);
  }
}

async function restoreCloset(file) {
  if (!file) return;
  if (!String(file.name || "").toLowerCase().endsWith(".zip")) {
    setNotice("请选择 ZIP 衣橱备份文件", "error");
    return;
  }
  if (Number(file.size || 0) > CLOSET_BACKUP_MAX_BYTES) {
    setNotice(`衣橱备份不能超过 ${CLOSET_BACKUP_MAX_MB} MB`, "error");
    return;
  }
  setBusy(true);
  try {
    const result = await apiUpload("page/closet/restore", file, { timeoutMs: 180000, timeoutMessage: "衣橱还原耗时较久" });
    state.closetPreviewCache.clear();
    applyClosetPayload(result);
    setNotice(`已还原 ${Number(result.restored || 0)} 条衣橱素材`, "success");
  } catch (error) {
    setNotice(userErrorMessage(error, "衣橱还原失败"), "error");
  } finally {
    setBusy(false);
  }
}

function renderDashboard() {
  const status = state.status || {};
  renderDay(status);
  renderDomains(status);
  renderWorld(status);
  renderLifecycle(status);
  renderExperience(status);
  renderMemoryPanel();
}

function syncTabSelection(tabs, datasetKey, activeValue, panel = null) {
  tabs.forEach((tab) => {
    const active = tab.dataset[datasetKey] === activeValue;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
    tab.tabIndex = active ? 0 : -1;
    if (active && panel && tab.id) panel.setAttribute("aria-labelledby", tab.id);
  });
}

function renderMemoryPanel() {
  const allowedTabs = new Set(["world", "experience", "lifecycle"]);
  const activeTab = allowedTabs.has(state.memoryTab) ? state.memoryTab : "world";
  state.memoryTab = activeTab;
  syncTabSelection(el.memoryTabs, "memoryTab", activeTab);
  el.memoryPanels.forEach((panel) => {
    panel.hidden = panel.dataset.memoryPanel !== activeTab;
  });
}

function bindRovingTabs(tabs, activate) {
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (event) => {
      let targetIndex = index;
      if (event.key === "ArrowRight") targetIndex = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") targetIndex = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") targetIndex = 0;
      else if (event.key === "End") targetIndex = tabs.length - 1;
      else return;
      event.preventDefault();
      const target = tabs[targetIndex];
      target.focus();
      activate(target);
    });
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
    setNotice(userErrorMessage(error, "状态加载失败"), "error");
  }
}

async function runAction(
  action,
  successMessage,
  { pendingMessage = "", button = null, busyLabel = "" } = {}
) {
  const restoreButtonLabel = setActionButtonBusyLabel(button, busyLabel);
  setBusy(true);
  if (pendingMessage) setNotice(pendingMessage, "info");
  try {
    const result = await action();
    const rendered = applyActionStatus(result);
    if (!rendered) await loadStatus({ quiet: true });
    const resolvedSuccessMessage = typeof successMessage === "function"
      ? successMessage(result)
      : successMessage;
    setNotice(resolvedSuccessMessage, "success");
    return result;
  } catch (error) {
    setNotice(userErrorMessage(error, "操作失败"), "error");
    return null;
  } finally {
    restoreButtonLabel();
    setBusy(false);
  }
}

function applyActionStatus(result) {
  if (!result || typeof result !== "object") return false;
  if (result.status && typeof result.status === "object") {
    state.timelineEditing = false;
    state.timelineDraft = [];
    applyStatus(result.status);
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
  const result = await runAction(
    () => apiPost(
      "page/action/reset-day",
      payload,
      { timeoutMs: GENERATION_TIMEOUT_MS, timeoutMessage: "重生成耗时较久，请稍后刷新面板查看结果" }
    ),
    useWeb ? "已联网填充今日生活背景" : "今天的时间轴和生活状态已重新生成",
    {
      pendingMessage: useWeb
        ? "正在联网重新安排今天的时间轴和生活状态，请稍等"
        : "正在重新安排今天的时间轴和生活状态，请稍等",
      button: el.resetDayButton,
      busyLabel: "重生中…",
    }
  );
  if (!result) await loadStatus({ quiet: true });
}

async function refreshState() {
  await runAction(
    () => apiPost(
      "page/action/refresh-state",
      {},
      { timeoutMs: GENERATION_TIMEOUT_MS, timeoutMessage: "今日刷新耗时较久，请稍后查看面板" }
    ),
    (result) => result?.weather_refreshed
      ? "天气与实时状态已刷新"
      : result?.status?.day?.weather
        ? "实时状态已刷新，天气保持当前数据"
        : "实时状态已刷新，天气暂不可用",
    { pendingMessage: "正在刷新天气与实时状态，请稍等" }
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
  state.timelineDraft.push({ time: "12:00", activity: "", status: "", execution_state: "planned" });
  renderTimelineEditor();
  el.timelineList.scrollTop = el.timelineList.scrollHeight;
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
  bindRovingTabs(el.memoryTabs, (tab) => {
    state.memoryTab = tab.dataset.memoryTab || "world";
    renderMemoryPanel();
  });
  bindRovingTabs(el.domainTabs, (tab) => {
    state.domainTab = tab.dataset.domainTab || "timeline";
    renderDomains(state.status || {});
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
  el.closetFilter?.addEventListener("change", () => {
    state.closetFilter = el.closetFilter.value || "all";
    state.closetPage = 1;
    renderClosetManagement();
  });
  el.closetPrevPage?.addEventListener("click", () => {
    state.closetPage = Math.max(1, Number(state.closetPage || 1) - 1);
    renderClosetManagement();
  });
  el.closetNextPage?.addEventListener("click", () => {
    state.closetPage = Number(state.closetPage || 1) + 1;
    renderClosetManagement();
  });
  el.closetImportButton?.addEventListener("click", () => el.closetImportFile?.click());
  el.closetImportFile?.addEventListener("change", () => {
    const files = Array.from(el.closetImportFile?.files || []);
    if (el.closetImportFile) el.closetImportFile.value = "";
    importClosetFiles(files);
  });
  el.closetBrowseButton?.addEventListener("click", openClosetBrowse);
  el.closetBackupButton?.addEventListener("click", backupCloset);
  el.closetRestoreButton?.addEventListener("click", () => el.closetRestoreFile?.click());
  el.closetRestoreFile?.addEventListener("change", () => {
    const file = Array.from(el.closetRestoreFile?.files || [])[0];
    if (el.closetRestoreFile) el.closetRestoreFile.value = "";
    restoreCloset(file);
  });
  el.closetManageButton?.addEventListener("click", beginClosetManage);
  el.closetCancelManageButton?.addEventListener("click", cancelClosetManage);
  el.closetBulkEnableButton?.addEventListener("click", () => setClosetStatus(closetSelectedIds(), "active"));
  el.closetBulkDisableButton?.addEventListener("click", () => setClosetStatus(closetSelectedIds(), "disabled"));
  el.closetBulkDeleteButton?.addEventListener("click", () => confirmClosetDelete(el.closetBulkDeleteButton, closetSelectedIds(), "删除选中"));
  el.closetDetailClose?.addEventListener("click", closeClosetDetail);
  el.closetDetailDialog?.addEventListener("click", (event) => {
    if (event.target === el.closetDetailDialog) closeClosetDetail();
  });
  el.closetBrowseClose?.addEventListener("click", closeClosetBrowse);
  el.closetBrowseDialog?.addEventListener("click", (event) => {
    if (event.target === el.closetBrowseDialog) closeClosetBrowse();
  });
  el.closetBrowseSubmit?.addEventListener("click", browseCloset);
  document.addEventListener("keydown", (event) => {
    if (trapDialogFocus(event, [el.emojiImportDialog, el.emojiDetailDialog, el.closetDetailDialog, el.closetBrowseDialog])) return;
    if (event.key !== "Escape") return;
    if (state.emojiDetailId) closeEmojiDetail();
    if (el.emojiImportDialog && !el.emojiImportDialog.hidden) closeEmojiImport();
    if (state.closetDetailId) closeClosetDetail();
    if (el.closetBrowseDialog && !el.closetBrowseDialog.hidden) closeClosetBrowse();
  });
  el.settingsView?.addEventListener("focusout", (event) => {
    if (event.relatedTarget && el.settingsView.contains(event.relatedTarget)) return;
    window.setTimeout(() => {
      if (!el.settingsView?.contains(document.activeElement)) flushConfigAutosave();
    }, 0);
  });
  bindRovingTabs(el.worldTabs, (tab) => {
    state.worldTab = tab.dataset.worldTab;
    renderWorld(state.status || {});
  });
  bindRovingTabs(el.experienceTabs, (tab) => {
    state.experienceTab = tab.dataset.experienceTab;
    renderExperience(state.status || {});
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
    setNotice(userErrorMessage(error, "桥接初始化失败"), "error");
  }
}

init();
