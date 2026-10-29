import { text } from "./format.js";

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

