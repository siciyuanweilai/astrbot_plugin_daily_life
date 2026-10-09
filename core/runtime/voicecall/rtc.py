"""veRTC AI 音视频互动方案的服务端适配。

这里不依赖火山 SDK：RTC Web SDK 只在浏览器使用，服务端负责生成 Token、
签名调用 OpenAPI，并接收字幕、状态和 Function Calling 回调。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import aiohttp


def _hmac(key: bytes | str, value: bytes | str) -> bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hmac.new(key, value, hashlib.sha256).digest()


class RtcTokenBuilder:
    """按官方 RTC_Token 结构生成浏览器进房令牌。"""

    VERSION = "001"
    PRIV_PUBLISH_STREAM = 0
    PRIV_SUBSCRIBE_STREAM = 4

    def __init__(self, app_id: str, app_key: str, room_id: str, user_id: str):
        self.app_id = str(app_id).strip()
        self.app_key = str(app_key).strip()
        self.room_id = str(room_id).strip()
        self.user_id = str(user_id).strip()
        self.issued_at = int(time.time())
        self.nonce = secrets.randbits(32)
        self.expire_at = 0
        self.privileges: dict[int, int] = {}

    def add_privilege(self, privilege: int, expire_timestamp: int) -> "RtcTokenBuilder":
        self.privileges[int(privilege)] = int(expire_timestamp)
        if privilege == self.PRIV_PUBLISH_STREAM:
            self.privileges.update(
                {1: int(expire_timestamp), 2: int(expire_timestamp), 3: int(expire_timestamp)}
            )
        return self

    def expire_time(self, expire_timestamp: int) -> "RtcTokenBuilder":
        self.expire_at = int(expire_timestamp)
        return self

    @staticmethod
    def _string(value: str) -> bytes:
        data = value.encode("utf-8")
        if len(data) > 0xFFFF:
            raise ValueError("RTC Token 字段过长")
        return struct.pack("<H", len(data)) + data

    def _message(self) -> bytes:
        privileges = b"".join(
            struct.pack("<HI", int(key), int(value))
            for key, value in sorted(self.privileges.items())
        )
        return (
            struct.pack("<III", self.nonce, self.issued_at, self.expire_at)
            + self._string(self.room_id)
            + self._string(self.user_id)
            + struct.pack("<H", len(self.privileges))
            + privileges
        )

    def build(self) -> str:
        if not self.app_id or not self.app_key or not self.room_id or not self.user_id:
            raise ValueError("RTC Token 缺少 AppId、AppKey、RoomId 或 UserId")
        if len(self.app_id) != 24:
            raise ValueError("RTC AppId 必须是 24 个字符")
        message = self._message()
        signature = _hmac(self.app_key, message)
        payload = struct.pack("<H", len(message)) + message + struct.pack("<H", len(signature)) + signature
        encoded = base64.b64encode(payload).decode("ascii")
        return f"{self.VERSION}{self.app_id}{encoded}"


class VolcRtcOpenApi:
    """火山 OpenAPI V4 签名客户端。"""

    host = "rtc.volcengineapi.com"
    service = "rtc"
    endpoint = "https://rtc.volcengineapi.com"

    def __init__(self, access_key: str, secret_key: str, *, region: str = "cn-north-1", session: aiohttp.ClientSession | None = None):
        self.access_key = str(access_key or "").strip()
        self.secret_key = str(secret_key or "").strip()
        self.region = str(region or "cn-north-1").strip() or "cn-north-1"
        self.session = session

    @property
    def configured(self) -> bool:
        return bool(self.access_key and self.secret_key)

    @staticmethod
    def _canonical_query(params: Mapping[str, Any]) -> str:
        items: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None:
                continue
            items.append((str(key), str(value)))
        items.sort(key=lambda item: (item[0], item[1]))
        return "&".join(
            f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}" for key, value in items
        )

    def _signed_headers(self, body: bytes, timestamp: datetime) -> tuple[dict[str, str], str]:
        body_hash = hashlib.sha256(body).hexdigest()
        headers = {
            "Host": self.host,
            "X-Date": timestamp.strftime("%Y%m%dT%H%M%SZ"),
            "X-Content-Sha256": body_hash,
            "Content-Type": "application/json",
        }
        return headers, body_hash

    def _authorization(self, action: str, version: str, body: bytes, timestamp: datetime) -> dict[str, str]:
        headers, body_hash = self._signed_headers(body, timestamp)
        signed_names = "content-type;host;x-content-sha256;x-date"
        canonical_headers = "\n".join(
            f"{name.lower()}:{headers[name].strip()}"
            for name in ("Content-Type", "Host", "X-Content-Sha256", "X-Date")
        )
        query = self._canonical_query({"Action": action, "Version": version})
        canonical_request = "\n".join(
            ("POST", "/", query, canonical_headers, "", signed_names, body_hash)
        )
        date = timestamp.strftime("%Y%m%d")
        scope = f"{date}/{self.region}/{self.service}/request"
        string_to_sign = "\n".join(
            (
                "HMAC-SHA256",
                headers["X-Date"],
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            )
        )
        k_date = _hmac(self.secret_key, date)
        k_region = _hmac(k_date, self.region)
        k_service = _hmac(k_region, self.service)
        signing_key = _hmac(k_service, "request")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["Authorization"] = (
            f"HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_names}, Signature={signature}"
        )
        return headers

    async def request(self, action: str, body: Mapping[str, Any], *, version: str = "2025-06-01") -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("veRTC 缺少 OpenAPI AccessKey 或 SecretKey")
        raw = json.dumps(dict(body), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        timestamp = datetime.now(timezone.utc)
        headers = self._authorization(action, version, raw, timestamp)
        session = self.session
        owned = False
        if session is None or session.closed:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
            owned = True
        try:
            async with session.post(
                self.endpoint,
                params={"Action": action, "Version": version},
                data=raw,
                headers=headers,
            ) as response:
                text = await response.text()
                try:
                    payload = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    payload = {"ResponseMetadata": {"Error": {"Message": text[:500]}}}
                if response.status >= 400:
                    raise RuntimeError(f"veRTC {action} HTTP {response.status}: {str(payload)[:500]}")
                if isinstance(payload, dict):
                    error = payload.get("ResponseMetadata", {}).get("Error")
                    if error:
                        raise RuntimeError(f"veRTC {action} 失败：{str(error)[:500]}")
                return payload if isinstance(payload, dict) else {}
        finally:
            if owned:
                await session.close()

    async def start_voice_chat(self, body: Mapping[str, Any], *, version: str = "2025-06-01") -> dict[str, Any]:
        return await self.request("StartVoiceChat", body, version=version)

    async def stop_voice_chat(self, body: Mapping[str, Any], *, version: str = "2025-06-01") -> dict[str, Any]:
        return await self.request("StopVoiceChat", body, version=version)

    async def update_voice_chat(self, body: Mapping[str, Any], *, version: str = "2025-06-01") -> dict[str, Any]:
        return await self.request("UpdateVoiceChat", body, version=version)


def _callback_signature_values(
    payload: Mapping[str, Any] | None,
    headers: Mapping[str, Any] | None,
) -> list[str]:
    values: list[str] = []
    for source in (payload or {}, headers or {}):
        for key, value in source.items():
            normalized = str(key).replace("-", "").replace("_", "").lower()
            if normalized in {
                "servermessagesignature",
                "servermessagesignatureforrts",
                "signature",
                "xsignature",
                "callbacksignature",
                "xcallbacksignature",
            }:
                text = str(value or "").strip()
                if text:
                    values.append(text)
    return values


def callback_signature_present(
    payload: Mapping[str, Any] | None,
    headers: Mapping[str, Any] | None = None,
) -> bool:
    """判断回调是否携带了可识别的签名字段。"""

    return bool(_callback_signature_values(payload, headers))


def _clean_callback_signature(value: str) -> str:
    value = str(value or "").strip()
    for prefix in ("sha256=", "hmac-sha256=", "hmac-sha256 "):
        if value.lower().startswith(prefix):
            return value[len(prefix) :].strip()
    return value


def callback_signature_valid(
    payload: Mapping[str, Any],
    expected: str,
    *,
    headers: Mapping[str, Any] | None = None,
    raw_body: bytes | None = None,
) -> bool:
    """校验回调正文或请求头签名；未配置校验值时视为通过。

    控制台固定回调可能把密钥原文放入签名头，也可能按
    ``request_id.timestamp`` 或请求正文计算 HMAC-SHA256。兼容这些形式，
    同时保留旧版正文 ``ServerMessageSignature`` 的校验方式。
    """

    expected = str(expected or "").strip()
    if not expected:
        return True
    expected_clean = _clean_callback_signature(expected)
    for received in _callback_signature_values(payload, headers):
        received_clean = _clean_callback_signature(received)
        if hmac.compare_digest(received_clean, expected_clean):
            return True

    normalized_headers = {
        str(key).replace("-", "").replace("_", "").lower(): str(value or "").strip()
        for key, value in (headers or {}).items()
    }
    received = normalized_headers.get("xcallbacksignature") or normalized_headers.get("callbacksignature")
    if not received:
        return False
    received_clean = _clean_callback_signature(received)
    request_id = (
        normalized_headers.get("xcallbackrequestid")
        or normalized_headers.get("callbackrequestid")
        or normalized_headers.get("xrequestid")
    )
    timestamp = (
        normalized_headers.get("xcallbacktimestamp")
        or normalized_headers.get("callbacktimestamp")
        or normalized_headers.get("xtimestamp")
    )
    messages: list[bytes] = []
    if request_id and timestamp:
        messages.append(f"{request_id}.{timestamp}".encode("utf-8"))
    if raw_body is not None:
        messages.append(bytes(raw_body))
    for message in messages:
        digest = hmac.new(expected.encode("utf-8"), message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(received_clean.lower(), digest.lower()):
            return True
        encoded = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        if hmac.compare_digest(received_clean, encoded):
            return True
    return False


def decode_callback_message(payload: Any) -> dict[str, Any]:
    """解包回调中的 message 字符串/嵌套 JSON，兼容不同回调版本。

    固定回调和 ``ServerMessageURLForRTS`` 在不同版本中可能分别把事件
    放在 ``Message``、``Data``、``Result`` 或 ``Payload`` 中；只展开这些
    明确的包装层，保留字幕列表和外层任务标识。
    """

    current = payload
    preserved_outer: dict[str, Any] = {}
    wrapper_keys = (
        "message",
        "Message",
        "data",
        "Data",
        "result",
        "Result",
        "response",
        "Response",
        "payload",
        "Payload",
        "body",
        "Body",
        "content",
        "Content",
        "event_data",
        "EventData",
    )
    outer_keys = (
        "Type", "type", "Event", "event", "EventName", "eventName",
        "MessageType", "messageType", "TaskId", "TaskID", "RoomId", "RoomID",
        "RunStage", "runStage", "Status", "status", "State", "state",
        "ErrorCode", "errorCode", "ErrorMessage", "errorMessage",
        "ExtraInfo", "extraInfo", "ServerMessageSignature",
        "ServerMessageSignatureForRTS", "serverMessageSignature", "signature",
    )
    for _ in range(6):
        if isinstance(current, Mapping):
            result = dict(current)
            for outer_key in outer_keys:
                if outer_key in result and result[outer_key] not in (None, "", [], {}):
                    preserved_outer.setdefault(outer_key, result[outer_key])
            for key in wrapper_keys:
                value = result.get(key)
                if isinstance(value, Mapping):
                    merged = dict(value)
                    for outer_key in outer_keys:
                        if outer_key in result and outer_key not in merged:
                            merged[outer_key] = result[outer_key]
                    current = merged
                    break
                if isinstance(value, (str, bytes)):
                    try:
                        nested = json.loads(value.decode() if isinstance(value, bytes) else value)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(nested, Mapping):
                        merged = dict(nested)
                        for outer_key in outer_keys:
                            if outer_key in result and outer_key not in merged:
                                merged[outer_key] = result[outer_key]
                        current = merged
                    else:
                        current = nested
                    break
            else:
                return result
            continue
        if isinstance(current, (str, bytes)):
            try:
                current = json.loads(current.decode() if isinstance(current, bytes) else current)
                continue
            except (TypeError, ValueError, json.JSONDecodeError):
                break
        break
    if isinstance(current, list):
        # 官方 Function Calling 回调的 Message 是 JSON 字符串数组；保留
        # 外层 Type/TaskId 等字段，便于状态日志和路由分支识别事件类型。
        return {**preserved_outer, "data": current}
    return dict(current) if isinstance(current, Mapping) else {}


__all__ = [
    "RtcTokenBuilder",
    "VolcRtcOpenApi",
    "callback_signature_present",
    "callback_signature_valid",
    "decode_callback_message",
]
