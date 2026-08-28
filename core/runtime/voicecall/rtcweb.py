"""veRTC 浏览器通话页。

页面只拿到临时 RTC Token，不接触 AppKey/OpenAPI 密钥。开启视频时，浏览器
会把摄像头作为 RTC 视频轨道发布给 AI 音视频互动任务，页面只显示本地预览。
"""

from __future__ import annotations

import json
from html import escape


def _safe(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def rtc_page(profile: dict, sdk_url: str) -> str:
    return r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>实时音视频通话</title>
<style>
:root {
  color-scheme: dark;
  font-family: ui-rounded, "SF Pro Rounded", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  color: #f8f7fb;
  background: #111521;
}
* { box-sizing: border-box; }
button { font: inherit; }
body {
  margin: 0;
  min-block-size: 100dvb;
  block-size: 100dvb;
  overflow: hidden;
  display: grid;
  place-items: center;
  position: relative;
  isolation: isolate;
  background: #f8f1f4;
}
body[data-wallpaper="ready"], body[data-wallpaper="fallback"] { background: #111521; }
body::before {
  position: fixed;
  z-index: 0;
  inset: 0;
  content: "";
  pointer-events: none;
  background-image:
    linear-gradient(180deg, #0d101b80 0%, #1115228c 54%, #131724a6 100%),
    var(--rtc-wallpaper, none);
  background-position: center;
  background-size: cover;
  opacity: 0;
  transition: opacity 240ms ease;
}
body[data-wallpaper="ready"]::before { opacity: 1; }
.card {
  position: relative;
  z-index: 1;
  isolation: isolate;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  inline-size: 100%;
  block-size: 100%;
  min-block-size: 100dvb;
  overflow: hidden;
  background: #111521;
  visibility: hidden;
  opacity: 0;
  pointer-events: none;
  transition: opacity 180ms ease;
}
.card[data-wallpaper="ready"], .card[data-wallpaper="fallback"] {
  visibility: visible;
  opacity: 1;
  pointer-events: auto;
}
.card::before {
  position: absolute;
  z-index: 0;
  inset: 0;
  content: "";
  background-image:
    linear-gradient(180deg, #0d101b82 0%, #111522a0 48%, #111521df 100%),
    var(--rtc-wallpaper, none);
  background-position: center;
  background-size: cover;
  opacity: 0;
  transition: opacity 240ms ease;
}
.card[data-wallpaper="ready"]::before { opacity: 1; }
.call-content, .controls { position: relative; z-index: 1; }
.status { position: absolute; inline-size: 1px; block-size: 1px; overflow: hidden; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }
.call-content {
  display: grid;
  grid-template-rows: auto auto auto minmax(0, 1fr);
  align-content: start;
  justify-items: center;
  min-block-size: 0;
  overflow: hidden;
  padding: clamp(18px, 5dvb, 54px) 22px 10px;
  text-align: center;
}
.avatar {
  position: relative;
  display: grid;
  place-items: center;
  inline-size: min(42vw, 178px);
  aspect-ratio: 1;
  border: 1px solid #8290a8;
  border-radius: 50%;
  background: #303a50;
  box-shadow: 0 0 0 14px #ffffff0b, 0 18px 45px #05071166;
  overflow: visible;
}
.avatar::after {
  position: absolute;
  inset: 10px;
  content: "";
  border: 1px solid #ffffff38;
  border-radius: inherit;
}
.avatar img { position: relative; z-index: 1; inline-size: calc(100% - 14px); block-size: calc(100% - 14px); border-radius: 50%; object-fit: cover; }
.avatar span { position: relative; z-index: 1; color: #f6b7d2; font-size: 42px; font-weight: 750; }
.video-preview {
  position: relative;
  display: none;
  inline-size: min(100%, 286px);
  aspect-ratio: 16 / 10;
  margin-top: clamp(30px, 5dvb, 48px);
  overflow: hidden;
  border: 1px solid #8d9ab0;
  border-radius: 18px;
  background: #20283ab8;
  box-shadow: 0 12px 30px #04060e66;
  color: #c5ccda;
  place-items: center;
  font-size: 13px;
  touch-action: manipulation;
}
.video-preview.enabled { display: grid; }
.video-preview::after { position: absolute; inset: 0; content: ""; border: 1px solid #ffffff1c; border-radius: inherit; pointer-events: none; }
.video-preview video { display: block; inline-size: 100%; block-size: 100%; object-fit: cover; transform: scaleX(-1); }
.video-preview[data-facing="environment"] video { transform: none; }
.video-preview span { padding: 16px; }
.meter { display: flex; align-items: end; justify-content: center; gap: 5px; block-size: 25px; margin-top: 22px; opacity: .72; }
.meter i { inline-size: 4px; block-size: 7px; border-radius: 99px; background: #f2a9c8; }
.meter i:nth-child(2) { block-size: 14px; }
.meter i:nth-child(3) { block-size: 22px; }
.meter i:nth-child(4) { block-size: 14px; }
.meter i:nth-child(5) { block-size: 7px; }
.card[data-speaking="true"] .meter i, .card[data-listening="true"] .meter i { animation: voice-pulse 820ms ease-in-out infinite alternate; }
.card[data-speaking="true"] .meter i:nth-child(2), .card[data-listening="true"] .meter i:nth-child(4) { animation-delay: 140ms; }
.card[data-speaking="true"] .meter i:nth-child(3), .card[data-listening="true"] .meter i:first-child { animation-delay: 280ms; }
.name { margin: 12px 0 0; color: #fff; font-size: 24px; font-weight: 780; line-height: 1.35; text-shadow: 0 2px 10px #050711cc; }
.call-content > div:last-child { inline-size: 100%; }
.hint { max-inline-size: 100%; margin-top: 7px; color: #bbc4d5; font-size: 14px; line-height: 1.45; text-shadow: 0 2px 9px #080b14cc; }
.controls {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) 72px;
  align-items: center;
  gap: 14px;
  padding: 16px 22px max(24px, env(safe-area-inset-bottom));
}
.control-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  color: inherit;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: transform 150ms ease, background-color 150ms ease, opacity 150ms ease;
}
.control-button:active:not(:disabled) { transform: scale(.96); }
.control-button:disabled { cursor: not-allowed; opacity: .42; }
.control-button svg { inline-size: 21px; block-size: 21px; stroke-width: 2; }
.mute-button, .hangup-button {
  flex-direction: column;
  gap: 4px;
  inline-size: 64px;
  block-size: 64px;
  min-block-size: 64px;
  border-radius: 50%;
}
.mute-button { border: 1px solid #4c566b; background: #292f3fcc; color: #f7f7fb; }
.mute-button[aria-pressed="true"] { border-color: #f5b3ca; background: #513343; color: #ffd5e4; }
.hangup-button { background: #de5b67; color: #fff; }
.mute-button span, .hangup-button span { font-size: 11px; line-height: 1; }
.start-button { gap: 9px; min-inline-size: 0; min-block-size: 56px; border-radius: 16px; background: #f279a7; color: #2a1827; font-size: 16px; font-weight: 750; }
.start-button[hidden] { display: none; }
.start-button svg { inline-size: 21px; block-size: 21px; }
.control-button:focus-visible { outline: 3px solid #f8d877; outline-offset: 3px; }
@keyframes voice-pulse { from { transform: scaleY(.52); } to { transform: scaleY(1); } }
@media (min-width: 720px) {
  body { padding: 24px; }
  .card { inline-size: min(100%, 440px); block-size: min(880px, calc(100dvb - 48px)); min-block-size: 0; border: 1px solid #3d4658; border-radius: 26px; box-shadow: 0 24px 70px #02040b80; }
}
@media (max-height: 720px) {
  .call-content { padding-top: 12px; }
  .avatar { inline-size: min(35vw, 146px); }
  .video-preview { inline-size: min(100%, 246px); margin-top: 20px; }
  .meter { margin-top: 14px; }
  .name { margin-top: 8px; font-size: 21px; }
  .hint { margin-top: 4px; font-size: 13px; }
  .controls { padding-top: 10px; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition-duration: .01ms !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; } }
</style></head><body><main class="card" id="callScreen" data-wallpaper="loading" data-speaking="false" data-listening="false"><div class="status" id="status" aria-live="polite">点击开始后允许麦克风权限</div><section class="call-content"><div class="avatar" id="avatar"><span>·</span></div><div class="video-preview" id="localVideo" aria-label="摄像头预览，双击切换前后摄像头"><span>摄像头未开启</span></div><div class="meter" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div><div><div class="name" id="name">对方</div><div class="hint" id="hint">veRTC 双向语音</div></div></section><footer class="controls"><button class="control-button mute-button" id="mute" type="button" aria-label="静音" aria-pressed="false" disabled><i data-lucide="mic"></i><span>静音</span></button><button class="control-button start-button" id="start" type="button"><i data-lucide="phone-call"></i><span>开始通话</span></button><button class="control-button hangup-button" id="end" type="button" aria-label="结束通话" title="结束通话" disabled><i data-lucide="phone-off"></i><span>挂断</span></button></footer></main>
<script id="profile" type="application/json">__PROFILE__</script><script src="https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js" defer></script><script src="__SDK_URL__"></script><script>
(() => {
  const token = location.pathname.split('/').pop();
  const profile = JSON.parse(document.getElementById('profile').textContent || '{}');
  const status = document.getElementById('status'), hint = document.getElementById('hint'), name = document.getElementById('name');
  const start = document.getElementById('start'), end = document.getElementById('end'), mute = document.getElementById('mute'), avatar = document.getElementById('avatar'), localVideo = document.getElementById('localVideo');
  const callScreen = document.getElementById('callScreen');
  const bot = profile.assistant || {}; name.textContent = bot.name || '对方';
  const avatarFallback = avatar.querySelector('span'); if (avatarFallback) avatarFallback.textContent = Array.from(name.textContent.trim()).slice(0, 1).join('') || '·';
  if (bot.avatar_url) { const img = new Image(); img.alt=''; img.onload=()=>{avatar.replaceChildren(img)}; img.src=bot.avatar_url; }
  const renderIcons = () => { if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons(); };
  const resetStartButton = () => {
    start.hidden = false;
    start.replaceChildren();
    const icon = document.createElement('i'); icon.setAttribute('data-lucide', 'phone-call');
    const label = document.createElement('span'); label.textContent = '开始通话';
    start.append(icon, label);
    renderIcons();
  };
  const markEndedButton = () => {
    start.hidden = true;
    start.disabled = true;
  };
  const loadWallpaper = () => {
    // 与实时语音通话页使用同一组随机壁纸接口，保持两种通话体验一致。
    const endpoints = [
      'https://api.nycnm.cn/api/v2/bizhi1',
      'https://api.nycnm.cn/api/v2/bizhi2',
    ];
    const startIndex = Math.floor(Math.random() * endpoints.length);
    const ordered = endpoints.slice(startIndex).concat(endpoints.slice(0, startIndex));
    let index = 0;
    const fallback = window.setTimeout(() => { callScreen.dataset.wallpaper = 'fallback'; document.body.dataset.wallpaper = 'fallback'; }, 4500);
    const tryNext = () => {
      const endpoint = ordered[index++];
      if (!endpoint) { window.clearTimeout(fallback); callScreen.dataset.wallpaper = 'fallback'; document.body.dataset.wallpaper = 'fallback'; return; }
      const image = new Image();
      const source = `${endpoint}?voice_call=${Date.now().toString(36)}`;
      image.onload = () => {
        window.clearTimeout(fallback);
        const value = `url("${source}")`;
        callScreen.style.setProperty('--rtc-wallpaper', value); document.body.style.setProperty('--rtc-wallpaper', value);
        callScreen.dataset.wallpaper = 'ready'; document.body.dataset.wallpaper = 'ready';
      };
      image.onerror = tryNext; image.src = source;
    };
    tryNext();
  };
  renderIcons();
  window.addEventListener('load', renderIcons, { once: true });
  loadWallpaper();
  let engine = null, joined = false, stopping = false, videoEnabled = false, requestedVideo = false, hasGreeting = false, remoteAudioSeen = false, playbackBlocked = false, localSpeechSeen = false, remoteVoiceSeen = false, speechWaitStartedAt = 0, botUserId = '', audioRetryTimer = null, audioActivityTimer = null, audioResume = null, audioContext = null, muted = false, cameraFacing = 'user', switchingCamera = false;
  const sdk = () => window.VERTC || window.volcengine || window.RTC;
  const setStatus = text => {
    status.textContent = text;
    const value = String(text || '');
    callScreen.dataset.speaking = /Bot 正在回应|正在播放 Bot/.test(value) ? 'true' : 'false';
    callScreen.dataset.listening = /已收到你的声音|正在听/.test(value) ? 'true' : 'false';
  };
  const cleanup = async () => {
    if (!engine) return;
    const api = sdk() || {};
    try { if (joined && videoEnabled && api.MediaType) await engine.unpublishStream(api.MediaType.VIDEO); } catch (_) {}
    try { if (joined && api.MediaType) await engine.unpublishStream(api.MediaType.AUDIO); } catch (_) {}
    try { if (videoEnabled) await engine.stopVideoCapture(); } catch (_) {}
    try { if (joined) await engine.stopAudioCapture(); } catch (_) {}
    try { if (joined) await engine.leaveRoom(); } catch (_) {}
    try { if (api.destroyEngine) api.destroyEngine(engine); } catch (_) {}
    localVideo.replaceChildren(); localVideo.dataset.facing = 'user'; localVideo.classList.remove('enabled');
    if (audioRetryTimer) { clearTimeout(audioRetryTimer); audioRetryTimer = null; }
    if (audioActivityTimer) { clearInterval(audioActivityTimer); audioActivityTimer = null; }
    audioResume = null;
    if (audioContext) { try { await audioContext.close(); } catch (_) {} audioContext = null; }
    engine=null; joined=false; videoEnabled=false; botUserId=''; remoteAudioSeen=false; playbackBlocked=false; localSpeechSeen=false; remoteVoiceSeen=false; speechWaitStartedAt=0; muted=false; cameraFacing='user'; switchingCamera=false;
    mute.disabled = true; mute.setAttribute('aria-pressed', 'false'); mute.setAttribute('aria-label', '静音');
    const muteLabel = mute.querySelector('span'); if (muteLabel) muteLabel.textContent = '静音';
  };
  const finish = async (notify=true, finalStatus='通话已结束', finalHint='') => {
    if (stopping || (!engine && !joined)) return;
    stopping=true; setStatus('正在结束通话'); await cleanup();
    if (notify) { try { await fetch('/rtc/finish/'+encodeURIComponent(token), {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}', keepalive:true}); } catch (_) {} }
    end.disabled=true; markEndedButton(); setStatus(finalStatus); if (finalHint) hint.textContent = finalHint; stopping=false;
  };
  const startCall = async () => {
    if (engine && audioResume) {
      try { await audioResume(); playbackBlocked = false; audioResume = null; hint.textContent = 'veRTC Bot 音频已恢复'; start.textContent = '通话中'; start.disabled = true; } catch (_) { setStatus('请再次点击以播放 Bot 音频'); }
      return;
    }
    if (engine || stopping || start.hidden) return;
    requestedVideo=false; start.disabled=true; setStatus('正在准备通话');
    try {
      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (AudioContextCtor) { try { audioContext = new AudioContextCtor(); await audioContext.resume(); } catch (_) {} }
      const response = await fetch('/rtc/session/'+encodeURIComponent(token), {cache:'no-store'}); const info = await response.json(); if (!response.ok) throw new Error(info.error || '会话不可用');
      const api = sdk(); if (!api || !api.createEngine) throw new Error('RTC Web SDK 加载失败');
      requestedVideo = Boolean(info.video_enabled); videoEnabled = requestedVideo; hasGreeting = Boolean(info.has_greeting); botUserId = String(info.bot_user_id || ''); if (videoEnabled) { localVideo.classList.add('enabled'); localVideo.replaceChildren(); setStatus('正在申请麦克风和摄像头权限'); } else { setStatus('正在申请麦克风权限'); }
      engine=api.createEngine(info.app_id, {autoPlayPolicy:0});
      const updateVoiceStatus = () => {
        if (stopping || playbackBlocked || remoteVoiceSeen) return;
        if (localSpeechSeen) {
          setStatus('已收到你的声音，等待 Bot 回应');
          hint.textContent = videoEnabled ? 'veRTC 语音 + 摄像头视觉理解 · 正在等待 Bot 回复' : 'veRTC 双向语音 · 正在等待 Bot 回复';
        } else if (!hasGreeting) {
          setStatus('已连接，请先说一句');
          hint.textContent = videoEnabled ? 'veRTC 语音 + 摄像头视觉理解 · 请先说一句，Bot 会回应' : 'veRTC 双向语音 · 请先说一句，Bot 会回应';
        } else {
          setStatus('已连接，等待 Bot 音频');
          hint.textContent = videoEnabled ? 'veRTC 语音 + 摄像头视觉理解 · 等待 Bot 音频' : 'veRTC 双向语音 · 等待 Bot 音频';
        }
      };
      if (api.events.onLocalAudioPropertiesReport) engine.on(api.events.onLocalAudioPropertiesReport, reports => {
        const items = Array.isArray(reports) ? reports : [];
        if (items.some(item => Number(item && item.audioPropertiesInfo && item.audioPropertiesInfo.linearVolume || 0) >= 26)) {
          localSpeechSeen = true; if (!speechWaitStartedAt) speechWaitStartedAt = Date.now();
          if (!remoteVoiceSeen && !hasGreeting) updateVoiceStatus();
        }
      });
      if (api.events.onRemoteAudioPropertiesReport) engine.on(api.events.onRemoteAudioPropertiesReport, reports => {
        const items = Array.isArray(reports) ? reports : [];
        const botSpeaking = items.some(item => {
          const streamKey = item && item.streamKey || {};
          const userId = String(streamKey.userId || streamKey.uid || '');
          return userId === botUserId && Number(item && item.audioPropertiesInfo && item.audioPropertiesInfo.linearVolume || 0) >= 26;
        });
        if (botSpeaking) {
          remoteVoiceSeen = true;
          remoteAudioSeen = true;
          if (!playbackBlocked) {
            setStatus('Bot 正在回应');
            hint.textContent = videoEnabled ? 'veRTC 语音 + 摄像头视觉理解 · 正在播放 Bot 语音' : 'veRTC 正在播放 Bot 语音';
          }
        }
      });
      if (api.events.onAutoplayFailed) engine.on(api.events.onAutoplayFailed, event => {
        if (event && event.kind === 'audio') {
          const eventUserId = String(event && event.userId || '');
          if (eventUserId && eventUserId !== botUserId) return;
          const playbackUserId = eventUserId || botUserId;
          playbackBlocked = true;
          audioResume = async () => {
            if (!playbackUserId || !engine) throw new Error('远端音频用户未知');
            await engine.play(playbackUserId, api.MediaType.AUDIO);
            remoteAudioSeen = true;
          };
          start.textContent = '播放 Bot 音频'; start.disabled = false;
          setStatus('已连接，请点击“播放 Bot 音频”解除浏览器静音限制');
        }
      });
      if (api.events.onRemoteAudioFirstFrame) engine.on(api.events.onRemoteAudioFirstFrame, event => {
        const userId = String(event && (event.userId || event.uid || event.userID) || '');
        if (botUserId && userId === botUserId) {
          remoteAudioSeen = true;
          if (playbackBlocked && audioResume) {
            start.textContent = '播放 Bot 音频'; start.disabled = false;
            setStatus('已连接，请点击“播放 Bot 音频”打开扬声器');
            hint.textContent = '音频已收到，但浏览器阻止了自动播放';
            return;
          }
          audioResume = null; start.textContent = '通话中'; start.disabled = true;
          if (hasGreeting) {
            setStatus('已连接，等待 Bot 音频');
            hint.textContent = videoEnabled ? 'veRTC 语音 + 摄像头视觉理解 · 等待 Bot 音频' : 'veRTC 双向语音 · 等待 Bot 音频';
          } else {
            setStatus('已连接，请先说一句');
            hint.textContent = videoEnabled ? 'veRTC 语音 + 摄像头视觉理解 · 请先说一句，Bot 会回应' : 'veRTC 双向语音 · 请先说一句，Bot 会回应';
          }
        }
      });
      engine.on(api.events.onUserPublishStream, async (event, publishedMediaType) => {
        // 不同 veRTC Web SDK 版本分别使用 (uid, mediaType) 或事件对象。
        const userId = String(typeof event === 'string' || typeof event === 'number' ? event : (event && (event.userId || event.uid || event.userID) || ''));
        if (!userId || !botUserId || userId !== botUserId) return;
        const mediaType = typeof event === 'object' && event && (event.mediaType || event.type) ? (event.mediaType || event.type) : (publishedMediaType || api.MediaType.AUDIO);
        try { await engine.subscribeStream(userId, api.MediaType.AUDIO); } catch (_) {}
        if (mediaType !== api.MediaType.AUDIO) { try { await engine.subscribeStream(userId, mediaType); } catch (_) {} }
        try {
          await engine.play(userId, api.MediaType.AUDIO);
          if (!playbackBlocked) {
            audioResume = null;
            if (hasGreeting) setStatus('已连接，等待 Bot 音频'); else setStatus('已连接，请先说一句');
            hint.textContent = hasGreeting ? (videoEnabled ? 'veRTC 语音 + 摄像头视觉理解 · 等待 Bot 音频' : 'veRTC 双向语音 · 等待 Bot 音频') : (videoEnabled ? 'veRTC 语音 + 摄像头视觉理解 · 请先说一句，Bot 会回应' : 'veRTC 双向语音 · 请先说一句，Bot 会回应');
          }
        } catch (_) {
          audioResume = async () => { await engine.play(userId, api.MediaType.AUDIO); remoteAudioSeen = true; };
          start.textContent = '播放 Bot 音频'; start.disabled = false;
          setStatus('已连接，但 Bot 音频播放被浏览器阻止');
        }
      });
      engine.on(api.events.onConnectionStateChanged, event => { if (event && event.state === 1 && !stopping) void finish(false); });
      setStatus('正在连接通话房间');
      await engine.joinRoom(info.rtc_token, info.room_id, {userId:info.user_id}, {isAutoPublish:false,isAutoSubscribeAudio:true,isAutoSubscribeVideo:false}); joined=true;
      await engine.startAudioCapture();
      if (videoEnabled) { engine.setLocalVideoPlayer(0, {renderDom:localVideo, renderMode:0, playerId:'local-video'}); await engine.startVideoCapture('user'); cameraFacing='user'; localVideo.dataset.facing = 'user'; }
      await engine.publishStream(api.MediaType.AUDIO); if (videoEnabled) await engine.publishStream(api.MediaType.VIDEO);
      mute.disabled = false;
      try { if (engine.enableAudioPropertiesReport) engine.enableAudioPropertiesReport({interval:200, enableInBackground:true}); } catch (_) {}
      setStatus('正在接入 Bot');
      const startResponse = await fetch('/rtc/start/'+encodeURIComponent(token), {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}', cache:'no-store'});
      const startInfo = await startResponse.json().catch(() => ({}));
      if (!startResponse.ok) throw new Error(startInfo.error || 'Bot 任务启动失败');
      end.disabled=false; setStatus(hasGreeting ? (videoEnabled ? '已连接，摄像头画面已共享；等待 Bot 音频' : '已连接，等待 Bot 音频') : (videoEnabled ? '已连接，摄像头画面已共享；请先说一句' : '已连接，请先说一句')); hint.textContent=hasGreeting ? (videoEnabled ? 'veRTC 语音 + 摄像头视觉理解 · 等待 Bot 音频' : 'veRTC 双向语音 · 等待 Bot 音频') : (videoEnabled ? 'veRTC 语音 + 摄像头视觉理解 · 请先说一句，Bot 会回应' : 'veRTC 双向语音 · 请先说一句，Bot 会回应');
      const pollRtcStatus = async () => {
        if (stopping || !engine || remoteAudioSeen) return;
        try {
          const statusResponse = await fetch('/rtc/status/'+encodeURIComponent(token), {cache:'no-store'});
          const statusInfo = await statusResponse.json().catch(() => ({}));
          const upstreamStatus = String(statusInfo.status || '').toLowerCase();
          const upstreamError = String(statusInfo.error || statusInfo.end_reason || '').trim();
          if (upstreamError || ['failed','error','ended','end','stopped','stop'].includes(upstreamStatus)) {
            const errorType = String(statusInfo.error_type || '');
            const message = errorType === 'tts_resource_mismatch'
              ? 'AI 音视频方案未绑定当前复刻音色'
              : (upstreamError || '云端任务已结束');
            const finalHint = errorType === 'tts_resource_mismatch'
              ? '请在 AI 音视频互动方案中购买/复刻同版本音色并绑定应用后重试'
              : '请重新发送通话邀请后再试';
            await finish(false, '通话已结束', finalHint);
            if (errorType) setStatus('Bot 音频接入失败：' + message);
            return;
          }
        } catch (_) {}
        // 云端 ASR/LLM/TTS 是异步任务，错误可能在启动成功数十秒后才回调。
        // 持续轮询到通话结束，避免页面永远停在“已连接”而没有可解释状态。
        if (!stopping && !remoteAudioSeen) {
          if (localSpeechSeen && speechWaitStartedAt && Date.now() - speechWaitStartedAt > 20000) {
            setStatus('已收到你的声音，但云端暂未返回 Bot 音频');
            hint.textContent = '请查看插件日志中的 ASR、LLM、TTS 状态；若没有回调，检查公网 HTTPS 回调地址';
          }
          setTimeout(() => void pollRtcStatus(), 1000);
        }
      };
      setTimeout(() => void pollRtcStatus(), 500);
      audioActivityTimer = setInterval(() => {
        if (!stopping && engine && !remoteVoiceSeen && !playbackBlocked) updateVoiceStatus();
      }, 2500);
      const retryBotAudio = async (attempt=0) => {
        if (stopping || remoteAudioSeen || !engine || !joined || !botUserId) return;
        try { await engine.subscribeStream(botUserId, api.MediaType.AUDIO); await engine.play(botUserId, api.MediaType.AUDIO); audioResume = null; if (hasGreeting) setStatus('已连接，等待 Bot 音频'); return; } catch (_) {}
        if (attempt < 6) audioRetryTimer = setTimeout(() => void retryBotAudio(attempt + 1), 1500);
        else if (hasGreeting) setStatus('已进入通话房间，但云端 Bot 尚未发布音频；请检查 AI 音视频方案任务状态');
      };
      audioRetryTimer = setTimeout(() => void retryBotAudio(), 800);
    } catch (error) {
      await cleanup(); resetStartButton(); start.disabled=false; end.disabled=true;
      const message = error && error.message ? error.message : '请稍后重试';
      setStatus((requestedVideo ? '摄像头或麦克风连接失败：' : '连接失败：') + message);
    }
  };
  const switchCamera = async () => {
    if (!engine || !joined || !videoEnabled || switchingCamera || typeof engine.setVideoCaptureDevice !== 'function') return;
    switchingCamera = true;
    const target = cameraFacing === 'user' ? 'environment' : 'user';
    try {
      await engine.setVideoCaptureDevice(target);
      cameraFacing = target;
      localVideo.dataset.facing = target;
      hint.textContent = target === 'user' ? 'veRTC 摄像头已切换为前置' : 'veRTC 摄像头已切换为后置';
    } catch (error) {
      setStatus('摄像头切换失败：' + (error && error.message ? error.message : '当前设备不支持切换'));
    } finally {
      switchingCamera = false;
    }
  };
  localVideo.addEventListener('dblclick', event => { event.preventDefault(); void switchCamera(); });
  mute.addEventListener('click', async () => {
    if (!engine || !joined || typeof engine.muteAudioCapture !== 'function') return;
    try {
      muted = !muted;
      await engine.muteAudioCapture(muted);
      mute.setAttribute('aria-pressed', muted ? 'true' : 'false');
      mute.setAttribute('aria-label', muted ? '取消静音' : '静音');
      const muteLabel = mute.querySelector('span'); if (muteLabel) muteLabel.textContent = muted ? '取消静音' : '静音';
    } catch (_) { muted = !muted; }
  });
  start.addEventListener('click', () => void startCall()); end.addEventListener('click', () => void finish(true)); window.addEventListener('pagehide', () => { void finish(true); });
})();
</script></body></html>'''.replace("__PROFILE__", _safe(profile)).replace("__SDK_URL__", escape(str(sdk_url or ""), quote=True))


__all__ = ["rtc_page"]
