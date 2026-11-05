"""取消本轮拥有的读取；被 shield 保护的共享请求由其服务管理。"""
from __future__ import annotations

import asyncio


async def gather_owned(*awaitables):
    tasks = [asyncio.ensure_future(work) for work in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
