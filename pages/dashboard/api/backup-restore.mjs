import { unzipSync, zipSync } from "../vendor/fflate-0.8.3.mjs";

const TYPES = {
  emoji: { label: "表情", maxMB: 200, maxFiles: 1000 },
  closet: { label: "衣橱", maxMB: 500, maxFiles: 1001 },
};
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });

function publicError(message) {
  return Object.assign(new Error(message), { isPublicMessage: true });
}

function safeAsset(name) {
  return typeof name === "string" && /^assets\/.+\.(png|jpe?g|gif|webp|bmp)$/i.test(name)
    && !name.includes("\\") && !name.split("/").some((part) => !part || part === "." || part === "..");
}

function unpack(bytes, name) {
  try {
    const result = unzipSync(bytes, { filter: (entry) => entry.name === name });
    if (!Object.hasOwn(result, name)) throw new Error("missing member");
    return result[name];
  } catch {
    throw publicError(name === "manifest.json" ? "备份清单缺失或损坏" : "备份中的图片缺失或损坏");
  }
}

// Only the manifest and the current image are decompressed. Related closet
// records share one upload, with pieces before outfits to preserve care facts.
export async function prepareBackup(file, kind) {
  const spec = TYPES[kind];
  if (!spec) throw publicError("不支持的备份类型");
  if (!String(file?.name || "").toLowerCase().endsWith(".zip")) {
    throw publicError(`请选择 ZIP ${spec.label}备份文件`);
  }
  const maxBytes = spec.maxMB * 1024 * 1024;
  if (file.size > maxBytes) throw publicError(`${spec.label}备份不能超过 ${spec.maxMB} MB`);
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length < 22) throw publicError("备份文件不是有效 ZIP");
  const entries = new Map();
  let total = 0;
  try {
    unzipSync(bytes, { filter(entry) {
      if (entries.has(entry.name)) throw publicError("备份中存在重复文件名");
      const directory = entry.name.endsWith("/");
      if (entry.name !== "manifest.json" && !safeAsset(entry.name)
        && !(directory && /^assets\/(?:[^.\\/][^\\/]*\/)*$/.test(entry.name))) {
        throw publicError("备份包含不支持的文件或不安全路径");
      }
      if (![0, 8].includes(entry.compression)) throw publicError("备份使用了不支持的压缩格式");
      if (!Number.isSafeInteger(entry.originalSize) || entry.originalSize < 0) throw new Error("invalid size");
      total += entry.originalSize;
      entries.set(entry.name, entry);
      if (entries.size > spec.maxFiles) throw publicError("备份文件数量过多");
      if (total > maxBytes) throw publicError(`备份解压后不能超过 ${spec.maxMB} MB`);
      if (entry.name === "manifest.json" && entry.originalSize > 16 * 1024 * 1024) {
        throw publicError("备份清单过大");
      }
      return false;
    } });
  } catch (error) {
    throw error.isPublicMessage ? error : publicError("备份文件不是有效 ZIP");
  }
  let manifest;
  try {
    manifest = JSON.parse(decoder.decode(unpack(bytes, "manifest.json")));
  } catch {
    throw publicError("备份清单缺失或损坏");
  }
  if (manifest?.format !== `daily_life_${kind}_backup` || Number(manifest.version) !== 1
    || !Array.isArray(manifest.items)) throw publicError(`${spec.label}备份格式或版本不受支持`);
  if (!manifest.items.length) throw publicError("备份中没有可恢复的记录");
  if (manifest.items.length > 1000) throw publicError("备份记录数量过多");

  const grouped = new Map();
  manifest.items.forEach((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw publicError("备份中存在无效记录");
    const asset = String(item.backup_asset || "");
    const key = asset || `record:${index}`;
    if (!grouped.has(key)) grouped.set(key, {
      asset, items: [], label: String(item.label || item.title || `素材 ${index + 1}`).slice(0, 100),
      state: "pending", restored: 0, error: "",
    });
    grouped.get(key).items.push(item);
  });
  const groups = [...grouped.values()];
  if (kind === "closet") {
    groups.forEach((group) => group.items.sort((a, b) => Number(a.kind === "outfit") - Number(b.kind === "outfit")));
    groups.sort((a, b) => Number(a.items.every((item) => item.kind === "outfit")) - Number(b.items.every((item) => item.kind === "outfit")));
  }
  return {
    kind, label: spec.label, groups, running: false,
    makeFile(group) {
      const files = { "manifest.json": encoder.encode(JSON.stringify({ ...manifest, items: group.items })) };
      if (group.asset) {
        if (!safeAsset(group.asset) || !entries.has(group.asset)) throw publicError("备份中的图片缺失或路径不安全");
        files[group.asset] = unpack(bytes, group.asset);
      }
      return new File([zipSync(files, { level: 0 })], `daily_life_${kind}_part.zip`, { type: "application/zip" });
    },
  };
}

export function backupProgress(job) {
  return {
    total: job.groups.length,
    completed: job.groups.filter((group) => group.state !== "pending").length,
    restored: job.groups.reduce((count, group) => count + group.restored, 0),
    failed: job.groups.filter((group) => group.state === "failed"),
    pending: job.groups.filter((group) => group.state === "pending").length,
  };
}

export async function runBackupRestore(job, { upload, onProgress = () => {}, shouldStop = () => false }) {
  if (job.running) throw publicError("恢复正在进行中");
  job.running = true;
  let payload = null;
  try {
    for (const group of job.groups) {
      if (shouldStop()) break;
      if (group.state === "done") continue;
      onProgress({ ...backupProgress(job), current: group.label });
      // Yield so the progress message paints before ZIP work starts.
      await new Promise((resolve) => setTimeout(resolve, 0));
      if (shouldStop()) break;
      try {
        const file = job.makeFile(group);
        const result = await upload(`page/${job.kind}/restore`, file, {
          // The AstrBot host aborts uploads after 60 s. Wait for that result
          // before allowing retries; never race an in-flight upload.
          timeoutMs: 65000, timeoutMessage: "这张素材上传超时，请重试",
        });
        const count = Number(result?.restored);
        if (!Number.isInteger(count) || count < 0 || count > group.items.length) {
          throw publicError("未收到有效的恢复结果，请重试");
        }
        payload = result;
        group.restored = Math.max(group.restored, count);
        if (count !== group.items.length || Number(result.skipped_records || 0) > 0) {
          throw publicError(`已恢复 ${count}/${group.items.length} 条，其余未通过素材校验，请检查图片大小或备份完整性`);
        }
        group.state = "done";
        group.error = "";
      } catch (error) {
        group.state = "failed";
        group.error = error.isPublicMessage ? error.message : "上传中断或恢复失败，请重试";
      }
      onProgress({ ...backupProgress(job), current: "" });
    }
    return { ...backupProgress(job), payload };
  } finally {
    job.running = false;
  }
}
