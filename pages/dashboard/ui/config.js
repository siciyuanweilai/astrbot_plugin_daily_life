import { apiGet, apiPost } from "../api/api.js";
import { clean, humanizeToken, text } from "../shared/display.js";
import { clone, parseList } from "../shared/utils.js";

const AUTOSAVE_DELAY_MS = 700;
const MAX_CHARACTER_REFERENCE_COUNT = 12;
const CHARACTER_REFERENCE_MAX_BYTES = 12 * 1024 * 1024;
const referencePreviewCache = new Map();
const MODEL_SECTION_KEY = "__model_provider_settings";
const MODEL_SECTION_SPEC = {
  description: "大语言模型",
  hint: "统一调整生成、状态、穿搭、复盘、邀约、约定、记忆、素材和视觉等功能使用的模型。每项留空都直接使用当前默认模型。",
};
const CONFIG_SECTION_ORDER = [
  "rhythm_config",
  "weekly_theme_config",
  "weather_awareness",
  "state_config",
  "relationship_aliases",
  "commitment_config",
  "memory_config",
  "memos_config",
  "response_gate_config",
  "proactive_config",
  "lifecycle_config",
  "image_generation_config",
  "video_generation_config",
  "voice_generation_config",
  "web_inspiration_config",
  "storage_config",
  "story_engine_config",
];
const CONFIG_SECTION_ORDER_INDEX = new Map(CONFIG_SECTION_ORDER.map((key, index) => [key, index]));

export function createConfigPanel({ state, el, node, empty, setBusy, setNotice, loadStatus }) {
  function configLabel(key, spec = {}) {
    return clean(spec.title || spec.description || key, key);
  }

  function configHint(spec = {}) {
    return clean(spec.hint || "", "");
  }

  function configOptionLabel(option, spec = {}) {
    const labels = spec.option_labels || {};
    return clean(labels[option] || humanizeToken(option), text(option));
  }

  function configDefault(spec = {}) {
    if (Object.prototype.hasOwnProperty.call(spec, "default")) return clone(spec.default);
    if (spec.type === "object") return buildConfig(spec.items || {}, {});
    if (spec.type === "list" || spec.type === "template_list") return [];
    if (spec.type === "bool") return false;
    if (spec.type === "int" || spec.type === "float") return 0;
    return "";
  }

  function buildConfig(schema = {}, current = {}) {
    const source = current && typeof current === "object" && !Array.isArray(current) ? current : {};
    const result = {};
    for (const [key, spec] of Object.entries(schema)) {
      if (spec.type === "object") {
        result[key] = buildConfig(spec.items || {}, source[key]);
      } else if (Object.prototype.hasOwnProperty.call(source, key)) {
        result[key] = clone(source[key]);
      } else {
        result[key] = configDefault(spec);
      }
    }
    return result;
  }

  function configPathValue(path) {
    return path.reduce((target, key) => {
      if (!target || typeof target !== "object") return undefined;
      return target[key];
    }, state.config);
  }

  function setConfigPathValue(path, value) {
    let target = state.config;
    for (let index = 0; index < path.length - 1; index += 1) {
      const key = path[index];
      if (!target[key] || typeof target[key] !== "object" || Array.isArray(target[key])) {
        target[key] = {};
      }
      target = target[key];
    }
    target[path[path.length - 1]] = value;
    state.configVersion += 1;
    markConfigDirty(true);
    scheduleConfigAutosave();
  }

  function markConfigDirty(value) {
    state.configDirty = Boolean(value);
    if (state.configDirty) {
      setConfigSaveStatus("pending");
    } else if (!state.configSaving) {
      setConfigSaveStatus(state.configLoaded ? "saved" : "idle");
    }
  }

  function setConfigSaveStatus(status) {
    state.configSaveStatus = status;
    const badge = el.configDirtyBadge;
    if (!badge) return;
    badge.classList.remove("pending", "saving", "saved", "error");
    badge.classList.add(status);
    if (status === "idle") {
      badge.hidden = true;
      badge.textContent = "";
    } else if (status === "pending") {
      badge.hidden = false;
      badge.textContent = "等待自动保存";
    } else if (status === "saving") {
      badge.hidden = false;
      badge.textContent = "保存中";
    } else if (status === "saved") {
      badge.hidden = false;
      badge.textContent = "已自动保存";
    } else {
      badge.hidden = false;
      badge.textContent = "保存失败";
    }
  }

  function clearConfigAutosaveTimer() {
    if (!state.configSaveTimer) return;
    clearTimeout(state.configSaveTimer);
    state.configSaveTimer = 0;
  }

  function scheduleConfigAutosave() {
    if (!state.configLoaded) return;
    clearConfigAutosaveTimer();
    state.configSaveTimer = window.setTimeout(() => {
      state.configSaveTimer = 0;
      saveConfig({ auto: true });
    }, AUTOSAVE_DELAY_MS);
  }

  function configSectionFields(spec = {}) {
    if (spec.type === "object") return Object.entries(spec.items || {});
    return spec.type ? [[state.configSectionKey, spec]] : [];
  }

  function isProviderConfigField(spec = {}) {
    return spec.type === "string" && spec._special === "select_provider";
  }

  function collectProviderConfigFields() {
    const fields = [];
    for (const [sectionKey, sectionSpec] of Object.entries(state.configSchema)) {
      if (sectionSpec.type === "object") {
        for (const [fieldKey, fieldSpec] of Object.entries(sectionSpec.items || {})) {
          if (isProviderConfigField(fieldSpec)) {
            fields.push({
              key: `${sectionKey}.${fieldKey}`,
              spec: fieldSpec,
              path: [sectionKey, fieldKey],
              sectionKey,
              fieldKey,
            });
          }
        }
      } else if (isProviderConfigField(sectionSpec)) {
        fields.push({
          key: sectionKey,
          spec: sectionSpec,
          path: [sectionKey],
          sectionKey,
          fieldKey: sectionKey,
        });
      }
    }
    return fields;
  }

  function sortConfigSectionEntries(entries = []) {
    return entries
      .map(([key, spec], index) => ({ key, spec, index }))
      .sort((left, right) => {
        const leftRank = CONFIG_SECTION_ORDER_INDEX.get(left.key) ?? Number.MAX_SAFE_INTEGER;
        const rightRank = CONFIG_SECTION_ORDER_INDEX.get(right.key) ?? Number.MAX_SAFE_INTEGER;
        if (leftRank !== rightRank) return leftRank - rightRank;
        return left.index - right.index;
      })
      .map(({ key, spec }) => [key, spec]);
  }

  function visibleConfigSections() {
    const schemaEntries = Object.entries(state.configSchema);
    return sortConfigSectionEntries(
      schemaEntries.filter(([key, spec]) => configSectionVisibleFields(key, spec).length > 0)
    );
  }

  function configSectionVisibleFields(sectionKey, spec = {}) {
    return configSectionFields(spec).filter(([, itemSpec]) => !isProviderConfigField(itemSpec));
  }

  function configTypeLabel(spec = {}) {
    if (spec._special === "select_provider") return "模型";
    if (spec._special === "character_reference_gallery") return "图片";
    if (spec.type === "bool") return "开关";
    if (spec.type === "int" || spec.type === "float") return "数值";
    if (spec.type === "template_list") return "条目";
    if (spec.type === "list") return "列表";
    if (Array.isArray(spec.options)) return "选项";
    return "文本";
  }

  function configIcon(index) {
    const icons = ["✦", "♡", "◇", "♪", "☆", "◎", "＋", "○"];
    return icons[index % icons.length];
  }

  function configFieldClass(spec = {}) {
    const classes = ["config-field"];
    if (spec.type === "list") {
      classes.push("list-field");
    } else if (spec.type === "template_list") {
      classes.push("template-list-field");
    } else if (spec.type === "text") {
      classes.push("text-field");
    }
    return classes.join(" ");
  }

  function numberValue(value, spec) {
    const raw = Number(value);
    const fallback = Number(configDefault(spec));
    const number = Number.isFinite(raw) ? raw : fallback;
    if (spec.type === "int") return Math.trunc(number);
    return number;
  }

  function applySliderBounds(input, spec = {}) {
    const slider = spec.slider || {};
    if (slider.min !== undefined) input.min = text(slider.min);
    if (slider.max !== undefined) input.max = text(slider.max);
    if (slider.step !== undefined) input.step = text(slider.step);
  }

  function renderConfigNav() {
    const providerFields = collectProviderConfigFields();
    const visibleSchemaEntries = visibleConfigSections();
    const entries = providerFields.length
      ? [[MODEL_SECTION_KEY, MODEL_SECTION_SPEC], ...visibleSchemaEntries]
      : visibleSchemaEntries;
    if (!entries.length) {
      el.configNav.replaceChildren(empty("暂无设置"));
      return;
    }
    el.configNav.replaceChildren(
      ...entries.map(([key, spec], index) => {
        const button = node("button", `config-tab${key === state.configSectionKey ? " active" : ""}`);
        button.type = "button";
        button.append(
          node("span", "config-tab-icon", configIcon(index)),
          node("span", "config-tab-label", configLabel(key, spec))
        );
        button.addEventListener("click", () => {
          state.configSectionKey = key;
          renderConfig();
        });
        return button;
      })
    );
  }

  function renderConfigField(key, spec, path) {
    const value = configPathValue(path);
    const field = node("article", configFieldClass(spec));
    const head = node("div", "field-head");
    const label = node("label", "field-title");
    label.htmlFor = path.join("__");
    label.append(node("strong", "", configLabel(key, spec)));
    const hint = configHint(spec);
    if (hint) label.append(node("span", "", hint));
    head.append(label, node("span", "field-type", configTypeLabel(spec)));
    const controls = node("div", "control-stack");
    controls.append(renderConfigControl(spec, path, value));
    field.append(head, controls);
    return field;
  }

  function renderConfigFieldSafe(field, sectionSpec, isModelSection) {
    try {
      if (isModelSection) return renderModelConfigField(field);
      const [key, itemSpec] = field;
      const path = sectionSpec.type === "object" ? [state.configSectionKey, key] : [state.configSectionKey];
      return renderConfigField(key, itemSpec, path);
    } catch (error) {
      const key = isModelSection ? field.fieldKey || field.key : field?.[0];
      const spec = isModelSection ? field.spec : field?.[1] || {};
      const fallback = node("article", "config-field config-field-error");
      const head = node("div", "field-head");
      const title = node("div", "field-title");
      title.append(node("strong", "", configLabel(key || "配置项", spec)));
      title.append(node("span", "", "这个设置项暂时无法显示，请检查配置结构。"));
      head.append(title, node("span", "field-type", "异常"));
      fallback.append(head, node("div", "record-body", error?.message || "渲染失败"));
      return fallback;
    }
  }

  function renderModelConfigField(field) {
    const value = configPathValue(field.path);
    const fieldNode = node("article", `${configFieldClass(field.spec)} model-config-field`);
    const modelLabel = configLabel(field.fieldKey, field.spec);
    const head = node("div", "field-head");
    const title = node("label", "field-title");
    title.htmlFor = field.path.join("__");
    if (modelLabel) title.append(node("strong", "config-source-label", modelLabel));
    const hint = configHint(field.spec);
    if (hint) title.append(node("span", "", hint));
    head.append(title, node("span", "field-type", configTypeLabel(field.spec)));
    const controls = node("div", "control-stack");
    controls.append(renderConfigControl(field.spec, field.path, value));
    fieldNode.append(head, controls);
    return fieldNode;
  }

  function renderConfigControl(spec, path, value) {
    if (spec.type === "bool") return renderConfigBool(path, Boolean(value));
    if (spec.type === "int" || spec.type === "float") return renderConfigNumber(spec, path, value);
    if (spec.type === "list" && spec._special === "character_reference_gallery") {
      return renderCharacterReferenceGallery(path, value);
    }
    if (spec.type === "template_list") return renderConfigTemplateList(spec, path, value);
    if (spec.type === "list") return renderConfigList(path, value);
    if (spec.type === "string" && spec._special === "select_provider" && state.providers.length) {
      return renderConfigProvider(path, value);
    }
    if (spec.type === "string" && Array.isArray(spec.options)) return renderConfigOptions(spec, path, value);
    return renderConfigText(spec, path, value);
  }

  function renderConfigBool(path, value) {
    const wrap = node("div", "switch-line");
    const input = document.createElement("input");
    input.id = path.join("__");
    input.type = "checkbox";
    input.checked = value;
    const label = node("span", "muted", input.checked ? "开启" : "关闭");
    input.addEventListener("change", () => {
      setConfigPathValue(path, input.checked);
      label.textContent = input.checked ? "开启" : "关闭";
    });
    wrap.append(input, label);
    return wrap;
  }

  function renderConfigNumber(spec, path, value) {
    const wrap = node("div", "number-line");
    const number = document.createElement("input");
    number.id = path.join("__");
    number.type = "number";
    applySliderBounds(number, spec);
    if (!number.step) number.step = spec.type === "int" ? "1" : "0.01";
    const normalized = numberValue(value, spec);
    number.value = text(normalized);
    const update = (source, slider = null) => {
      const next = numberValue(source.value, spec);
      if (slider) slider.value = text(next);
      number.value = text(next);
      setConfigPathValue(path, next);
    };
    if (spec.slider) {
      const slider = document.createElement("input");
      slider.type = "range";
      applySliderBounds(slider, spec);
      slider.value = text(normalized);
      slider.addEventListener("input", () => update(slider, slider));
      number.addEventListener("change", () => update(number, slider));
      wrap.append(slider, number);
      return wrap;
    }
    number.addEventListener("change", () => update(number));
    wrap.classList.add("single");
    wrap.append(number);
    return wrap;
  }

  function renderConfigList(path, value) {
    const input = document.createElement("textarea");
    input.id = path.join("__");
    input.rows = 5;
    input.value = Array.isArray(value) ? value.join("\n") : text(value);
    input.addEventListener("input", () => setConfigPathValue(path, parseList(input.value)));
    return input;
  }

  function renderConfigOptions(spec, path, value) {
    const select = document.createElement("select");
    select.id = path.join("__");
    for (const option of spec.options || []) {
      select.append(new Option(configOptionLabel(option, spec), text(option)));
    }
    select.value = text(value || configDefault(spec));
    select.addEventListener("change", () => setConfigPathValue(path, select.value));
    return select;
  }

  function renderConfigProvider(path, value) {
    const select = document.createElement("select");
    select.id = path.join("__");
    select.append(new Option("当前默认模型", ""));
    for (const provider of state.providers) {
      select.append(new Option(provider.label || provider.id, provider.id));
    }
    const current = text(value);
    if (current && !state.providers.some((provider) => provider.id === current)) {
      select.append(new Option(current, current));
    }
    select.value = current;
    select.addEventListener("change", () => setConfigPathValue(path, select.value));
    return select;
  }

  function renderConfigText(spec, path, value) {
    const body = text(value);
    const multiline = spec.type === "text" || body.length > 80 || text(spec.hint).length > 90;
    const input = document.createElement(multiline ? "textarea" : "input");
    input.id = path.join("__");
    if (input.tagName === "INPUT") input.type = "text";
    if (input.tagName === "TEXTAREA") input.rows = spec.type === "text" ? 8 : 5;
    input.value = body;
    input.addEventListener("input", () => setConfigPathValue(path, input.value));
    return input;
  }

  function templateEntries(spec = {}) {
    return Object.entries(spec.templates || {}).filter(([, item]) => item && typeof item === "object");
  }

  function templateEntry(spec = {}, key = "") {
    const entries = templateEntries(spec);
    return entries.find(([entryKey]) => entryKey === key) || entries[0] || ["item", { name: "条目", items: {} }];
  }

  function normalizeTemplateListItem(spec = {}, item = {}) {
    const source = item && typeof item === "object" && !Array.isArray(item) ? item : {};
    const [templateKey, templateSpec] = templateEntry(spec, text(source.__template_key));
    const result = { __template_key: templateKey };
    for (const [fieldKey, fieldSpec] of Object.entries(templateSpec.items || {})) {
      result[fieldKey] = Object.prototype.hasOwnProperty.call(source, fieldKey)
        ? clone(source[fieldKey])
        : configDefault(fieldSpec);
    }
    return result;
  }

  function normalizeTemplateListValue(spec = {}, value) {
    return Array.isArray(value)
      ? value
        .filter((item) => item && typeof item === "object" && !Array.isArray(item))
        .map((item) => normalizeTemplateListItem(spec, item))
      : [];
  }

  function templateListItemTitle(spec = {}, item = {}, index = 0) {
    const [, templateSpec] = templateEntry(spec, text(item.__template_key));
    return clean(templateSpec.name || templateSpec.description, `条目 ${index + 1}`);
  }

  function renderTemplateListItemControl(fieldSpec, value, onChange, inputId) {
    if (fieldSpec.type === "bool") {
      const wrap = node("div", "switch-line");
      const input = document.createElement("input");
      input.id = inputId;
      input.type = "checkbox";
      input.checked = Boolean(value);
      const label = node("span", "muted", input.checked ? "开启" : "关闭");
      input.addEventListener("change", () => {
        onChange(input.checked);
        label.textContent = input.checked ? "开启" : "关闭";
      });
      wrap.append(input, label);
      return wrap;
    }
    if (fieldSpec.type === "int" || fieldSpec.type === "float") {
      const input = document.createElement("input");
      input.id = inputId;
      input.type = "number";
      applySliderBounds(input, fieldSpec);
      if (!input.step) input.step = fieldSpec.type === "int" ? "1" : "0.01";
      input.value = text(numberValue(value, fieldSpec));
      input.addEventListener("change", () => onChange(numberValue(input.value, fieldSpec)));
      return input;
    }
    if (fieldSpec.type === "list") {
      const input = document.createElement("textarea");
      input.id = inputId;
      input.rows = 4;
      input.value = Array.isArray(value) ? value.join("\n") : text(value);
      input.addEventListener("input", () => onChange(parseList(input.value)));
      return input;
    }
    if (fieldSpec.type === "string" && Array.isArray(fieldSpec.options)) {
      const select = document.createElement("select");
      select.id = inputId;
      for (const option of fieldSpec.options || []) {
        select.append(new Option(configOptionLabel(option, fieldSpec), text(option)));
      }
      select.value = text(value || configDefault(fieldSpec));
      select.addEventListener("change", () => onChange(select.value));
      return select;
    }
    const body = text(value);
    const multiline = fieldSpec.type === "text" || body.length > 80 || text(fieldSpec.hint).length > 90;
    const input = document.createElement(multiline ? "textarea" : "input");
    input.id = inputId;
    if (input.tagName === "INPUT") input.type = "text";
    if (input.tagName === "TEXTAREA") input.rows = fieldSpec.type === "text" ? 6 : 4;
    input.value = body;
    input.addEventListener("input", () => onChange(input.value));
    return input;
  }

  function renderConfigTemplateList(spec, path, value) {
    const wrap = node("div", "template-list-control");
    const compactChannels = path.join(".") === "image_generation_config.channels";
    if (compactChannels) wrap.classList.add("template-list-control-compact");
    let items = normalizeTemplateListValue(spec, value);
    let itemIdSeed = 0;
    let itemIds = items.map(() => `template-list-${Date.now().toString(36)}-${itemIdSeed++}`);
    const list = node("div", "template-list-items");
    const summary = node("div", "template-list-summary");
    const addButton = node("button", "soft-button", "");
    addButton.type = "button";

    const newItemId = () => `template-list-${Date.now().toString(36)}-${itemIdSeed++}`;
    const syncItemIds = () => {
      while (itemIds.length < items.length) itemIds.push(newItemId());
      if (itemIds.length > items.length) itemIds = itemIds.slice(0, items.length);
    };
    const captureListRects = () => {
      const rects = new Map();
      list.querySelectorAll(".template-list-item").forEach((entry) => {
        const id = entry.dataset.templateListId || "";
        if (id) rects.set(id, entry.getBoundingClientRect());
      });
      return rects;
    };
    const animateListFrom = (beforeRects) => {
      if (!beforeRects?.size || window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return;
      list.querySelectorAll(".template-list-item").forEach((entry) => {
        const before = beforeRects.get(entry.dataset.templateListId || "");
        if (!before) return;
        const after = entry.getBoundingClientRect();
        const dx = before.left - after.left;
        const dy = before.top - after.top;
        if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
        entry.animate(
          [
            { transform: `translate(${dx}px, ${dy}px)` },
            { transform: "translate(0, 0)" },
          ],
          { duration: 190, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" }
        );
      });
    };
    const saveItems = (nextItems, options = {}) => {
      const beforeRects = options.beforeRects || (options.animate ? captureListRects() : null);
      items = normalizeTemplateListValue(spec, nextItems);
      itemIds = Array.isArray(options.itemIds) ? options.itemIds.slice(0, items.length) : itemIds;
      syncItemIds();
      setConfigPathValue(path, items);
      renderList();
      animateListFrom(beforeRects);
    };
    const updateItem = (index, fieldKey, fieldValue) => {
      const next = items.map((item) => clone(item));
      if (!next[index]) return;
      next[index][fieldKey] = fieldValue;
      saveItems(next);
    };
    let dragState = null;
    const clearDragState = () => {
      list.querySelectorAll(".template-list-item").forEach((entry) => {
        entry.classList.remove("is-dragging", "is-shifting", "is-drop-target");
        entry.style.transform = "";
        entry.style.zIndex = "";
      });
      dragState = null;
    };
    const reorderItem = (fromIndex, toIndex, options = {}) => {
      if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= items.length || toIndex >= items.length) return;
      const next = items.map((item) => clone(item));
      const nextIds = itemIds.slice();
      const [current] = next.splice(fromIndex, 1);
      const [currentId] = nextIds.splice(fromIndex, 1);
      next.splice(toIndex, 0, current);
      nextIds.splice(toIndex, 0, currentId);
      saveItems(next, { animate: true, itemIds: nextIds, ...options });
    };
    const dragGap = () => {
      const style = window.getComputedStyle?.(list);
      return Number.parseFloat(style?.rowGap || style?.gap || "10") || 10;
    };
    const applyDragPreview = (clientY) => {
      if (!dragState) return;
      const cards = [...list.querySelectorAll(".template-list-item")];
      const draggedCard = cards[dragState.fromIndex];
      if (!draggedCard) return;
      const deltaY = clientY - dragState.startY;
      draggedCard.style.transform = `translateY(${deltaY}px)`;
      draggedCard.style.zIndex = "5";
      let targetIndex = 0;
      cards.forEach((card, cardIndex) => {
        const rect = card.getBoundingClientRect();
        if (clientY > rect.top + rect.height / 2) targetIndex = cardIndex;
      });
      dragState.targetIndex = Math.max(0, Math.min(targetIndex, items.length - 1));
      const shift = dragState.height + dragState.gap;
      cards.forEach((card, cardIndex) => {
        if (cardIndex === dragState.fromIndex) return;
        let offset = 0;
        if (dragState.targetIndex > dragState.fromIndex && cardIndex > dragState.fromIndex && cardIndex <= dragState.targetIndex) {
          offset = -shift;
        } else if (dragState.targetIndex < dragState.fromIndex && cardIndex >= dragState.targetIndex && cardIndex < dragState.fromIndex) {
          offset = shift;
        }
        card.classList.toggle("is-shifting", offset !== 0);
        card.classList.toggle("is-drop-target", cardIndex === dragState.targetIndex);
        card.style.transform = offset ? `translateY(${offset}px)` : "";
      });
    };
    const renderItem = (item, index) => {
      const [templateKey, templateSpec] = templateEntry(spec, text(item.__template_key));
      const card = node("article", "template-list-item");
      card.dataset.templateListId = itemIds[index] || "";
      if (compactChannels) card.classList.add("template-list-item-compact");
      const head = node("div", "template-list-item-head");
      const itemTitle = templateListItemTitle(spec, item, index);
      const titleLine = node("div", "template-list-item-title");
      const dragButton = node("button", "template-list-drag", "≡");
      dragButton.type = "button";
      dragButton.setAttribute("aria-label", `${itemTitle} 拖动排序`);
      dragButton.setAttribute("aria-disabled", items.length > 1 ? "false" : "true");
      dragButton.addEventListener("pointerdown", (event) => {
        if (items.length <= 1 || event.button !== 0) return;
        event.preventDefault();
        const rect = card.getBoundingClientRect();
        dragState = {
          fromIndex: index,
          targetIndex: index,
          startY: event.clientY,
          height: rect.height,
          gap: dragGap(),
        };
        card.classList.add("is-dragging");
        dragButton.setPointerCapture?.(event.pointerId);
        const move = (moveEvent) => {
          moveEvent.preventDefault();
          applyDragPreview(moveEvent.clientY);
        };
        const finish = (finishEvent) => {
          finishEvent.preventDefault();
          const state = dragState;
          const beforeRects = captureListRects();
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", finish);
          window.removeEventListener("pointercancel", cancel);
          clearDragState();
          if (state && state.targetIndex !== state.fromIndex) {
            reorderItem(state.fromIndex, state.targetIndex, { beforeRects });
          }
        };
        const cancel = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", finish);
          window.removeEventListener("pointercancel", cancel);
          clearDragState();
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", finish);
        window.addEventListener("pointercancel", cancel);
      });
      dragButton.addEventListener("keydown", (event) => {
        if (event.key === "ArrowUp") {
          event.preventDefault();
          reorderItem(index, index - 1);
        } else if (event.key === "ArrowDown") {
          event.preventDefault();
          reorderItem(index, index + 1);
        }
      });
      titleLine.append(dragButton, node("strong", "", itemTitle));
      const headActions = node("div", "template-list-item-actions");
      const removeButton = node("button", "danger", "删除");
      removeButton.type = "button";
      removeButton.addEventListener("click", () => saveItems(
        items.filter((_, itemIndex) => itemIndex !== index),
        { itemIds: itemIds.filter((_, itemIndex) => itemIndex !== index) }
      ));
      headActions.append(removeButton);
      head.append(titleLine, headActions);
      const body = node("div", "template-list-item-grid");
      if (templateEntries(spec).length > 1) {
        const templateSelect = document.createElement("select");
        templateSelect.id = [...path, index, "__template_key"].join("__");
        for (const [entryKey, entrySpec] of templateEntries(spec)) {
          templateSelect.append(new Option(clean(entrySpec.name || entryKey, entryKey), entryKey));
        }
        templateSelect.value = templateKey;
        templateSelect.addEventListener("change", () => {
          const next = items.map((entry) => clone(entry));
          next[index] = normalizeTemplateListItem(spec, { __template_key: templateSelect.value });
          saveItems(next, { itemIds });
        });
        const templateField = node("label", "template-list-subfield");
        templateField.append(node("span", "", "条目类型"), templateSelect);
        body.append(templateField);
      }
      for (const [fieldKey, fieldSpec] of Object.entries(templateSpec.items || {})) {
        const label = node("label", "template-list-subfield");
        label.htmlFor = [...path, index, fieldKey].join("__");
        const title = node("span", "", configLabel(fieldKey, fieldSpec));
        const control = renderTemplateListItemControl(
          fieldSpec,
          item[fieldKey],
          (nextValue) => updateItem(index, fieldKey, nextValue),
          label.htmlFor
        );
        label.append(title, control);
        body.append(label);
      }
      card.append(head, body);
      return card;
    };
    const renderList = () => {
      const entries = templateEntries(spec);
      addButton.disabled = entries.length <= 0;
      addButton.textContent = "添加接口";
      summary.textContent = items.length ? `已添加 ${items.length} 条` : "尚未添加接口通道";
      if (!items.length) {
        list.replaceChildren(node("div", "empty compact-empty", "还没有条目"));
        return;
      }
      list.replaceChildren(...items.map((item, index) => renderItem(item, index)));
    };
    addButton.addEventListener("click", () => {
      const [templateKey] = templateEntry(spec);
      saveItems(
        [...items, normalizeTemplateListItem(spec, { __template_key: templateKey })],
        { itemIds: [...itemIds, newItemId()] }
      );
    });
    const actions = node("div", "template-list-actions");
    actions.append(addButton);
    wrap.append(summary, list, actions);
    renderList();
    return wrap;
  }

  function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener("load", () => resolve(String(reader.result || "")));
      reader.addEventListener("error", () => reject(reader.error || new Error("图片读取失败")));
      reader.readAsDataURL(file);
    });
  }

  function referenceMaxCount(path) {
    const sectionPath = path.slice(0, -1);
    const count = Number(configPathValue([...sectionPath, "reference_max_count"]) || 6);
    return Math.max(1, Math.min(Number.isFinite(count) ? count : 6, MAX_CHARACTER_REFERENCE_COUNT));
  }

  function formatFileSize(size) {
    const bytes = Number(size || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return "未知大小";
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function normalizeReferenceItems(value) {
    return Array.isArray(value)
      ? value.filter((item) => item && typeof item === "object" && text(item.path)).map((item) => ({
        path: text(item.path),
        name: text(item.name || item.path).split(/[\\/]/).pop(),
        mime: text(item.mime),
        size: Number(item.size || 0),
        preview: text(item.preview),
      }))
      : [];
  }

  function referenceItemsForConfig(value) {
    return normalizeReferenceItems(value).map(({ path, name, mime, size }) => ({ path, name, mime, size }));
  }

  function createReferencePreviewImage(src, altText) {
    const preview = document.createElement("img");
    preview.className = "reference-gallery-preview";
    preview.alt = altText;
    preview.decoding = "async";
    preview.loading = "lazy";
    preview.src = src;
    return preview;
  }

  function setReferencePreviewCache(path, dataUrl) {
    const key = text(path);
    const value = text(dataUrl);
    if (key && value) referencePreviewCache.set(key, value);
  }

  function cachedReferencePreview(path) {
    return referencePreviewCache.get(text(path)) || "";
  }

  function showReferencePreview(thumb, src, altText) {
    if (!thumb) return null;
    const preview = createReferencePreviewImage(src, altText);
    thumb.classList.remove("is-loading", "is-error");
    thumb.querySelector(".reference-gallery-preview")?.remove();
    thumb.prepend(preview);
    return preview;
  }

  async function loadReferencePreview(item, thumb, altText) {
    if (!item.path || item.preview) return;
    const cachedPreview = cachedReferencePreview(item.path);
    if (cachedPreview) {
      item.preview = cachedPreview;
      showReferencePreview(thumb, cachedPreview, altText);
      return;
    }
    thumb?.classList.add("is-loading");
    thumb?.classList.remove("is-error");
    let timeoutId = null;
    const finishWithError = () => {
      if (timeoutId) window.clearTimeout(timeoutId);
      thumb?.querySelector(".reference-gallery-preview")?.remove();
      thumb?.classList.remove("is-loading");
      thumb?.classList.add("is-error");
    };
    try {
      const result = await apiPost("page/config/character-reference/preview", { path: item.path });
      if (!result.data_url) throw new Error("参考图预览为空");
      item.preview = result.data_url;
      setReferencePreviewCache(item.path, result.data_url);
      if (!thumb?.isConnected) return;
      const preview = createReferencePreviewImage(result.data_url, altText);
      preview.classList.add("is-pending");
      preview.onload = () => {
        if (timeoutId) window.clearTimeout(timeoutId);
        preview.classList.remove("is-pending");
        thumb.classList.remove("is-loading", "is-error");
      };
      preview.onerror = finishWithError;
      thumb.querySelector(".reference-gallery-preview")?.remove();
      thumb.prepend(preview);
      if (preview.complete && preview.naturalWidth > 0) {
        preview.onload();
      } else {
        timeoutId = window.setTimeout(finishWithError, 8000);
      }
    } catch {
      finishWithError();
    }
  }

  function renderCharacterReferenceGallery(path, value) {
    const wrap = node("div", "reference-gallery");
    let items = normalizeReferenceItems(value);

    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = "image/png,image/jpeg,image/webp,image/gif";
    fileInput.multiple = true;
    fileInput.hidden = true;

    const summary = node("div", "reference-gallery-summary");
    const list = node("div", "reference-gallery-list");

    const pickButton = node("button", "soft-button", "上传图片");
    pickButton.type = "button";
    pickButton.addEventListener("click", () => fileInput.click());

    const clearButton = node("button", "", "清空");
    clearButton.type = "button";
    const updateGallery = (nextItems) => {
      items = normalizeReferenceItems(nextItems).slice(0, referenceMaxCount(path));
      setConfigPathValue(path, referenceItemsForConfig(items));
      renderList();
    };
    const removeItem = async (item) => {
      try {
        if (item.path) {
          await apiPost("page/config/character-reference/delete", { path: item.path });
        }
      } catch (error) {
        setNotice(error.message || "参考图删除失败", "error");
      }
      referencePreviewCache.delete(text(item.path));
      updateGallery(items.filter((entry) => entry.path !== item.path));
    };
    clearButton.addEventListener("click", () => {
      items.forEach((item) => {
        apiPost("page/config/character-reference/delete", { path: item.path }).catch(() => {});
        referencePreviewCache.delete(text(item.path));
      });
      updateGallery([]);
    });
    const renderList = () => {
      const limit = referenceMaxCount(path);
      summary.textContent = items.length ? `已保存 ${items.length}/${limit} 张参考图` : "尚未上传参考图";
      clearButton.disabled = items.length <= 0;
      pickButton.textContent = items.length ? "继续上传" : "上传图片";
      if (!items.length) {
        list.replaceChildren(node("div", "empty compact-empty", "还没有参考图"));
        return;
      }
      list.replaceChildren(...items.map((item, index) => {
        const row = node("div", "reference-gallery-item");
        const thumb = node("div", "reference-gallery-thumb");
        const altText = item.name || `参考图 ${index + 1}`;
        if (item.preview) {
          thumb.append(createReferencePreviewImage(item.preview, altText));
        } else {
          loadReferencePreview(item, thumb, altText);
        }
        const removeButton = node("button", "reference-gallery-remove");
        removeButton.type = "button";
        removeButton.setAttribute("aria-label", `删除 ${item.name || `参考图 ${index + 1}`}`);
        removeButton.innerHTML = `
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M3 6h18"></path>
            <path d="M8 6V4h8v2"></path>
            <path d="M6.5 6l1 14h9l1-14"></path>
            <path d="M10 11v5"></path>
            <path d="M14 11v5"></path>
          </svg>
        `;
        removeButton.addEventListener("click", () => removeItem(item));
        thumb.append(removeButton);
        row.append(thumb, node("span", "reference-gallery-name", item.name || `参考图 ${index + 1}`));
        return row;
      }));
    };

    fileInput.addEventListener("change", async () => {
      const selected = Array.from(fileInput.files || []);
      fileInput.value = "";
      if (!selected.length) return;
      const limit = referenceMaxCount(path);
      if (items.length >= limit) {
        setNotice("参考图数量已达上限", "error");
        return;
      }
      setBusy(true);
      try {
        const next = [...items];
        for (const file of selected.slice(0, Math.max(0, limit - items.length))) {
          if (!String(file.type || "").startsWith("image/")) {
            setNotice("已跳过非图片文件", "error");
            continue;
          }
          if (file.size > CHARACTER_REFERENCE_MAX_BYTES) {
            setNotice("已跳过超过大小上限的图片", "error");
            continue;
          }
          const image = await fileToDataUrl(file);
          const result = await apiPost("page/config/character-reference", {
            image,
            filename: file.name || "",
          });
          if (result.item && result.item.path && !next.some((item) => item.path === result.item.path)) {
            setReferencePreviewCache(result.item.path, image);
            result.item.preview = image;
            next.push(result.item);
          }
        }
        updateGallery(next);
        setNotice("角色形象参考图已更新", "success");
      } catch (error) {
        setNotice(error.message || "图片上传失败", "error");
      } finally {
        setBusy(false);
      }
    });

    const actions = node("div", "reference-gallery-actions");
    actions.append(pickButton, clearButton);
    wrap.append(summary, list, actions, fileInput);
    renderList();
    return wrap;
  }

  function renderConfigSection() {
    const isModelSection = state.configSectionKey === MODEL_SECTION_KEY;
    const spec = isModelSection ? MODEL_SECTION_SPEC : state.configSchema[state.configSectionKey];
    if (!spec) {
      el.configSectionTitle.textContent = "设置";
      el.configSectionHint.textContent = "";
      el.configFieldList.replaceChildren(empty("暂无配置项"));
      return;
    }

    const fields = isModelSection ? collectProviderConfigFields() : configSectionVisibleFields(state.configSectionKey, spec);
    el.configSectionTitle.textContent = isModelSection ? spec.description : configLabel(state.configSectionKey, spec);
    el.configSectionHint.textContent = configHint(spec);
    if (!fields.length) {
      el.configFieldList.replaceChildren(empty("暂无可调整的设置项"));
      return;
    }
    el.configFieldList.replaceChildren(...fields.map((field) => renderConfigFieldSafe(field, spec, isModelSection)));
  }

  function renderConfig() {
    renderConfigNav();
    renderConfigSection();
    markConfigDirty(state.configDirty);
  }

  async function loadConfig({ quiet = false } = {}) {
    setBusy(true);
    clearConfigAutosaveTimer();
    state.configSaveQueued = false;
    state.configVersion = 0;
    try {
      const data = await apiGet("page/config", { _ts: Date.now() });
      state.configSchema = data.schema || {};
      state.config = buildConfig(state.configSchema, data.config || {});
      state.providers = Array.isArray(data.providers) ? data.providers : [];
      state.configLoaded = true;
      const providerFields = collectProviderConfigFields();
      const visibleSections = visibleConfigSections().map(([key]) => key);
      if (
        !state.configSectionKey
        || (
          state.configSectionKey !== MODEL_SECTION_KEY
          && !visibleSections.includes(state.configSectionKey)
        )
      ) {
        state.configSectionKey = providerFields.length ? MODEL_SECTION_KEY : visibleSections[0] || "";
      }
      markConfigDirty(false);
      renderConfig();
      if (!quiet) setNotice("");
    } catch (error) {
      setNotice(error.message || "设置加载失败", "error");
    } finally {
      setBusy(false);
    }
  }

  async function saveConfig({ auto = false } = {}) {
    if (!state.configLoaded || state.configSaving) {
      if (state.configSaving) state.configSaveQueued = true;
      return;
    }
    clearConfigAutosaveTimer();
    const payload = clone(state.config);
    const savingVersion = state.configVersion;
    state.configSaving = true;
    state.configSaveQueued = false;
    setConfigSaveStatus("saving");
    if (!auto) setBusy(true);
    try {
      const data = await apiPost("page/config", { config: payload });
      state.configSchema = data.schema || state.configSchema;
      const hasNewerChanges = state.configVersion !== savingVersion;
      if (!hasNewerChanges) {
        state.config = buildConfig(state.configSchema, data.config || state.config);
      }
      state.providers = Array.isArray(data.providers) ? data.providers : state.providers;
      state.configLoaded = true;
      state.configDirty = hasNewerChanges;
      renderConfig();
      setConfigSaveStatus(hasNewerChanges ? "pending" : "saved");
      if (!auto) setNotice("设置已保存", "success");
      await loadStatus({ quiet: true });
    } catch (error) {
      state.configDirty = true;
      setConfigSaveStatus("error");
      setNotice(error.message || "设置保存失败", "error");
    } finally {
      state.configSaving = false;
      if (!auto) setBusy(false);
      if (state.configDirty || state.configSaveQueued) {
        scheduleConfigAutosave();
      }
    }
  }

  return {
    loadConfig,
    renderConfig,
    saveConfig,
    clearConfigAutosaveTimer,
  };
}
