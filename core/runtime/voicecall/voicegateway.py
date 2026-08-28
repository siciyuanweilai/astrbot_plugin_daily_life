from __future__ import annotations

import asyncio
import base64
import contextlib
import gzip
import json
import struct
import time
import uuid
from typing import Any

import aiohttp
try:
    from aiohttp import web
except (ImportError, AttributeError):  # 测试桩或精简运行环境可能只提供客户端 API。
    web = None  # type: ignore[assignment]

from astrbot.api import logger

from .web import VOICE_CALL_PAGE
from .rtcweb import rtc_page

VOLCENGINE_DUPLEX_ENDPOINT = (
    "wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue"
)
VOLCENGINE_O20_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"

_O20_CONNECTION_EVENTS = frozenset({1, 2, 50, 51, 52})
_O20_FULL_CLIENT = 0x10
_O20_AUDIO_CLIENT = 0x20
_O20_FULL_SERVER = 0x90
_O20_AUDIO_SERVER = 0xB0
_O20_ERROR = 0xF0
_O20_WITH_EVENT = 0x04
_O20_JSON = 0x10
_O20_RAW = 0x00
# 该模型使用新版 JSON 全双工协议，但不接受手动 response.create；
# 连接建立后由服务端 VAD/用户音频自动驱动首轮响应。
_MODELS_WITHOUT_MANUAL_RESPONSE_CREATE = frozenset({"1.2.6.1"})


def voice_gateway_listener_address(settings: Any) -> str:
    """返回日志中可安全展示的本地监听地址。"""

    host = str(getattr(settings, "listen_host", "127.0.0.1") or "127.0.0.1").strip()
    port = str(getattr(settings, "listen_port", 6186) or 6186).strip()
    return f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"


def voice_gateway_start_error_detail(exc: BaseException, settings: Any) -> str:
    """把监听失败转换为可排查且不暴露配置秘密的日志文本。"""

    parts = [
        f"类型={type(exc).__name__}",
        f"监听={voice_gateway_listener_address(settings)}",
    ]
    errno = getattr(exc, "errno", None)
    if errno is not None:
        parts.append(f"errno={errno}")
    detail = " ".join(str(exc).split())[:240]
    if detail:
        parts.append(f"详情={detail}")
    return "；".join(parts)


def _o20_error_guidance(detail: str) -> str:
    normalized = str(detail or "").lower()
    speaker_error_markers = (
        "invalidspeaker",
        "speaker related resource",
        "resource id is mismatched",
        "speaker resource",
    )
    if any(marker in normalized for marker in speaker_error_markers):
        return (
            f"{detail}；当前模型使用火山 O2.0 协议，请在语音配置中改用已开通 "
            "O2.0 权限的官方音色 ID，并确认 API Key 对该音色有使用权限。"
        )
    return detail


def _encode_o20_frame(
    event_id: int,
    payload: bytes = b"",
    *,
    session_id: str = "",
    audio: bool = False,
) -> bytes:
    """按火山 O2.0 事件协议编码一帧客户端消息。"""

    message_type = _O20_AUDIO_CLIENT if audio else _O20_FULL_CLIENT
    serialization = _O20_RAW if audio else _O20_JSON
    frame = bytearray((0x11, message_type | _O20_WITH_EVENT, serialization, 0x00))
    frame.extend(struct.pack(">i", int(event_id)))
    if event_id not in _O20_CONNECTION_EVENTS:
        session_bytes = str(session_id or "").encode("utf-8")
        if not session_bytes:
            raise ValueError(f"O2.0 事件 {event_id} 缺少 session_id")
        frame.extend(struct.pack(">I", len(session_bytes)))
        frame.extend(session_bytes)
    frame.extend(struct.pack(">I", len(payload)))
    frame.extend(payload)
    return bytes(frame)


def _decode_o20_frame(frame: bytes) -> dict[str, Any]:
    """解析火山 O2.0 服务端帧，并保留压缩和错误元数据。"""

    if len(frame) < 4:
        raise ValueError("O2.0 响应帧长度不足")
    header_size = max(4, (frame[0] & 0x0F) * 4)
    if len(frame) < header_size:
        raise ValueError("O2.0 响应帧头不完整")
    message_type = frame[1] & 0xF0
    flags = frame[1] & 0x0F
    serialization = frame[2] & 0xF0
    compression = frame[2] & 0x0F
    offset = header_size
    error_code: int | None = None
    event_id: int | None = None
    session_id = ""
    connect_id = ""

    def read_uint32() -> int:
        nonlocal offset
        if offset + 4 > len(frame):
            raise ValueError("O2.0 响应帧字段不完整")
        value = struct.unpack(">I", frame[offset : offset + 4])[0]
        offset += 4
        return value

    if message_type == _O20_ERROR:
        error_code = read_uint32()
    contains_sequence = (flags & 0x01) == 0x01 or (flags & 0x03) == 0x03
    if contains_sequence and message_type in {_O20_AUDIO_CLIENT, _O20_AUDIO_SERVER}:
        read_uint32()
    if flags & _O20_WITH_EVENT:
        event_id = struct.unpack(">i", struct.pack(">I", read_uint32()))[0]
        if event_id not in _O20_CONNECTION_EVENTS:
            session_size = read_uint32()
            if offset + session_size > len(frame):
                raise ValueError("O2.0 响应帧 session_id 不完整")
            session_id = frame[offset : offset + session_size].decode(
                "utf-8", errors="replace"
            )
            offset += session_size
        if event_id in {50, 51, 52}:
            connect_size = read_uint32()
            if offset + connect_size > len(frame):
                raise ValueError("O2.0 响应帧 connect_id 不完整")
            connect_id = frame[offset : offset + connect_size].decode(
                "utf-8", errors="replace"
            )
            offset += connect_size
    payload_size = read_uint32()
    if offset + payload_size > len(frame):
        raise ValueError("O2.0 响应帧 payload 不完整")
    payload = frame[offset : offset + payload_size]
    if compression == 0x01:
        payload = gzip.decompress(payload)
    return {
        "message_type": message_type,
        "serialization": serialization,
        "compression": compression,
        "event_id": event_id,
        "session_id": session_id,
        "connect_id": connect_id,
        "error_code": error_code,
        "payload": payload,
    }


def _o20_payload_object(decoded: dict[str, Any]) -> dict[str, Any]:
    payload = decoded.get("payload")
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        return {}
    try:
        value = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _transcript_page_html(profile_json: str, turns_json: str) -> str:
    """返回独立只读转写页面；转写内容通过短轮询保持更新。"""

    return r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>通话转写</title>
  <style>
    :root { color-scheme: dark; font-family: ui-rounded, "SF Pro Rounded", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; background: #171b27; color: #f8f7fb; }
    * { box-sizing: border-box; }
    body { min-block-size: 100dvb; margin: 0; background: #171b27; }
    .page { inline-size: min(100%, 680px); min-block-size: 100dvb; margin-inline: auto; padding: max(20px, env(safe-area-inset-top)) 20px max(26px, env(safe-area-inset-bottom)); }
    header { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-block-size: 48px; }
    h1 { margin: 0; font-size: 19px; line-height: 1.35; letter-spacing: 0; }
    .status { margin: 0; color: #9da8bd; font-size: 13px; line-height: 1.4; }
    #transcript { display: grid; align-content: start; gap: 16px; min-block-size: calc(100dvb - 110px); padding-block: 28px; }
    .empty { margin: 56px auto; color: #8490a6; font-size: 15px; line-height: 1.55; text-align: center; }
    .turn { display: flex; align-items: end; gap: 9px; max-inline-size: 92%; }
    .turn.user { justify-self: end; flex-direction: row-reverse; }
    .turn.peer { justify-self: start; }
    .avatar { display: grid; place-items: center; flex: 0 0 38px; inline-size: 38px; block-size: 38px; overflow: hidden; border: 1px solid #65728b; border-radius: 50%; background: #374157; color: #f8f7fb; font-size: 14px; font-weight: 700; }
    .avatar img { display: none; inline-size: 100%; block-size: 100%; object-fit: cover; }
    .avatar[data-has-avatar="true"] img { display: block; }
    .avatar[data-has-avatar="true"] span { display: none; }
    .content { display: grid; min-inline-size: 0; }
    .bubble { position: relative; padding: 11px 13px; border: 1px solid #f1b6cd; border-radius: 18px 18px 18px 7px; background: #fff8fb; color: #553448; font-size: 16px; line-height: 1.58; text-align: left; white-space: pre-wrap; overflow-wrap: anywhere; }
    .turn.peer .bubble::after { position: absolute; inset-inline-start: -5px; inset-block-end: 3px; inline-size: 10px; block-size: 10px; content: ""; border-inline-start: 1px solid #f1b6cd; border-block-end: 1px solid #f1b6cd; background: #fff8fb; transform: rotate(45deg); }
    .turn.user .bubble { border-color: #b7d8b0; border-radius: 16px 8px 16px 16px; background: #d7efd1; color: #18311a; }
    @media (min-width: 720px) { body { background: #11151f; } .page { min-block-size: 100dvb; background: #171b27; box-shadow: 0 0 80px #02040b80; } }
  </style>
</head>
<body>
  <main class="page">
    <header><h1>通话转写</h1><p class="status" id="status">正在同步</p></header>
    <section id="transcript" aria-live="polite"></section>
  </main>
  <script id="voiceCallProfile" type="application/json">__VOICE_CALL_PROFILE__</script>
  <script id="voiceCallTurns" type="application/json">__VOICE_CALL_TURNS__</script>
  <script>
  (() => {
    const token = location.pathname.split('/').pop();
    const transcript = document.getElementById('transcript');
    const status = document.getElementById('status');
    const readJson = (id, fallback) => { try { return JSON.parse(document.getElementById(id)?.textContent || ''); } catch (_) { return fallback; } };
    const profiles = readJson('voiceCallProfile', {});
    let turns = readJson('voiceCallTurns', []);
    const profileFor = (role) => profiles[role] || {};
    const initial = (name) => Array.from(String(name || '').trim()).slice(0, 1).join('') || '·';
    const appendAvatar = (row, profile) => {
      const avatar = document.createElement('div');
      avatar.className = 'avatar'; avatar.dataset.hasAvatar = 'false';
      const image = document.createElement('img'); image.alt = '';
      const fallback = document.createElement('span'); fallback.textContent = initial(profile.name);
      avatar.append(image, fallback);
      const source = String(profile.avatar_url || '').trim();
      if (source) { image.src = source; image.onload = () => { avatar.dataset.hasAvatar = 'true'; }; image.onerror = () => { image.removeAttribute('src'); avatar.dataset.hasAvatar = 'false'; }; }
      row.append(avatar);
    };
    const render = () => {
      transcript.replaceChildren();
      const usable = Array.isArray(turns) ? turns.filter(turn => turn && (turn.role === 'user' || turn.role === 'assistant') && String(turn.text || '').trim()) : [];
      if (!usable.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = '等待通话中的第一段转写'; transcript.append(empty); return; }
      usable.forEach(turn => {
        const role = turn.role === 'user' ? 'user' : 'assistant';
        const row = document.createElement('article'); row.className = `turn ${role === 'user' ? 'user' : 'peer'}`;
        const profile = profileFor(role); appendAvatar(row, profile);
        const content = document.createElement('div'); content.className = 'content';
        const bubble = document.createElement('div'); bubble.className = 'bubble'; bubble.textContent = String(turn.text || '');
        if (role === 'assistant' && turn.interrupted) bubble.append(document.createTextNode('\u2026'));
        content.append(bubble); row.append(content); transcript.append(row);
      });
      document.documentElement.scrollTop = document.documentElement.scrollHeight;
    };
    const sync = async () => {
      try {
        const response = await fetch(`/transcript-data/${encodeURIComponent(token)}`, { cache: 'no-store' });
        if (response.status === 410) { status.textContent = '通话已结束'; return false; }
        if (!response.ok) throw new Error(String(response.status));
        const payload = await response.json();
        turns = payload.turns || []; render(); status.textContent = '实时同步中'; return true;
      } catch (_) { status.textContent = '等待连接恢复'; return true; }
    };
    render();
    window.setInterval(() => { void sync(); }, 1200);
    void sync();
  })();
  </script>
</body>
</html>'''.replace("__VOICE_CALL_PROFILE__", profile_json).replace(
        "__VOICE_CALL_TURNS__", turns_json
    )


def _web_module():
    if web is None:
        raise RuntimeError("实时语音通话需要安装 aiohttp 的 web 模块")
    return web


_FORWARDED_BROWSER_EVENTS = {
    "input_audio_buffer.commit",
    "input_audio_mute.commit",
    "input_audio_unmute.commit",
    "speech_text_buffer.commit",
    "speech_text_buffer.replacement.append",
    "speech_text_buffer.replacement.commit",
    "conversation.item.create",
    "conversation.item.update",
    "conversation.item.retrieve",
    "conversation.item.delete",
    "response.cancel",
    "session.update",
    "session.close",
}


class VoiceCallGateway:
    """为一次性邀请提供浏览器 WebSocket 与火山实时语音的桥接。"""

    def __init__(self, manager: Any):
        self.manager = manager
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._session: aiohttp.ClientSession | None = None
        self._server_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._site is not None

    @property
    def client_session(self) -> aiohttp.ClientSession | None:
        """复用网关连接池，避免短链请求为每次邀请重复创建连接。"""

        return self._session

    async def start(self) -> None:
        if self.running:
            return
        async with self._server_lock:
            if self.running:
                return
            web_api = _web_module()
            app = web_api.Application()
            app.add_routes(
                [
                    web_api.get("/healthz", self._health),
                    web_api.get("/call/{token}", self._page),
                    web_api.get("/rtc/session/{token}", self._rtc_session),
                    web_api.post("/rtc/start/{token}", self._rtc_start),
                    web_api.get("/rtc/status/{token}", self._rtc_status),
                    web_api.post("/rtc/finish/{token}", self._rtc_finish),
                    web_api.post("/rtc/callback", self._rtc_callback_static),
                    web_api.post("/rtc/callback/{token}", self._rtc_callback),
                    web_api.get("/transcript/{token}", self._transcript_page),
                    web_api.get("/transcript-data/{token}", self._transcript_data),
                    web_api.get("/ws/{token}", self._websocket),
                ]
            )
            self._runner = web_api.AppRunner(app, access_log=None)
            await self._runner.setup()
            settings = self.manager.settings
            self._site = web_api.TCPSite(
                self._runner,
                str(getattr(settings, "listen_host", "127.0.0.1") or "127.0.0.1"),
                int(getattr(settings, "listen_port", 6186) or 6186),
            )
            try:
                await self._site.start()
            except BaseException:
                self._site = None
                await self._runner.cleanup()
                self._runner = None
                raise
            try:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(
                        total=None, sock_connect=15, sock_read=None
                    )
                )
            except BaseException:
                self._site = None
                await self._runner.cleanup()
                self._runner = None
                raise
            logger.info(
                "[日常生活] 实时语音通话网关已启动：%s",
                voice_gateway_listener_address(settings),
            )

    async def close(self) -> None:
        async with self._server_lock:
            session, runner = self._session, self._runner
            self._session = None
            self._site = None
            self._runner = None
            if session is not None:
                await session.close()
            if runner is not None:
                await runner.cleanup()

    async def _health(self, _request: web.Request) -> web.Response:
        settings = self.manager.settings
        return web.json_response(
            {
                "ok": True,
                "service": "daily_life_voice_call",
                "enabled": bool(getattr(settings, "enabled", False)),
                "transports": ["websocket", "rtc"],
                "websocket_configured": bool(
                    getattr(self.manager, "api_key", "")
                    and getattr(self.manager, "speaker_id", "")
                ),
                "rtc_configured": not bool(
                    getattr(getattr(self.manager, "rtc", None), "rtc_configuration_error", lambda: "rtc")()
                ),
                "active_calls": self.manager.active_count,
            }
        )

    async def _page(self, request: web.Request) -> web.Response:
        token = str(request.match_info.get("token") or "")
        invite = self.manager.pending_invite(token)
        rtc_manager = getattr(self.manager, "rtc", None)
        rtc_invite = rtc_manager.pending_invite(token) if rtc_manager else None
        if invite is None and rtc_invite is None:
            raise web.HTTPGone(text="通话邀请已失效")
        if rtc_invite is not None:
            profile = rtc_manager.page_profile_payload(rtc_invite)
            return web.Response(
                text=rtc_page(
                    profile,
                    str(getattr(rtc_manager.settings, "rtc_sdk_url", "") or ""),
                ),
                content_type="text/html",
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )
        profile_json = json.dumps(
            self.manager.page_profile_payload(invite),
            ensure_ascii=False,
            separators=(",", ":"),
        ).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        return web.Response(
            text=VOICE_CALL_PAGE.replace("__VOICE_CALL_PROFILE__", profile_json),
            content_type="text/html",
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    async def _rtc_session(self, request: web.Request) -> web.Response:
        manager = getattr(self.manager, "rtc", None)
        token = str(request.match_info.get("token") or "")
        if manager is None:
            return web.json_response({"ok": False, "error": "AI 音视频通话未启用"}, status=404)
        try:
            payload = await manager.prepare_rtc_session(token)
        except (RuntimeError, ValueError) as exc:
            return web.json_response({"ok": False, "error": str(exc)[:500]}, status=409)
        except Exception as exc:
            logger.warning(f"[日常生活] veRTC 会话创建失败：{type(exc).__name__}")
            return web.json_response({"ok": False, "error": "实时通话服务暂时不可用"}, status=502)
        return web.json_response(payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    async def _rtc_start(self, request: web.Request) -> web.Response:
        manager = getattr(self.manager, "rtc", None)
        token = str(request.match_info.get("token") or "")
        if manager is None:
            return web.json_response({"ok": False, "error": "AI 音视频通话未启用"}, status=404)
        try:
            payload = await manager.start_rtc_session(token)
        except (RuntimeError, ValueError) as exc:
            return web.json_response({"ok": False, "error": str(exc)[:500]}, status=409)
        except Exception as exc:
            logger.warning(f"[日常生活] veRTC 任务启动失败：{type(exc).__name__}")
            return web.json_response({"ok": False, "error": "实时通话任务暂时不可用"}, status=502)
        return web.json_response(payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    async def _rtc_status(self, request: web.Request) -> web.Response:
        manager = getattr(self.manager, "rtc", None)
        token = str(request.match_info.get("token") or "")
        payload = manager.rtc_status_payload(token) if manager else None
        if payload is None:
            return web.json_response({"ok": False, "error": "通话不存在"}, status=404)
        return web.json_response(payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    async def _rtc_finish(self, request: web.Request) -> web.Response:
        manager = getattr(self.manager, "rtc", None)
        token = str(request.match_info.get("token") or "")
        invite = await manager.finish_rtc_session(token) if manager else None
        if invite is None:
            return web.json_response({"ok": False, "error": "通话不存在"}, status=404)
        return web.json_response({"ok": True})

    async def _rtc_callback(self, request: web.Request) -> web.Response:
        manager = getattr(self.manager, "rtc", None)
        token = str(request.match_info.get("token") or "")
        if manager is None:
            return web.Response(status=404, text="rtc unavailable")
        try:
            raw = await request.read()
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = raw
            await manager.handle_rtc_callback(token, payload, headers=dict(request.headers), raw_body=raw)
        except PermissionError:
            return web.Response(status=403, text="invalid signature")
        except LookupError:
            return web.Response(status=404, text="unknown call")
        except Exception as exc:
            logger.warning("[日常生活] veRTC 回调处理失败：%s；原因=%s", type(exc).__name__, str(exc)[:240])
            return web.Response(status=500, text="callback failed")
        return web.Response(text="ok")

    async def _rtc_callback_static(self, request: web.Request) -> web.Response:
        manager = getattr(self.manager, "rtc", None)
        if manager is None:
            return web.Response(status=404, text="rtc unavailable")
        try:
            raw = await request.read()
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = raw
            await manager.handle_rtc_callback_static(payload, headers=dict(request.headers), raw_body=raw)
        except PermissionError:
            return web.Response(status=403, text="invalid signature")
        except LookupError:
            return web.Response(status=404, text="unknown call")
        except Exception as exc:
            logger.warning("[日常生活] veRTC 固定回调处理失败：%s；原因=%s", type(exc).__name__, str(exc)[:240])
            return web.Response(status=500, text="callback failed")
        return web.Response(text="ok")

    async def _transcript_page(self, request: web.Request) -> web.Response:
        """提供独立只读的通话转写页，不领取或中断正在进行的通话。"""

        token = str(request.match_info.get("token") or "")
        invite = self.manager.transcript_invite(token)
        if invite is None:
            raise web.HTTPGone(text="通话转写已不可查看")
        profile_json = json.dumps(
            self.manager.page_profile_payload(invite),
            ensure_ascii=False,
            separators=(",", ":"),
        ).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        turns_json = json.dumps(
            self.manager.transcript_payload(invite),
            ensure_ascii=False,
            separators=(",", ":"),
        ).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        return web.Response(
            text=_transcript_page_html(profile_json, turns_json),
            content_type="text/html",
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    async def _transcript_data(self, request: web.Request) -> web.Response:
        token = str(request.match_info.get("token") or "")
        invite = self.manager.transcript_invite(token)
        if invite is None:
            raise web.HTTPGone(text="通话转写已不可查看")
        return web.json_response(
            {"turns": self.manager.transcript_payload(invite)},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        websocket = web.WebSocketResponse(heartbeat=20)
        await websocket.prepare(request)
        token = str(request.match_info.get("token") or "")
        invite = self.manager.pending_invite(token)
        if invite is None:
            await websocket.send_json({"kind": "status", "message": "通话邀请已失效"})
            await websocket.close(code=1008, message=b"expired invite")
            return websocket
        bridge = _VoiceCallBridge(self, websocket, invite, token)
        self.manager.attach_bridge(invite, bridge)
        end_reason = "通话结束"
        end_state = "ended"
        retryable_failure = False
        try:
            await bridge.run()
        except asyncio.CancelledError:
            end_reason = "网关任务取消"
            end_state = "cancelled"
            raise
        except Exception as exc:
            end_reason = f"通话服务异常：{type(exc).__name__}"
            end_state = "failed"
            retryable_failure = bridge.claimed and invite.active_at <= 0
            logger.warning(
                "[日常生活] 实时语音通话结束："
                f"异常类型={type(exc).__name__}，详情={str(exc)[:240]}"
            )
            with contextlib.suppress(Exception):
                await websocket.send_json(
                    {
                        "kind": "status",
                        "message": "语音服务暂时不可用，正在恢复连接",
                    }
                )
        finally:
            self.manager.detach_bridge(invite, bridge)
            # 预连只校验邀请；页面关闭或刷新不能消耗一次性邀请。
            if bridge.claimed:
                end_reason = str(invite.end_reason or end_reason).strip() or "通话结束"
                if end_reason in {"用户结束通话", "浏览器断开", "上游会话结束"}:
                    end_state = "ended"
                if (
                    not retryable_failure
                    and invite.active_at <= 0
                    and (
                        end_reason in {
                            "上游服务错误",
                            "上游返回错误",
                            "上游会话结束",
                            "浏览器断开",
                        }
                        or end_reason.startswith("上游服务错误：")
                        or end_reason.startswith("上游返回错误：")
                        or end_reason.startswith("实时语音会话初始化失败：")
                    )
                ):
                    retryable_failure = True
                if retryable_failure and self.manager.reset_invite_for_retry(
                    invite, reason=end_reason
                ):
                    with contextlib.suppress(Exception):
                        await websocket.send_json(
                            {
                                "kind": "status",
                                "message": "语音服务正在恢复连接",
                            }
                        )
                    with contextlib.suppress(Exception):
                        await websocket.close(
                            code=1011, message=b"retryable upstream failure"
                        )
                else:
                    await self.manager.finish_invite(
                        invite,
                        reason=end_reason,
                        state=end_state,
                    )
        return websocket


class _VoiceCallBridge:
    def __init__(
        self,
        gateway: VoiceCallGateway,
        browser: web.WebSocketResponse,
        invite: Any,
        token: str,
    ):
        self.gateway = gateway
        self.browser = browser
        self.invite = invite
        self.token = token
        self.upstream: aiohttp.ClientWebSocketResponse | None = None
        self._write_lock = asyncio.Lock()
        self._event_counter = 0
        self._last_user_activity = time.monotonic()
        self._browser_started = asyncio.Event()
        self._hangup_requested = asyncio.Event()
        self._response_finished = asyncio.Event()
        self._audio_playback_finished = asyncio.Event()
        self._hangup_reason = ""
        # 保留上游最后一次 error 的可读原因，避免多次重试后只剩泛化异常。
        self._last_upstream_error = ""
        self.claimed = False
        self._function_arguments: dict[str, str] = {}
        self._function_names: dict[str, str] = {}
        self._handled_function_calls: set[str] = set()
        self._tool_tasks: set[asyncio.Task[Any]] = set()

    @property
    def manager(self):
        return self.gateway.manager

    def request_hangup(self, reason: str = "Bot结束通话") -> None:
        """由内置通话工具请求结束当前浏览器和上游连接。"""

        if self.invite.ended_at:
            return
        self._hangup_reason = str(reason or "Bot结束通话").strip()[:160] or "Bot结束通话"
        self.manager.mark_ending(self.invite, self._hangup_reason)
        self._hangup_requested.set()

    def _event_id(self) -> str:
        self._event_counter += 1
        return f"event_call_{self._event_counter}"

    def _update_response_lifecycle(self, event: dict[str, Any]) -> None:
        """按响应轮次维护完成状态，避免复用上一轮的已完成事件。"""

        event_type = str(event.get("type") or "")
        if event_type in {
            "response.created",
            "response.started",
            "response.output_audio.started",
            "response.output_text.delta",
            "response.audio_transcript.delta",
        }:
            self._response_finished.clear()
        elif event_type in {"response.done", "response.completed"}:
            self._response_finished.set()

    async def _send_upstream(self, payload: dict[str, Any]) -> None:
        if self.upstream is None or self.upstream.closed:
            return
        async with self._write_lock:
            await self.upstream.send_str(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )

    async def _send_o20_event(
        self,
        event_id: int,
        payload: bytes | dict[str, Any] = b"",
        *,
        audio: bool = False,
        with_session: bool = True,
    ) -> None:
        if self.upstream is None or self.upstream.closed:
            return
        if isinstance(payload, dict):
            payload = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        session_id = self.invite.token_id if with_session else ""
        frame = _encode_o20_frame(
            event_id,
            bytes(payload),
            session_id=session_id,
            audio=audio,
        )
        async with self._write_lock:
            await self.upstream.send_bytes(frame)

    async def _close_upstream_session(self) -> None:
        if bool(getattr(self.manager, "uses_o20_protocol", False)):
            await self._send_o20_event(102, {})
            return
        await self._send_upstream(
            {"type": "session.close", "event_id": self._event_id()}
        )

    async def _start_initial_response(self) -> None:
        """有自然开场白时触发首轮语音；没有开场白就先听用户说话。"""

        if not self._should_start_initial_response():
            return
        greeting = str(getattr(self.invite, "greeting", "") or "").strip()
        if bool(getattr(self.manager, "uses_o20_protocol", False)):
            # O2.0 使用专门的 SayHello 事件播报开场，不能把开场白伪装成
            # 用户的 ChatTextQuery，否则会改变对话角色并生成额外回答。
            await self._send_o20_event(300, {"content": greeting})
            return
        await self._send_upstream(
            {"type": "response.create", "event_id": self._event_id()}
        )

    def _should_start_initial_response(self) -> bool:
        """只为主模型明确准备了自然开场的通话触发首轮生成。"""

        if not bool(str(getattr(self.invite, "greeting", "") or "").strip()):
            return False
        if bool(getattr(self.manager, "uses_o20_protocol", False)):
            return True
        model = str(
            getattr(getattr(self.manager, "settings", None), "model", "") or ""
        ).strip()
        return model not in _MODELS_WITHOUT_MANUAL_RESPONSE_CREATE

    @staticmethod
    def _function_call_item(event: dict[str, Any]) -> dict[str, Any]:
        """兼容实时接口将函数调用放在 item 或 items 中的事件格式。"""

        item = event.get("item")
        if isinstance(item, dict):
            return item
        items = event.get("items")
        if isinstance(items, list):
            for candidate in items:
                if isinstance(candidate, dict) and candidate.get("type") == "function_call":
                    return candidate
            for candidate in items:
                if isinstance(candidate, dict):
                    return candidate
        return {}

    @staticmethod
    def _is_function_call_completion(event: dict[str, Any]) -> bool:
        """判断事件是否可能触发耗时工具执行。"""

        event_type = str(event.get("type") or "")
        if event_type.endswith(".delta"):
            return False
        return event_type.startswith("response.function_call_arguments.") or event_type in {
            "response.function_call.done",
            "response.output_item.done",
        }

    def _schedule_function_call(self, event: dict[str, Any]) -> None:
        """把工具执行移出上游接收循环，避免图片/搜索阻塞音频事件。"""

        task = asyncio.create_task(self._handle_function_call(event))
        self._tool_tasks.add(task)

        def finish(done: asyncio.Task[Any]) -> None:
            self._tool_tasks.discard(done)
            if done.cancelled():
                return
            with contextlib.suppress(asyncio.CancelledError):
                error = done.exception()
                if error is not None:
                    logger.warning(
                        "[日常生活] 实时语音工具任务失败：类型=%s；详情=%s",
                        type(error).__name__,
                        str(error)[:240],
                    )

        task.add_done_callback(finish)

    @staticmethod
    def _function_call_parts(event: dict[str, Any]) -> tuple[str, str, str, bool]:
        """从实时事件的不同变体中提取函数调用信息。"""

        event_type = str(event.get("type") or "")
        item = _VoiceCallBridge._function_call_item(event)
        function = event.get("function") if isinstance(event.get("function"), dict) else {}
        call_id = str(
            event.get("call_id")
            or item.get("call_id")
            or function.get("call_id")
            or event.get("id")
            or item.get("id")
            or ""
        ).strip()
        name = str(
            event.get("name")
            or item.get("name")
            or function.get("name")
            or ""
        ).strip()
        arguments = event.get("arguments")
        if arguments is None:
            arguments = event.get("function_call_arguments")
        if arguments is None:
            # 实时接口通常会在 response.function_call_arguments.delta 事件中
            # 使用 delta 字段承载一段函数参数。
            arguments = event.get("delta")
        if arguments is None:
            arguments = event.get("arguments_delta")
        if arguments is None:
            arguments = item.get("arguments")
        if arguments is None:
            arguments = function.get("arguments")
        if isinstance(arguments, (dict, list)):
            arguments = json.dumps(arguments, ensure_ascii=False)
        arguments = str(arguments or "")
        is_done = event_type.endswith(".done") or event_type in {
            "response.function_call.done",
            "response.output_item.done",
        }
        if (
            isinstance(item, dict)
            and item.get("type") == "function_call"
            and event_type != "conversation.item.created"
        ):
            is_done = True
        return call_id, name, arguments, is_done

    async def _handle_function_call(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        items = event.get("items")
        if isinstance(items, list) and len(items) > 1:
            # 官方全双工事件允许一次携带多个函数项；逐项处理，确保每个
            # call_id 都得到独立的 role=tool 结果。
            for item in items:
                if isinstance(item, dict):
                    await self._handle_function_call(
                        {**event, "item": item, "items": [item]}
                    )
            return
        if event_type == "conversation.item.created":
            item = self._function_call_item(event)
            if item.get("type") != "function_call":
                return
            call_id, name, arguments, _is_done = self._function_call_parts(event)
            if call_id and name:
                self._function_names[call_id] = name
            if call_id and arguments:
                self._function_arguments[call_id] = arguments
            return
        if not (
            event_type.startswith("response.function_call_arguments.")
            or event_type in {"response.function_call.done", "response.output_item.done"}
        ):
            return
        call_id, name, arguments, is_done = self._function_call_parts(event)
        if not call_id:
            call_id = f"anonymous_{len(self._function_arguments) + 1}"
        if event_type.endswith(".delta"):
            self._function_arguments[call_id] = self._function_arguments.get(call_id, "") + arguments
            if name:
                self._function_names[call_id] = name
            return
        if not is_done or call_id in self._handled_function_calls:
            return
        self._handled_function_calls.add(call_id)
        name = name or self._function_names.pop(call_id, "")
        buffered_arguments = self._function_arguments.pop(call_id, "")
        raw_arguments = arguments or buffered_arguments or "{}"
        try:
            parsed_arguments = json.loads(raw_arguments)
            if not isinstance(parsed_arguments, dict):
                raise ValueError("工具参数必须是对象")
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed_arguments = {}
        if not name:
            result = "上游没有提供工具名称，无法执行。"
        else:
            bridge = self.manager.tool_bridge(self.invite)
            result = await bridge.call(name, parsed_arguments)
        if self._hangup_requested.is_set():
            # 结束控制不是普通工具结果：不再创建下一轮响应，避免挂断后模型继续说话。
            return
        await self._send_upstream(
            {
                "type": "conversation.item.create",
                "event_id": self._event_id(),
                "items": [
                    {
                        "type": "message",
                        "role": "tool",
                        "call_id": call_id,
                        "content": [
                            {
                                "type": "input_text",
                                "text": str(result or "工具没有返回结果。"),
                            }
                        ],
                    }
                ],
            }
        )
        # 全双工协议在收到 role=tool 的结果后会自动继续生成，不能再发送
        # response.create，否则第一次工具调用可能被打断或变成空响应。

    async def _hangup_watch(self) -> None:
        """等待 Bot 的结束请求，并有序关闭浏览器与上游会话。"""

        await self._hangup_requested.wait()
        reason = self._hangup_reason or "Bot结束通话"
        self.manager.mark_ending(self.invite, reason)
        # 模型应在调用控制前完成一句告别。等本轮响应收束，避免结束信号
        # 把刚发出的最后一句语音截断；异常上游没有完成事件时也不能无限等待。
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._response_finished.wait(), timeout=30)
        # response.done 只表示上游生成完成，浏览器仍可能有已排队的 PCM 音频。
        # 先等待浏览器确认播放队列排空，再关闭 WebSocket，避免截断最后一句告别。
        self._audio_playback_finished.clear()
        with contextlib.suppress(Exception):
            await self.browser.send_json({"kind": "await_playback"})
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._audio_playback_finished.wait(), timeout=30)
        with contextlib.suppress(Exception):
            await self.browser.send_json({"kind": "status", "message": "通话已结束"})
        with contextlib.suppress(Exception):
            await self._close_upstream_session()
        with contextlib.suppress(Exception):
            await self.browser.close(code=1000, message=b"bot hangup")

    @staticmethod
    def _upstream_error_detail(event: dict[str, Any]) -> str:
        error = event.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or error.get("code")
        else:
            detail = event.get("message") or error
        return str(detail or "上游未返回可用会话")[:240]

    @staticmethod
    def _o20_error_detail(decoded: dict[str, Any]) -> str:
        payload = _o20_payload_object(decoded)
        detail = payload.get("message") or payload.get("error") or payload.get("code")
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("code")
        if not detail:
            raw = decoded.get("payload")
            if isinstance(raw, (bytes, bytearray)):
                detail = bytes(raw).decode("utf-8", errors="replace").strip()
        code = decoded.get("error_code")
        if code is not None and detail:
            detail = f"{code}: {detail}"
        else:
            detail = str(detail or code or "上游未返回可用会话")
        return _o20_error_guidance(detail[:240])

    async def _receive_o20_frame(self, *, timeout: float | None = None) -> dict[str, Any]:
        if self.upstream is None:
            raise RuntimeError("实时语音上游连接未建立")
        while True:
            message = await self.upstream.receive(timeout=timeout)
            if message.type == aiohttp.WSMsgType.BINARY:
                return _decode_o20_frame(bytes(message.data))
            if message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            }:
                raise RuntimeError("O2.0 上游在会话初始化时断开")

    async def _wait_for_o20_event(self, expected_event: int, *, stage: str) -> None:
        while True:
            decoded = await self._receive_o20_frame(timeout=12)
            event_id = decoded.get("event_id")
            if decoded.get("message_type") == _O20_ERROR or event_id == 153:
                detail = self._o20_error_detail(decoded)
                self._last_upstream_error = detail
                logger.warning(
                    "[日常生活] O2.0 实时语音上游拒绝会话：阶段=%s；详情=%s",
                    stage,
                    detail,
                )
                self.manager.mark_ending(self.invite, f"上游服务错误：{detail}")
                raise RuntimeError(f"O2.0 上游拒绝会话：{detail}")
            if event_id == expected_event:
                return

    async def _wait_for_session_ready(self) -> None:
        """等待上游确认会话创建，再允许浏览器开始推送麦克风音频。"""

        if self.upstream is None:
            raise RuntimeError("实时语音上游连接未建立")
        while True:
            message = await self.upstream.receive(timeout=12)
            if message.type == aiohttp.WSMsgType.BINARY:
                continue
            if message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            }:
                raise RuntimeError("上游在会话初始化时断开")
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                event = json.loads(message.data)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            self.manager.record_event(self.invite, event)
            await self.browser.send_json({"kind": "upstream", "event": event})
            if event_type == "session.created":
                return
            if event_type == "error":
                detail = self._upstream_error_detail(event)
                self._last_upstream_error = detail
                logger.warning(
                    "[日常生活] 实时语音上游拒绝创建会话：阶段=初始化；详情=%s",
                    detail,
                )
                # record_event 会先写入通用原因；这里必须立即覆盖为完整原因，
                # 否则网关 finally 会把真正的上游错误吞掉，只显示“上游返回错误”。
                self.manager.mark_ending(self.invite, f"上游服务错误：{detail}")
                raise RuntimeError(f"上游拒绝创建会话：{detail}")
            if event_type == "session.closed":
                raise RuntimeError("上游在会话初始化时结束")

    async def _connect_upstream_session(self) -> None:
        """在同一条浏览器连接内完成可用上游会话的建立与重试。"""

        session = self.gateway._session
        if session is None:
            raise RuntimeError("通话网关尚未就绪")
        uses_o20 = bool(getattr(self.manager, "uses_o20_protocol", False))
        endpoint = (
            VOLCENGINE_O20_ENDPOINT
            if uses_o20
            else str(
                getattr(self.manager.settings, "endpoint_url", "")
                or VOLCENGINE_DUPLEX_ENDPOINT
            )
        )
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                await self.browser.send_json(
                    {
                        "kind": "status",
                        "message": (
                            "正在连接语音服务"
                            if attempt == 1
                            else f"正在恢复语音服务（{attempt}/3）"
                        ),
                    }
                )
                self.manager.mark_connecting(self.invite)
                headers = {"X-Api-Key": self.manager.api_key}
                if uses_o20:
                    headers.update(
                        {
                            "X-Api-Resource-Id": "volc.speech.dialog",
                            "X-Api-Connect-Id": str(uuid.uuid4()),
                        }
                    )
                self.upstream = await session.ws_connect(
                    endpoint,
                    headers=headers,
                    heartbeat=20,
                    receive_timeout=None,
                )
                if uses_o20:
                    logger.info(
                        "[日常生活] 实时语音创建 O2.0 上游会话：模型=%s；音色=%s",
                        str(getattr(self.manager.settings, "model", "") or "")[:80],
                        self.manager.speaker_id[:120],
                    )
                    await self._send_o20_event(1, {}, with_session=False)
                    await self._wait_for_o20_event(50, stage="建立连接")
                    await self._send_o20_event(
                        100,
                        self.manager.o20_session_payload(self.invite),
                    )
                    await self._wait_for_o20_event(150, stage="创建会话")
                    created_event = {"type": "session.created"}
                    self.manager.record_event(self.invite, created_event)
                    await self.browser.send_json(
                        {"kind": "upstream", "event": created_event}
                    )
                    await self.browser.send_json(
                        {"kind": "ready", "message": "已连接，可以说话"}
                    )
                    return
                payload = self.manager.session_create_payload(self.invite)
                session_payload = payload.get("session") if isinstance(payload, dict) else {}
                session_payload = session_payload if isinstance(session_payload, dict) else {}
                tools = session_payload.get("tools")
                tool_names = [
                    str(tool.get("name") or "")
                    for tool in tools
                    if isinstance(tool, dict) and str(tool.get("name") or "")
                ] if isinstance(tools, list) else []
                logger.info(
                    "[日常生活] 实时语音创建上游会话：模型=%s；工具数=%d；工具=%s",
                    str(session_payload.get("model") or "")[:80],
                    len(tool_names),
                    ",".join(tool_names)[:240] or "无",
                )
                await self._send_upstream(payload)
                await self._wait_for_session_ready()
                await self.browser.send_json(
                    {"kind": "ready", "message": "已连接，可以说话"}
                )
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[日常生活] 实时语音会话初始化失败："
                    f"第{attempt}/3次，类型={type(exc).__name__}，详情={str(exc)[:240]}"
                )
                if self.upstream is not None:
                    with contextlib.suppress(Exception):
                        await self.upstream.close()
                    self.upstream = None
                if attempt < 3:
                    await asyncio.sleep(0.8 * attempt)
        detail = str(last_error or "未知错误")[:240]
        raise RuntimeError(
            "实时语音会话初始化失败："
            f"{type(last_error).__name__ if last_error else '未知错误'}；详情={detail}"
        )

    async def run(self) -> None:
        settings = self.manager.settings
        browser_task = asyncio.create_task(self._browser_to_upstream())
        start_wait_task = asyncio.create_task(self._browser_started.wait())
        tasks: set[asyncio.Task[Any]] = {browser_task, start_wait_task}
        try:
            # 网页加载即预连网关；实际点击开始前不创建上游会话，也不占用麦克风。
            await self.browser.send_json(
                {"kind": "gateway_ready", "message": "通话已准备，点击开始通话"}
            )
            wait_timeout = max(1, int(self.invite.expires_at - time.time()))
            waiting, _pending = await asyncio.wait(
                {browser_task, start_wait_task},
                timeout=wait_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if start_wait_task not in waiting:
                if browser_task in waiting:
                    if not browser_task.cancelled():
                        error = browser_task.exception()
                        if error is not None:
                            raise error
                    return
                await self.browser.send_json(
                    {"kind": "status", "message": "通话邀请已过期"}
                )
                return
            tasks.discard(start_wait_task)
            start_wait_task.cancel()
            await asyncio.gather(start_wait_task, return_exceptions=True)
            configuration_error = self.manager.upstream_configuration_error()
            if configuration_error:
                raise RuntimeError(configuration_error)
            await self._connect_upstream_session()
            tasks.update(
                {
                asyncio.create_task(self._upstream_to_browser()),
                asyncio.create_task(self._idle_watch()),
                asyncio.create_task(self._hangup_watch()),
                }
            )
            if self._should_start_initial_response():
                await self._start_initial_response()
            timeout = max(
                30,
                int(getattr(settings, "max_duration_seconds", 1800) or 1800),
            )
            done, _pending = await asyncio.wait(
                tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                self.manager.mark_ending(self.invite, "达到最长通话时长")
                await self.browser.send_json({"kind": "status", "message": "已达到本次通话时长上限"})
            else:
                for task in done:
                    if task.cancelled():
                        continue
                    error = task.exception()
                    if error is not None:
                        raise error
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            tool_tasks = list(self._tool_tasks)
            for task in tool_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tool_tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await self._close_upstream_session()
            if self.upstream is not None:
                await self.upstream.close()

    async def _browser_to_upstream(self) -> None:
        async for message in self.browser:
            if message.type == aiohttp.WSMsgType.BINARY:
                if bool(getattr(self.manager, "uses_o20_protocol", False)):
                    await self._send_o20_event(200, bytes(message.data), audio=True)
                else:
                    await self._send_upstream(
                        {"type": "input_audio_buffer.append", "audio": base64.b64encode(message.data).decode("ascii")}
                    )
                continue
            if message.type != aiohttp.WSMsgType.TEXT:
                if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR}:
                    self.manager.mark_ending(self.invite, "浏览器断开")
                    return
                continue
            try:
                payload = json.loads(message.data)
            except (TypeError, ValueError):
                continue
            kind = str(payload.get("type") or "")
            if kind == "start":
                if not self.claimed:
                    claimed = self.manager.claim_invite(self.token)
                    if claimed is not self.invite:
                        await self.browser.send_json(
                            {"kind": "status", "message": "通话邀请已由其他页面开始"}
                        )
                        return
                    self.claimed = True
                    self._browser_started.set()
            elif kind == "audio":
                audio = str(payload.get("audio") or "")
                if audio:
                    if bool(getattr(self.manager, "uses_o20_protocol", False)):
                        try:
                            pcm = base64.b64decode(audio, validate=True)
                        except (ValueError, TypeError):
                            pcm = b""
                        if pcm:
                            await self._send_o20_event(200, pcm, audio=True)
                    else:
                        await self._send_upstream({"type": "input_audio_buffer.append", "audio": audio})
            elif kind == "hangup":
                self.manager.mark_ending(self.invite, "用户结束通话")
                return
            elif kind == "playback_finished":
                self._audio_playback_finished.set()
            elif kind == "event" and isinstance(payload.get("event"), dict):
                event = dict(payload["event"])
                if (
                    not bool(getattr(self.manager, "uses_o20_protocol", False))
                    and str(event.get("type") or "") in _FORWARDED_BROWSER_EVENTS
                ):
                    await self._send_upstream(event)
        if self.claimed and not self.invite.end_reason:
            self.manager.mark_ending(self.invite, "浏览器断开")

    async def _publish_o20_event(self, event: dict[str, Any]) -> None:
        self.manager.record_event(self.invite, event)
        self._update_response_lifecycle(event)
        if self.manager.is_transcript_event(event):
            await self.browser.send_json(
                {
                    "kind": "transcript",
                    "turns": self.manager.transcript_payload(self.invite),
                }
            )
        await self.browser.send_json({"kind": "upstream", "event": event})

    async def _o20_upstream_to_browser(self) -> None:
        """把 O2.0 二进制事件翻译为现有网页和历史记录使用的事件。"""

        assert self.upstream is not None
        async for message in self.upstream:
            if message.type != aiohttp.WSMsgType.BINARY:
                if message.type in {
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                }:
                    self.manager.mark_ending(self.invite, "O2.0 上游会话结束")
                    return
                continue
            try:
                decoded = _decode_o20_frame(bytes(message.data))
            except (OSError, ValueError, struct.error) as exc:
                logger.warning(
                    "[日常生活] O2.0 实时语音响应解析失败：%s",
                    str(exc)[:240],
                )
                continue
            event_id = decoded.get("event_id")
            if decoded.get("message_type") == _O20_ERROR or event_id == 153:
                detail = self._o20_error_detail(decoded)
                self._last_upstream_error = detail
                logger.warning(
                    "[日常生活] O2.0 实时语音上游返回错误：详情=%s",
                    detail,
                )
                self.manager.mark_ending(self.invite, f"上游服务错误：{detail}")
                await self._publish_o20_event(
                    {"type": "error", "error": {"message": detail}}
                )
                return

            payload = _o20_payload_object(decoded)
            if decoded.get("message_type") == _O20_AUDIO_SERVER or event_id == 352:
                raw_audio = decoded.get("payload")
                if isinstance(raw_audio, (bytes, bytearray)) and raw_audio:
                    await self.browser.send_json(
                        {
                            "kind": "upstream",
                            "event": {
                                "type": "response.output_audio.delta",
                                "delta": base64.b64encode(raw_audio).decode("ascii"),
                            },
                        }
                    )
                continue
            if event_id == 350:
                await self._publish_o20_event(
                    {"type": "response.output_audio.started"}
                )
                continue
            if event_id == 450:
                self._last_user_activity = time.monotonic()
                await self._publish_o20_event(
                    {
                        "type": "conversation.item.input_audio_transcription.started",
                        "item_id": str(payload.get("question_id") or ""),
                    }
                )
                continue
            if event_id == 451:
                results = payload.get("results")
                result = results[0] if isinstance(results, list) and results else {}
                if not isinstance(result, dict):
                    result = {}
                text = str(result.get("text") or "").strip()
                if not text:
                    continue
                self._last_user_activity = time.monotonic()
                item_id = str(
                    result.get("question_id") or payload.get("question_id") or ""
                )
                if bool(result.get("is_interim", True)):
                    event = {
                        "type": "conversation.item.input_audio_transcription.delta",
                        "item_id": item_id,
                        "delta": text,
                    }
                else:
                    event = {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": item_id,
                        "text": text,
                    }
                await self._publish_o20_event(event)
                continue
            if event_id == 459:
                await self._publish_o20_event(
                    {"type": "conversation.item.input_audio_transcription.completed"}
                )
                continue
            if event_id == 550:
                text = str(payload.get("content") or payload.get("text") or "")
                if text:
                    await self._publish_o20_event(
                        {
                            "type": "response.output_text.delta",
                            "response_id": str(payload.get("reply_id") or ""),
                            "delta": text,
                        }
                    )
                continue
            if event_id == 351:
                # TTSSentenceEnd 只表示当前音频句子结束，文本可能仍会继续
                # 通过 ChatResponse(550) 返回，不能在此结束整轮响应。
                continue
            if event_id == 359:
                await self._publish_o20_event({"type": "response.output_audio.done"})
                continue
            if event_id == 559:
                await self._publish_o20_event({"type": "response.output_text.done"})
                await self._publish_o20_event({"type": "response.done"})
                continue
            if event_id in {152, 600}:
                self.manager.mark_ending(self.invite, "O2.0 上游会话结束")
                return

    async def _upstream_to_browser(self) -> None:
        if bool(getattr(self.manager, "uses_o20_protocol", False)):
            await self._o20_upstream_to_browser()
            return
        assert self.upstream is not None
        async for message in self.upstream:
            if message.type == aiohttp.WSMsgType.BINARY:
                await self.browser.send_json(
                    {"kind": "upstream", "event": {"type": "response.output_audio.delta", "delta": base64.b64encode(message.data).decode("ascii")}}
                )
                continue
            if message.type != aiohttp.WSMsgType.TEXT:
                if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR}:
                    self.manager.mark_ending(self.invite, "上游会话结束")
                    return
                continue
            try:
                event = json.loads(message.data)
            except (TypeError, ValueError):
                continue
            if isinstance(event, dict):
                self.manager.record_event(self.invite, event)
                self._update_response_lifecycle(event)
                if self.manager.is_transcript_event(event):
                    await self.browser.send_json(
                        {
                            "kind": "transcript",
                            "turns": self.manager.transcript_payload(self.invite),
                        }
                    )
                if event.get("type") in {
                    "conversation.item.input_audio_transcription.started",
                    "conversation.item.input_audio_transcription.completed",
                }:
                    self._last_user_activity = time.monotonic()
                await self.browser.send_json({"kind": "upstream", "event": event})
                if self._is_function_call_completion(event):
                    self._schedule_function_call(event)
                else:
                    # 参数增量和 function item 建立事件必须按顺序更新缓冲区；
                    # 真正的工具执行在完成事件中异步调度。
                    await self._handle_function_call(event)
                if event.get("type") == "error":
                    detail = self._upstream_error_detail(event)
                    self._last_upstream_error = detail
                    logger.warning(
                        "[日常生活] 实时语音上游返回错误：阶段=通话中；详情=%s",
                        detail,
                    )
                    self.manager.mark_ending(self.invite, f"上游服务错误：{detail}")
                    with contextlib.suppress(Exception):
                        await self.browser.send_json(
                            {
                                "kind": "status",
                                "message": f"语音服务返回错误，正在恢复：{detail}",
                                "retryable": True,
                            }
                        )
                    return
                if event.get("type") == "session.closed":
                    self.manager.mark_ending(self.invite, "上游会话结束")
                    return

    async def _idle_watch(self) -> None:
        timeout = max(
            30,
            int(getattr(self.manager.settings, "idle_timeout_seconds", 90) or 90),
        )
        self._last_user_activity = time.monotonic()
        while True:
            await asyncio.sleep(min(5, timeout))
            if time.monotonic() - self._last_user_activity >= timeout:
                self.manager.mark_ending(self.invite, "长时间无交流")
                await self.browser.send_json({"kind": "status", "message": "长时间没有交流，通话已结束"})
                return


__all__ = [
    "VOLCENGINE_DUPLEX_ENDPOINT",
    "VOLCENGINE_O20_ENDPOINT",
    "VoiceCallGateway",
]
