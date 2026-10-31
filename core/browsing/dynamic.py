"""图文动态的上传与投递事务；分享决策由 daily_share 提供。"""
from __future__ import annotations

import asyncio
import hashlib
import io
import time
from pathlib import Path

from .feed import BiliReadError, clean
from .platform import BiliPlatform, BiliWriteUncertain


def prepare_picture(path: str) -> dict:
    """只读取已选中的本地成品，验证图像并去除 EXIF 等附带元数据。"""
    from PIL import Image, ImageOps

    source = Path(path)
    if not source.is_file() or not 0 < source.stat().st_size <= 20 * 1024 * 1024:
        raise ValueError("动态配图不存在或超过 20 MiB")
    raw = source.read_bytes()
    with Image.open(io.BytesIO(raw)) as original:
        if original.width * original.height > 40_000_000 or getattr(original, "n_frames", 1) != 1:
            raise ValueError("动态配图需要尺寸合理的静态图片")
        original.load()
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((4096, 4096))
        output = io.BytesIO()
        image.save(output, "JPEG", quality=90)
        data = output.getvalue()
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("处理后的动态配图超过 10 MiB")
        return {"data": data, "sha256": hashlib.sha256(data).hexdigest()}


def dynamic_matches(row: dict, remote: dict) -> bool:
    """图文回执必须同时匹配正文和图片，纯文字不能证明配图已发出。"""
    if remote.get("text") != row.get("text"):
        return False
    try:
        expected = [BiliPlatform.picture_key(pic["img_src"]) for pic in row.get("pictures") or []]
        actual = [BiliPlatform.picture_key(url) for url in remote.get("pictures") or []]
    except (KeyError, TypeError, ValueError):
        return False
    return expected == actual


class DynamicPublisher:
    def __init__(self, social):
        self.social = social

    async def publish(self, store, draft, guard):
        """在 social 的写锁内执行；恢复原上传，不重复创建结果未明的动态。"""
        intent = str(draft.get("intent") or "")
        text = clean(draft.get("text"), 2000)
        paths = draft.get("images") or []
        source_id = str(draft.get("source_id") or "")
        if not intent or len(intent) > 160 or not source_id or not text or not isinstance(paths, list) or len(paths) > 9:
            raise ValueError("动态草稿缺少有效编号、正文或图片清单")
        if any(not isinstance(path, str) or not path for path in paths):
            raise ValueError("动态配图必须来自已完成的图片素材")
        # 与生活侧原生动态共用来源键；切换接管者不会产生第二次发布。
        key = "dynamic:" + source_id + ":once"
        previous = await store.get(store.identity(key))
        if previous:
            if previous["status"] in {"accepted", "confirmed", "uncertain", "cancelled"} or (
                    previous["status"] == "sending" and previous.get("phase") != "preparing"):
                return previous
            if previous["status"] == "rejected" and not store.retryable(previous):
                return previous
            if previous.get("phase") == "preparing" and (
                    previous.get("text") != text or previous.get("image_paths", []) != paths):
                raise ValueError("同一动态意图不能替换正文或图片")
        await guard()
        # 未上传的文件先全部验证，避免选图错误导致半套素材进入网络。
        preparation = previous if previous and previous.get("text") == text and previous.get("image_paths", []) == paths else {}
        uploads = list((preparation or {}).get("pictures") or [])
        prepared = [await asyncio.to_thread(prepare_picture, path) for path in paths[len(uploads):]]
        hashes = list((preparation or {}).get("image_hashes") or [])
        for index, pic in enumerate(prepared, len(uploads)):
            if index < len(hashes) and hashes[index] != pic["sha256"]:
                raise ValueError("原动态图片已变化，停止续发")
        hashes = hashes[:len(uploads)] + [pic["sha256"] for pic in prepared]
        now = self.social.browsing.now().isoformat(timespec="seconds")
        if previous and previous["status"] == "sending" and previous.get("phase") == "preparing":
            journal = previous
        else:
            journal = await store.claim(key, {
                "kind": "dynamic", "scope": "global", "at": now, "text": text,
                "record_id": str(draft.get("source_id") or ""), "outbound": True,
                "phase": "preparing", "image_paths": paths, "image_hashes": hashes,
                "pictures": uploads, "external_owner": "daily_share",
            }, daily_limit=self.social.config.max_daily_interactions)
            if journal is None:
                raise ValueError("动态已处理、结果待核对，或今天达到互动上限")
        platform = BiliPlatform(self.social.browsing.reader, store.account)
        platform.before_write = guard
        try:
            for picture in prepared:
                await guard()
                uploaded = await platform.upload_dynamic_picture(picture)
                uploads.append(uploaded)
                journal = await store.finish(journal, pictures=list(uploads))
            await guard()
        except asyncio.CancelledError:
            # 最终发布尚未调用；保留上传进度，恢复时继续原任务。
            raise
        except Exception as exc:
            return await store.finish(journal, status="rejected", phase="preparing",
                                      retry_after=time.time() + 300, reason="图片准备未完成，动态未提交：" + type(exc).__name__)
        journal = await store.finish(journal, phase="submitting", at=self.social.browsing.now().isoformat(timespec="seconds"))
        try:
            result = await platform.dynamic(text, pictures=uploads)
        except (BiliWriteUncertain, asyncio.CancelledError):
            await store.finish(journal, status="uncertain", reason="动态发布结果待核对，不会自动重发")
            raise
        except (BiliReadError, ValueError) as exc:
            return await store.finish(journal, status="rejected", phase="submitting", retry_after=time.time() + 300, reason=clean(exc, 200))
        except Exception:
            await store.finish(journal, status="uncertain", reason="动态发布异常，先核对平台结果")
            raise
        return await store.finish(journal, status="accepted", phase="submitted", **result)
