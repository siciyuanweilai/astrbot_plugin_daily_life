from __future__ import annotations

VOICE_CALL_PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>实时语音通话</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: ui-rounded, "SF Pro Rounded", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
      background: #171b27;
      color: #f8f7fb;
    }
    * { box-sizing: border-box; }
    button { font: inherit; }
    body {
      margin: 0;
      block-size: 100vh;
      min-block-size: 100dvb;
      block-size: 100dvb;
      overflow: hidden;
      position: relative;
      isolation: isolate;
      background: #f8f1f4;
      color: #f8f7fb;
    }
    body[data-wallpaper-visible="true"] { background: #171b27; }
    .call-screen {
      position: relative;
      isolation: isolate;
      display: grid;
      grid-template-rows: minmax(0, 1fr) auto;
      block-size: 100%;
      min-block-size: 100dvb;
      max-block-size: 100dvb;
      overflow: hidden;
      background: #171b27;
      visibility: hidden;
      opacity: 0;
      pointer-events: none;
      transition: opacity 180ms ease;
    }
    .call-screen[data-wallpaper="ready"],
    .call-screen[data-wallpaper="fallback"] {
      visibility: visible;
      opacity: 1;
      pointer-events: auto;
    }
    .call-screen::before {
      position: absolute;
      z-index: 0;
      inset: 0;
      content: "";
      background-image:
        linear-gradient(180deg, #0d101b38 0%, #11152245 54%, #13172466 100%),
        var(--voice-wallpaper, none);
      background-position: center;
      background-size: cover;
      opacity: 0;
      transition: opacity 240ms ease;
    }
    .call-screen[data-wallpaper="ready"]::before { opacity: 1; }
    .call-stage,
    .controls,
    .transcript-view { position: relative; z-index: 1; }
    .call-screen[data-view="transcript"] {
      grid-template-rows: minmax(0, 1fr);
      overflow: hidden;
    }
    .call-screen[data-view="transcript"] > .call-stage,
    .call-screen[data-view="transcript"] > .controls { display: none; }
    .control-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 0;
      color: inherit;
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
    }
    .control-button svg { inline-size: 21px; block-size: 21px; stroke-width: 2; }
    .call-stage {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      align-content: start;
      justify-items: center;
      min-block-size: 0;
      overflow: hidden;
      padding: max(clamp(44px, 8dvb, 68px), calc(env(safe-area-inset-top) + 24px)) 26px 16px;
      text-align: center;
    }
    .call-presence { display: grid; justify-items: center; }
    .profile-disc {
      position: relative;
      display: grid;
      place-items: center;
      inline-size: min(44vw, 188px);
      aspect-ratio: 1;
      border: 1px solid #7e8ca4;
      border-radius: 50%;
      background: #303a50;
      box-shadow: 0 0 0 14px #ffffff0a;
      overflow: visible;
    }
    .profile-disc::after {
      position: absolute;
      inset: 11px;
      content: "";
      border: 1px solid #ffffff38;
      border-radius: inherit;
    }
    .profile-mark {
      position: relative;
      z-index: 1;
      display: grid;
      place-items: center;
      inline-size: 72px;
      block-size: 72px;
      border-radius: 50%;
      background: #f279a7;
      color: #2e1c2c;
      font-size: 31px;
      font-weight: 750;
      line-height: 1;
    }
    .profile-avatar,
    .transcript-avatar img {
      inline-size: 100%;
      block-size: 100%;
      border-radius: inherit;
      object-fit: cover;
    }
    .profile-avatar {
      position: absolute;
      z-index: 1;
      inset: 8px;
      inline-size: calc(100% - 16px);
      block-size: calc(100% - 16px);
      display: none;
    }
    .profile-disc[data-has-avatar="true"] .profile-avatar { display: block; }
    .profile-disc[data-has-avatar="true"] .profile-mark { display: none; }
    .voice-bars {
      position: absolute;
      inset: auto 0 -25px;
      display: flex;
      align-items: end;
      justify-content: center;
      gap: 5px;
      block-size: 25px;
      opacity: .36;
      transition: opacity 160ms ease;
    }
    .voice-bars span { inline-size: 4px; block-size: 7px; border-radius: 99px; background: #ffb5ca; }
    .voice-bars span:nth-child(2) { block-size: 14px; }
    .voice-bars span:nth-child(3) { block-size: 22px; }
    .voice-bars span:nth-child(4) { block-size: 14px; }
    .voice-bars span:nth-child(5) { block-size: 7px; }
    .call-screen[data-state="listening"] .voice-bars,
    .call-screen[data-state="speaking"] .voice-bars { opacity: 1; }
    .call-screen[data-state="listening"] .voice-bars span,
    .call-screen[data-state="speaking"] .voice-bars span { animation: voice-pulse 820ms ease-in-out infinite alternate; }
    .call-screen[data-state="listening"] .voice-bars span:nth-child(2),
    .call-screen[data-state="speaking"] .voice-bars span:nth-child(4) { animation-delay: 140ms; }
    .call-screen[data-state="listening"] .voice-bars span:nth-child(3),
    .call-screen[data-state="speaking"] .voice-bars span:nth-child(1) { animation-delay: 280ms; }
    .peer-name { margin: 42px 0 0; font-size: 21px; font-weight: 720; line-height: 1.35; letter-spacing: 0; }
    .peer-name,
    .call-status,
    .call-duration,
    .call-note { text-shadow: 0 2px 9px #080b14cc; }
    .call-status { margin: 7px 0 0; color: #c5ccda; font-size: 15px; font-weight: 600; line-height: 1.4; letter-spacing: 0; }
    .call-duration { display: none; margin-top: 8px; color: #bbc4d5; font-size: 14px; font-variant-numeric: tabular-nums; }
    .call-screen[data-connected="true"] .call-duration { display: block; }
    .call-note { margin: 10px 0 0; color: #9da8bd; font-size: 13px; line-height: 1.45; }
    .call-note:empty { display: none; }
    .call-screen[data-state="ended"] .call-status,
    .call-screen[data-state="ended"] .call-note { display: none; }
    .lyric-preview {
      position: relative;
      display: block;
      align-self: stretch;
      min-block-size: 0;
      inline-size: 100%;
      margin-top: 28px;
      overflow: hidden;
      border: 0;
      background: transparent;
      color: inherit;
      cursor: pointer;
      text-align: center;
      text-decoration: none;
      -webkit-tap-highlight-color: transparent;
    }
    .lyric-scroller {
      block-size: 100%;
      overflow-y: auto;
      overscroll-behavior: contain;
      scrollbar-width: none;
      scroll-behavior: smooth;
    }
    .lyric-scroller::-webkit-scrollbar { display: none; }
    .lyric-track {
      display: grid;
      align-content: center;
      gap: 16px;
      min-block-size: 100%;
      padding-block: 62px;
    }
    .transcript-view {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      block-size: 100%;
      min-block-size: 0;
      padding: max(18px, env(safe-area-inset-top)) 20px max(24px, env(safe-area-inset-bottom));
    }
    .transcript-view[hidden] { display: none; }
    .transcript-header {
      display: flex;
      align-items: center;
      justify-content: center;
      min-block-size: 48px;
    }
    .transcript-heading { min-inline-size: 0; text-align: center; }
    .transcript-heading h1 { margin: 0; font-size: 19px; line-height: 1.35; letter-spacing: 0; }
    .transcript-heading p { margin: 3px 0 0; color: #9da8bd; font-size: 12px; line-height: 1.4; }
    .transcript-view:focus { outline: 0; }
    .full-transcript {
      display: grid;
      align-content: start;
      gap: 16px;
      min-block-size: 0;
      padding-block: 28px;
      overflow-y: auto;
      overscroll-behavior: contain;
      scrollbar-width: none;
      scroll-behavior: smooth;
    }
    .full-transcript::-webkit-scrollbar { display: none; }
    .full-transcript-empty { margin: 56px auto; color: #8490a6; font-size: 15px; line-height: 1.55; text-align: center; }
    .transcript-turn { display: flex; align-items: end; gap: 9px; max-inline-size: 92%; }
    .transcript-turn.user { justify-self: end; flex-direction: row-reverse; }
    .transcript-turn.peer { justify-self: start; }
    .transcript-avatar {
      display: grid;
      place-items: center;
      flex: 0 0 38px;
      inline-size: 38px;
      block-size: 38px;
      overflow: hidden;
      border: 1px solid #65728b;
      border-radius: 50%;
      background: #374157;
      color: #f8f7fb;
      font-size: 14px;
      font-weight: 700;
    }
    .transcript-avatar img { display: none; }
    .transcript-avatar[data-has-avatar="true"] img { display: block; }
    .transcript-avatar[data-has-avatar="true"] span { display: none; }
    .transcript-content { display: grid; min-inline-size: 0; }
    .transcript-bubble {
      position: relative;
      padding: 11px 13px;
      border: 1px solid #f1b6cd;
      border-radius: 18px 18px 18px 7px;
      background: #fff8fb;
      box-shadow: 0 8px 18px rgb(43 15 34 / 18%);
      color: #553448;
      font-size: 16px;
      line-height: 1.58;
      text-align: left;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .transcript-turn.peer .transcript-bubble::after {
      position: absolute;
      inset-inline-start: -5px;
      inset-block-end: 3px;
      inline-size: 10px;
      block-size: 10px;
      content: "";
      border-inline-start: 1px solid #f1b6cd;
      border-block-end: 1px solid #f1b6cd;
      background: #fff8fb;
      transform: rotate(45deg);
    }
    .transcript-turn.user .transcript-bubble { border-color: #b7d8b0; border-radius: 16px 8px 16px 16px; background: #d7efd1; color: #18311a; }
    .controls {
      display: grid;
      grid-template-columns: 72px minmax(0, 1fr) 72px;
      align-items: center;
      gap: 14px;
      padding: 18px 22px max(24px, env(safe-area-inset-bottom));
    }
    .control-button { min-block-size: 56px; transition: background-color 150ms ease, transform 150ms ease, opacity 150ms ease; }
    .control-button:active:not(:disabled) { transform: scale(.96); }
    .control-button:disabled { cursor: not-allowed; opacity: .42; }
    .mute-button {
      flex-direction: column;
      gap: 4px;
      inline-size: 64px;
      block-size: 64px;
      min-block-size: 64px;
      border: 1px solid #4c566b;
      border-radius: 50%;
      background: #292f3f;
      color: #f7f7fb;
    }
    .mute-button span { font-size: 11px; line-height: 1; }
    .mute-button[aria-pressed="true"] { border-color: #f5b3ca; background: #513343; color: #ffd5e4; }
    .start-button {
      gap: 9px;
      min-inline-size: 0;
      border-radius: 16px;
      background: #f279a7;
      color: #2a1827;
      font-size: 16px;
      font-weight: 750;
    }
    .start-button[hidden] { display: none; }
    .hangup-button {
      flex-direction: column;
      gap: 4px;
      inline-size: 64px;
      block-size: 64px;
      min-block-size: 64px;
      border-radius: 50%;
      background: #de5b67;
      color: #fff;
    }
    .hangup-button span { font-size: 11px; line-height: 1; }
    .control-button:focus-visible { outline: 3px solid #f8d877; outline-offset: 3px; }
    .lyric-preview:focus-visible { outline: 0; opacity: .94; }
    @keyframes voice-pulse { from { transform: scaleY(.52); } to { transform: scaleY(1); } }
    @media (min-width: 720px) {
      body { display: grid; place-items: center; padding: 24px; background: #f8f1f4; }
      body::before {
        position: fixed;
        z-index: 0;
        inset: 0;
        content: "";
        pointer-events: none;
        background-image:
          linear-gradient(180deg, #0d101b80 0%, #1115228c 54%, #131724a6 100%),
          var(--voice-wallpaper, none);
        background-position: center;
        background-size: cover;
        opacity: 0;
        transition: opacity 240ms ease;
      }
      body[data-wallpaper="ready"]::before { opacity: 1; }
      .call-screen { z-index: 1; inline-size: min(100%, 440px); block-size: min(880px, calc(100dvb - 48px)); min-block-size: 0; max-block-size: min(880px, calc(100dvb - 48px)); border: 1px solid #3d4658; border-radius: 26px; box-shadow: 0 24px 70px #02040b80; }
      .call-screen[data-view="transcript"] { overflow: hidden; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; }
    }
  </style>
</head>
<body>
<main class="call-screen" id="callScreen" data-state="preparing" data-connected="false" data-view="call" data-wallpaper="loading">
  <section class="call-stage" aria-live="polite">
    <div class="call-presence">
      <div class="profile-disc" id="peerProfile" data-has-avatar="false">
        <img class="profile-avatar" id="peerAvatar" alt="">
        <div class="profile-mark" id="peerInitial" aria-hidden="true">对</div>
        <div class="voice-bars"><span></span><span></span><span></span><span></span><span></span></div>
      </div>
      <p class="peer-name" id="peerName">对方</p>
      <p class="call-status" id="status">正在准备通话</p>
      <time class="call-duration" id="duration" datetime="PT0S">00:00</time>
      <p class="call-note" id="callNote"></p>
    </div>
    <button class="lyric-preview" id="transcriptOpen" type="button" aria-label="打开完整实时转写" title="打开完整实时转写">
      <div class="lyric-scroller" id="lyricScroller" aria-live="polite">
        <div class="lyric-track" id="lyricTrack"></div>
      </div>
    </button>
  </section>

  <footer class="controls">
    <button class="control-button mute-button" id="mute" type="button" aria-label="静音" aria-pressed="false" disabled>
      <i data-lucide="mic"></i><span id="muteLabel">静音</span>
    </button>
    <button class="control-button start-button" id="start" type="button">
      <i data-lucide="phone-call"></i><span>开始通话</span>
    </button>
    <button class="control-button hangup-button" id="hangup" type="button" aria-label="结束通话" title="结束通话" disabled>
      <i data-lucide="phone-off"></i><span>挂断</span>
    </button>
  </footer>
  <section class="transcript-view" id="transcriptView" aria-labelledby="transcriptTitle" tabindex="-1" hidden>
    <header class="transcript-header">
      <div class="transcript-heading">
        <h1 id="transcriptTitle">实时转写</h1>
        <p id="transcriptViewStatus">通话中持续更新</p>
      </div>
    </header>
    <div class="full-transcript" id="fullTranscript" aria-live="polite"></div>
  </section>
</main>
<script id="voiceCallProfile" type="application/json">__VOICE_CALL_PROFILE__</script>
<script src="https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js" defer></script>
<script>
(() => {
  const token = location.pathname.split('/').pop();
  const readPageProfiles = () => {
    try { return JSON.parse(document.getElementById('voiceCallProfile')?.textContent || '{}'); }
    catch (_) { return {}; }
  };
  const profiles = readPageProfiles();
  const callScreen = document.getElementById('callScreen');
  const statusEl = document.getElementById('status');
  const durationEl = document.getElementById('duration');
  const callNote = document.getElementById('callNote');
  const peerProfile = document.getElementById('peerProfile');
  const peerAvatar = document.getElementById('peerAvatar');
  const peerInitial = document.getElementById('peerInitial');
  const peerName = document.getElementById('peerName');
  const transcriptOpen = document.getElementById('transcriptOpen');
  const transcriptView = document.getElementById('transcriptView');
  const transcriptViewStatus = document.getElementById('transcriptViewStatus');
  const fullTranscript = document.getElementById('fullTranscript');
  const lyricScroller = document.getElementById('lyricScroller');
  const lyricTrack = document.getElementById('lyricTrack');
  const startButton = document.getElementById('start');
  const hangupButton = document.getElementById('hangup');
  const muteButton = document.getElementById('mute');
  const muteLabel = document.getElementById('muteLabel');
  let socket, audioContext, inputStream, processor, captureNode, captureSink, workletUrl, nextPlayAt = 0;
  let vadSpeaking = false, vadSilenceMs = 0;
  let captureChunks = [], captureSamples = 0;
  const activeSources = new Set();
  let transcriptTurns = [];
  let terminalStatus = '', reconnectTimer = null;
  let manualHangup = false, gatewayReady = false, sessionEstablished = false, startRequested = false, startSent = false;
  let captureAttached = false, reconnectAttempt = 0, sourceNode = null;
  let muted = false, callStartedAt = 0, durationTimer = null;
  let playbackDrainRequested = false, playbackDrainTimer = null;
  const voiceWallpaperEndpoints = Object.freeze([
    'https://api.nycnm.cn/api/v2/bizhi1',
    'https://api.nycnm.cn/api/v2/bizhi2',
  ]);
  const wallpaperTimeoutMs = 4500;

  const profileFor = (role) => {
    const profile = profiles[role] || {};
    return {
      name: String(profile.name || (role === 'user' ? '你' : '对方')).trim().slice(0, 80) || (role === 'user' ? '你' : '对方'),
      avatarUrl: String(profile.avatar_url || '').trim(),
    };
  };
  const initialsFor = (name) => Array.from(String(name || '').trim()).slice(0, 1).join('') || '·';
  const setImage = (image, container, profile) => {
    if (!image || !container) return;
    image.removeAttribute('src');
    container.dataset.hasAvatar = 'false';
    if (!profile.avatarUrl) return;
    image.src = profile.avatarUrl;
    image.onload = () => { container.dataset.hasAvatar = 'true'; };
    image.onerror = () => { image.removeAttribute('src'); container.dataset.hasAvatar = 'false'; };
  };
  const setCallState = (state) => { callScreen.dataset.state = state; };
  const setConnected = (connected) => { callScreen.dataset.connected = connected ? 'true' : 'false'; };
  const loadVoiceWallpaper = () => {
    const start = Math.floor(Math.random() * voiceWallpaperEndpoints.length);
    const endpoints = voiceWallpaperEndpoints.map((_, index) => voiceWallpaperEndpoints[(start + index) % voiceWallpaperEndpoints.length]);
    let index = 0;
    let settled = false;
    const timeout = window.setTimeout(() => finish('fallback'), wallpaperTimeoutMs);
    const finish = (state) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      callScreen.dataset.wallpaper = state;
      document.body.dataset.wallpaper = state;
      document.body.dataset.wallpaperVisible = 'true';
    };
    const loadNext = () => {
      const endpoint = endpoints[index++];
      if (!endpoint) {
        finish('fallback');
        return;
      }
      const image = new Image();
      const source = `${endpoint}${endpoint.includes('?') ? '&' : '?'}voice_call=${Date.now().toString(36)}`;
      image.onload = () => {
        if (settled) return;
        const value = `url("${source}")`;
        callScreen.style.setProperty('--voice-wallpaper', value);
        document.body.style.setProperty('--voice-wallpaper', value);
        finish('ready');
      };
      image.onerror = loadNext;
      image.src = source;
    };
    loadNext();
  };
  const setStatus = (text) => {
    const status = String(text || '正在准备通话');
    statusEl.textContent = status;
    if (/正在听/.test(status)) {
      setCallState('listening'); callNote.textContent = '正在接收你的声音';
    } else if (/正在说/.test(status)) {
      setCallState('speaking'); callNote.textContent = '对方正在回应';
    } else if (/已连接|可以说话/.test(status)) {
      setCallState('connected'); callNote.textContent = '';
    } else if (/结束|失效|过期|错误|无法/.test(status)) {
      setCallState('ended'); callNote.textContent = '';
    } else {
      setCallState('connecting'); callNote.textContent = '';
    }
  };
  const updateDuration = () => {
    const seconds = Math.max(0, Math.floor((Date.now() - callStartedAt) / 1000));
    const minutes = Math.floor(seconds / 60);
    durationEl.dateTime = `PT${seconds}S`;
    durationEl.textContent = `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  };
  const startDuration = () => {
    if (callStartedAt) return;
    callStartedAt = Date.now(); updateDuration();
    durationTimer = window.setInterval(updateDuration, 1000);
  };
  const stopDuration = () => {
    if (durationTimer) { window.clearInterval(durationTimer); durationTimer = null; }
  };
  const renderIcons = () => {
    if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
  };
  const peer = profileFor('assistant');
  peerName.textContent = peer.name;
  peerInitial.textContent = initialsFor(peer.name);
  setImage(peerAvatar, peerProfile, peer);
  const renderTranscript = () => {
    lyricTrack.replaceChildren();
    const visibleTurns = transcriptTurns.filter(turn => turn.text);
    if (!visibleTurns.length) return;
    const start = Math.max(0, visibleTurns.length - 5);
    let currentTurn = null;
    visibleTurns.slice(start).forEach((turn, index, turns) => {
      const role = turn.role === 'user' ? 'user' : 'assistant';
      const row = document.createElement('article');
      row.className = `transcript-turn ${role === 'user' ? 'user' : 'peer'}`;
      const profile = profileFor(role);
      appendTranscriptAvatar(row, profile);
      const content = document.createElement('div');
      content.className = 'transcript-content';
      const bubble = document.createElement('div');
      bubble.className = 'transcript-bubble';
      bubble.textContent = turn.text;
      if (role === 'assistant' && turn.interrupted) bubble.append(document.createTextNode('\u2026'));
      content.append(bubble);
      row.append(content);
      lyricTrack.append(row);
      if (index === turns.length - 1) currentTurn = row;
    });
    window.requestAnimationFrame(() => {
      currentTurn?.scrollIntoView({
        block: 'center',
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      });
    });
  };
  const appendTranscriptAvatar = (row, profile) => {
    const avatar = document.createElement('div');
    avatar.className = 'transcript-avatar'; avatar.dataset.hasAvatar = 'false';
    const image = document.createElement('img'); image.alt = '';
    const fallback = document.createElement('span'); fallback.textContent = initialsFor(profile.name);
    avatar.append(image, fallback);
    if (profile.avatarUrl) {
      image.src = profile.avatarUrl;
      image.onload = () => { avatar.dataset.hasAvatar = 'true'; };
      image.onerror = () => { image.removeAttribute('src'); avatar.dataset.hasAvatar = 'false'; };
    }
    row.append(avatar);
  };
  const renderFullTranscript = () => {
    fullTranscript.replaceChildren();
    const visibleTurns = transcriptTurns.filter(turn => turn.text);
    if (!visibleTurns.length) {
      const empty = document.createElement('p');
      empty.className = 'full-transcript-empty'; empty.textContent = '等待通话中的第一段转写';
      fullTranscript.append(empty);
      return;
    }
    visibleTurns.forEach(turn => {
      const role = turn.role === 'user' ? 'user' : 'assistant';
      const row = document.createElement('article');
      row.className = `transcript-turn ${role === 'user' ? 'user' : 'peer'}`;
      const profile = profileFor(role); appendTranscriptAvatar(row, profile);
      const content = document.createElement('div'); content.className = 'transcript-content';
      const bubble = document.createElement('div'); bubble.className = 'transcript-bubble'; bubble.textContent = turn.text;
      if (role === 'assistant' && turn.interrupted) bubble.append(document.createTextNode('\u2026'));
      content.append(bubble); row.append(content); fullTranscript.append(row);
    });
    if (callScreen.dataset.view === 'transcript') {
      window.requestAnimationFrame(() => { fullTranscript.scrollTop = fullTranscript.scrollHeight; });
    }
  };
  const replaceTranscriptTurns = (turns) => {
    transcriptTurns = Array.isArray(turns)
      ? turns
          .filter(turn => turn && (turn.role === 'user' || turn.role === 'assistant') && String(turn.text || '').trim())
          .map(turn => ({ role: turn.role, text: String(turn.text || ''), finished: Boolean(turn.finalized), interrupted: Boolean(turn.interrupted) }))
      : [];
    renderTranscript();
    renderFullTranscript();
  };
  const setTranscriptView = (visible) => {
    callScreen.dataset.view = visible ? 'transcript' : 'call';
    transcriptView.hidden = !visible;
    if (visible) {
      renderFullTranscript(); transcriptView.focus({ preventScroll: true });
    } else {
      transcriptOpen.focus({ preventScroll: true });
    }
  };
  const openTranscriptView = () => {
    if (callScreen.dataset.view === 'transcript') return;
    history.pushState({ ...(history.state || {}), voiceCallTranscript: true }, '', `${location.pathname}${location.search}#transcript`);
    setTranscriptView(true);
  };
  const closeTranscriptView = () => {
    if (history.state?.voiceCallTranscript) { history.back(); return; }
    setTranscriptView(false);
  };
  const downsample = (input, inputRate, outputRate) => {
    if (inputRate === outputRate) return input;
    const ratio = inputRate / outputRate;
    const length = Math.round(input.length / ratio);
    const output = new Float32Array(length);
    let offset = 0;
    for (let i = 0; i < length; i++) {
      const next = Math.round((i + 1) * ratio);
      let total = 0, count = 0;
      for (; offset < next && offset < input.length; offset++) { total += input[offset]; count++; }
      output[i] = count ? total / count : 0;
    }
    return output;
  };
  const pcm16 = (input) => {
    const output = new ArrayBuffer(input.length * 2);
    const view = new DataView(output);
    for (let i = 0; i < input.length; i++) {
      const sample = Math.max(-1, Math.min(1, input[i]));
      view.setInt16(i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return output;
  };
  const base64Bytes = (value) => {
    const raw = atob(value || '');
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes;
  };
  const playPcm = (value) => {
    if (!audioContext) return;
    const bytes = base64Bytes(value);
    const samples = new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
    const buffer = audioContext.createBuffer(1, samples.length, 24000);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
    const source = audioContext.createBufferSource();
    source.buffer = buffer; source.connect(audioContext.destination);
    activeSources.add(source);
    source.onended = () => {
      activeSources.delete(source);
      notifyPlaybackFinished();
    };
    const now = audioContext.currentTime;
    nextPlayAt = Math.max(nextPlayAt, now);
    source.start(nextPlayAt); nextPlayAt += buffer.duration;
  };
  const stopAudioPlayback = () => {
    playbackDrainRequested = false;
    if (playbackDrainTimer) { clearTimeout(playbackDrainTimer); playbackDrainTimer = null; }
    for (const source of activeSources) { try { source.stop(); } catch (_) {} }
    activeSources.clear();
    nextPlayAt = audioContext ? audioContext.currentTime : 0;
  };
  const notifyPlaybackFinished = () => {
    if (!playbackDrainRequested || !socket || socket.readyState !== WebSocket.OPEN) return;
    const remaining = audioContext ? nextPlayAt - audioContext.currentTime : 0;
    if (activeSources.size || remaining > 0.03) {
      if (playbackDrainTimer) clearTimeout(playbackDrainTimer);
      playbackDrainTimer = setTimeout(() => {
        playbackDrainTimer = null;
        notifyPlaybackFinished();
      }, Math.max(30, Math.ceil(remaining * 1000) + 30));
      return;
    }
    playbackDrainRequested = false;
    send({ type: 'playback_finished' });
  };
  const send = (payload) => { if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload)); };
  const stopAudio = () => {
    if (processor) { processor.disconnect(); processor.onaudioprocess = null; processor = null; }
    if (captureNode) { captureNode.port.onmessage = null; captureNode.disconnect(); captureNode = null; }
    if (captureSink) { captureSink.disconnect(); captureSink = null; }
    if (workletUrl) { URL.revokeObjectURL(workletUrl); workletUrl = null; }
    if (inputStream) { inputStream.getTracks().forEach(track => track.stop()); inputStream = null; }
    if (audioContext) { audioContext.close(); audioContext = null; }
    vadSpeaking = false; vadSilenceMs = 0; captureChunks = []; captureSamples = 0;
    captureAttached = false; sourceNode = null;
    muted = false; muteButton.disabled = true;
    muteButton.setAttribute('aria-pressed', 'false');
    muteButton.setAttribute('aria-label', '静音');
    muteLabel.textContent = '静音';
  };
  const setMuted = (value) => {
    muted = Boolean(value);
    if (inputStream) inputStream.getAudioTracks().forEach(track => { track.enabled = !muted; });
    muteButton.setAttribute('aria-pressed', muted ? 'true' : 'false');
    muteButton.setAttribute('aria-label', muted ? '取消静音' : '静音');
    muteLabel.textContent = muted ? '取消静音' : '静音';
  };
  const closeCall = () => {
    manualHangup = true;
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    flushCapture();
    if (vadSpeaking) send({ type: 'event', event: { type: 'input_audio_buffer.commit', event_id: `browser_commit_${Date.now()}` } });
    send({ type: 'hangup' });
    if (socket) socket.close();
    stopAudioPlayback();
    stopAudio();
    gatewayReady = false; sessionEstablished = false; startRequested = false; startSent = false;
    setConnected(false); stopDuration();
    startButton.hidden = true; startButton.disabled = true;
    hangupButton.disabled = true; muteButton.disabled = true;
    setStatus('通话已结束');
  };
  const flushCapture = () => {
    if (!captureSamples || !audioContext || !socket || socket.readyState !== WebSocket.OPEN) return;
    const joined = new Float32Array(captureSamples);
    let offset = 0;
    for (const chunk of captureChunks) { joined.set(chunk, offset); offset += chunk.length; }
    const data = downsample(joined, audioContext.sampleRate, 16000);
    send({ type: 'audio', audio: btoa(String.fromCharCode(...new Uint8Array(pcm16(data)))) });
    captureChunks = []; captureSamples = 0;
  };
  const consumeInput = (input) => {
    if (!audioContext || !socket || socket.readyState !== WebSocket.OPEN) return;
    let energy = 0;
    for (let i = 0; i < input.length; i++) energy += input[i] * input[i];
    const rms = Math.sqrt(energy / Math.max(1, input.length));
    const frameMs = input.length * 1000 / audioContext.sampleRate;
    let shouldCommit = false;
    if (rms >= 0.02) {
      vadSpeaking = true; vadSilenceMs = 0;
    } else if (vadSpeaking) {
      vadSilenceMs += frameMs;
      if (vadSilenceMs >= 450) {
        shouldCommit = true;
        vadSpeaking = false; vadSilenceMs = 0;
      }
    }
    captureChunks.push(new Float32Array(input)); captureSamples += input.length;
    if (captureSamples >= 2048 || shouldCommit) flushCapture();
    if (shouldCommit) send({ type: 'event', event: { type: 'input_audio_buffer.commit', event_id: `browser_commit_${Date.now()}` } });
  };
  const attachCapture = async (source) => {
    if (audioContext.audioWorklet && typeof AudioWorkletNode !== 'undefined') {
      const module = `class DailyLifePcmCapture extends AudioWorkletProcessor {
        process(inputs, outputs) {
          const input = inputs[0] && inputs[0][0];
          if (input && input.length) {
            const copy = new Float32Array(input);
            this.port.postMessage(copy.buffer, [copy.buffer]);
          }
          const output = outputs[0] && outputs[0][0];
          if (output) output.fill(0);
          return true;
        }
      }
      registerProcessor('daily-life-pcm-capture', DailyLifePcmCapture);`;
      try {
        workletUrl = URL.createObjectURL(new Blob([module], { type: 'application/javascript' }));
        await audioContext.audioWorklet.addModule(workletUrl);
        captureNode = new AudioWorkletNode(audioContext, 'daily-life-pcm-capture', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
        captureNode.port.onmessage = (event) => consumeInput(new Float32Array(event.data));
        captureSink = audioContext.createGain(); captureSink.gain.value = 0;
        source.connect(captureNode); captureNode.connect(captureSink); captureSink.connect(audioContext.destination);
        return;
      } catch (_) {
        if (captureNode) { captureNode.disconnect(); captureNode = null; }
        if (captureSink) { captureSink.disconnect(); captureSink = null; }
        if (workletUrl) { URL.revokeObjectURL(workletUrl); workletUrl = null; }
      }
    }
    processor = audioContext.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (event) => {
      consumeInput(event.inputBuffer.getChannelData(0));
      event.outputBuffer.getChannelData(0).fill(0);
    };
    source.connect(processor); processor.connect(audioContext.destination);
  };
  const handleUpstream = (event) => {
    const type = event.type || '';
    if (type === 'session.created') {
      sessionEstablished = true; terminalStatus = ''; setConnected(true); startDuration(); setStatus('已连接，可以说话');
    }
    else if (type === 'conversation.item.input_audio_transcription.started' || type === 'input_audio_buffer.speech_started' || type === 'speech_started') {
      stopAudioPlayback();
      send({ type: 'event', event: { type: 'response.cancel', event_id: `browser_cancel_${Date.now()}` } });
      setStatus('正在听你说');
    }
    else if (type === 'response.output_audio.delta') playPcm(event.delta);
    else if (type === 'response.output_audio.started') setStatus('对方正在说话');
    else if (type === 'response.output_audio.done') setStatus('已连接，可以说话');
    else if (type === 'error') {
      console.error(event);
      // session.create 期间的错误由网关在同一邀请内恢复，不能提前把它
      // 视作最终失败，否则浏览器会停止等待后续的可用会话。
      if (sessionEstablished) {
        terminalStatus = '语音服务返回错误，通话已结束';
        setStatus(terminalStatus);
      } else {
        setStatus('语音服务正在恢复');
      }
    }
  };
  const beginUpstream = () => {
    if (!gatewayReady || !socket || socket.readyState !== WebSocket.OPEN || startSent) return;
    startSent = true;
    setStatus('正在连接语音服务');
    send({ type: 'start' });
  };
  const scheduleReconnect = () => {
    if (manualHangup || sessionEstablished || reconnectTimer) return;
    const delay = Math.min(3000, 500 * Math.max(1, reconnectAttempt));
    setStatus(startRequested ? '正在恢复通话连接' : '正在准备通话');
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      openSocket();
    }, delay);
  };
  const openSocket = () => {
    if (manualHangup || (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN))) return;
    gatewayReady = false; startSent = false;
    reconnectAttempt += 1;
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/${encodeURIComponent(token)}`);
    socket.onopen = () => {
      setStatus(startRequested ? '正在连接语音服务' : '正在准备通话');
      hangupButton.disabled = !startRequested;
    };
    socket.onmessage = async (message) => {
      try {
        const payload = JSON.parse(message.data);
        if (payload.kind === 'upstream') handleUpstream(payload.event || {});
        else if (payload.kind === 'transcript') replaceTranscriptTurns(payload.turns);
        else if (payload.kind === 'gateway_ready') {
          gatewayReady = true;
          setStatus(startRequested ? '正在连接语音服务' : String(payload.message || '通话已准备，点击开始通话'));
          if (startRequested) beginUpstream();
        }
        else if (payload.kind === 'ready') {
          sessionEstablished = true; reconnectAttempt = 0;
          setConnected(true); startDuration();
          setStatus(String(payload.message || '已连接，可以说话'));
          if (!captureAttached && sourceNode) { await attachCapture(sourceNode); captureAttached = true; }
          muteButton.disabled = !inputStream;
        }
        else if (payload.kind === 'await_playback') {
          playbackDrainRequested = true;
          notifyPlaybackFinished();
        }
        else if (payload.kind === 'status') {
          const status = String(payload.message || '');
          if (/失效|过期|其他页面/.test(status)) terminalStatus = status;
          setStatus(status);
        }
      } catch (error) {
        terminalStatus = '音频初始化失败，请重新点击开始通话';
        setStatus(terminalStatus); console.error(error);
        if (socket) socket.close();
      }
    };
    socket.onerror = () => {
      // 由 close 统一安排预连恢复，避免一次网络抖动要求用户重新点击。
    };
    socket.onclose = () => {
      socket = null; gatewayReady = false; startSent = false;
      if (!manualHangup && !sessionEstablished && !terminalStatus) {
        scheduleReconnect();
        return;
      }
      const completedCall = manualHangup || sessionEstablished;
      stopAudioPlayback(); stopAudio(); setConnected(false); stopDuration();
      hangupButton.disabled = true;
      if (completedCall) { startButton.hidden = true; startButton.disabled = true; }
      else startButton.disabled = false;
      setStatus(terminalStatus || '通话已结束');
    };
  };
  const startCall = async () => {
    startButton.disabled = true; terminalStatus = ''; manualHangup = false; startRequested = true; setStatus('正在申请麦克风权限');
    try {
      if (!inputStream) {
        inputStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
        audioContext = new AudioContext();
        await audioContext.resume();
        sourceNode = audioContext.createMediaStreamSource(inputStream);
      }
      setMuted(false); muteButton.disabled = false; hangupButton.disabled = false;
      if (gatewayReady) beginUpstream();
      else { setStatus('正在准备通话'); openSocket(); }
    } catch (error) {
      stopAudio(); startButton.disabled = false; terminalStatus = '无法使用麦克风，请检查浏览器权限'; setStatus(terminalStatus); console.error(error);
    }
  };
  startButton.onclick = startCall;
  hangupButton.onclick = closeCall;
  muteButton.onclick = () => setMuted(!muted);
  transcriptOpen.onclick = openTranscriptView;
  window.addEventListener('popstate', () => setTranscriptView(Boolean(history.state?.voiceCallTranscript)));
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && callScreen.dataset.view === 'transcript') closeTranscriptView();
  });
  window.addEventListener('load', renderIcons, { once: true });
  setTranscriptView(Boolean(history.state?.voiceCallTranscript));
  renderTranscript(); renderFullTranscript(); renderIcons();
  loadVoiceWallpaper();
  openSocket();
})();
</script>
</body>
</html>'''


__all__ = ["VOICE_CALL_PAGE"]
