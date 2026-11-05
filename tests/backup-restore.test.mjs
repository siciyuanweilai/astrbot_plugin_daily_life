import assert from "node:assert/strict";
import { File } from "node:buffer";
import { test } from "node:test";
import { prepareBackup, runBackupRestore } from "../pages/dashboard/api/backup-restore.mjs";
import { zipSync, unzipSync } from "../pages/dashboard/vendor/fflate-0.8.3.mjs";

globalThis.File = File;
const encode = (value) => new TextEncoder().encode(JSON.stringify(value));
const image = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
function archive(kind, items, assets = {}, level = 6) {
  return new File([zipSync({
    "manifest.json": encode({ format: `daily_life_${kind}_backup`, version: 1, items }),
    ...assets,
  }, { level })], "backup.zip");
}
async function contents(file) {
  const files = unzipSync(new Uint8Array(await file.arrayBuffer()));
  return { files, items: JSON.parse(new TextDecoder().decode(files["manifest.json"])).items };
}
const records = ["a", "b", "c"].map((id) => ({
  backup_asset: `assets/${id}.png`, file_hash: id, label: `表情 ${id}`,
  description: "原说明", emotions: ["开心"], sendable: false, used_count: 7,
}));
const assets = Object.fromEntries(records.map((item) => [item.backup_asset, image]));

test("stored and compressed legacy backups produce one image per request with all metadata", async () => {
  for (const level of [0, 6]) {
    const job = await prepareBackup(archive("emoji", records, assets, level), "emoji");
    let inFlight = 0;
    const seen = [];
    const result = await runBackupRestore(job, { upload: async (endpoint, file) => {
      assert.equal(++inFlight, 1);
      assert.equal(endpoint, "page/emoji/restore");
      const part = await contents(file);
      assert.equal(Object.keys(part.files).length, 2);
      assert.deepEqual(part.items, [records[seen.length]]);
      assert.deepEqual(part.files[part.items[0].backup_asset], image);
      seen.push(part.items[0]);
      await new Promise((resolve) => setTimeout(resolve, 2));
      inFlight--;
      return { restored: 1, skipped_records: 0 };
    } });
    assert.equal(result.restored, 3);
    assert.equal(result.failed.length, 0);
  }
});

test("a network failure does not discard successes; retry only uploads the failed image", async () => {
  const job = await prepareBackup(archive("emoji", records, assets), "emoji");
  const seen = [];
  const first = await runBackupRestore(job, { upload: async (_, file) => {
    const id = (await contents(file)).items[0].file_hash;
    seen.push(id);
    if (id === "b") throw new Error("timeout of 60000ms exceeded");
    return { restored: 1 };
  } });
  assert.deepEqual(seen, ["a", "b", "c"]);
  assert.equal(first.restored, 2);
  assert.equal(first.failed.length, 1);
  const second = await runBackupRestore(job, { upload: async (_, file) => {
    assert.equal((await contents(file)).items[0].file_hash, "b");
    return { restored: 1 };
  } });
  assert.equal(second.restored, 3);
  assert.equal(second.failed.length, 0);
});

test("pause waits for the current upload; resume skips completed groups", async () => {
  const job = await prepareBackup(archive("emoji", records, assets), "emoji");
  let stop = false;
  const first = await runBackupRestore(job, { shouldStop: () => stop, upload: async () => {
    stop = true;
    return { restored: 1 };
  } });
  assert.equal(first.restored, 1);
  assert.equal(first.pending, 2);
  let calls = 0;
  const next = await runBackupRestore(job, { upload: async () => { calls++; return { restored: 1 }; } });
  assert.equal(calls, 2);
  assert.equal(next.restored, 3);
});

test("closet keeps same-image pieces and outfit together, with piece care facts first", async () => {
  const items = ["outfit", "bottom", "top"].map((kind) => ({
    kind, source_image_hash: "same-image", backup_asset: "assets/shared.png",
    attributes: { wardrobe: { ownership: "owned", condition: kind === "top" ? "washing" : "clean" } },
  }));
  const job = await prepareBackup(archive("closet", items, { "assets/shared.png": image }), "closet");
  assert.equal(job.groups.length, 1);
  const part = await contents(job.makeFile(job.groups[0]));
  assert.deepEqual(part.items.map((item) => item.kind), ["bottom", "top", "outfit"]);
  assert.equal(part.items[1].attributes.wardrobe.condition, "washing");
  assert.equal(Object.keys(part.files).length, 2);
  const first = await runBackupRestore(job, { upload: async () => ({ restored: 2, skipped_records: 1 }) });
  assert.equal(first.failed.length, 1);
  assert.equal(first.restored, 2);
  const retry = await runBackupRestore(job, { upload: async () => ({ restored: 3, skipped_records: 0 }) });
  assert.equal(retry.restored, 3);
});

test("missing pictures fail individually; metadata-only records remain restorable", async () => {
  const job = await prepareBackup(archive("emoji", [
    records[0], { file_hash: "remote", file_path: "https://example.test/a.gif", backup_asset: "" }, records[2],
  ], { "assets/c.png": image }), "emoji");
  const result = await runBackupRestore(job, { upload: async (_, file) => {
    const part = await contents(file);
    if (part.items[0].file_hash === "remote") assert.equal(Object.keys(part.files).length, 1);
    return { restored: 1 };
  } });
  assert.equal(result.restored, 2);
  assert.equal(result.failed.length, 1);
});

test("wrong format, traversal, truncated and oversized archives fail before uploading", async () => {
  await assert.rejects(prepareBackup(archive("closet", records, assets), "emoji"), /格式/);
  await assert.rejects(prepareBackup(archive("emoji", records, { "../secret.png": image }), "emoji"), /路径/);
  await assert.rejects(prepareBackup(new File(["bad"], "backup.zip"), "emoji"), /有效 ZIP/);
  await assert.rejects(prepareBackup(archive("emoji", [], {}), "emoji"), /没有/);
  await assert.rejects(prepareBackup({ name: "large.zip", size: 201 * 1024 * 1024 }, "emoji"), /200 MB/);
});

test("unacknowledged or skipped restores are failures, never reported as successful", async () => {
  for (const response of [{}, { restored: 0, skipped_records: 1 }, { restored: 10 }]) {
    const job = await prepareBackup(archive("emoji", [records[0]], assets), "emoji");
    const result = await runBackupRestore(job, { upload: async () => response });
    assert.equal(result.failed.length, 1);
    assert.equal(result.restored, 0);
  }
});
