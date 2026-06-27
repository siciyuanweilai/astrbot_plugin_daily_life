import { createCatalogPanel } from "./catalog.js";
import { createEditorTools } from "./editor.js";
import { createHairPanel } from "./hair.js";
import { createTemplatePanel } from "./template.js";

export function createWorkshopPanel({ state, el, node, empty, setNotice, runAction }) {
  const tools = createEditorTools({ state, setNotice });
  const shared = { state, el, node, empty, setNotice, runAction, tools };

  const templatePanel = createTemplatePanel(shared);
  const catalogPanel = createCatalogPanel(shared);
  const hairPanel = createHairPanel({
    ...shared,
    refreshCatalog: renderCatalog,
  });

  function renderCatalog(status) {
    catalogPanel.renderCatalog(status);
    hairPanel.renderHairStyles(Array.isArray(status.catalog?.hair_styles) ? status.catalog.hair_styles : []);
  }

  function bindEvents() {
    templatePanel.bindEvents();
    catalogPanel.bindEvents();
    hairPanel.bindEvents();
  }

  return {
    bindEvents,
    renderCatalog,
    renderTemplates: templatePanel.renderTemplates,
  };
}
