export const bridge = window.AstrBotPluginPage;
export const REQUEST_TIMEOUT_MS = 30000;
export const GENERATION_TIMEOUT_MS = 180000;

export function withTimeout(promise, message = "请求超时", timeoutMs = REQUEST_TIMEOUT_MS) {
  let timeoutId = 0;
  const timeout = new Promise((_, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => window.clearTimeout(timeoutId));
}

export async function apiGet(endpoint, params = {}) {
  return normalizeResult(await withTimeout(bridge.apiGet(endpoint, params)));
}

export async function apiPost(endpoint, body = {}, options = {}) {
  return normalizeResult(await withTimeout(
    bridge.apiPost(endpoint, body),
    options.timeoutMessage || "请求超时",
    options.timeoutMs || REQUEST_TIMEOUT_MS
  ));
}

function normalizeResult(result) {
  if (result && typeof result === "object" && Object.prototype.hasOwnProperty.call(result, "ok")) {
    if (!result.ok) {
      throw new Error(result.error?.message || result.message || "请求失败");
    }
    return result.data || {};
  }
  return result || {};
}
