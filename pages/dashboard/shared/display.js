import {
  ACTION_LABELS,
  ATMOSPHERE_LABELS,
  BOT_WATCH_STATE_LABELS,
  BOUNDARY_POLICY_LABELS,
  CATALOG_CATEGORY_LABELS,
  CURRENT_SLEEP_LABELS,
  EPISODE_KIND_LABELS,
  EPISODE_STATUS_LABELS,
  EVENT_STATUS_LABELS,
  EVIDENCE_TYPE_LABELS,
  FEEDBACK_RESULT_LABELS,
  FRESHNESS_LABELS,
  HEALTH_CHECK_LABELS,
  INTERRUPT_LEVEL_LABELS,
  LIFE_MODE_LABELS,
  OUTFIT_DECISION_LABELS,
  PAGE_STATUS_REASON_LABELS,
  PLACE_TYPE_LABELS,
  PLAN_OUTFIT_DECISION_LABELS,
  PREFERENCE_CATEGORY_LABELS,
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
  TEMPLATE_LABELS,
  TIME_PERIOD_LABELS,
  UNDERSTANDING_LABELS,
  VISIBILITY_LABELS,
  WEEKDAY_LABELS
} from "./labels.js";

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

function templateLabel(templateId) {
  return enumLabel(templateId, TEMPLATE_LABELS);
}

function catalogCategoryLabel(categoryName) {
  return enumLabel(categoryName, CATALOG_CATEGORY_LABELS);
}

function evidenceText(value, source = "") {
  const raw = clean(value, "");
  if (!raw) return enumLabel(source, SOURCE_LABELS);
  const translated = enumLabel(raw, SOURCE_LABELS);
  return translated && translated !== raw ? translated : raw;
}

function translateStructuredText(value) {
  let body = text(value);
  if (!body) return "";
  const fieldSpecs = [
    ["life_mode", SCHEDULE_TONE_LABELS, "日程基调"],
    ["sleep_mode", SLEEP_MODE_LABELS, "睡眠倾向"],
    ["schedule_intent", SCHEDULE_INTENT_LABELS, "活动倾向"],
    ["plan_outfit_decision", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"],
    ["outfit_decision", OUTFIT_DECISION_LABELS, "穿搭"],
    ["生活模式", SCHEDULE_TONE_LABELS, "日程基调"],
    ["睡眠模式", SLEEP_MODE_LABELS, "睡眠倾向"],
    ["日程倾向", SCHEDULE_INTENT_LABELS, "活动倾向"],
    ["换装", OUTFIT_DECISION_LABELS, "穿搭"],
    ["穿搭", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"],
    ["日程穿搭", PLAN_OUTFIT_DECISION_LABELS, "日程穿搭"],
  ];
  for (const [field, labels, label] of fieldSpecs) {
    body = translateStructuredField(body, field, labels, label);
  }
  return body;
}

function stateLogText(value) {
  let body = translateStructuredText(value);
  if (!body) return "";
  const fieldSpecs = [
    ["watch_state", BOT_WATCH_STATE_LABELS, "观看状态"],
    ["interrupt_level", INTERRUPT_LEVEL_LABELS, "打断等级"],
    ["sleep_depth", SLEEP_DEPTH_LABELS, "睡眠层级"],
    ["time_period", TIME_PERIOD_LABELS, "时段"],
    ["source", SOURCE_LABELS, "来源"],
    ["reason", PAGE_STATUS_REASON_LABELS, "原因"],
    ["status_reason", PAGE_STATUS_REASON_LABELS, "更新原因"],
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
    body = translateStructuredField(body, field, labels, label, "：:=");
  }
  return enumText(body, stateLogDictionaries()) || body;
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
  const dictionaries = [
    SCHEDULE_TONE_LABELS,
    LIFE_MODE_LABELS,
    SCHEDULE_INTENT_LABELS,
    PLAN_OUTFIT_DECISION_LABELS,
    OUTFIT_DECISION_LABELS,
    SLEEP_MODE_LABELS,
  ];
  const body = text(value);
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
    result += enumText(token, dictionaries) || token;
  }
  return result;
}

function stateLogDictionaries() {
  return [
    TIME_PERIOD_LABELS,
    SCHEDULE_TONE_LABELS,
    LIFE_MODE_LABELS,
    SLEEP_MODE_LABELS,
    CURRENT_SLEEP_LABELS,
    SLEEP_DEPTH_LABELS,
    SCHEDULE_INTENT_LABELS,
    PLAN_OUTFIT_DECISION_LABELS,
    OUTFIT_DECISION_LABELS,
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

function translateStructuredField(body, field, labels, label, separators = "：:") {
  let cursor = 0;
  let result = "";
  while (cursor < body.length) {
    const found = body.indexOf(field, cursor);
    if (found < 0) {
      result += body.slice(cursor);
      break;
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
    result += raw ? `${label}：${enumLabel(raw, labels)}` : body.slice(found, pos);
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
    return !(targetType === "life_episode" && evidenceType === "daily_generation" && episodeIds.has(targetId));
  });
}

function visibleLifeEpisodes(episodes) {
  return episodes.filter((item) => (
    item
    && typeof item === "object"
    && text(item.kind).trim().toLowerCase() !== "daily_plan"
  ));
}

function evidenceTargetTitle(item) {
  item = item && typeof item === "object" ? item : {};
  const typeLabel = enumLabel(item.target_type, TARGET_TYPE_LABELS);
  const label = clean(item.target_label, "");
  if (label) return `${typeLabel} ${label}`;
  return `${typeLabel} #${clean(item.target_id)}`;
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
    TEMPLATE_LABELS,
    TIME_PERIOD_LABELS,
    SCHEDULE_TONE_LABELS,
    LIFE_MODE_LABELS,
    SLEEP_MODE_LABELS,
    CURRENT_SLEEP_LABELS,
    SLEEP_DEPTH_LABELS,
    SCHEDULE_INTENT_LABELS,
    PLAN_OUTFIT_DECISION_LABELS,
    OUTFIT_DECISION_LABELS,
    TARGET_TYPE_LABELS,
    EVIDENCE_TYPE_LABELS,
    CATALOG_CATEGORY_LABELS,
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
    STORAGE_TABLE_LABELS,
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

function labeledTemplateName(template = {}) {
  const label = templateLabel(template.template_id);
  return clean(template.name || label, label || "--");
}

export {
  catalogCategoryLabel,
  clean,
  currentOutfitDisplayText,
  enumLabel,
  evidenceTargetTitle,
  evidenceText,
  humanizeToken,
  labelValuePair,
  labeledTemplateName,
  lifeEpisodeLines,
  moodColorText,
  outfitDecisionText,
  recordLines,
  scheduleTypeText,
  stateLogText,
  structuredTextLines,
  templateLabel,
  text,
  visibleExperienceEvidence,
  visibleLifeEpisodes
};
