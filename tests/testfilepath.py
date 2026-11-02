"""本地媒体协议与实际文件路径一致，原始文件名只在 URI 输入时解码。"""

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from core.sight.filepath import local_media_path
from core.sight.probe import clean_source
from core.sight.embed import embed_local_markdown_images
from core.sight.sample import local_video_path


class MediaFilePathTest(unittest.TestCase):
    def test_local_paths_and_file_uris_preserve_unicode_percent_and_fragment_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "画面 #100%20 (片段).mp4"
            path.write_bytes(b"sample")
            for source in (str(path), path.as_uri(), path.as_uri().replace("file://", "file://localhost", 1)):
                with self.subTest(source=source):
                    self.assertEqual(local_media_path(source), path)
                    self.assertEqual(local_video_path(source), path)
                    self.assertEqual(Path(clean_source(source)), path)

    def test_windows_drive_paths_are_local_instead_of_url_schemes(self):
        for source in ("C:/media/frame.png", r"C:\media\frame.png", "D:/截图%20.png"):
            self.assertEqual(local_media_path(source), Path(source))

    def test_unc_file_uri_preserves_authority(self):
        self.assertEqual(local_media_path("file://files/share/frame%20one.png"),
                         Path("//files/share/frame one.png"))

    def test_remote_inline_and_unknown_schemes_are_not_local_files(self):
        for source in ("", "https://example.com/a.gif", "http://example.com/a.mp4",
                       "data:image/png;base64,AAAA", "base64://AAAA", "ftp://example.com/a", "file:"):
            with self.subTest(source=source):
                self.assertIsNone(local_media_path(source))

    def test_markdown_embedding_accepts_encoded_file_uri(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "帧 #100%20.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            markdown = embed_local_markdown_images(f"![画面]({path.as_uri()})")
            self.assertIn("![画面](data:image/png;base64,", markdown)
            self.assertNotIn("file:", markdown)
