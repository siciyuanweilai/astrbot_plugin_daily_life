export function createEditorTools({ state, setNotice }) {
  function setEditorHint(element, message = "") {
    if (!element) return;
    const body = String(message || "").trim();
    element.hidden = !body;
    element.textContent = body;
  }

  function selectEditorText(element) {
    if (!element) return;
    element.focus();
    if (typeof element.select === "function") element.select();
  }

  function setLockedDisabled(element, locked) {
    if (!element) return;
    if (locked) {
      element.dataset.lockDisabled = "true";
    } else {
      delete element.dataset.lockDisabled;
    }
    element.disabled = state.busy || Boolean(locked);
  }

  function setReadOnly(elements, locked) {
    elements.forEach((element) => {
      if (element) element.readOnly = Boolean(locked);
    });
  }

  function formTextValue(input, message) {
    const description = String(input?.value || "").trim();
    if (!description) setNotice(message, "error");
    return description;
  }

  return {
    formTextValue,
    selectEditorText,
    setEditorHint,
    setLockedDisabled,
    setReadOnly,
  };
}
