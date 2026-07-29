let returnFocus = null;

const FOCUS_SELECTOR = [
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function focusDialog(dialog, preferred) {
  if (!dialog) return;
  returnFocus = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
  window.setTimeout(() => {
    const target = preferred || dialog.querySelector(FOCUS_SELECTOR);
    target?.focus();
  }, 0);
}

export function restoreDialogFocus() {
  const target = returnFocus;
  returnFocus = null;
  if (target?.isConnected) window.setTimeout(() => target.focus(), 0);
}

export function trapDialogFocus(event, dialogs) {
  if (event.key !== "Tab") return false;
  const dialog = dialogs.find((item) => item && !item.hidden);
  if (!dialog) return false;
  const focusable = Array.from(dialog.querySelectorAll(FOCUS_SELECTOR))
    .filter((item) => item instanceof HTMLElement && item.offsetParent !== null);
  if (!focusable.length) {
    event.preventDefault();
    return true;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
  return true;
}
