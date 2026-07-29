const cursorTrailIntervalMs = 78;
const cursorTrailMinDistance = 20;
const cursorTrailMaxItems = 28;
const driftDesktopPieces = 32;
const driftMobilePieces = 18;

const driftDepths = [
  {
    className: "is-far",
    depth: 1,
    sizeScale: 0.74,
    opacityRange: [0.22, 0.36],
    durationRange: [20, 30],
    swayRange: [8, 18],
    windRange: [8, 22],
    spinRange: [90, 260],
  },
  {
    className: "is-mid",
    depth: 2,
    sizeScale: 1,
    opacityRange: [0.38, 0.62],
    durationRange: [15, 24],
    swayRange: [14, 32],
    windRange: [16, 38],
    spinRange: [180, 480],
  },
  {
    className: "is-near",
    depth: 3,
    sizeScale: 1.18,
    opacityRange: [0.5, 0.78],
    durationRange: [12, 18],
    swayRange: [22, 44],
    windRange: [26, 52],
    spinRange: [280, 620],
  },
];

const driftKinds = ["is-petal", "is-ticket", "is-spark", "is-dot"];
const cursorKinds = ["is-dot", "is-spark", "is-ticket"];

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

function pick(items) {
  return items[Math.floor(Math.random() * items.length)] || items[0];
}

function mediaMatches(query) {
  return Boolean(window.matchMedia?.(query)?.matches);
}

function setStyleVar(element, name, value) {
  if (typeof element?.style?.setProperty === "function") {
    element.style.setProperty(name, value);
  } else if (element?.style) {
    element.style[name] = value;
  }
}

function appendElement(parent, child) {
  if (typeof parent?.appendChild === "function") parent.appendChild(child);
}

export function isMotionReduced() {
  return mediaMatches("(prefers-reduced-motion: reduce)");
}

export function createDashboardEffects({ lifeDriftLayer, cursorTrailLayer } = {}) {
  const cursorState = {
    bound: false,
    lastAt: 0,
    lastX: 0,
    lastY: 0,
  };
  let mediaWatcherBound = false;

  function clearLifeDrift() {
    if (lifeDriftLayer) lifeDriftLayer.textContent = "";
  }

  function clearCursorTrail() {
    if (cursorTrailLayer) cursorTrailLayer.textContent = "";
  }

  function initLifeDrift() {
    if (!lifeDriftLayer || isMotionReduced()) {
      clearLifeDrift();
      return;
    }

    const isMobile = mediaMatches("(max-width: 720px)");
    const pieceCount = isMobile ? driftMobilePieces : driftDesktopPieces;
    lifeDriftLayer.textContent = "";

    for (let index = 0; index < pieceCount; index += 1) {
      const piece = document.createElement("span");
      const shape = document.createElement("span");
      const depth = driftDepths[index % driftDepths.length];
      const kind = pick(driftKinds);
      const windDirection = Math.random() > 0.35 ? 1 : -1;
      const windStrength = randomBetween(depth.windRange[0], depth.windRange[1]);
      const size = (7 + Math.random() * 11) * depth.sizeScale;
      const left = Math.random() * 100;
      const windA = windDirection * windStrength * randomBetween(0.22, 0.48);
      const windB = windDirection * windStrength * randomBetween(0.62, 0.96);
      const windC = windDirection * windStrength * randomBetween(0.38, 0.78);
      const drift = windDirection * windStrength + randomBetween(-9, 9);
      const sway = randomBetween(depth.swayRange[0], depth.swayRange[1]);
      const spin = randomBetween(depth.spinRange[0], depth.spinRange[1]);

      piece.className = `life-drift-piece ${depth.className} ${kind}`;
      shape.className = "life-drift-shape";
      setStyleVar(piece, "--drift-left", `${left.toFixed(2)}vw`);
      setStyleVar(piece, "--drift-size", `${size.toFixed(1)}px`);
      setStyleVar(piece, "--drift-depth", depth.depth);
      setStyleVar(piece, "--drift-opacity", randomBetween(depth.opacityRange[0], depth.opacityRange[1]).toFixed(2));
      setStyleVar(piece, "--drift-duration", `${randomBetween(depth.durationRange[0], depth.durationRange[1]).toFixed(2)}s`);
      setStyleVar(piece, "--drift-sway-duration", `${randomBetween(3.2, 6.4).toFixed(2)}s`);
      setStyleVar(piece, "--drift-delay", `${(-Math.random() * 20).toFixed(2)}s`);
      setStyleVar(piece, "--drift-wind-a", `${windA.toFixed(1)}vw`);
      setStyleVar(piece, "--drift-wind-b", `${windB.toFixed(1)}vw`);
      setStyleVar(piece, "--drift-wind-c", `${windC.toFixed(1)}vw`);
      setStyleVar(piece, "--drift-end", `${drift.toFixed(1)}vw`);
      setStyleVar(piece, "--drift-sway", `${sway.toFixed(1)}px`);
      setStyleVar(piece, "--drift-rotate", `${Math.floor(Math.random() * 360)}deg`);
      setStyleVar(piece, "--drift-spin-a", `${(spin * 0.22).toFixed(0)}deg`);
      setStyleVar(piece, "--drift-spin-b", `${(spin * 0.5).toFixed(0)}deg`);
      setStyleVar(piece, "--drift-spin-c", `${(spin * 0.74).toFixed(0)}deg`);
      setStyleVar(piece, "--drift-spin", `${spin.toFixed(0)}deg`);

      appendElement(piece, shape);
      appendElement(lifeDriftLayer, piece);
    }
  }

  function isCursorTrailAvailable() {
    return Boolean(cursorTrailLayer)
      && mediaMatches("(pointer: fine)")
      && !isMotionReduced();
  }

  function createCursorTrailItem(x, y) {
    if (!isCursorTrailAvailable()) {
      clearCursorTrail();
      return;
    }

    while ((cursorTrailLayer.children?.length || 0) >= cursorTrailMaxItems) {
      cursorTrailLayer.firstElementChild?.remove();
    }

    const item = document.createElement("span");
    const kind = pick(cursorKinds);
    const size = kind === "is-ticket" ? randomBetween(12, 20) : randomBetween(7, 15);
    item.className = `cursor-note ${kind}`;
    setStyleVar(item, "--cursor-x", `${x.toFixed(1)}px`);
    setStyleVar(item, "--cursor-y", `${y.toFixed(1)}px`);
    setStyleVar(item, "--cursor-size", `${size.toFixed(1)}px`);
    setStyleVar(item, "--cursor-opacity", randomBetween(0.42, 0.76).toFixed(2));
    setStyleVar(item, "--cursor-duration", `${randomBetween(620, 980).toFixed(0)}ms`);
    setStyleVar(item, "--cursor-drift-x", `${randomBetween(-22, 22).toFixed(1)}px`);
    setStyleVar(item, "--cursor-drift-y", `${randomBetween(-38, -18).toFixed(1)}px`);
    setStyleVar(item, "--cursor-rotate", `${randomBetween(-40, 42).toFixed(0)}deg`);
    setStyleVar(item, "--cursor-spin", `${randomBetween(45, 160).toFixed(0)}deg`);
    setStyleVar(item, "--cursor-scale", randomBetween(0.72, 1.18).toFixed(2));
    if (typeof item.addEventListener === "function") {
      item.addEventListener("animationend", () => item.remove(), { once: true });
    }
    appendElement(cursorTrailLayer, item);
  }

  function handleCursorMove(event) {
    if (event.pointerType && event.pointerType !== "mouse") return;
    if (!isCursorTrailAvailable()) return;

    const now = window.performance.now();
    const distance = Math.hypot(event.clientX - cursorState.lastX, event.clientY - cursorState.lastY);
    if (now - cursorState.lastAt < cursorTrailIntervalMs && distance < cursorTrailMinDistance) return;

    cursorState.lastAt = now;
    cursorState.lastX = event.clientX;
    cursorState.lastY = event.clientY;
    createCursorTrailItem(event.clientX, event.clientY);
  }

  function bindMediaWatchers() {
    if (mediaWatcherBound) return;
    mediaWatcherBound = true;

    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const finePointer = window.matchMedia?.("(pointer: fine)");
    reducedMotion?.addEventListener?.("change", () => {
      clearCursorTrail();
      clearLifeDrift();
      if (!isMotionReduced()) initLifeDrift();
    });
    finePointer?.addEventListener?.("change", clearCursorTrail);
    window.addEventListener("pagehide", () => {
      clearCursorTrail();
      clearLifeDrift();
    });
  }

  function initCursorTrail() {
    bindMediaWatchers();
    if (cursorState.bound) return;
    cursorState.bound = true;
    window.addEventListener("pointermove", handleCursorMove, { passive: true });
  }

  return {
    clearCursorTrail,
    clearLifeDrift,
    initCursorTrail,
    initLifeDrift,
    isMotionReduced,
  };
}
