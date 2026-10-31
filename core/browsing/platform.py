"""B站账号能力；共用登录、限速与退避，不执行外部项目脚本。"""
from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urlsplit

import aiohttp

from .feed import API, BiliReadError, clean, video

LIVE = "https://api.live.bilibili.com"
IM = "https://api.vc.bilibili.com"


def positive(value) -> str:
    value = str(value or "")
    return value if value.isascii() and value.isdigit() and int(value) > 0 else ""


class BiliWriteUncertain(BiliReadError):
    """请求可能已被接收，不能直接重试。"""


class BiliPlatform:
    def __init__(self, reader, expected_mid=""):
        self.reader = reader
        self.expected_mid = expected_mid
        self.before_write = None

    async def get(self, path, params=None, *, base=API):
        mid = await self.reader.require_login()
        if self.expected_mid and self.expected_mid != mid:
            raise BiliReadError("B站账号已切换，操作已停止")
        if base not in {API, LIVE, IM}:
            raise ValueError("不支持的B站接口")
        return await self.reader._request(base + path, params, expected_account=self.reader._account)

    async def post(self, path, payload, *, base=API, json_body=False, signed=False, files=None):
        mid = await self.reader.require_login()
        if self.expected_mid and self.expected_mid != mid:
            raise BiliReadError("B站账号已切换，发送已停止")
        if base not in {API, LIVE, IM}:
            raise ValueError("不支持的B站写入接口")
        account = self.reader._account
        jar = await asyncio.to_thread(self.reader.cookies) or {}
        csrf = str(jar.get("bili_jct") or "")
        if len(csrf) != 32 or any(ch not in "0123456789abcdefABCDEF" for ch in csrf):
            raise BiliReadError("B站写入需要有效的 bili_jct，请重新登录")
        if account != await self.reader._account_key():
            raise BiliReadError("B站账号发生变化，操作已停止")
        values, params = dict(payload), None
        if json_body:
            params = self.reader.sign_values({"csrf": csrf}) if signed else {"csrf": csrf}
        else:
            values.update(csrf=csrf, csrf_token=csrf)
        if files:
            if json_body:
                raise ValueError("图片上传不能使用 JSON 请求体")
            form = aiohttp.FormData()
            for key, value in values.items():
                form.add_field(key, str(value))
            for key, (name, content, mime) in files.items():
                form.add_field(key, content, filename=name, content_type=mime)
            values = form
        return await self.reader._request(base + path, params, method="POST",
                                          data=None if json_body else values,
                                          json_body=values if json_body else None, expected_account=account,
                                          **({"before_write": self.before_write} if self.before_write is not None else {}))

    async def folders(self):
        mid = await self.reader.require_login()
        data = await self.get("/x/v3/fav/folder/created/list-all", {"up_mid": mid})
        return [item for item in data.get("list", []) if positive(item.get("id"))]

    async def favorites(self, folder="", page=1):
        if not positive(folder):
            folders = await self.folders()
            if not folders:
                return []
            folder = str(folders[0]["id"])
        data = await self.get("/x/v3/fav/resource/list", {"media_id": folder, "pn": page, "ps": 20})
        result = []
        for item in data.get("medias") or []:
            if item.get("type", 2) != 2:
                continue
            upper = item.get("upper") or {}
            entry = video({**item, "owner": {"mid": upper.get("mid"), "name": upper.get("name")}}, "收藏重看")
            if entry:
                entry["folder_id"] = folder
                result.append(entry)
        return result

    async def favorite_membership(self, aid):
        mid = await self.reader.require_login()
        data = await self.get("/x/v3/fav/folder/created/list-all", {"up_mid": mid, "rid": aid, "type": 2})
        return {str(r["id"]) for r in data.get("list") or [] if positive(r.get("id")) and r.get("fav_state") == 1}

    async def favorite(self, record, *, remove=False):
        folders = await self.folders()
        if not folders:
            raise BiliReadError("账号没有可用收藏夹")
        folder = positive(record.get("favorite_folder"))
        folder = folder or str(folders[0]["id"])
        if folder not in {str(item["id"]) for item in folders}:
            raise BiliReadError("原收藏夹已不存在，请刷新收藏记录")
        await self.post("/x/v3/fav/resource/deal", {"rid": record["aid"], "type": 2,
                        "add_media_ids": "" if remove else folder,
                        "del_media_ids": folder if remove else "", "platform": "web"})
        return {"folder_id": folder, "accepted": True}

    async def comments(self, aid, root="", *, page=1):
        params = {"type": 1, "oid": aid, "pn": page, "ps": 20}
        path = "/x/v2/reply"
        if positive(root):
            path += "/reply"
            params["root"] = root
        data = await self.get(path, params)
        items = ([data["root"]] if isinstance(data.get("root"), dict) else []) + (data.get("replies") or [])
        return [{"id": positive(item.get("rpid_str") or item.get("rpid")),
                 "uid": positive((item.get("member") or {}).get("mid") or item.get("mid")),
                 "text": clean((item.get("content") or {}).get("message"), 2100),
                 "mentioned_uids": [positive(member.get("mid")) for member in (item.get("content") or {}).get("members") or []
                                    if isinstance(member, dict) and positive(member.get("mid"))],
                 "root": positive(item.get("root")), "parent": positive(item.get("parent")), "at": int(item.get("ctime") or 0)}
                for item in items if isinstance(item, dict)][:21]

    async def user_card(self, uid):
        uid = positive(uid)
        if not uid:
            raise ValueError("无效的B站用户 UID")
        data = await self.get("/x/web-interface/card", {"mid": uid})
        card = data.get("card") or {}
        return {"mid": positive(card.get("mid")), "name": clean(card.get("name"), 80)}

    async def find_comment(self, aid, root, identity, uid):
        for page in range(1, 7):
            rows = await self.comments(aid, root, page=page)
            match = next((r for r in rows if r["id"] == identity and r["uid"] == uid), None)
            if match:
                return match
            if len(rows) < (21 if positive(root) else 20):
                break
        return None

    async def notification_comment(self, aid, root, source, uid, timestamp):
        candidates = {}
        for page in range(1, 7):
            rows = await self.comments(aid, root, page=page)
            exact = next((r for r in rows if r["id"] == source and r["uid"] == uid), None)
            if exact:
                return exact
            for row in rows:
                if row["uid"] == uid and row["parent"] == source and timestamp and abs(row["at"] - timestamp) <= 5:
                    candidates[row["id"]] = row
            if len(rows) < (21 if positive(root) else 20):
                break
        return next(iter(candidates.values())) if len(candidates) == 1 else None

    async def comment(self, record, text, *, root="", parent="", mention=None):
        payload = {"type": 1, "oid": record["aid"], "message": text, "plat": 1}
        if mention is not None:
            uid, name = positive(mention.get("uid")), clean(mention.get("name"), 80)
            if not uid or not name:
                raise ValueError("@ 提及需要有效的用户 UID 和昵称")
            payload["message"] = f"@{name} {text}".strip()
            payload["at_name_to_mid"] = json.dumps({name: uid}, ensure_ascii=False, separators=(",", ":"))
        if positive(parent):
            payload.update(root=positive(root) or positive(parent), parent=positive(parent))
        data = await self.post("/x/v2/reply/add", payload)
        identity = positive(data.get("rpid_str") or data.get("rpid") or (data.get("reply") or {}).get("rpid"))
        return {"remote_id": identity, "accepted": True}

    async def notification_page(self, kind, *, cursor=None):
        if kind not in {"reply", "at"}:
            raise ValueError("不支持的通知类型")
        params = {"platform": "web", "build": 0, "mobi_app": "web"}
        if cursor:
            params.update(id=cursor["id"], **{kind + "_time": cursor["time"]})
        data = await self.get("/x/msgfeed/" + kind, params)
        items, next_cursor = data.get("items") or [], data.get("cursor") or {}
        more = bool(items) and next_cursor.get("is_end") is False and positive(next_cursor.get("id"))
        return {"items": items, "cursor": {"id": str(next_cursor["id"]), "time": int(next_cursor.get("time") or 0)} if more else None}

    async def dm_history(self, uid, *, before=""):
        return (await self.dm_page(uid, before=before))["rows"]

    async def dm_page(self, uid, *, before=""):
        if not positive(uid):
            raise ValueError("无效的B站 UID")
        params = {"talker_id": uid, "session_type": 1, "size": 30, "build": 0, "mobi_app": "web"}
        if positive(before):
            params["begin_seqno"] = before
        await self.reader.require_login()
        data = await self.get("/svr_sync/v1/svr_sync/fetch_session_msgs", self.reader.sign_values(params), base=IM)
        result = []
        for item in data.get("messages") or []:
            if item.get("msg_type") != 1:
                continue
            try:
                content = json.loads(item.get("content") or "{}")
                text = clean(content.get("content"), 2000)
            except (ValueError, TypeError, AttributeError):
                continue
            identity = positive(item.get("msg_key") or item.get("msg_seqno"))
            if text and identity:
                result.append({"id": identity, "uid": positive(item.get("sender_uid")),
                               "text": text, "at": int(item.get("timestamp") or 0),
                               "seq": positive(item.get("msg_seqno"))})
        raw = data.get("messages") or []
        oldest = min((int(r.get("timestamp") or 0) for r in raw), default=0)
        cursor = min((int(r.get("msg_seqno") or 0) for r in raw if positive(r.get("msg_seqno"))), default=0)
        return {"rows": sorted(result, key=lambda row: (row["at"], int(row["id"]))),
                "oldest": oldest, "before": str(cursor) if cursor else "", "more": data.get("has_more") not in (0, "0", False) if "has_more" in data else len(raw) >= 30}


    async def dm(self, uid, text, device):
        mid = await self.reader.require_login()
        if not positive(uid) or uid == mid:
            raise ValueError("私信对象无效")
        data = await self.post("/web_im/v1/web_im/send_msg", {
            "msg[sender_uid]": mid, "msg[receiver_id]": uid, "msg[receiver_type]": 1,
            "msg[msg_type]": 1, "msg[msg_status]": 0, "msg[timestamp]": int(time.time()),
            "msg[dev_id]": device, "msg[content]": json.dumps({"content": text}, ensure_ascii=False)}, base=IM)
        return {"accepted": True, "remote_id": positive(data.get("msg_key"))}

    @staticmethod
    def picture_key(url):
        parsed = urlsplit(str(url or ""))
        if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or
                parsed.port not in {None, 80, 443} or not (parsed.hostname or "").endswith(".hdslb.com")):
            raise ValueError("动态配图必须是 B站上传接口返回的图片")
        return parsed.hostname + parsed.path

    async def upload_dynamic_picture(self, picture):
        data = await self.post("/x/dynamic/feed/draw/upload_bfs", {"biz": "new_dyn", "category": "daily"},
                               files={"file_up": ("dynamic.jpg", picture["data"], "image/jpeg")})
        url = str(data.get("image_url") or "")
        self.picture_key(url)
        width, height = positive(data.get("image_width")), positive(data.get("image_height"))
        if not width or not height or int(width) * int(height) > 40_000_000:
            raise BiliReadError("图片上传回执缺少有效尺寸，未发布动态")
        return {"img_src": url, "img_width": int(width), "img_height": int(height)}

    async def dynamic(self, text, *, pictures=None):
        pictures = list(pictures or [])
        if len(pictures) > 9:
            raise ValueError("一条图文动态最多选择 9 张图片")
        for picture in pictures:
            self.picture_key(picture.get("img_src"))
            if not positive(picture.get("img_width")) or not positive(picture.get("img_height")):
                raise ValueError("动态配图缺少尺寸")
        request = {
            "content": {"contents": [{"raw_text": text, "type": 1, "biz_id": ""}]},
            "scene": 2 if pictures else 1,
            "meta": {"app_meta": {"from": "create.dynamic.web", "mobi_app": "web"}}}
        if pictures:
            request["pics"] = pictures
        data = await self.post("/x/dynamic/feed/create/dyn", {"dyn_req": request}, json_body=True, signed=True)
        identity = positive(data.get("dyn_id_str") or data.get("dyn_id"))
        if not identity:
            raise BiliWriteUncertain("动态发布未返回编号，结果留待回查")
        return {"accepted": True, "remote_id": identity}

    async def dynamics(self):
        return (await self.dynamic_page())["rows"]

    async def dynamic_page(self, *, offset=""):
        mid = await self.reader.require_login()
        # 新版图文正文只在 Opus 结构中返回；旧版字段可能只有图片而没有正文。
        params = {"host_mid": mid, "features": "itemOpusStyle"}
        if offset:
            params["offset"] = offset
        data = await self.get("/x/polymer/web-dynamic/v1/feed/space", params)
        rows = []
        for item in data.get("items") or []:
            modules = item.get("modules") or {}
            dynamic = modules.get("module_dynamic") or {}
            major = dynamic.get("major") or {}
            opus = major.get("opus") or {}
            draw = major.get("draw") or {}
            description = (opus.get("summary") if opus else dynamic.get("desc")) or {}
            pictures = (opus.get("pics") if opus else draw.get("items")) or []
            rows.append({"id": positive(item.get("id_str")),
                         "at": int((modules.get("module_author") or {}).get("pub_ts") or 0),
                         "text": clean(description.get("text"), 2000),
                         "pictures": [str(pic.get("url" if opus else "src") or "") for pic in pictures]})
        return {"rows": rows, "offset": str(data.get("offset") or "") if data.get("has_more") else ""}

    async def live_rooms(self):
        result = {}
        for page in range(1, 7):
            data = await self.get("/xlive/web-ucenter/v1/xfetter/GetWebList", {"page": page, "page_size": 20}, base=LIVE)
            rows = data.get("rooms") or []
            for row in rows:
                if positive(row.get("room_id")):
                    result[str(row["room_id"])] = {"id": str(row["room_id"]), "uid": positive(row.get("uid")),
                        "title": clean(row.get("title"), 160), "author": clean(row.get("uname"), 80)}
            if len(rows) < 20 or (data.get("count") is not None and page * 20 >= int(data["count"])):
                break
        return list(result.values())

    async def live_info(self, room):
        if not positive(room):
            raise ValueError("无效的直播间")
        return await self.get("/room/v1/Room/get_info", {"room_id": room}, base=LIVE)

    async def live_history(self, room):
        data = await self.get("/xlive/web-room/v1/dM/gethistory", {"roomid": room, "room_type": 0}, base=LIVE)
        return [{"uid": positive(row.get("uid")), "text": clean(row.get("text"), 200),
                 "at": str(row.get("timeline") or "")} for row in (data.get("room") or [])[-20:]]

    async def live_stream(self, room, *, exclude=()):
        data = await self.get("/xlive/web-room/v2/index/getRoomPlayInfo", {
            "room_id": room, "protocol": "0,1", "format": "0,1,2", "codec": "0", "qn": 80, "platform": "web"}, base=LIVE)
        streams = ((data.get("playurl_info") or {}).get("playurl") or {}).get("stream") or []
        choices = []
        for stream in streams:
            for fmt in stream.get("format") or []:
                kind = fmt.get("format_name")
                if (stream.get("protocol_name"), kind) not in {("http_stream", "flv"), ("http_hls", "ts"), ("http_hls", "fmp4")}:
                    continue
                for codec in fmt.get("codec") or []:
                    for host in codec.get("url_info") or []:
                        url = str(host.get("host") or "") + str(codec.get("base_url") or "") + str(host.get("extra") or "")
                        if url not in exclude:
                            choices.append((0 if kind == "flv" else 1, url))
        if choices:
            return min(choices, key=lambda row: row[0])[1]
        raise BiliReadError("直播暂时没有可读取的有限片段流")

    async def danmaku(self, room, text):
        if not positive(room) or not 1 <= len(text) <= 30:
            raise ValueError("直播弹幕需要有效房间号，长度为1到30字")
        await self.post("/msg/send", {"roomid": room, "msg": text, "rnd": int(time.time()),
                        "fontsize": 25, "mode": 1, "color": 16777215, "bubble": 0}, base=LIVE)
        return {"accepted": True}
