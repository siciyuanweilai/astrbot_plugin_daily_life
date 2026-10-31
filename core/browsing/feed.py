from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from collections import Counter
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree

import aiohttp

from ..sight.auth import cookie_header
from ..sight.bili import BVID_RE

API = "https://api.bilibili.com"
# B 站公开 WBI 签名的固定置换表；密钥每次从 nav 读取，不保存 Cookie。
_WBI = (
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
)


def clean(value, limit=500):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).split())[
        :limit
    ]


def duration(value) -> int:
    try:
        if isinstance(value, str) and ":" in value:
            result = 0
            for part in value.split(":"):
                result = result * 60 + int(part)
            return max(0, result)
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def video(item: dict, source: str) -> dict | None:
    bvid = str(item.get("bvid") or "")
    if not BVID_RE.fullmatch(bvid):
        return None
    owner = item.get("owner") or {}
    return {
        "bvid": bvid,
        "page": 1,
        "url": f"https://www.bilibili.com/video/{bvid}",
        "title": clean(item.get("title"), 180),
        "author": clean(item.get("author") or owner.get("name"), 80),
        "author_id": str(item.get("mid") or owner.get("mid") or ""),
        "duration": duration(item.get("duration") or item.get("length")),
        "source": source,
    }


class BiliReadError(RuntimeError):
    pass


class BiliLoginRequired(BiliReadError):
    """浏览需要有效的账号登录态。"""


class BiliReader:
    """官方读取接口；所有浏览能力共用账号验证、限速与风控退避。"""

    def __init__(self, cookies, archive=None):
        self.cookies = cookies
        self._archive = archive
        self._feed_state = {}
        self._feed_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self.backoff_until = 0.0
        self._key = ""
        self._key_at = 0.0
        self._auth_lock = asyncio.Lock()
        self._following_lock = asyncio.Lock()
        self._account = ""
        self._mid = ""
        self._auth_until = 0.0
        self._login_failed_until = 0.0
        self._follow_pages = {}
        self._follow_page_count = 1
        self._follow_until = 0.0
        self._follow_failed_until = 0.0

    def invalidate_login(self, account=""):
        self._account = account
        self._mid = ""
        self._auth_until = self._login_failed_until = 0.0
        self._key = ""
        self._key_at = 0.0
        self._follow_pages.clear()
        self._follow_page_count = 1
        self._follow_until = self._follow_failed_until = 0.0
        self._feed_state.clear()

    async def _account_key(self):
        jar = await asyncio.to_thread(self.cookies)
        if not jar or not jar.get("SESSDATA"):
            return ""
        # 仅在内存中比较登录态，不记录 Cookie 或账号标识。
        return hashlib.sha256(
            json.dumps(jar, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()

    async def require_login(self) -> str:
        async with self._auth_lock:
            account = await self._account_key()
            if account != self._account or not account:
                self.invalidate_login(account)
            if not account:
                raise BiliLoginRequired(
                    "刷刷B站需要先登录，请管理员在私聊使用 /B站登录"
                )
            if time.time() < self._login_failed_until:
                raise BiliLoginRequired("B站登录已失效，请先 /B站登出，再 /B站登录")
            if self._mid and time.time() < self._auth_until:
                return self._mid
            nav = await self._request(API + "/x/web-interface/nav")
            if account != await self._account_key():
                self.invalidate_login()
                raise BiliLoginRequired("B站账号登录态已变更，请重新尝试")
            mid = str(nav.get("mid") or "")
            if (
                nav.get("isLogin") is not True
                or not mid.isascii()
                or not mid.isdigit()
                or int(mid) <= 0
            ):
                self.invalidate_login(account)
                self._login_failed_until = time.time() + 60
                raise BiliLoginRequired("B站登录已失效，请先 /B站登出，再 /B站登录")
            self._mid = mid
            self._auth_until = time.time() + 300
            image = nav.get("wbi_img") or {}
            raw = "".join(
                urlparse(str(image.get(k) or "")).path.rsplit("/", 1)[-1].split(".")[0]
                for k in ("img_url", "sub_url")
            )
            if len(raw) >= 64:
                self._key = "".join(raw[i] for i in _WBI)[:32]
                self._key_at = time.time()
            return mid

    async def followed_up(self, cursor=0) -> str:
        """从本人关注列表分页轮换取源；只缓存读取结果，不替角色建立兴趣。"""
        async with self._following_lock:
            mid = await self.require_login()
            if time.time() < self._follow_failed_until:
                return ""
            if time.time() >= self._follow_until:
                self._follow_pages.clear()
                self._follow_page_count = 1
                self._follow_until = time.time() + 1800
            page = cursor % self._follow_page_count + 1
            if page not in self._follow_pages:
                account = self._account
                try:
                    data = await self._request(
                        API + "/x/relation/followings",
                        {"vmid": mid, "pn": page, "ps": 50, "order": "desc"},
                    )
                    await self.require_login()
                    if account != self._account:
                        raise BiliLoginRequired("B站账号已切换，请重新尝试")
                    items = data.get("list")
                    total = data.get("total")
                    if (
                        not isinstance(items, list)
                        or not isinstance(total, int)
                        or isinstance(total, bool)
                        or total < 0
                    ):
                        raise BiliReadError("B站关注列表格式异常")
                    self._follow_page_count = max(1, (total + 49) // 50)
                    self._follow_pages[page] = list(
                        dict.fromkeys(
                            str(item["mid"])
                            for item in items
                            if isinstance(item, dict)
                            and str(item.get("mid", "")).isascii()
                            and str(item.get("mid", "")).isdigit()
                            and int(item["mid"]) > 0
                        )
                    )
                except BiliLoginRequired:
                    raise
                except BiliReadError:
                    if time.time() < self.backoff_until:
                        raise
                    self._follow_failed_until = time.time() + 300
                    return ""
            followed = self._follow_pages[page]
            return (
                followed[(cursor // self._follow_page_count) % len(followed)]
                if followed
                else ""
            )

    async def _request(self, url, params=None, *, raw=False, credentials=True,
                       method="GET", data=None, json_body=None, expected_account="", before_write=None):
        async with self._lock:
            if time.time() < self.backoff_until:
                raise BiliReadError("B站请求正在退避，稍后再看")
            gap = 1.5 - (time.monotonic() - self._last_request)
            if gap > 0:
                await asyncio.sleep(gap)
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.bilibili.com/",
            }
            if credentials:
                jar = await asyncio.to_thread(self.cookies) or {}
                if expected_account and expected_account != hashlib.sha256(json.dumps(jar, sort_keys=True, ensure_ascii=True).encode()).hexdigest():
                    raise BiliLoginRequired("B站账号已变化，发送已停止")
                if urlparse(url).hostname == "api.bilibili.com" and not jar.get(
                    "SESSDATA"
                ):
                    self.invalidate_login()
                    raise BiliLoginRequired(
                        "刷刷B站需要先登录，请管理员在私聊使用 /B站登录"
                    )
                header = cookie_header(jar)
                if header:
                    headers["Cookie"] = header
            self._last_request = time.monotonic()
            if method != "GET" and before_write is not None:
                await before_write()
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=18),
                    headers=headers,
                    trust_env=False,
                ) as session:
                    request = session.get if method == "GET" else session.post
                    options = {"params": params, "allow_redirects": False}
                    if method != "GET":
                        if urlparse(url).hostname not in {"api.bilibili.com", "api.vc.bilibili.com", "api.live.bilibili.com"}:
                            raise BiliReadError("不支持的B站写入地址")
                        options.update(data=data, json=json_body)
                    async with request(url, **options) as response:
                        if response.status in {403, 412, 429}:
                            self.backoff_until = time.time() + 1800
                            raise BiliReadError("B站暂时限制访问，已暂停请求")
                        if response.status != 200:
                            if method != "GET":
                                from .platform import BiliWriteUncertain
                                raise BiliWriteUncertain(f"B站发送回执不明（HTTP {response.status}），不会自动重发")
                            raise BiliReadError(
                                f"B站内容暂不可用（HTTP {response.status}）"
                            )
                        chunks, size = [], 0
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > 2_000_000:
                                if method != "GET":
                                    from .platform import BiliWriteUncertain
                                    raise BiliWriteUncertain("B站发送回执过大，结果留待核对")
                                raise BiliReadError("B站返回内容过大")
                            chunks.append(chunk)
                        body = b"".join(chunks)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                self.backoff_until = time.time() + 300
                if method != "GET":
                    from .platform import BiliWriteUncertain
                    raise BiliWriteUncertain("B站发送结果尚未确认，已保留回执，不会自动重发") from exc
                raise BiliReadError("B站连接暂不可用") from exc
            if raw:
                return body.decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError) as exc:
                if method != "GET":
                    from .platform import BiliWriteUncertain
                    raise BiliWriteUncertain("B站发送回执无法解析，结果留待核对") from exc
                raise BiliReadError("B站返回内容无法解析") from exc
            if method != "GET" and (not isinstance(payload, dict) or "code" not in payload):
                from .platform import BiliWriteUncertain
                raise BiliWriteUncertain("B站发送回执缺少结果，不能确认已发送")
            if not isinstance(payload, dict):
                raise BiliReadError("B站返回内容格式异常")
            code = payload.get("code", 0)
            if code in {-352, -412, -509}:
                self.backoff_until = time.time() + 1800
            if code == -101:
                self.invalidate_login(self._account)
                self._login_failed_until = time.time() + 60
                raise BiliLoginRequired("B站登录已失效，请先 /B站登出，再 /B站登录")
            if code:
                raise BiliReadError(f"B站内容暂不可用（{code}）")
            return payload.get("data") or payload.get("result") or {}

    async def signed(self, path, params):
        await self.require_login()
        return await self._request(API + path, self.sign_values(params))

    def sign_values(self, params):
        if not self._key or time.time() - self._key_at > 3600:
            raise BiliReadError("B站签名信息暂不可用")
        values = {**params, "wts": int(time.time())}
        query = urlencode(
            sorted((k, re.sub(r"[!'()*]", "", str(v))) for k, v in values.items())
        )
        values["w_rid"] = hashlib.md5((query + self._key).encode()).hexdigest()
        return values

    async def candidates(
        self, keywords: list[str], *, discover=True, cursor=0
    ) -> list[dict]:
        await self.require_login()
        groups = []
        if keywords:
            keyword = keywords[cursor % len(keywords)]
            try:
                result = await self.signed(
                    "/x/web-interface/wbi/search/type",
                    {
                        "search_type": "video",
                        "keyword": keyword,
                        "order": "pubdate" if cursor % 2 else "click",
                        "page": 1,
                    },
                )
                groups.append((result.get("result") or [], f"兴趣搜索：{keyword}"))
            except BiliLoginRequired:
                raise
            except BiliReadError:
                if time.time() < self.backoff_until:
                    raise
        try:
            groups.append((await self.following_feed(), "关注更新"))
        except BiliLoginRequired:
            raise
        except BiliReadError:
            if time.time() < self.backoff_until:
                raise
        mid = await self.followed_up(cursor)
        if mid:
            try:
                result = await self.signed(
                    "/x/space/wbi/arc/search",
                    {"mid": mid, "ps": 10, "pn": 1, "order": "pubdate"},
                )
                groups.append(((result.get("list") or {}).get("vlist") or [], "关注UP"))
            except BiliLoginRequired:
                raise
            except BiliReadError:
                if time.time() < self.backoff_until:
                    raise
        if discover:
            result = await self._request(
                API + "/x/web-interface/popular", {"pn": 1, "ps": 20}
            )
            groups.append((result.get("list") or [], "偶遇"))
        chosen = {}
        # 各来源交错，避免热榜或搜索结果淹没其他来源。
        for index in range(12):
            for items, source in groups:
                if index < len(items) and isinstance(items[index], dict):
                    item = video(items[index], source)
                    if item:
                        chosen.setdefault(item["bvid"], item)
        return list(chosen.values())[:24]

    async def following_feed(self) -> list[dict]:
        """持续翻页补齐关注更新；有读取预算，但不因重启丢失分页位置。"""
        from .correspondence import BiliSocialStore
        async with self._feed_lock:
            mid = await self.require_login()
            store = BiliSocialStore(self._archive, mid) if self._archive else None
            saved = (await store.state()).get("following_progress", {}) if store else self._feed_state.get(mid, {})
            after = int(saved.get("after") or 0)
            high = int(saved.get("high_water") or after)
            offset = str(saved.get("offset") or "")
            cache = {r["bvid"]: r for r in saved.get("recent", []) if r.get("bvid")}
            complete = False
            # 每次先读新近更新，再接着上次的位置；旧更新不会被下一次首页覆盖。
            for index in range(6):
                current = "" if index == 0 else offset
                params = {"type": "video"}
                if current:
                    params["offset"] = current
                data = await self._request(API + "/x/polymer/web-dynamic/v1/feed/all", params)
                rows = data.get("items") or []
                ids = [int(r.get("id_str") or 0) for r in rows if str(r.get("id_str") or "").isdigit()]
                if not (index == 0 and saved.get("offset")):
                    high = max([high, *ids])
                for row in self._feed_videos(rows):
                    cache[row["bvid"]] = row
                following = str(data.get("offset") or "") if data.get("has_more") else ""
                if index == 0 and offset:
                    # 先完成已有回填，再从新高水位开始下一轮。
                    continue
                if not following or following == current or (after and ids and min(ids) < after):
                    complete = True
                    offset = ""
                    break
                offset = following
            recent = sorted(cache.values(), key=lambda row: int(row.get("dynamic_id") or 0), reverse=True)[:240]
            rotation = int(saved.get("rotation") or 0) % max(1, len(recent))
            progress = {"after": high if complete else after, "high_water": high, "offset": offset,
                        "recent": recent, "rotation": (rotation + 12) % max(1, len(recent))}
            if mid != await self.require_login():
                raise BiliReadError("B站账号已切换，关注更新未提交")
            if store:
                await store.update({"following_progress": progress})
            else:
                self._feed_state[mid] = progress
            return (recent[rotation:] + recent[:rotation])[:40]

    @staticmethod
    def _feed_videos(items):
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            modules = item.get("modules") or {}
            archive = ((modules.get("module_dynamic") or {}).get("major") or {}).get("archive") or {}
            author = modules.get("module_author") or {}
            if archive.get("bvid"):
                result.append({**archive, "dynamic_id": str(item.get("id_str") or "0"), "duration": archive.get("duration_text"),
                               "owner": {"mid": author.get("mid"), "name": author.get("name")}})
        return result

    async def metadata(self, bvid: str, page=1) -> dict:
        await self.require_login()
        data = await self._request(API + "/x/web-interface/view", {"bvid": bvid})
        value = video(data, "链接")
        pages = data.get("pages") or []
        if not value or page < 1 or page > len(pages):
            raise BiliReadError("视频或分P不存在")
        selected = pages[page - 1]
        value.update(
            page=page,
            cid=int(selected.get("cid") or 0),
            aid=int(data.get("aid") or 0),
            duration=duration(selected.get("duration")),
            description=clean(data.get("desc"), 800),
        )
        value["url"] += f"?p={page}" if page > 1 else ""
        return value

    async def subtitles(self, metadata: dict) -> list[dict]:
        await self.require_login()
        data = await self.signed(
            "/x/player/wbi/v2", {"bvid": metadata["bvid"], "cid": metadata["cid"]}
        )
        subtitles = (data.get("subtitle") or {}).get("subtitles") or []
        subtitles = sorted(
            subtitles,
            key=lambda item: (
                not str(item.get("lan") or "").startswith("zh"),
                bool(item.get("ai_type")),
            ),
        )
        for item in subtitles[:3]:
            url = str(item.get("subtitle_url") or "")
            if url.startswith("//"):
                url = "https:" + url
            parsed = urlparse(url)
            # 仅允许字幕 CDN；Cookie 只发往官方 API，禁止重定向。
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or parsed.port not in {None, 443}
                or not any(
                    (parsed.hostname or "").endswith("." + domain)
                    or parsed.hostname == domain
                    for domain in ("hdslb.com", "bilibili.com")
                )
            ):
                continue
            payload = await self._request(url, raw=True, credentials=False)
            try:
                rows = json.loads(payload).get("body") or []
                result = []
                for row in rows[:12000]:
                    start, end = float(row["from"]), float(row["to"])
                    text = clean(row.get("content"), 500)
                    if 0 <= start < end <= metadata["duration"] + 5 and text:
                        result.append(
                            {
                                "start": start,
                                "end": end,
                                "text": text,
                                "source": "B站字幕",
                            }
                        )
                if result:
                    return sorted(result, key=lambda row: row["start"])
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        return []

    async def audience(self, metadata: dict) -> dict:
        await self.require_login()
        comments, danmaku = [], []
        try:
            data = await self._request(
                API + "/x/v2/reply",
                {"type": 1, "oid": metadata["aid"], "sort": 2, "pn": 1, "ps": 8},
            )
            comments = [
                clean((item.get("content") or {}).get("message"), 280)
                for item in (data.get("replies") or [])[:8]
            ]
        except BiliLoginRequired:
            raise
        except BiliReadError:
            pass
        try:
            xml = await self._request(
                f"https://comment.bilibili.com/{int(metadata['cid'])}.xml",
                raw=True,
                credentials=False,
            )
            if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
                raise ValueError("不支持的 XML")
            nodes = ElementTree.fromstring(xml)
            for node in nodes.findall("d")[:10000]:
                text = clean(node.text, 160)
                if 8 <= len(text) <= 160:
                    danmaku.append(
                        {
                            "at": float(node.attrib.get("p", "0").split(",")[0]),
                            "text": text,
                        }
                    )
            counts = Counter(item["text"] for item in danmaku)
            danmaku = [item for item in danmaku if counts[item["text"]] <= 3]
        except BiliLoginRequired:
            raise
        except (BiliReadError, ValueError, ElementTree.ParseError):
            pass
        return {
            "comments": [text for text in comments if len(text) >= 8][:5],
            "danmaku": danmaku[:1500],
        }
