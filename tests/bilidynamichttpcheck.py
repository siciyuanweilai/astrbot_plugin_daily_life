"""在独立进程中用真实 aiohttp 序列化 multipart；没有外部网络请求。"""
import asyncio
from unittest.mock import AsyncMock

import aiohttp as real_aiohttp
import support
from core.browsing import platform
from core.browsing.feed import BiliReader


async def main():
    platform.aiohttp = real_aiohttp
    reader = BiliReader(lambda: {'SESSDATA':'fake', 'bili_jct':'a'*32})
    reader.require_login = AsyncMock(return_value='1234')
    reader._account = await reader._account_key()
    reader._request = AsyncMock(return_value={'image_url':'https://i0.hdslb.com/bfs/new_dyn/a.jpg','image_width':24,'image_height':32})
    service = platform.BiliPlatform(reader, '1234')
    guard = service.before_write = AsyncMock()
    image = await service.upload_dynamic_picture({'data':b'checked-image-bytes'})
    call = reader._request.await_args
    assert call.args[0].endswith('/upload_bfs')
    assert call.kwargs['expected_account'] == reader._account
    assert call.kwargs['before_write'] is guard
    payload = call.kwargs['data']()
    chunks = []
    class Sink:
        async def write(self, value):
            chunks.append(value)
    await payload.write(Sink())
    wire = b''.join(chunks)
    for expected in (b'name="file_up"', b'filename="dynamic.jpg"', b'checked-image-bytes', b'image/jpeg',
                     b'name="csrf"', b'a'*32, b'new_dyn', b'daily'):
        assert expected in wire, expected
    assert image['img_width'] == 24


if __name__ == '__main__':
    asyncio.run(main())
