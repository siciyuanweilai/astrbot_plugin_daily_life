"""在独立进程中验证真实分段 HTTP 响应，避免主套件的网络 fixture。"""

import asyncio
import sys
import tempfile
from unittest.mock import AsyncMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web
from core.browsing.feed import BiliReader, BiliReadError
from core.browsing.visiting import capture_live
from core.sight.sample import _video_duration, ffmpeg_executable, ffprobe_executable


async def main():
    async def stream(request):
        response = web.StreamResponse(status=200)
        await response.prepare(request)
        await response.write(b'{"code":0,"data":')
        await asyncio.sleep(0.02)
        await response.write(b'{"complete":true}}')
        return response

    async def large(request):
        return web.Response(body=b"x" * 2_000_001)

    media = tempfile.TemporaryDirectory()
    root = Path(media.name)
    ffmpeg, probe = ffmpeg_executable(), ffprobe_executable()
    assert ffmpeg and probe, '需要真实 ffmpeg 和 ffprobe 验证直播解码'
    for kind in ('mpegts', 'fmp4'):
        directory = root / kind
        directory.mkdir()
        process = await asyncio.create_subprocess_exec(
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=10',
            '-t', '6', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-g', '10', '-f', 'hls', '-hls_time', '1',
            '-hls_list_size', '0', '-hls_segment_type', kind, 'index.m3u8',
            cwd=directory, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, error = await process.communicate()
        assert process.returncode == 0, error.decode()[:200]
        if kind == 'fmp4':
            assert (directory / 'init.mp4').is_file(), '初始化分片必须位于测试播放列表目录'
    app = web.Application()
    app.router.add_get("/split", stream)
    app.router.add_get("/large", large)
    app.router.add_static("/media", root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        assert await BiliReader(lambda: {})._request(
            f"http://127.0.0.1:{port}/split"
        ) == {"complete": True}
        try:
            await BiliReader(lambda: {})._request(f"http://127.0.0.1:{port}/large")
        except BiliReadError as exc:
            assert "内容过大" in str(exc)
        else:
            raise AssertionError("未限制响应大小")
        with patch('core.browsing.visiting.validate_stream_url', AsyncMock()):
            # 仅本地 fixture 放行地址检查；网络、播放列表解析、拼片、解码均走真实实现。
            for kind in ('mpegts', 'fmp4'):
                target = root / (kind + '.video')
                seconds = await capture_live(f'http://127.0.0.1:{port}/media/{kind}/index.m3u8', target, seconds=3)
                decoded = await asyncio.to_thread(_video_duration, probe, target)
                assert 0 < seconds <= 3 and decoded >= seconds, (kind, seconds, decoded)
                print(kind + '：真实 HLS HTTP 读取与解码通过')
    finally:
        await runner.cleanup()
        media.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
