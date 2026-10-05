from __future__ import annotations

import asyncio
import math
import time
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import aiohttp

from astrbot.api import logger

from ..base import LOG_PREFIX, normalize_openai_base_url, upstream_error_text
from .pipe import ImageRequest, ImageRoute


class ImageTaskError(RuntimeError):
    """图片操作可能已被受理时，阻止重复提交。"""


class ImageTaskFailed(ImageTaskError):
    """接口明确确认原任务失败，无需继续查询。"""


class ImageTaskResult(dict[str, Any]):
    def __init__(self, payload: dict[str, Any], task_id: str) -> None:
        super().__init__(payload)
        self.task_id = task_id


def _poll_delay(headers: Any) -> float:
    value = str(headers.get("Retry-After", "") or "").strip()
    if not value:
        return 3.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return 3.0
    return max(3.0, seconds) if math.isfinite(seconds) else 3.0


async def request_image_task(
    session: aiohttp.ClientSession,
    route: ImageRoute,
    request: ImageRequest,
    *,
    on_accepted: Callable[[str, ImageRoute], Awaitable[None]] | None = None,
    resume_task_id: str = "",
) -> ImageTaskResult | None:
    task_id = resume_task_id
    accepted = bool(task_id)
    request_id = request.headers["Idempotency-Key"]
    timeout = aiohttp.ClientTimeout(total=min(30, route.timeout_seconds))
    try:
        async with asyncio.timeout(route.timeout_seconds):
            delay = 0.0
            if not task_id:
                async with session.post(
                    f"{request.url}/async",
                    json=request.payload,
                    headers=request.headers,
                    timeout=timeout,
                ) as response:
                    if response.status == 404:
                        return None
                    if response.status != 202:
                        if response.status < 400:
                            raise ImageTaskError(
                                f"图片异步提交返回非预期状态（HTTP {response.status}）；"
                                "已停止自动切换通道，避免重复生成"
                            )
                        detail = await response.text()
                        raise RuntimeError(f"HTTP {response.status}：{detail[:1000]}")
                    accepted = True
                    submitted = await response.json()
                    if not isinstance(submitted, dict):
                        raise ValueError("异步提交响应不是对象")
                    raw_id = submitted.get("task_id") or submitted.get("id")
                    if not isinstance(raw_id, str) or not raw_id.strip():
                        raise ValueError("异步提交响应没有任务编号")
                    task_id = raw_id.strip()
                    delay = _poll_delay(getattr(response, "headers", {}))
                if on_accepted is not None:
                    await on_accepted(task_id, route)

            logger.info(
                f"{LOG_PREFIX} 图片异步任务开始查询：分组={route.label}；"
                f"任务={task_id}；"
                f"参考图={request.reference_image_count}"
            )
            # 构造同源 URL，不向响应返回的 poll_url 发送密钥。
            status_url = (
                f"{normalize_openai_base_url(route.api_url)}/images/tasks/"
                f"{quote(task_id, safe='')}"
            )
            while True:
                await asyncio.sleep(delay)
                try:
                    async with session.get(
                        status_url,
                        headers={"Authorization": request.headers["Authorization"]},
                        timeout=timeout,
                        allow_redirects=False,
                    ) as response:
                        delay = _poll_delay(getattr(response, "headers", {}))
                        if response.status in {408, 429, 500, 502, 503, 504}:
                            logger.debug(
                                f"{LOG_PREFIX} 图片任务查询暂不可用，继续查询原任务："
                                f"{task_id}（HTTP {response.status}）"
                            )
                            continue
                        if response.status != 200:
                            raise ImageTaskError(
                                f"图片任务 {task_id} 状态查询失败（HTTP {response.status}）；"
                                "已停止自动切换通道，避免重复生成"
                            )
                        data = await response.json()
                except (TimeoutError, aiohttp.ClientError):
                    logger.debug(
                        f"{LOG_PREFIX} 图片任务查询连接暂不可用，继续查询原任务：{task_id}"
                    )
                    continue
                if not isinstance(data, dict):
                    raise ValueError("图片任务状态响应不是对象")
                status = data.get("status")
                if status == "processing":
                    continue
                if status == "failed":
                    raise ImageTaskFailed(
                        f"图片任务 {task_id} 失败（HTTP {data.get('http_status')}）："
                        f"{upstream_error_text(data)}"
                    )
                if status != "completed":
                    raise ValueError(f"图片任务返回未知状态：{status}")
                if data.get("http_status", 200) != 200:
                    raise ImageTaskFailed(
                        f"图片任务 {task_id} 返回错误（HTTP {data.get('http_status')}）："
                        f"{upstream_error_text(data)}"
                    )
                result = data.get("result")
                if not isinstance(result, dict) or not result.get("data"):
                    raise ValueError("图片任务已完成但没有成品数据")
                logger.info(f"{LOG_PREFIX} 图片异步任务已完成：{task_id}")
                return ImageTaskResult(result, task_id)
    except (TimeoutError, aiohttp.ClientError) as exc:
        identifier = f"任务 {task_id}" if task_id else f"请求 {request_id}"
        raise ImageTaskError(
            f"图片{identifier} 状态尚未确认；已停止自动切换通道，避免重复生成"
        ) from exc
    except ImageTaskError:
        raise
    except Exception as exc:
        if accepted:
            raise ImageTaskError(
                f"图片任务 {task_id or request_id} 已被接收，处理结果失败：{exc}；"
                "已停止自动切换通道，避免重复生成"
            ) from exc
        raise
