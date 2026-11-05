import { prepareBackup, runBackupRestore } from "../api/backup-restore.mjs";

export function createBackupRestore({ kind, root, upload, setBusy, isBusy, notice, beforeRun, afterRun, applyPayload }) {
  const label = kind === "emoji" ? "表情" : "衣橱";
  const status = root.querySelector("[data-restore-status]");
  const retry = root.querySelector("[data-restore-retry]");
  const details = root.querySelector("details");
  const failures = root.querySelector("ul");
  let job = null;
  let running = false;

  function render(value, active) {
    status.textContent = active
      ? `请保持页面打开。正在恢复${label} ${value.completed}/${value.total} 份素材${value.current ? `：${value.current}` : ""}；已恢复 ${value.restored} 条记录。`
      : `恢复结束：已恢复 ${value.restored} 条记录，失败 ${value.failed.length} 份，待处理 ${value.pending} 份。`;
    retry.hidden = active || (!value.failed.length && !value.pending);
    retry.disabled = active;
    retry.textContent = value.pending ? "重试未完成项" : "重试失败项";
    details.hidden = value.failed.length === 0;
    failures.replaceChildren(...value.failed.map((group) => {
      const li = document.createElement("li");
      li.textContent = `${group.label}：${group.error}`;
      return li;
    }));
  }

  async function run(file) {
    if (running || isBusy()) return;
    running = true;
    setBusy(true);
    beforeRun?.();
    root.hidden = false;
    retry.hidden = true;
    status.textContent = "请保持页面打开。正在读取备份…";
    try {
      if (file) {
        job = null;
        details.hidden = true;
        failures.replaceChildren();
        job = await prepareBackup(file, kind);
      }
      if (!job) return;
      const result = await runBackupRestore(job, {
        upload, onProgress: (value) => render(value, true),
      });
      if (result.payload) applyPayload(result.payload);
      render(result, false);
      notice(result.failed.length || result.pending
        ? `${label}已恢复 ${result.restored} 条，剩余项目可在进度区重试`
        : `${label}已全部恢复，共 ${result.restored} 条记录`, result.failed.length ? "error" : "success");
      if (!result.failed.length && !result.pending) job = null;
    } catch (error) {
      const message = error.isPublicMessage ? error.message : `${label}备份读取失败，请检查文件后重试`;
      status.textContent = message;
      notice(message, "error");
    } finally {
      running = false;
      setBusy(false);
      afterRun?.();
    }
  }

  retry.addEventListener("click", () => run());
  return { start: (file) => file ? run(file) : undefined };
}
