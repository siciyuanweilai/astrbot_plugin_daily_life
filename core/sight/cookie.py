from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from astrbot.api import logger

from ..runtime.markers import LOG_PREFIX


REQUIRED_KEY = "SESSDATA"


class BiliCookieJar:
    def __init__(self, data_dir: str | Path):
        base = Path(data_dir)
        if base.suffix:
            base = base.parent
        root = base if base.name == "sight" else base.parent if base.parent.name == "sight" else base / "sight"
        self._path = root / "bili_cookies.json"
        self._cookies: dict[str, str] = {}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def cookiefile(self) -> Path:
        return self._path.with_suffix(".txt")

    def save(self, cookies: Mapping[str, str]) -> bool:
        clean = {str(key): str(value) for key, value in dict(cookies or {}).items() if value}
        if not clean.get(REQUIRED_KEY):
            logger.warning(f"{LOG_PREFIX} B站登录信息保存失败：缺少 SESSDATA")
            return False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json(clean)
            self._write_netscape(clean)
        except OSError as exc:
            logger.warning(f"{LOG_PREFIX} B站登录信息保存失败：{exc}")
            return False
        self._cookies = clean
        logger.info(f"{LOG_PREFIX} B站登录信息已保存")
        return True

    def get(self) -> dict[str, str]:
        return dict(self._cookies)

    def is_logged_in(self) -> bool:
        return bool(self._cookies.get(REQUIRED_KEY))

    def clear(self) -> None:
        self._cookies = {}
        for path in (self._path, self.cookiefile):
            with contextlib.suppress(OSError):
                path.unlink()

    def ensure_cookiefile(self) -> Path | None:
        if not self.is_logged_in():
            return None
        if self.cookiefile.is_file():
            return self.cookiefile
        try:
            self._write_netscape(self._cookies)
            return self.cookiefile
        except OSError as exc:
            logger.debug(f"{LOG_PREFIX} B站 cookie 文件准备失败：{exc}")
            return None

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"{LOG_PREFIX} B站登录信息读取失败：{exc}")
            return
        if isinstance(data, dict) and data.get(REQUIRED_KEY):
            self._cookies = {str(key): str(value) for key, value in data.items() if value}

    def _write_json(self, data: Mapping[str, str]) -> None:
        self._atomic_write(self._path, json.dumps(dict(data), ensure_ascii=False, indent=2))

    def _write_netscape(self, data: Mapping[str, str]) -> None:
        lines = ["# Netscape HTTP Cookie File"]
        for key, value in data.items():
            if value:
                lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{key}\t{value}")
        self._atomic_write(self.cookiefile, "\n".join(lines) + "\n")

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
            temp_path = Path(tmp.name)
        try:
            temp_path.replace(path)
        except OSError:
            with contextlib.suppress(OSError):
                temp_path.unlink()
            raise
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
