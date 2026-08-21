from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
from typing import Any

import aiohttp
try:
    from aiohttp import web
except (ImportError, AttributeError):  # 测试桩或精简运行环境可能只提供客户端 API。
    web = None  # type: ignore[assignment]

from astrbot.api import logger

from .web import VOICE_CALL_PAGE

VOLCENGINE_DUPLEX_ENDPOINT = (
    "wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue"
)


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
    .bubble { position: relative; padding: 11px 13px; border: 1px solid #f1b6cd; border-radius: 18px 18px 18px 7px; background: #fff8fb; box-shadow: 0 8px 18px rgb(43 15 34 / 18%); color: #553448; font-size: 16px; line-height: 1.58; text-align: left; white-space: pre-wrap; overflow-wrap: anywhere; }
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
                "[日常生活] 实时语音通话网关已启动："
                f"{getattr(settings, 'listen_host', '')}:{getattr(settings, 'listen_port', '')}"
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
                "active_calls": self.manager.active_count,
            }
        )

    async def _page(self, request: web.Request) -> web.Response:
        token = str(request.match_info.get("token") or "")
        invite = self.manager.pending_invite(token)
        if invite is None:
            raise web.HTTPGone(text="通话邀请已失效")
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
                    and end_reason
                    in {
                        "上游服务错误",
                        "上游返回错误",
                        "上游会话结束",
                        "浏览器断开",
                    }
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
        self.claimed = False
        self._function_arguments: dict[str, str] = {}
        self._function_names: dict[str, str] = {}
        self._handled_function_calls: set[str] = set()

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

    async def _start_initial_response(self) -> None:
        """有自然开场白时触发首轮语音；没有开场白就先听用户说话。"""

        await self._send_upstream(
            {"type": "response.create", "event_id": self._event_id()}
        )

    def _should_start_initial_response(self) -> bool:
        """只为主模型明确准备了自然开场的通话触发首轮生成。"""

        return bool(str(getattr(self.invite, "greeting", "") or "").strip())

    @staticmethod
    def _function_call_parts(event: dict[str, Any]) -> tuple[str, str, str, bool]:
        """从实时事件的不同变体中提取函数调用信息。"""

        event_type = str(event.get("type") or "")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
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
        if event_type == "conversation.item.created":
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
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
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": str(result or "工具没有返回结果。"),
                },
            }
        )
        # 函数结果需要在实时协议中开启新一轮模型生成。
        await self._send_upstream({"type": "response.create", "event_id": self._event_id()})

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
            await self._send_upstream({"type": "session.close", "event_id": self._event_id()})
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
                raise RuntimeError(
                    f"上游拒绝创建会话：{self._upstream_error_detail(event)}"
                )
            if event_type == "session.closed":
                raise RuntimeError("上游在会话初始化时结束")

    async def _connect_upstream_session(self) -> None:
        """在同一条浏览器连接内完成可用上游会话的建立与重试。"""

        session = self.gateway._session
        if session is None:
            raise RuntimeError("通话网关尚未就绪")
        endpoint = str(
            getattr(self.manager.settings, "endpoint_url", "")
            or VOLCENGINE_DUPLEX_ENDPOINT
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
                self.upstream = await session.ws_connect(
                    endpoint,
                    headers={"X-Api-Key": self.manager.api_key},
                    heartbeat=20,
                    receive_timeout=None,
                )
                await self._send_upstream(self.manager.session_create_payload(self.invite))
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
        raise RuntimeError(
            "实时语音会话初始化失败："
            f"{type(last_error).__name__ if last_error else '未知错误'}"
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
            if not self.manager.api_key or not self.manager.speaker_id:
                raise RuntimeError("实时通话缺少火山 API Key 或音色 ID")
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
            with contextlib.suppress(Exception):
                await self._send_upstream({"type": "session.close", "event_id": self._event_id()})
            if self.upstream is not None:
                await self.upstream.close()

    async def _browser_to_upstream(self) -> None:
        async for message in self.browser:
            if message.type == aiohttp.WSMsgType.BINARY:
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
                    await self._send_upstream({"type": "input_audio_buffer.append", "audio": audio})
            elif kind == "hangup":
                self.manager.mark_ending(self.invite, "用户结束通话")
                return
            elif kind == "playback_finished":
                self._audio_playback_finished.set()
            elif kind == "event" and isinstance(payload.get("event"), dict):
                event = dict(payload["event"])
                if str(event.get("type") or "") in _FORWARDED_BROWSER_EVENTS:
                    await self._send_upstream(event)
        if self.claimed and not self.invite.end_reason:
            self.manager.mark_ending(self.invite, "浏览器断开")

    async def _upstream_to_browser(self) -> None:
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
                await self._handle_function_call(event)
                if event.get("type") == "error":
                    self.manager.mark_ending(self.invite, "上游服务错误")
                    with contextlib.suppress(Exception):
                        await self.browser.send_json(
                            {
                                "kind": "status",
                                "message": "语音服务返回错误，请重新点击开始通话",
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


__all__ = ["VOLCENGINE_DUPLEX_ENDPOINT", "VoiceCallGateway"]
