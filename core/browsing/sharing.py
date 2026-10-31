"""只向分享插件提供公开事实；生活调度唤醒不代表必须发布。"""
from __future__ import annotations

import hashlib

from ..life.appearance import format_image_appearance_context
from .dynamic import DynamicPublisher


class BiliDynamicSharing:
    def __init__(self, social):
        self.social = social

    def delegate(self, capability="dynamic"):
        context = getattr(self.social.runtime, "context", None)
        reader = getattr(context, "get_all_stars", None)
        if not callable(reader):
            return None
        for metadata in reader() or []:
            if (getattr(metadata, "root_dir_name", "") != "astrbot_plugin_daily_share" and
                    getattr(metadata, "name", "") != "astrbot_plugin_daily_share"):
                continue
            plugin = getattr(metadata, "star_cls", None)
            enabled = getattr(plugin, "bili_" + capability + "_enabled", None)
            if getattr(metadata, "activated", False) and callable(enabled) and enabled():
                return plugin
        return None

    async def opportunity(self, store, day, records, persona, recent):
        materials = [self.social.public_card(row) for row in records[:6]]
        originals = {row["id"]: row for row in records[:6]}
        assets = {}
        appearance = format_image_appearance_context(day)
        if appearance and str(getattr(day, "date", "")) == self.social.browsing.now().date().isoformat():
            key = "appearance:" + hashlib.sha256((str(day.date) + appearance).encode()).hexdigest()[:32]
            row = {"id": key, "kind": "appearance", "scope": "global", "appearance": appearance,
                   "title": "当前实际外观", "evidence": [{"quote": appearance}], "impression": ""}
            materials.append(row)
            originals[key] = row
        reader = getattr(self.social.runtime.archive, "get_living_entities", None)
        if callable(reader):
            for item in await reader("qzone_broadcast", kind="object", limit=18):
                resource = item.get("resource") or {}
                if (item.get("scope") not in {"global", "qzone_broadcast"} or item.get("visibility") != "public" or
                        item.get("state") != "available" or resource.get("media_kind") != "image"):
                    continue
                paths = resource.get("artifacts") or []
                if not paths or not all(isinstance(path, str) for path in paths):
                    continue
                images = [{"id": item["id"] + ":" + str(i), "description": item.get("title", "公开作品")}
                          for i in range(min(9, len(paths)))]
                assets.update({image["id"]: paths[i] for i, image in enumerate(images)})
                row = {"id": item["id"], "kind": "public_image", "scope": "global", "title": item.get("title", "公开作品"),
                       "evidence": [{"quote": item.get("provenance", "已归档的公开图片")}], "images": images,
                       "impression": "", "asset_revision": item.get("revision")}
                materials.append({k: v for k, v in row.items() if k != "asset_revision"})
                originals[row["id"]] = row
        available = []
        for material in materials:
            previous = await store.get(store.identity("dynamic:" + material["id"] + ":once"))
            if previous and (previous.get("status") in {"accepted", "confirmed", "uncertain", "cancelled"} or
                             previous.get("status") == "sending" and previous.get("phase") != "preparing"):
                continue
            available.append(material)
        return {"account": store.account, "persona": persona, "materials": available,
                "assets": assets, "recent_public_actions": recent}, originals

    async def consider(self, delegate, store, day, public, persona, recent):
        epoch = self.social.browsing._epoch
        opportunity, originals = await self.opportunity(store, day, public, persona, recent)
        draft = await delegate.prepare_bili_dynamic(opportunity)
        if not isinstance(draft, dict) or draft.get("status") not in {"ready", "check"}:
            return False
        intent = str(draft.get("intent") or "")
        identity = store.identity("dynamic:" + str(draft.get("source_id") or "") + ":once")
        previous = await store.get(identity)
        if draft.get("status") == "check":
            if previous is None or previous.get("phase") == "preparing":
                receipt = {"status": "cancelled"} if draft.get("source_expired") else {"status": "uncertain"}
                if previous is not None and receipt["status"] == "cancelled":
                    await store.finish(previous, status="cancelled", reason="公开素材已失效，最终发布未提交")
            else:
                receipt = previous
            await delegate.record_bili_dynamic_receipt(store.account, intent, receipt)
            return False
        source = originals.get(draft.get("source_id"))

        async def guard():
            browsing = self.social.browsing
            if (browsing._closed or epoch != browsing._epoch or not self.social.config.enabled or
                    not self.social.config.dynamics_enabled or self.delegate() is not delegate or
                    not delegate.config.get("enable_auto_share", False) or not source):
                raise ValueError("生活状态、分享设置或公开素材已变化，暂不发布")
            if source.get("kind") == "public_image":
                item = await self.social.runtime.archive.get_living_entity(source["id"], "qzone_broadcast")
                if not item or item.get("visibility") != "public" or item.get("state") != "available" or item.get("revision") != source["asset_revision"]:
                    raise ValueError("图片公开权限或内容已变化")
            elif source.get("kind") == "appearance":
                current = day
                reader = getattr(self.social.runtime.archive, "get_day", None)
                if callable(reader):
                    current = await reader(str(day.date))
                if current is None or format_image_appearance_context(current) != source["appearance"]:
                    raise ValueError("当前穿搭已变化，旧配图暂不发布")
            else:
                current = await self.social.browsing.archive.get(source["id"]) or await store.get(source["id"])
                if not current or current.get("scope") != "global" or not current.get("evidence"):
                    raise ValueError("公开视频经历已失效")
            if (browsing._closed or epoch != browsing._epoch or not self.social.config.enabled or not self.social.config.dynamics_enabled or
                    self.delegate() is not delegate or not delegate.config.get("enable_auto_share", False) or
                    draft.get("images") and not delegate.config.get("bilibili_conf", {}).get("enable_image", True)):
                raise ValueError("发送前生活状态已变化")

        async with self.social._write_lock:
            result = await DynamicPublisher(self.social).publish(store, draft, guard)
        await delegate.record_bili_dynamic_receipt(store.account, intent, result)
        return result.get("status") in {"accepted", "confirmed"}
