import { apiPost, GENERATION_TIMEOUT_MS } from "../api/api.js";
import { clean, text } from "../shared/display.js";
import { clone, formatLines, splitLines } from "../shared/utils.js";

export function createHairPanel({ state, el, node, empty, setNotice, runAction, tools, refreshCatalog }) {
  const {
    formTextValue,
    selectEditorText,
    setEditorHint,
    setLockedDisabled,
    setReadOnly,
  } = tools;

  function hairStyles() {
    return Array.isArray(state.status?.catalog?.hair_styles) ? state.status.catalog.hair_styles : [];
  }

  function findHairStyle(styleId) {
    return hairStyles().find((style) => style.style_id === styleId) || null;
  }

  function defaultHairDraft() {
    return {
      style_id: "",
      name: "",
      hairstyles: [],
      enabled: true,
      custom: true,
      editable: true,
    };
  }

  function fillHairEditor(style = null, { copy = false, blank = false } = {}) {
    const source = blank ? defaultHairDraft() : clone(style || defaultHairDraft());
    if (copy) {
      source.style_id = "";
      source.enabled = true;
      source.custom = true;
      source.editable = true;
      source.copying = true;
    }
    state.hairDraft = source;
    el.hairNameInput.value = text(source.name);
    el.hairOptionsInput.value = formatLines(source.hairstyles);
    el.hairEnabledInput.checked = source.enabled !== false;
    const locked = source.custom === false && !source.copying;
    setReadOnly([el.hairNameInput, el.hairOptionsInput], locked);
    setLockedDisabled(el.hairEnabledInput, locked);
    setLockedDisabled(el.hairSaveButton, locked);
    el.hairSaveButton.textContent = source.copying ? "保存副本" : (locked ? "先复制" : "保存");
    setEditorHint(
      el.hairEditorHint,
      source.copying
        ? "副本待保存：可以调整名称和发型列表，点击“保存副本”后会新增为自定义发型组。"
        : (locked ? "内置发型组只能查看，不能直接编辑；请先复制为自定义发型组。" : "")
    );
    if (source.style_id && [...el.hairStyleSelect.options].some((option) => option.value === source.style_id)) {
      el.hairStyleSelect.value = source.style_id;
    }
    if (copy) {
      selectEditorText(el.hairNameInput);
      setNotice("已复制到发型组编辑区，保存后会新增为自定义发型组。", "success");
    }
  }

  function hairFromEditor() {
    if (state.hairDraft?.custom === false && !state.hairDraft?.copying) {
      throw new Error("内置发型组不能直接保存，请先点复制。");
    }
    const name = el.hairNameInput.value.trim();
    const hairstyles = splitLines(el.hairOptionsInput.value);
    if (!name) throw new Error("请填写发型风格名称");
    if (!hairstyles.length) throw new Error("请至少填写一个发型");
    return {
      style_id: state.hairDraft?.custom && !state.hairDraft?.copying ? text(state.hairDraft.style_id) : "",
      name,
      hairstyles,
      enabled: el.hairEnabledInput.checked,
    };
  }

  function copyHairDraft() {
    if (!state.hairDraft || !text(state.hairDraft.name).trim()) {
      setNotice("请先选择一个发型组再复制。", "error");
      return;
    }
    fillHairEditor(state.hairDraft, { copy: true });
  }

  function renderHairStyles(styles) {
    el.hairStyleSelect.replaceChildren(
      ...styles.map((style) => new Option(`${clean(style.name, style.style_id)} (${style.hairstyles?.length || 0})`, style.style_id))
    );
    if (!state.hairDraft && styles.length) fillHairEditor(styles[0]);
    if (!styles.length) {
      el.hairList.replaceChildren(empty("暂无发型组"));
      return;
    }
    el.hairList.replaceChildren(
      ...styles.map((style) => {
        const row = node("div", "catalog-row");
        const title = node("div", "template-title");
        title.append(node("span", "", clean(style.name)), node("span", "muted", style.custom ? "自定义" : "内置"));
        const preview = Array.isArray(style.hairstyles) ? style.hairstyles.slice(0, 3).join("、") : "";
        const actions = node("div", "catalog-actions");
        const copyButton = node("button", "", "复制");
        copyButton.type = "button";
        copyButton.addEventListener("click", () => fillHairEditor(style, { copy: true }));
        if (style.editable) {
          const edit = node("button", "", "编辑");
          edit.type = "button";
          edit.addEventListener("click", () => fillHairEditor(style));
          actions.append(edit);
        }
        actions.append(copyButton);
        const toggle = node("button", "", style.enabled ? "禁用" : "启用");
        toggle.type = "button";
        toggle.addEventListener("click", () => setHairEnabled(style.style_id, !style.enabled));
        if (style.editable) {
          const remove = node("button", "danger", "删除");
          remove.type = "button";
          remove.addEventListener("click", () => deleteHairStyle(style.style_id));
          actions.append(remove);
        }
        actions.append(toggle);
        row.append(title, node("div", "template-body", style.enabled === false ? "已禁用" : clean(preview)), actions);
        return row;
      })
    );
  }

  async function saveHairStyle() {
    let style;
    try {
      style = hairFromEditor();
    } catch (error) {
      setNotice(error.message || "发型组填写不完整", "error");
      return;
    }
    const result = await runAction(
      () => apiPost("page/hair/save", { style }),
      "发型组已保存"
    );
    if (result?.style) {
      refreshCatalog(state.status || {});
      fillHairEditor(findHairStyle(result.style.style_id) || result.style);
    }
  }

  async function createHairStyle(description, useWeb = false) {
    const result = await runAction(
      () => apiPost(
        "page/hair/create",
        { description, use_web: useWeb },
        { timeoutMs: GENERATION_TIMEOUT_MS, timeoutMessage: "发型组生成耗时较久，请稍后刷新面板查看结果" }
      ),
      useWeb ? "联网发型组已创建" : "发型组已创建"
    );
    if (result?.style) {
      refreshCatalog(state.status || {});
      fillHairEditor(findHairStyle(result.style.style_id) || result.style);
      el.hairText.value = "";
    }
  }

  async function setHairEnabled(styleId, enabled) {
    await runAction(
      () => apiPost("page/hair/enabled", { style_id: styleId, enabled }),
      "发型组状态已更新"
    );
  }

  async function deleteHairStyle(styleId) {
    if (state.hairDraft?.style_id === styleId) {
      state.hairDraft = null;
    }
    const result = await runAction(
      () => apiPost("page/hair/delete", { style_id: styleId }),
      "发型组已删除"
    );
    if (result) {
      fillHairEditor(null, { blank: true });
    }
  }

  function bindEvents() {
    el.hairStyleSelect.addEventListener("change", () => {
      fillHairEditor(hairStyles().find((style) => style.style_id === el.hairStyleSelect.value));
    });
    el.hairNewButton.addEventListener("click", () => fillHairEditor(null, { blank: true }));
    el.hairCopyButton.addEventListener("click", copyHairDraft);
    el.hairSaveButton.addEventListener("click", saveHairStyle);
    el.hairForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const description = formTextValue(el.hairText, "请先填写发型组描述");
      if (!description) return;
      createHairStyle(description);
    });
    el.hairWebButton.addEventListener("click", () => {
      const description = formTextValue(el.hairText, "请先填写发型组描述");
      if (description) createHairStyle(description, true);
    });
  }

  return {
    bindEvents,
    renderHairStyles,
  };
}
