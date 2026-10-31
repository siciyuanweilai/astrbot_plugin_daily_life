from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .pipe import ImageRoute
from .polling import ImageTaskError, ImageTaskFailed
from .routes import task_reference


async def wait_for_image(service: Any, work: Any) -> Any:
    """视频首帧等后台依赖继续等待原任务，不把图片作为独立聊天成品投递。"""
    try:
        return await work
    except ImageTaskFailed:
        raise
    except ImageTaskError as exc:
        if not exc.task_id or not exc.route_reference:
            raise
        task_id, reference = exc.task_id, exc.route_reference
    while True:
        await asyncio.sleep(60)
        try:
            return await service.resume_async_image(task_id, reference)
        except ImageTaskFailed:
            raise
        except ImageTaskError:
            continue


class ImageRecovery:
    """保存一次生成的冻结请求和通道游标；恢复时不重新读取聊天或角色参考图。"""

    def __init__(self, directory: Path, data: dict[str, Any]):
        self.directory = directory
        self.data = data

    @property
    def path(self) -> Path:
        return self.directory / f"{self.data['id']}.json"

    @staticmethod
    def route_data(route: ImageRoute) -> dict[str, Any]:
        result = asdict(route)
        result.pop("api_key")
        result["reference"] = task_reference(route)
        return result

    @staticmethod
    def restore_route(saved: dict[str, Any], candidates: list[ImageRoute]) -> ImageRoute:
        matched = next(
            (route for route in candidates if task_reference(route) == saved["reference"]),
            None,
        )
        if matched is None:
            raise ValueError("原请求的通道已移除或凭据已更换")
        return ImageRoute(
            **{key: value for key, value in saved.items() if key != "reference"},
            api_key=matched.api_key,
        )

    @classmethod
    def create(cls, directory: Path, entries: list[dict], mode: str) -> ImageRecovery:
        return cls(directory, {
            "id": uuid.uuid4().hex,
            "entries": entries,
            "mode": mode,
            "index": -1,
            "status": "ready",
        })

    @staticmethod
    def _alias(directory: Path, task_id: str, reference: dict) -> Path:
        identity = json.dumps([reference, task_id], sort_keys=True, ensure_ascii=False)
        return directory / ("task_" + hashlib.sha256(identity.encode()).hexdigest() + ".json")

    @classmethod
    async def load(cls, directory: Path, task_id: str, reference: dict) -> ImageRecovery | None:
        def read():
            alias = cls._alias(directory, task_id, reference)
            if not alias.is_file():
                return None
            identifier = json.loads(alias.read_text(encoding="utf-8"))["id"]
            # 文件名只接受本插件产生的 UUID，不能借助别名访问其他路径。
            identifier = uuid.UUID(identifier).hex
            return cls(directory, json.loads((directory / f"{identifier}.json").read_text(encoding="utf-8")))

        try:
            return await asyncio.to_thread(read)
        except (OSError, ValueError, KeyError) as exc:
            raise ImageTaskError("图片恢复快照暂时无法读取，保留原任务") from exc

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            # Windows 文件索引/扫描可能短暂占用目标；只重试本地原子替换，不重发生成请求。
            for attempt in range(4):
                try:
                    temporary.replace(path)
                    break
                except PermissionError as exc:
                    if attempt == 3 or getattr(exc, "winerror", None) not in {5, 32, 33}:
                        raise
                    time.sleep(0.05 * (2 ** attempt))
        finally:
            temporary.unlink(missing_ok=True)

    async def save(self) -> None:
        try:
            await asyncio.to_thread(self._write, self.path, self.data)
        except OSError as exc:
            raise ImageTaskError("图片通道进度保存失败，暂停切换通道") from exc

    async def start(self, index: int) -> None:
        self.data.update(index=index, status="submitting", task_id="")
        await self.save()

    async def accepted(self, task_id: str, route: ImageRoute) -> None:
        self.data.update(status="processing", task_id=task_id)
        await self.save()
        await asyncio.to_thread(
            self._write, self._alias(self.directory, task_id, task_reference(route)),
            {"id": self.data["id"]},
        )

    async def failed(self, index: int, error: str) -> None:
        self.data["entries"][index]["error"] = error
        self.data.update(index=index, status="failed")
        await self.save()

    async def finish(self, *, path: str = "", error: str = "") -> None:
        self.data.update(status="completed" if path else "exhausted", path=path, error=error)
        # 终态只保留回执，释放提示词与参考图快照占用的空间。
        for entry in self.data["entries"]:
            entry.pop("parts", None)
        await self.save()
