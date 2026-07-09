import {
  ACTION_LABELS,
  ATMOSPHERE_LABELS,
  BOT_WATCH_STATE_LABELS,
  BOUNDARY_POLICY_LABELS,
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
  LIFE_MODE_LABELS,
  OUTFIT_DECISION_LABELS,
  OUTFIT_SCENE_CATEGORY_LABELS,
  OUTFIT_STYLE_POOL_LABELS,
  PAGE_STATUS_REASON_LABELS,
  PLACE_TYPE_LABELS,
  PLAN_OUTFIT_DECISION_LABELS,
  PREFERENCE_CATEGORY_LABELS,
  RHYTHM_LIFECYCLE_LABELS,
  SCENE_TYPE_LABELS,
  SCHEDULE_INTENT_LABELS,
  SCHEDULE_TONE_LABELS,
  SCOPE_LABELS,
  SLEEP_DEPTH_LABELS,
  SLEEP_MODE_LABELS,
  SOURCE_LABELS,
  STORAGE_CATEGORY_LABELS,
  STORAGE_TABLE_LABELS,
  TARGET_TYPE_LABELS,
  TIME_PERIOD_LABELS,
  UNDERSTANDING_LABELS,
  VISIBILITY_LABELS,
  WEEK_PROGRESS_STATUS_LABELS,
  WEEKDAY_LABELS
} from "./labels.js";

const FIELD_TOKEN_LABELS = {
  life_mode: "日程基调",
  lifemode: "日程基调",
  sleep_mode: "睡眠倾向",
  sleepmode: "睡眠倾向",
  sleep_depth: "睡眠层级",
  sleepdepth: "睡眠层级",
  schedule_intent: "活动倾向",
  scheduleintent: "活动倾向",
  plan_outfit_decision: "日程穿搭",
  planoutfitdecision: "日程穿搭",
  outfit_decision: "穿搭",
  outfitdecision: "穿搭",
  style_pool: "风格池",
  stylepool: "风格池",
  outfit_style_pool: "穿搭风格池",
  outfitstylepool: "穿搭风格池",
  scene_category: "场景",
  scenecategory: "场景",
  watch_state: "观看状态",
  watchstate: "观看状态",
  interrupt_level: "打断等级",
  interruptlevel: "打断等级",
  physiological_rhythm: "生理节律",
  physiologicalrhythm: "生理节律",
  energy_curve: "精力曲线",
  energycurve: "精力曲线",
  body_condition: "身体状态",
  bodycondition: "身体状态",
  recovery_actions: "恢复动作",
  recoveryactions: "恢复动作",
  social_battery: "社交电量",
  socialbattery: "社交电量",
  attention_state: "注意力状态",
  attentionstate: "注意力状态",
  optional_cycle: "可选周期",
  optionalcycle: "可选周期",
  lifecycle_kind: "生命周期",
  lifecyclekind: "生命周期",
  time_period: "时段",
  timeperiod: "时段",
  status_reason: "更新原因",
  statusreason: "更新原因",
  target_type: "目标类型",
  targettype: "目标类型",
  target_id: "目标ID",
  targetid: "目标ID",
  evidence: "证据",
  evidence_type: "证据类型",
  evidencetype: "证据类型",
  source_table: "来源表",
  sourcetable: "来源表",
  source_id: "来源ID",
  sourceid: "来源ID",
  source_scope: "来源范围",
  sourcescope: "来源范围",
  target_scope: "目标范围",
  targetscope: "目标范围",
};

const EMBEDDED_ENUM_DICTIONARIES = [
  FIELD_TOKEN_LABELS,
  OUTFIT_DECISION_LABELS,
  PLAN_OUTFIT_DECISION_LABELS,
  LIFE_DECISION_KIND_LABELS,
  LIFE_MODE_LABELS,
  SCHEDULE_TONE_LABELS,
  SCHEDULE_INTENT_LABELS,
  SLEEP_MODE_LABELS,
  CURRENT_SLEEP_LABELS,
  SLEEP_DEPTH_LABELS,
  OUTFIT_STYLE_POOL_LABELS,
  OUTFIT_SCENE_CATEGORY_LABELS,
  RHYTHM_LIFECYCLE_LABELS,
  EVENT_STATUS_LABELS,
  EPISODE_STATUS_LABELS,
  FEEDBACK_RESULT_LABELS,
  FRESHNESS_LABELS,
  UNDERSTANDING_LABELS,
  BOT_WATCH_STATE_LABELS,
  INTERRUPT_LEVEL_LABELS,
  ATMOSPHERE_LABELS,
  VISIBILITY_LABELS,
  ACTION_LABELS,
  SCENE_TYPE_LABELS,
  SOURCE_LABELS,
  PAGE_STATUS_REASON_LABELS,
  EVIDENCE_TYPE_LABELS,
  TARGET_TYPE_LABELS,
  PLACE_TYPE_LABELS,
  SCOPE_LABELS,
];

function text(value, fallback = "") {
  if (value === null || value === undefined) return fallback;
  return String(value);
}

function clean(value, fallback = "--") {
  const body = text(value).trim();
  return translateStructuredText(body) || fallback;
}

function node(tag, className = "", content = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined && content !== null) element.textContent = String(content);
  return element;
}

function enumLabel(value, labels) {
  const key = text(value).trim();
  if (!key) return "";
  return labels[key] || labels[key.toLowerCase()] || humanizeToken(key);
}

const STATE_METRIC_LABELS = {
  energy: "体力",
  mood_score: "心情分",
  moodscore: "心情分",
  busyness: "忙碌度",
  social: "社交意愿",
  stress: "压力感",
  focus: "专注度",
  sleepiness: "困倦度",
  outgoing: "外出意愿",
  emotional_stability: "情绪稳定",
  emotionalstability: "情绪稳定",
  interaction_capacity: "互动意愿",
  interactioncapacity: "互动意愿",
  social_battery: "社交电量",
  socialbattery: "社交电量",
  body_condition: "身体状态",
  bodycondition: "身体状态",
};

function evidenceText(value, source = "") {
  const raw = clean(value, "");
  if (!raw) return enumLabel(source, SOURCE_LABELS);
  const translated = enumLabel(raw, SOURCE_LABELS);
  return translated && translated !== raw ? translated : raw;
}

function translateStructuredText(value, options = {}) {
  let body = text(value);
  if (!body) return "";
  const embeddedEnums = options.embeddedEnums !== false;
  const fieldSpecs = [
    ["life_mode", SCHEDULE_TONE_LABELS, "日程基调"],
    ["lifemode", SCHEDULE_TONE_LABELS, "日程基调"],
    ["sleep_mode", SLEEP_MODE_LABELS, "睡眠倾向"],
    ["sleepmode", SLEEP_MODE_LABELS, "睡眠倾向"],
    ["schedule_intent", SCHEDULE_INTENT_LABELS, "活动倾向"],
    ["scheduleintent", SCHEDULE_INTENT_LABELS, "活动倾向"],
    ["plan_outfit_decision", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"],
    ["planoutfitdecision", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"],
    ["outfit_decision", OUTFIT_DECISION_LABELS, "穿搭"],
    ["outfitdecision", OUTFIT_DECISION_LABELS, "穿搭"],
    ["style_pool", OUTFIT_STYLE_POOL_LABELS, "风格池"],
    ["stylepool", OUTFIT_STYLE_POOL_LABELS, "风格池"],
    ["outfit_style_pool", OUTFIT_STYLE_POOL_LABELS, "穿搭风格池"],
    ["outfitstylepool", OUTFIT_STYLE_POOL_LABELS, "穿搭风格池"],
    ["scene_category", OUTFIT_SCENE_CATEGORY_LABELS, "场景"],
    ["scenecategory", OUTFIT_SCENE_CATEGORY_LABELS, "场景"],
    ["target_type", TARGET_TYPE_LABELS, "目标类型"],
    ["targettype", TARGET_TYPE_LABELS, "目标类型"],
    ["evidence", SOURCE_LABELS, "证据"],
    ["evidence_type", EVIDENCE_TYPE_LABELS, "证据类型"],
    ["evidencetype", EVIDENCE_TYPE_LABELS, "证据类型"],
    ["source_scope", SCOPE_LABELS, "来源范围"],
    ["sourcescope", SCOPE_LABELS, "来源范围"],
    ["target_scope", SCOPE_LABELS, "目标范围"],
    ["targetscope", SCOPE_LABELS, "目标范围"],
    ["source", SOURCE_LABELS, "来源"],
    ["scope", SCOPE_LABELS, "范围"],
    ["生活模式", SCHEDULE_TONE_LABELS, "日程基调"],
    ["睡眠模式", SLEEP_MODE_LABELS, "睡眠倾向"],
    ["日程倾向", SCHEDULE_INTENT_LABELS, "活动倾向"],
    ["换装", OUTFIT_DECISION_LABELS, "穿搭"],
    ["穿搭", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"],
    ["日程穿搭", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"],
    ["风格池", OUTFIT_STYLE_POOL_LABELS, "风格池"],
    ["穿搭风格池", OUTFIT_STYLE_POOL_LABELS, "穿搭风格池"],
    ["场景分类", OUTFIT_SCENE_CATEGORY_LABELS, "场景分类"],
  ];
  for (const [field, labels, label] of fieldSpecs) {
    body = translateStructuredField(body, field, labels, label);
  }
  body = translateStateMetricTokens(body);
  return embeddedEnums ? translateEmbeddedEnumTokens(body) : body;
}

function stateLogText(value) {
  let body = translateStructuredText(value, { embeddedEnums: false });
  if (!body) return "";
  const fieldSpecs = [
    ["watch_state", BOT_WATCH_STATE_LABELS, "观看状态"],
    ["watchstate", BOT_WATCH_STATE_LABELS, "观看状态"],
    ["interrupt_level", INTERRUPT_LEVEL_LABELS, "打断等级"],
    ["interruptlevel", INTERRUPT_LEVEL_LABELS, "打断等级"],
    ["sleep_depth", SLEEP_DEPTH_LABELS, "睡眠层级"],
    ["sleepdepth", SLEEP_DEPTH_LABELS, "睡眠层级"],
    ["time_period", TIME_PERIOD_LABELS, "时段"],
    ["timeperiod", TIME_PERIOD_LABELS, "时段"],
    ["source", SOURCE_LABELS, "来源"],
    ["reason", PAGE_STATUS_REASON_LABELS, "原因"],
    ["status_reason", PAGE_STATUS_REASON_LABELS, "更新原因"],
    ["statusreason", PAGE_STATUS_REASON_LABELS, "更新原因"],
    ["留意", VISIBILITY_LABELS, "留意"],
    ["裁定", ACTION_LABELS, "裁定"],
    ["动作", ACTION_LABELS, "动作"],
    ["观看状态", BOT_WATCH_STATE_LABELS, "观看状态"],
    ["打断等级", INTERRUPT_LEVEL_LABELS, "打断等级"],
    ["可打断等级", INTERRUPT_LEVEL_LABELS, "可打断等级"],
    ["客观打断信号", INTERRUPT_LEVEL_LABELS, "客观打断信号"],
    ["睡眠层级", SLEEP_DEPTH_LABELS, "睡眠层级"],
    ["当前睡眠", CURRENT_SLEEP_LABELS, "当前睡眠"],
    ["时间标签", TIME_PERIOD_LABELS, "时间标签"],
    ["时段标签", TIME_PERIOD_LABELS, "时段标签"],
    ["触发来源", SOURCE_LABELS, "触发来源"],
    ["更新原因", PAGE_STATUS_REASON_LABELS, "更新原因"],
  ];
  for (const [field, labels, label] of fieldSpecs) {
    body = translateStructuredField(body, field, labels, label, "：:=为");
  }
  return enumText(body, stateLogDictionaries()) || translateEmbeddedEnumTokens(body);
}

function enumText(value, dictionaries) {
  const raw = text(value).trim();
  if (!raw) return "";
  const direct = raw.toLowerCase();
  for (const dict of dictionaries) {
    if (dict[raw]) return dict[raw];
    if (dict[direct]) return dict[direct];
  }
  return "";
}

function translateEmbeddedEnumTokens(value) {
  const body = text(value);
  const direct = enumText(body, EMBEDDED_ENUM_DICTIONARIES);
  if (direct) return direct;
  if (!/[：:=为|｜\/;；,，·()\[\]{}<>]/.test(body)) return body;
  let result = "";
  let cursor = 0;
  while (cursor < body.length) {
    if (!isTokenChar(body[cursor])) {
      result += body[cursor];
      cursor += 1;
      continue;
    }
    const start = cursor;
    while (cursor < body.length && isTokenChar(body[cursor])) cursor += 1;
    const token = body.slice(start, cursor);
    result += enumText(token, EMBEDDED_ENUM_DICTIONARIES) || token;
  }
  return result;
}

function stateLogDictionaries() {
  return [
    STATE_METRIC_LABELS,
    FIELD_TOKEN_LABELS,
    TIME_PERIOD_LABELS,
    SCHEDULE_TONE_LABELS,
    LIFE_MODE_LABELS,
    LIFE_DECISION_KIND_LABELS,
    WEEK_PROGRESS_STATUS_LABELS,
    SLEEP_MODE_LABELS,
    CURRENT_SLEEP_LABELS,
    SLEEP_DEPTH_LABELS,
    SCHEDULE_INTENT_LABELS,
    PLAN_OUTFIT_DECISION_LABELS,
    OUTFIT_DECISION_LABELS,
    OUTFIT_STYLE_POOL_LABELS,
    OUTFIT_SCENE_CATEGORY_LABELS,
    SOURCE_LABELS,
    PAGE_STATUS_REASON_LABELS,
    VISIBILITY_LABELS,
    ACTION_LABELS,
    SCENE_TYPE_LABELS,
    BOT_WATCH_STATE_LABELS,
    INTERRUPT_LEVEL_LABELS,
    ATMOSPHERE_LABELS,
  ];
}

function isTokenChar(char) {
  return "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-".includes(char);
}

function escapeRegExp(value) {
  return text(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function translateStateMetricTokens(value) {
  const body = text(value);
  if (!body) return "";
  const aliases = Object.keys(STATE_METRIC_LABELS).sort((left, right) => right.length - left.length);
  const pattern = new RegExp(
    `(^|[^A-Za-z0-9_-])(${aliases.map(escapeRegExp).join("|")})(\\s*[:=：]?\\s*)(-?\\d+(?:\\.\\d+)?(?:\\s*/\\s*100)?)`,
    "gi"
  );
  return body.replace(pattern, (match, prefix, field, separator, amount) => {
    const label = STATE_METRIC_LABELS[field] || STATE_METRIC_LABELS[String(field).toLowerCase()] || field;
    const hasSeparator = /[:=：]/.test(separator || "");
    return `${prefix}${label}${hasSeparator ? "：" : ""}${amount.replace(/\s+/g, "")}`;
  });
}

function translateStructuredField(body, field, labels, label, separators = "：:=为") {
  let cursor = 0;
  let result = "";
  while (cursor < body.length) {
    const found = body.indexOf(field, cursor);
    if (found < 0) {
      result += body.slice(cursor);
      break;
    }
    const before = found > 0 ? body[found - 1] : "";
    const after = body[found + field.length] || "";
    if ((before && isTokenChar(before)) || (after && isTokenChar(after))) {
      result += body.slice(cursor, found + field.length);
      cursor = found + field.length;
      continue;
    }
    result += body.slice(cursor, found);
    let pos = found + field.length;
    while (body[pos] === " " || body[pos] === "\t") pos += 1;
    if (pos >= body.length || !separators.includes(body[pos])) {
      result += body.slice(found, pos);
      cursor = pos;
      continue;
    }
    pos += 1;
    while (body[pos] === " " || body[pos] === "\t") pos += 1;
    const start = pos;
    while (pos < body.length && isTokenChar(body[pos])) pos += 1;
    const raw = body.slice(start, pos);
    result += raw ? `${label}：${enumLabel(raw, labels)}` : `${label}：`;
    cursor = pos;
  }
  return result;
}

function structuredTextLines(value) {
  const body = clean(value, "");
  if (!body) return [];
  const parts = splitStructuredText(body);
  const lines = [];
  for (const part of parts) {
    const evidence = splitFieldText(part, "今日生成依据");
    if (evidence) {
      lines.push(evidence[0]);
      lines.push(evidence[1]);
    } else {
      lines.push(part);
    }
  }
  return lines;
}

function splitStructuredText(body) {
  const normalized = text(body).replaceAll("；", ";").replaceAll(" · ", ";");
  return normalized.split(";").map((part) => part.trim()).filter(Boolean);
}

function splitFieldText(part, field) {
  if (!part.startsWith(field)) return null;
  let pos = field.length;
  while (part[pos] === " " || part[pos] === "\t") pos += 1;
  if (part[pos] !== "：" && part[pos] !== ":") return null;
  pos += 1;
  while (part[pos] === " " || part[pos] === "\t") pos += 1;
  const value = part.slice(pos).trim();
  return value ? [field, value] : null;
}

function labelValuePair(value, maxLabelLength = 10) {
  const body = text(value).trim();
  const limit = Math.min(body.length, maxLabelLength + 1);
  for (let index = 1; index < limit; index += 1) {
    if (body[index] !== "：" && body[index] !== ":") continue;
    const label = body.slice(0, index).trim();
    const fieldValue = body.slice(index + 1).trim();
    return label && fieldValue ? [label, fieldValue] : null;
  }
  return null;
}

function leadingFieldLabel(value, maxLabelLength = 10) {
  const body = text(value).trim();
  const limit = Math.min(body.length, maxLabelLength + 1);
  for (let index = 1; index < limit; index += 1) {
    if (body[index] === "：" || body[index] === ":") {
      return body.slice(0, index).trim();
    }
  }
  return "";
}

function recordLine(content, value = null) {
  const line = node("div", "record-line");
  if (value !== null && value !== undefined) {
    line.append(node("span", "record-line-label", content), node("span", "record-line-value", value));
    return line;
  }
  const body = clean(content, "");
  const pair = labelValuePair(body);
  if (pair) {
    line.append(node("span", "record-line-label", pair[0]), node("span", "record-line-value", pair[1]));
    return line;
  }
  line.classList.add("full");
  line.textContent = body;
  return line;
}

function recordLines(items) {
  const body = node("div", "record-body record-lines");
  for (const item of items.flatMap((value) => Array.isArray(value) ? [value] : structuredTextLines(value))) {
    if (Array.isArray(item)) {
      body.append(recordLine(item[0], item[1]));
    } else {
      body.append(recordLine(item));
    }
  }
  return body;
}

function visibleExperienceEvidence(evidence, episodes) {
  const episodeIds = new Set(episodes
    .filter((item) => item && typeof item === "object")
    .map((item) => text(item.id).trim())
    .filter(Boolean));
  return evidence.filter((item) => {
    if (!item || typeof item !== "object") return false;
    const targetType = text(item.target_type).trim().toLowerCase();
    const evidenceType = text(item.evidence_type).trim().toLowerCase();
    const targetId = text(item.target_id).trim();
    if (targetType === "life_episode" && evidenceType === "daily_generation" && episodeIds.has(targetId)) {
      return false;
    }
    return true;
  });
}

function visibleLifeEpisodes(episodes) {
  return episodes.filter((item) => (
    item
    && typeof item === "object"
    && text(item.kind).trim().toLowerCase() !== "daily_plan"
  ));
}

function evidenceTargetTitle(item, displayIndex = null) {
  item = item && typeof item === "object" ? item : {};
  const typeLabel = enumLabel(item.target_type, TARGET_TYPE_LABELS);
  const label = clean(item.target_label, "");
  if (label) return `${typeLabel} ${label}`;
  const targetType = text(item.target_type).trim().toLowerCase();
  if (targetType === "life_decision") {
    return typeLabel;
  }
  const targetId = clean(item.target_id, "");
  return targetId ? `${typeLabel} ${targetId}` : typeLabel;
}

function lifeEpisodeLines(item) {
  item = item && typeof item === "object" ? item : {};
  const hiddenLabels = new Set(["时间轴", "地点"]);
  const lines = structuredTextLines(item.correction || item.summary || item.impact);
  return lines.filter((line) => {
    const body = Array.isArray(line) ? text(line[0]).trim() : clean(line, "");
    const label = leadingFieldLabel(body);
    return !label || !hiddenLabels.has(label);
  });
}

function moodColorText(value) {
  const body = clean(value, "");
  return body.includes("·") ? body : "";
}

function scheduleTypeText(value) {
  return clean(value, "");
}

function currentOutfitDisplayText(day = {}, meta = {}) {
  const style = clean(meta.style, "");
  const outfit = clean(day.outfit, "");
  return { style, outfit };
}

function outfitDecisionText(meta = {}) {
  const decision = enumLabel(meta.outfit_decision, OUTFIT_DECISION_LABELS);
  const reason = translateEmbeddedEnumTokens(clean(meta.outfit_reason, ""));
  return { decision, reason };
}

function humanizeToken(value) {
  const raw = text(value).trim();
  if (!raw) return "";
  const dictionaries = [
    WEEKDAY_LABELS,
    TIME_PERIOD_LABELS,
    SCHEDULE_TONE_LABELS,
    LIFE_MODE_LABELS,
    LIFE_DECISION_KIND_LABELS,
    WEEK_PROGRESS_STATUS_LABELS,
    SLEEP_MODE_LABELS,
    CURRENT_SLEEP_LABELS,
    SLEEP_DEPTH_LABELS,
    SCHEDULE_INTENT_LABELS,
    PLAN_OUTFIT_DECISION_LABELS,
    OUTFIT_DECISION_LABELS,
    OUTFIT_STYLE_POOL_LABELS,
    OUTFIT_SCENE_CATEGORY_LABELS,
    TARGET_TYPE_LABELS,
    EVIDENCE_TYPE_LABELS,
    STORAGE_CATEGORY_LABELS,
    SOURCE_LABELS,
    PAGE_STATUS_REASON_LABELS,
    VISIBILITY_LABELS,
    ACTION_LABELS,
    SCENE_TYPE_LABELS,
    PLACE_TYPE_LABELS,
    SCOPE_LABELS,
    PREFERENCE_CATEGORY_LABELS,
    EVENT_STATUS_LABELS,
    EPISODE_KIND_LABELS,
    EPISODE_STATUS_LABELS,
    FEEDBACK_RESULT_LABELS,
    BOUNDARY_POLICY_LABELS,
    FRESHNESS_LABELS,
    UNDERSTANDING_LABELS,
    BOT_WATCH_STATE_LABELS,
    INTERRUPT_LEVEL_LABELS,
    ATMOSPHERE_LABELS,
    HEALTH_CHECK_LABELS,
    RHYTHM_LIFECYCLE_LABELS,
    STORAGE_TABLE_LABELS,
    STATE_METRIC_LABELS,
    FIELD_TOKEN_LABELS,
  ];
  const direct = raw.toLowerCase();
  for (const dict of dictionaries) {
    if (dict[raw]) return dict[raw];
    if (dict[direct]) return dict[direct];
  }
  const parts = splitTokenParts(raw);
  if (parts.length > 1) {
    const translated = parts.map((part) => {
      const lower = part.toLowerCase();
      for (const dict of dictionaries) {
        if (dict[part]) return dict[part];
        if (dict[lower]) return dict[lower];
      }
      return part;
    });
    return translated.join("");
  }
  return raw;
}

function splitTokenParts(value) {
  const parts = [];
  let current = "";
  for (const char of text(value)) {
    if (char === "_" || char === "-" || char === "/" || char === " " || char === "\t" || char === "\n" || char === "\r") {
      if (current) {
        parts.push(current);
        current = "";
      }
    } else {
      current += char;
    }
  }
  if (current) parts.push(current);
  return parts;
}

export {
  clean,
  currentOutfitDisplayText,
  enumLabel,
  evidenceTargetTitle,
  evidenceText,
  humanizeToken,
  labelValuePair,
  lifeEpisodeLines,
  moodColorText,
  outfitDecisionText,
  recordLines,
  scheduleTypeText,
  stateLogText,
  structuredTextLines,
  text,
  visibleExperienceEvidence,
  visibleLifeEpisodes
};
