export const bridge = window.AstrBotPluginPage;
export const REQUEST_TIMEOUT_MS = 30000;
export const GENERATION_TIMEOUT_MS = 180000;

function publicError(message) {
  const error = new Error(String(message || "请求失败"));
  error.isPublicMessage = true;
  return error;
}

export function userErrorMessage(error, fallback = "操作失败") {
  if (!error?.isPublicMessage) return fallback;
  const message = String(error.message || "").trim();
  return message || fallback;
}

export function withTimeout(promise, message = "请求超时", timeoutMs = REQUEST_TIMEOUT_MS) {
  let timeoutId = 0;
  const timeout = new Promise((_, reject) => {
    timeoutId = window.setTimeout(() => reject(publicError(message)), timeoutMs);
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

export async function apiUpload(endpoint, file, options = {}) {
  return normalizeResult(await withTimeout(
    bridge.upload(endpoint, file),
    options.timeoutMessage || "文件上传超时",
    options.timeoutMs || REQUEST_TIMEOUT_MS
  ));
}

export async function apiDownload(endpoint, params = {}, filename = "", options = {}) {
  return withTimeout(
    bridge.download(endpoint, params, filename),
    options.timeoutMessage || "文件下载超时",
    options.timeoutMs || REQUEST_TIMEOUT_MS
  );
}

function normalizeResult(result) {
  if (result && typeof result === "object" && Object.prototype.hasOwnProperty.call(result, "ok")) {
    if (!result.ok) {
      const message = result.error?.public === true
        ? result.error?.message
        : "请求失败，请查看后台日志";
      throw publicError(message);
    }
    return result.data || {};
  }
  return result || {};
}
