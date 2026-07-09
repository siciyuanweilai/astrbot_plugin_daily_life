const MENU_GAP = 6;
const VIEWPORT_PADDING = 12;
const MENU_MAX_HEIGHT = 268;
const MENU_MIN_HEIGHT = 96;

function valueText(value) {
  return String(value ?? "").trim();
}

function optionText(option) {
  return valueText(option?.label || option?.textContent || option?.value) || "Select";
}

function currentOption(select) {
  return select?.options?.[select.selectedIndex] || select?.options?.[0] || null;
}

function selectableIndexes(select) {
  return Array.from(select?.options || [])
    .map((option, index) => (option.disabled ? -1 : index))
    .filter((index) => index >= 0);
}

function optionSignature(select) {
  return Array.from(select?.options || [])
    .map((option) => [
      option.value,
      optionText(option),
      option.disabled ? "1" : "0",
    ].join("\u001f"))
    .join("\u001e");
}

function selectLabel(select) {
  const explicit = valueText(select?.getAttribute?.("aria-label"));
  if (explicit) return explicit;
  const label = select?.closest?.("label");
  const caption = label
    ? Array.from(label.children || []).find((child) => child.tagName === "SPAN")?.textContent
    : "";
  return valueText(caption) || "Select";
}

function openHostsForSelect(select) {
  const selectors = [
    ".control-stack",
    ".config-field",
    ".template-list-item",
    ".emoji-filter-tools",
    ".emoji-tools",
    ".config-cover",
    ".panel",
  ];
  const hosts = selectors
    .map((selector) => select?.closest?.(selector))
    .filter(Boolean);
  return Array.from(new Set(hosts));
}

export function createLifeSelectControls({ root = document } = {}) {
  const controllers = new Map();
  let started = false;

  function placeMenu(controller) {
    if (!controller || controller.menu.hidden) return;
    const rect = controller.trigger.getBoundingClientRect?.();
    if (!rect) return;
    const spaceBelow = window.innerHeight - rect.bottom - VIEWPORT_PADDING;
    const spaceAbove = rect.top - VIEWPORT_PADDING;
    const menuHeight = Math.min(controller.menu.scrollHeight || MENU_MAX_HEIGHT, MENU_MAX_HEIGHT);
    const dropUp = spaceBelow < menuHeight && spaceAbove > spaceBelow;
    const available = Math.max(
      MENU_MIN_HEIGHT,
      Math.min(MENU_MAX_HEIGHT, (dropUp ? spaceAbove : spaceBelow) - MENU_GAP)
    );
    controller.wrapper.classList.toggle("is-drop-up", dropUp);
    controller.wrapper.classList.toggle("is-drop-down", !dropUp);
    controller.wrapper.style.setProperty("--life-select-menu-max-height", `${available}px`);
  }

  function clearPlacement(controller) {
    controller.wrapper.classList.remove("is-drop-up", "is-drop-down");
    controller.wrapper.style.removeProperty("--life-select-menu-max-height");
  }

  function buildOptions(select, controller, selected) {
    return Array.from(select.options || []).map((option, index) => {
      const item = document.createElement("button");
      item.type = "button";
      item.id = `${controller.id}-option-${index}`;
      item.className = "life-select-option";
      item.dataset.index = String(index);
      item.role = "option";
      item.addEventListener("click", () => commitSelect(select, index));
      return syncOption(item, option, option === selected);
    });
  }

  function syncOption(item, option, selected) {
    item.disabled = Boolean(option?.disabled);
    item.textContent = optionText(option);
    item.setAttribute("aria-selected", selected ? "true" : "false");
    item.classList.toggle("is-selected", selected);
    return item;
  }

  function setActive(select, index) {
    const controller = controllers.get(select);
    if (!controller) return;
    const selectable = selectableIndexes(select);
    const fallback = selectable.includes(select.selectedIndex) ? select.selectedIndex : selectable[0] ?? -1;
    controller.activeIndex = selectable.includes(index) ? index : fallback;
    controller.menu.querySelectorAll(".life-select-option").forEach((item) => {
      const active = Number(item.dataset.index) === controller.activeIndex;
      item.classList.toggle("is-active", active);
      if (!active) return;
      controller.trigger.setAttribute("aria-activedescendant", item.id);
      if (!controller.menu.hidden) item.scrollIntoView?.({ block: "nearest" });
    });
  }

  function syncSelect(select) {
    const controller = controllers.get(select);
    if (!controller) return;
    const selected = currentOption(select);
    const selectedText = optionText(selected);
    const disabled = Boolean(select.disabled || !select.options?.length);
    const signature = optionSignature(select);

    if (signature !== controller.optionSignature) {
      controller.menu.replaceChildren(...buildOptions(select, controller, selected));
      controller.optionSignature = signature;
    } else {
      controller.menu.querySelectorAll(".life-select-option").forEach((item) => {
        const option = select.options?.[Number(item.dataset.index)];
        if (option) syncOption(item, option, option === selected);
      });
    }

    if (controller.value.textContent !== selectedText) {
      controller.value.textContent = selectedText;
    }
    controller.wrapper.classList.toggle("is-disabled", disabled);
    controller.trigger.disabled = disabled;
    controller.trigger.setAttribute("aria-disabled", disabled ? "true" : "false");
    controller.trigger.setAttribute("aria-label", `${selectLabel(select)}: ${selectedText}`);
    setActive(select, selected ? select.selectedIndex : -1);
  }

  function closeSelect(select) {
    const controller = controllers.get(select);
    if (!controller || !controller.wrapper.classList.contains("is-open")) return;
    controller.wrapper.classList.remove("is-open");
    controller.openHosts.forEach((host) => host.classList.remove("has-open-select"));
    controller.menu.hidden = true;
    controller.trigger.setAttribute("aria-expanded", "false");
    controller.trigger.removeAttribute("aria-activedescendant");
    clearPlacement(controller);
  }

  function closeAll(except = null) {
    controllers.forEach((_controller, select) => {
      if (select !== except) closeSelect(select);
    });
  }

  function openSelect(select) {
    const controller = controllers.get(select);
    if (!controller || controller.trigger.disabled) return;
    syncSelect(select);
    closeAll(select);
    controller.wrapper.classList.add("is-open");
    controller.openHosts.forEach((host) => host.classList.add("has-open-select"));
    controller.menu.hidden = false;
    controller.trigger.setAttribute("aria-expanded", "true");
    placeMenu(controller);
    setActive(select, select.selectedIndex);
  }

  function moveActive(select, step) {
    const selectable = selectableIndexes(select);
    if (!selectable.length) return;
    const controller = controllers.get(select);
    const current = controller?.activeIndex ?? select.selectedIndex;
    const currentPosition = Math.max(0, selectable.indexOf(current));
    const nextPosition = (currentPosition + step + selectable.length) % selectable.length;
    setActive(select, selectable[nextPosition]);
  }

  function commitSelect(select, index) {
    const option = select.options?.[index];
    const controller = controllers.get(select);
    if (!option || option.disabled || !controller) return;
    const previous = select.value;
    select.selectedIndex = index;
    syncSelect(select);
    closeSelect(select);
    controller.trigger.focus?.({ preventScroll: true });
    if (select.value !== previous) {
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function handleKeydown(select, event) {
    const controller = controllers.get(select);
    if (!controller) return;
    const open = controller.wrapper.classList.contains("is-open");
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) openSelect(select);
      moveActive(select, event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      if (!open) openSelect(select);
      const selectable = selectableIndexes(select);
      setActive(select, event.key === "Home" ? selectable[0] : selectable.at(-1));
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) commitSelect(select, controller.activeIndex);
      else openSelect(select);
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      closeSelect(select);
    }
  }

  function enhanceSelect(select) {
    if (!select || controllers.has(select) || select.classList?.contains("life-native-select")) return;
    const id = `life-select-${select.id || controllers.size}`;
    const wrapper = document.createElement("div");
    const trigger = document.createElement("button");
    const value = document.createElement("span");
    const arrow = document.createElement("span");
    const menu = document.createElement("div");

    wrapper.className = "life-select";
    if (select.closest?.(".emoji-filter-tools")) wrapper.classList.add("life-select-filter");
    if (select.closest?.(".config-field, .template-list-item")) wrapper.classList.add("life-select-config");
    wrapper.dataset.selectFor = select.id || "";

    trigger.type = "button";
    trigger.className = "life-select-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", `${id}-listbox`);

    value.className = "life-select-value";
    arrow.className = "life-select-arrow";
    arrow.setAttribute("aria-hidden", "true");
    menu.id = `${id}-listbox`;
    menu.className = "life-select-menu";
    menu.role = "listbox";
    menu.hidden = true;

    trigger.append(value, arrow);
    wrapper.append(trigger, menu);
    select.classList.add("life-native-select");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    select.insertAdjacentElement?.("afterend", wrapper);

    const controller = {
      id,
      wrapper,
      trigger,
      value,
      menu,
      activeIndex: select.selectedIndex,
      optionSignature: "",
      openHosts: openHostsForSelect(select),
    };
    controllers.set(select, controller);

    trigger.addEventListener("click", () => {
      if (wrapper.classList.contains("is-open")) closeSelect(select);
      else openSelect(select);
    });
    trigger.addEventListener("keydown", (event) => handleKeydown(select, event));
    select.addEventListener("change", () => syncSelect(select));
    syncSelect(select);
  }

  function scopeContains(scope, element) {
    if (!scope || scope === root || scope === document) return true;
    return scope === element || Boolean(scope.contains?.(element));
  }

  function pruneDisconnected() {
    controllers.forEach((controller, select) => {
      if (select.isConnected !== false && controller.wrapper.isConnected !== false) return;
      controllers.delete(select);
    });
  }

  function refresh(scope = root) {
    pruneDisconnected();
    const target = scope?.querySelectorAll ? scope : root?.querySelectorAll ? root : document;
    if (target.matches?.("select")) enhanceSelect(target);
    target.querySelectorAll?.("select")?.forEach(enhanceSelect);
    syncExisting(scope);
  }

  function syncExisting(scope = null) {
    pruneDisconnected();
    controllers.forEach((controller, select) => {
      if (scopeContains(scope, select) || scopeContains(scope, controller.wrapper)) {
        syncSelect(select);
      }
    });
  }

  function updateOpenPlacements() {
    controllers.forEach((controller) => {
      if (!controller.menu.hidden) placeMenu(controller);
    });
  }

  function init() {
    if (started) {
      refresh();
      return;
    }
    started = true;
    refresh();
    document.addEventListener?.("click", (event) => {
      for (const controller of controllers.values()) {
        if (controller.wrapper.contains?.(event.target)) return;
      }
      closeAll();
    });
    window.addEventListener?.("resize", updateOpenPlacements);
    window.addEventListener?.("scroll", updateOpenPlacements, { passive: true, capture: true });
  }

  function destroy() {
    controllers.clear();
    started = false;
  }

  return {
    closeAll,
    destroy,
    init,
    refresh,
    syncSelect,
    syncSelects: syncExisting,
  };
}
