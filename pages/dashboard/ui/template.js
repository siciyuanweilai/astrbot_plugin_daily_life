import { apiPost, GENERATION_TIMEOUT_MS } from "../api/api.js";
import { clean, labeledTemplateName, text } from "../shared/display.js";
import { clone, formatHints, formatLines, parseHints, splitLines } from "../shared/utils.js";

export function createTemplatePanel({ state, el, node, empty, setNotice, runAction, tools }) {
  const {
    formTextValue,
    selectEditorText,
    setEditorHint,
    setLockedDisabled,
    setReadOnly,
  } = tools;

  function templateItems() {
    return Array.isArray(state.status?.templates) ? state.status.templates : [];
  }

  function findTemplate(templateId) {
    return templateItems().find((item) => item.template_id === templateId) || null;
  }

  function normalizeTemplateId(value) {
    const source = text(value).trim().toLowerCase();
    const chars = [];
    let lastWasUnderscore = false;
    for (const ch of source) {
      const allowed = ch === "_" || (ch >= "a" && ch <= "z") || (ch >= "0" && ch <= "9");
      if (allowed) {
        chars.push(ch);
        lastWasUnderscore = ch === "_";
      } else if ((ch === "-" || ch === " " || ch === ".") && !lastWasUnderscore) {
        chars.push("_");
        lastWasUnderscore = true;
      }
    }
    while (chars[0] === "_") chars.shift();
    while (chars[chars.length - 1] === "_") chars.pop();
    return chars.join("").slice(0, 40);
  }

  function uniqueTemplateId(seed) {
    const base = normalizeTemplateId(seed) || "custom_week";
    const used = new Set(templateItems().map((item) => item.template_id));
    if (!used.has(base)) return base;
    for (let index = 2; index < 100; index += 1) {
      const suffix = `_${index}`;
      const candidate = `${base.slice(0, 40 - suffix.length)}${suffix}`;
      if (!used.has(candidate)) return candidate;
    }
    return `${base.slice(0, 31)}_${Date.now().toString().slice(-8)}`;
  }

  function defaultTemplateDraft() {
    return {
      template_id: "",
      name: "",
      emoji: "♡",
      description: "",
      weight: 0.1,
      enabled: true,
      cooldown_weeks: 3,
      goals: [],
      daily_hints: {},
      suggested_activities: { weekday: [], weekend: [] },
      tags: [],
      custom: true,
      editable: true,
    };
  }

  function fillTemplateEditor(template = null, { copy = false, blank = false } = {}) {
    const source = blank ? defaultTemplateDraft() : clone(template || defaultTemplateDraft());
    if (copy) {
      source.template_id = uniqueTemplateId(`${source.template_id || source.name}_copy`);
      source.name = source.name ? `${source.name}副本` : "";
      source.enabled = true;
      source.custom = true;
      source.editable = true;
      source.copying = true;
    }
    state.templateDraft = source;
    state.templateDraftId = text(source.template_id);
    el.templateIdInput.value = text(source.template_id);
    el.templateNameInput.value = text(source.name);
    el.templateEmojiInput.value = text(source.emoji || "♡");
    el.templateWeightInput.value = text(source.weight ?? 0.1);
    el.templateCooldownInput.value = text(source.cooldown_weeks ?? 3);
    el.templateEnabledInput.checked = source.enabled !== false;
    el.templateDescriptionInput.value = text(source.description);
    el.templateGoalsInput.value = formatLines(source.goals);
    el.templateHintsInput.value = formatHints(source.daily_hints || {});
    el.templateWeekdayInput.value = formatLines(source.suggested_activities?.weekday);
    el.templateWeekendInput.value = formatLines(source.suggested_activities?.weekend);
    el.templateTagsInput.value = formatLines(source.tags);
    const locked = source.custom === false && !source.copying;
    setReadOnly(
      [
        el.templateIdInput,
        el.templateNameInput,
        el.templateEmojiInput,
        el.templateWeightInput,
        el.templateCooldownInput,
        el.templateDescriptionInput,
        el.templateGoalsInput,
        el.templateHintsInput,
        el.templateWeekdayInput,
        el.templateWeekendInput,
        el.templateTagsInput,
      ],
      locked
    );
    setLockedDisabled(el.templateEnabledInput, locked);
    setLockedDisabled(el.templateSaveButton, locked);
    el.templateSaveButton.textContent = source.copying ? "保存副本" : (locked ? "先复制" : "保存");
    setEditorHint(
      el.templateEditorHint,
      source.copying
        ? "副本待保存：可以调整模板标识、名称和周计划内容，点击“保存副本”后会新增为自定义周模板。"
        : (locked ? "内置周模板只能查看，不能直接编辑；请先复制为自定义周模板。" : "")
    );
    if (source.template_id && [...el.templateEditorSelect.options].some((option) => option.value === source.template_id)) {
      el.templateEditorSelect.value = source.template_id;
    }
    if (copy) {
      selectEditorText(el.templateIdInput);
      setNotice("已复制到周模板编辑区，保存后会新增为自定义周模板。", "success");
    }
  }

  function templateFromEditor() {
    if (state.templateDraft?.custom === false && !state.templateDraft?.copying) {
      throw new Error("内置周模板不能直接保存，请先点复制。");
    }
    const templateId = normalizeTemplateId(el.templateIdInput.value);
    el.templateIdInput.value = templateId;
    if (!templateId) {
      throw new Error("请填写模板标识，只能使用小写字母、数字和下划线");
    }
    const name = el.templateNameInput.value.trim();
    if (!name) {
      throw new Error("请填写模板名称");
    }
    const weight = Number(el.templateWeightInput.value);
    const cooldown = Number(el.templateCooldownInput.value);
    return {
      template_id: templateId,
      name,
      emoji: clean(el.templateEmojiInput.value, "♡"),
      description: clean(el.templateDescriptionInput.value, name),
      weight: Number.isFinite(weight) ? Math.max(weight, 0) : 0.1,
      enabled: el.templateEnabledInput.checked,
      cooldown_weeks: Number.isFinite(cooldown) ? Math.max(Math.trunc(cooldown), 0) : 3,
      goals: splitLines(el.templateGoalsInput.value),
      daily_hints: parseHints(el.templateHintsInput.value),
      suggested_activities: {
        weekday: splitLines(el.templateWeekdayInput.value),
        weekend: splitLines(el.templateWeekendInput.value),
      },
      tags: splitLines(el.templateTagsInput.value),
    };
  }

  function copyTemplateDraft() {
    const selected = findTemplate(el.templateEditorSelect.value) || findTemplate(state.templateDraftId);
    if (!selected) {
      setNotice("请先选择一个周模板再复制。", "error");
      return;
    }
    fillTemplateEditor(selected, { copy: true });
  }

  function syncTemplateEditor(templates) {
    const current = state.templateDraftId || el.templateEditorSelect.value;
    el.templateEditorSelect.replaceChildren(
      ...templates.map((template) => {
        const option = document.createElement("option");
        option.value = text(template.template_id);
        option.textContent = `${clean(template.emoji, "")} ${labeledTemplateName(template)}`.trim();
        return option;
      })
    );
    if (!templates.length) {
      fillTemplateEditor(null, { blank: true });
      return;
    }
    const next = templates.some((template) => template.template_id === current)
      ? current
      : templates[0].template_id;
    el.templateEditorSelect.value = next;
    const selected = templates.find((template) => template.template_id === next) || findTemplate(next);
    fillTemplateEditor(selected);
  }

  function renderTemplates(status) {
    const templates = Array.isArray(status.templates) ? status.templates : [];
    syncTemplateEditor(templates);
    if (!templates.length) {
      el.templateList.replaceChildren(empty("暂无模板"));
      return;
    }

    el.templateList.replaceChildren(
      ...templates.map((template) => {
        const row = node("div", "template-row");
        const title = node("div", "template-title");
        title.append(
          node("span", "", `${clean(template.emoji, "")} ${labeledTemplateName(template)}`.trim()),
          node("span", "muted", template.custom ? "自定义" : "内置")
        );
        const goals = Array.isArray(template.goals) && template.goals.length
          ? `目标：${template.goals.join("、")}`
          : "";
        row.append(title, node("div", "template-body", clean(`${template.description || ""} ${goals}`.trim())));
        const actions = node("div", "template-actions");
        const copyButton = node("button", "", "复制");
        copyButton.type = "button";
        copyButton.addEventListener("click", () => fillTemplateEditor(template, { copy: true }));
        if (template.editable) {
          const edit = node("button", "", "编辑");
          edit.type = "button";
          edit.addEventListener("click", () => fillTemplateEditor(template));
          actions.append(edit);
        }
        actions.append(copyButton);
        const toggle = node("button", "", template.enabled ? "禁用" : "启用");
        toggle.type = "button";
        toggle.addEventListener("click", () => setTemplateEnabled(template.template_id, !template.enabled));
        if (template.editable) {
          const weight = document.createElement("input");
          weight.type = "number";
          weight.className = "template-weight-input";
          weight.min = "0";
          weight.step = "0.01";
          weight.value = text(template.weight || 0);
          weight.setAttribute("aria-label", `${template.template_id} 自定义模板权重`);
          const saveWeight = node("button", "", "保存权重");
          saveWeight.type = "button";
          saveWeight.addEventListener("click", () => updateTemplateWeight(template.template_id, weight.value));
          const remove = node("button", "danger", "删除");
          remove.type = "button";
          remove.addEventListener("click", () => deleteTemplate(template.template_id));
          actions.append(weight, saveWeight, remove);
        }
        actions.append(toggle);
        row.append(actions);
        return row;
      })
    );
  }

  async function createTemplate(description, useWeb = false) {
    const result = await runAction(
      () => apiPost(
        "page/template/create",
        { description, use_web: useWeb },
        { timeoutMs: GENERATION_TIMEOUT_MS, timeoutMessage: "模板生成耗时较久，请稍后刷新面板查看结果" }
      ),
      useWeb ? "联网模板已创建" : "模板已创建"
    );
    const templateId = result?.template?.template_id;
    if (templateId) {
      state.templateDraftId = templateId;
      fillTemplateEditor(findTemplate(templateId) || result.template);
    }
    el.templateText.value = "";
  }

  async function saveTemplate() {
    let template;
    try {
      template = templateFromEditor();
    } catch (error) {
      setNotice(error.message || "模板填写不完整", "error");
      return;
    }
    const result = await runAction(
      () => apiPost("page/template/save", { template }),
      "模板已保存"
    );
    if (!result?.template) return;
    const templateId = result?.template?.template_id || template.template_id;
    state.templateDraftId = templateId;
    renderTemplates(state.status || {});
    fillTemplateEditor(findTemplate(templateId) || result.template);
  }

  async function updateTemplateWeight(templateId, weight) {
    await runAction(
      () => apiPost("page/template/weight", { template_id: templateId, weight }),
      "自定义模板权重已更新"
    );
  }

  async function setTemplateEnabled(templateId, enabled) {
    await runAction(
      () => apiPost("page/template/enabled", { template_id: templateId, enabled }),
      "模板状态已更新"
    );
  }

  async function deleteTemplate(templateId) {
    if (state.templateDraftId === templateId) {
      state.templateDraftId = "";
    }
    const result = await runAction(
      () => apiPost("page/template/delete", { template_id: templateId }),
      "模板已删除"
    );
    if (result) {
      const next = templateItems()[0] || null;
      if (!next || !findTemplate(templateId)) {
        fillTemplateEditor(next, { blank: !next });
      }
    }
  }

  function bindEvents() {
    el.templateEditorSelect.addEventListener("change", () => {
      state.templateDraftId = el.templateEditorSelect.value;
      fillTemplateEditor(findTemplate(state.templateDraftId));
    });
    el.templateNewButton.addEventListener("click", () => fillTemplateEditor(null, { blank: true }));
    el.templateCopyButton.addEventListener("click", copyTemplateDraft);
    el.templateSaveButton.addEventListener("click", saveTemplate);
    el.templateForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const description = formTextValue(el.templateText, "请先填写模板描述");
      if (!description) return;
      createTemplate(description);
    });
    el.templateWebButton.addEventListener("click", () => {
      const description = formTextValue(el.templateText, "请先填写模板描述");
      if (description) createTemplate(description, true);
    });
  }

  return {
    bindEvents,
    renderTemplates,
  };
}
