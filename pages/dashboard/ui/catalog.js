import { apiPost, GENERATION_TIMEOUT_MS } from "../api/api.js";
import { clean, text } from "../shared/display.js";
import { clone } from "../shared/utils.js";

export function createCatalogPanel({ state, el, node, empty, setNotice, runAction, tools }) {
  const {
    formTextValue,
    selectEditorText,
    setEditorHint,
    setLockedDisabled,
    setReadOnly,
  } = tools;

  function catalogPools() {
    return Array.isArray(state.status?.catalog?.pools) ? state.status.catalog.pools : [];
  }

  function currentCatalogPool() {
    const pools = catalogPools();
    if (!pools.length) return null;
    if (!state.catalogCategory || !pools.some((pool) => pool.category === state.catalogCategory)) {
      state.catalogCategory = pools[0].category;
    }
    return pools.find((pool) => pool.category === state.catalogCategory) || pools[0];
  }

  function findCatalogItem(category, itemId) {
    const pool = catalogPools().find((item) => item.category === category);
    return Array.isArray(pool?.items) ? pool.items.find((item) => item.item_id === itemId) || null : null;
  }

  function defaultCatalogDraft() {
    return {
      category: state.catalogCategory || currentCatalogPool()?.category || "",
      item_id: "",
      text: "",
      enabled: true,
      custom: true,
      editable: true,
    };
  }

  function fillCatalogEditor(item = null, { copy = false, blank = false } = {}) {
    const pool = currentCatalogPool();
    const source = blank ? defaultCatalogDraft() : clone(item || defaultCatalogDraft());
    source.category = source.category || pool?.category || "";
    if (copy) {
      source.item_id = "";
      source.enabled = true;
      source.custom = true;
      source.editable = true;
      source.copying = true;
    }
    state.catalogDraft = source;
    el.catalogTextInput.value = text(source.text);
    el.catalogEnabledInput.checked = source.enabled !== false;
    const locked = source.custom === false && !source.copying;
    setReadOnly([el.catalogTextInput], locked);
    setLockedDisabled(el.catalogEnabledInput, locked);
    setLockedDisabled(el.catalogSaveButton, locked);
    el.catalogSaveButton.textContent = source.copying ? "保存副本" : (locked ? "先复制" : "保存");
    setEditorHint(
      el.catalogEditorHint,
      source.copying
        ? "副本待保存：可以直接修改内容，点击“保存副本”后会新增为自定义素材。"
        : (locked ? "内置素材只能查看，不能直接编辑；请先复制为自定义素材。" : "")
    );
    if (copy) {
      selectEditorText(el.catalogTextInput);
      setNotice("已复制到素材编辑区，保存后会新增为自定义素材。", "success");
    }
  }

  function catalogFromEditor() {
    if (state.catalogDraft?.custom === false && !state.catalogDraft?.copying) {
      throw new Error("内置素材不能直接保存，请先点复制。");
    }
    const category = state.catalogCategory || currentCatalogPool()?.category || "";
    const body = el.catalogTextInput.value.trim();
    if (!category) throw new Error("请先选择素材分类");
    if (!body) throw new Error("请填写素材内容");
    return {
      category,
      item_id: state.catalogDraft?.custom && !state.catalogDraft?.copying ? text(state.catalogDraft.item_id) : "",
      text: body,
      enabled: el.catalogEnabledInput.checked,
    };
  }

  function copyCatalogDraft() {
    if (!state.catalogDraft || !text(state.catalogDraft.text).trim()) {
      setNotice("请先在右侧列表选择一个素材再复制。", "error");
      return;
    }
    fillCatalogEditor(state.catalogDraft, { copy: true });
  }

  function syncCatalogCategorySelect() {
    const pools = catalogPools();
    el.catalogCategorySelect.replaceChildren(
      ...pools.map((pool) => new Option(`${clean(pool.label, pool.category)} (${pool.items?.length || 0})`, pool.category))
    );
    const pool = currentCatalogPool();
    if (pool) el.catalogCategorySelect.value = pool.category;
  }

  function renderCatalog(status) {
    syncCatalogCategorySelect();
    const pool = currentCatalogPool();
    const items = Array.isArray(pool?.items) ? pool.items : [];
    if (!state.catalogDraft && items.length) fillCatalogEditor(items[0]);
    if (!items.length) {
      el.catalogList.replaceChildren(empty("暂无素材"));
      return;
    }
    el.catalogList.replaceChildren(
      ...items.map((item) => {
        const row = node("div", "catalog-row");
        const title = node("div", "template-title");
        title.append(node("span", "", clean(item.text)), node("span", "muted", item.custom ? "自定义" : "内置"));
        const meta = node("div", "template-body", item.enabled === false ? "已禁用" : clean(pool?.label, item.category));
        const actions = node("div", "catalog-actions");
        const copyButton = node("button", "", "复制");
        copyButton.type = "button";
        copyButton.addEventListener("click", () => fillCatalogEditor(item, { copy: true }));
        if (item.editable) {
          const edit = node("button", "", "编辑");
          edit.type = "button";
          edit.addEventListener("click", () => fillCatalogEditor(item));
          actions.append(edit);
        }
        actions.append(copyButton);
        const toggle = node("button", "", item.enabled ? "禁用" : "启用");
        toggle.type = "button";
        toggle.addEventListener("click", () => setCatalogEnabled(item.category, item.item_id, !item.enabled));
        if (item.editable) {
          const remove = node("button", "danger", "删除");
          remove.type = "button";
          remove.addEventListener("click", () => deleteCatalogItem(item.category, item.item_id));
          actions.append(remove);
        }
        actions.append(toggle);
        row.append(title, meta, actions);
        return row;
      })
    );
  }

  async function createCatalogItem(description, useWeb = false) {
    const category = state.catalogCategory || currentCatalogPool()?.category || el.catalogCategorySelect.value || "";
    if (!category) {
      setNotice("请先选择素材分类", "error");
      return;
    }
    const result = await runAction(
      () => apiPost(
        "page/catalog/create",
        { category, description, use_web: useWeb },
        { timeoutMs: GENERATION_TIMEOUT_MS, timeoutMessage: "素材生成耗时较久，请稍后刷新面板查看结果" }
      ),
      useWeb ? "联网素材已创建" : "素材已创建"
    );
    if (result?.item) {
      const itemCategory = result.item.category || category;
      state.catalogCategory = itemCategory;
      renderCatalog(state.status || {});
      fillCatalogEditor(findCatalogItem(itemCategory, result.item.item_id) || result.item);
      el.catalogText.value = "";
    }
  }

  async function saveCatalogItem() {
    let item;
    try {
      item = catalogFromEditor();
    } catch (error) {
      setNotice(error.message || "素材填写不完整", "error");
      return;
    }
    const result = await runAction(
      () => apiPost("page/catalog/save", { item }),
      "素材已保存"
    );
    if (result?.item) {
      const itemCategory = result.item.category || item.category;
      state.catalogCategory = itemCategory;
      renderCatalog(state.status || {});
      fillCatalogEditor(findCatalogItem(itemCategory, result.item.item_id) || result.item);
    }
  }

  async function setCatalogEnabled(category, itemId, enabled) {
    await runAction(
      () => apiPost("page/catalog/enabled", { category, item_id: itemId, enabled }),
      "素材状态已更新"
    );
  }

  async function deleteCatalogItem(category, itemId) {
    if (state.catalogDraft?.category === category && state.catalogDraft?.item_id === itemId) {
      state.catalogDraft = null;
    }
    const result = await runAction(
      () => apiPost("page/catalog/delete", { category, item_id: itemId }),
      "素材已删除"
    );
    if (result) {
      fillCatalogEditor(null, { blank: true });
    }
  }

  function bindEvents() {
    el.catalogCategorySelect.addEventListener("change", () => {
      state.catalogCategory = el.catalogCategorySelect.value;
      state.catalogDraft = null;
      fillCatalogEditor(null, { blank: true });
      renderCatalog(state.status || {});
    });
    el.catalogNewButton.addEventListener("click", () => fillCatalogEditor(null, { blank: true }));
    el.catalogCopyButton.addEventListener("click", copyCatalogDraft);
    el.catalogSaveButton.addEventListener("click", saveCatalogItem);
    el.catalogForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const description = formTextValue(el.catalogText, "请先填写素材描述");
      if (!description) return;
      createCatalogItem(description);
    });
    el.catalogWebButton.addEventListener("click", () => {
      const description = formTextValue(el.catalogText, "请先填写素材描述");
      if (description) createCatalogItem(description, true);
    });
  }

  return {
    bindEvents,
    renderCatalog,
  };
}
