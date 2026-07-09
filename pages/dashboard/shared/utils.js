import { DAY_ALIASES, WEEKDAYS } from "./labels.js";
import { clean, text } from "./display.js";

export function clone(value) {
  if (Array.isArray(value)) return value.map((item) => clone(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clone(item)]));
  }
  return value;
}

export function clampPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.min(100, number));
}

export function clampRange(value, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(min, Math.min(max, number));
}

export function parseTimeMinutes(value) {
  const body = text(value).trim();
  const sep = body.indexOf(":");
  if (sep <= 0 || body.indexOf(":", sep + 1) >= 0) return null;
  const hourText = body.slice(0, sep);
  const minuteText = body.slice(sep + 1);
  if (hourText.length > 2 || minuteText.length !== 2 || !isAsciiDigits(hourText) || !isAsciiDigits(minuteText)) {
    return null;
  }
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!Number.isFinite(hour) || !Number.isFinite(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return null;
  }
  return hour * 60 + minute;
}

export function formatDate(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

export function formatClock(date) {
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

export function parseStatusNow(value) {
  const body = text(value).trim();
  if (body.length < 16 || body[4] !== "-" || body[7] !== "-" || (body[10] !== " " && body[10] !== "T")) {
    return null;
  }
  const year = body.slice(0, 4);
  const month = body.slice(5, 7);
  const day = body.slice(8, 10);
  const time = body.slice(11);
  const firstSep = time.indexOf(":");
  if (firstSep <= 0 || firstSep > 2) return null;
  const hour = time.slice(0, firstSep);
  const minute = time.slice(firstSep + 1, firstSep + 3);
  let second = "0";
  if (time[firstSep + 3] === ":") {
    second = time.slice(firstSep + 4, firstSep + 6);
    if (second.length !== 2 || !isAsciiDigits(second)) return null;
  }
  if (
    !isAsciiDigits(year)
    || !isAsciiDigits(month)
    || !isAsciiDigits(day)
    || !isAsciiDigits(hour)
    || minute.length !== 2
    || !isAsciiDigits(minute)
  ) {
    return null;
  }
  const date = new Date(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second)
  );
  return Number.isNaN(date.getTime()) ? null : date;
}

export function firstClockMinutes(value) {
  const body = text(value);
  for (let index = 0; index < body.length; index += 1) {
    if (!isAsciiDigit(body[index]) || (index > 0 && isAsciiDigit(body[index - 1]))) continue;
    for (const hourLength of [2, 1]) {
      const hourText = body.slice(index, index + hourLength);
      const sepIndex = index + hourLength;
      if (!isAsciiDigits(hourText) || body[sepIndex] !== ":") continue;
      const minuteText = body.slice(sepIndex + 1, sepIndex + 3);
      if (minuteText.length !== 2 || !isAsciiDigits(minuteText)) continue;
      if (isAsciiDigit(body[sepIndex + 3])) continue;
      const hour = Number(hourText);
      const minute = Number(minuteText);
      if (hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59) {
        return hour * 60 + minute;
      }
    }
  }
  return null;
}

export function formatLines(items) {
  return Array.isArray(items) ? items.filter(Boolean).join("\n") : "";
}

export function formatHints(hints = {}) {
  const lines = [];
  const used = new Set();
  for (const [key, label] of WEEKDAYS) {
    const value = clean(hints[key], "");
    if (value) {
      lines.push(`${label}: ${value}`);
      used.add(key);
    }
  }
  for (const [key, value] of Object.entries(hints)) {
    if (!used.has(key) && clean(value, "")) {
      lines.push(`${key}: ${value}`);
    }
  }
  return lines.join("\n");
}

export function parseHints(value) {
  const hints = {};
  let nextDay = 0;
  for (const line of splitLines(value)) {
    const sep = hintSeparatorIndex(line);
    if (sep >= 0) {
      const key = hintKey(line.slice(0, sep));
      const body = line.slice(sep + 1).trim();
      if (key && body) hints[key] = body;
      continue;
    }
    if (nextDay < WEEKDAYS.length) {
      hints[WEEKDAYS[nextDay][0]] = line;
      nextDay += 1;
    }
  }
  return hints;
}

export function splitLines(value) {
  return text(value)
    .replaceAll("\r\n", "\n")
    .replaceAll("\r", "\n")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function parseList(value) {
  return splitLines(value);
}

function isAsciiDigit(char) {
  return char >= "0" && char <= "9";
}

function isAsciiDigits(value) {
  const body = text(value);
  if (!body) return false;
  for (const char of body) {
    if (!isAsciiDigit(char)) return false;
  }
  return true;
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function hintSeparatorIndex(line) {
  const colon = line.indexOf(":");
  const cnColon = line.indexOf("：");
  if (colon < 0) return cnColon;
  if (cnColon < 0) return colon;
  return Math.min(colon, cnColon);
}

function hintKey(value) {
  const raw = text(value).trim();
  return DAY_ALIASES.get(raw) || DAY_ALIASES.get(raw.toLowerCase()) || "";
}
