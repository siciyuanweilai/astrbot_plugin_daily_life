import ast
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import support  # noqa: F401
from core import logguard as log_guard


class LogHandler:
    def __init__(self):
        self.filters = []

    def addFilter(self, callback):
        self.filters.append(callback)

    def removeFilter(self, callback):
        self.filters.remove(callback)

    def accepts(self, record):
        return all(callback(record) for callback in self.filters)


class ProviderLogGuardTest(unittest.TestCase):
    def setUp(self):
        self.plugin_handler = LogHandler()
        self.framework_handler = LogHandler()
        self.sdk_handler = LogHandler()
        self.root = types.SimpleNamespace(handlers=[self.sdk_handler], parent=None)
        self.framework = types.SimpleNamespace(
            handlers=[self.framework_handler], parent=self.root
        )
        self.api_logger = types.SimpleNamespace(
            handlers=[self.plugin_handler], parent=self.framework
        )
        self.guard = log_guard.ProviderLogGuard()
        with patch.object(log_guard, "logger", self.api_logger):
            self.guard.install()
        self.addCleanup(self.guard.close)

    @staticmethod
    def record(name="astrbot", level="DEBUG", path="", message="普通调试日志"):
        return types.SimpleNamespace(
            name=name,
            levelname=level,
            pathname=path,
            getMessage=lambda: message,
        )

    def test_sdk_noise_is_filtered_at_framework_output(self):
        for namespace in (
            "openai",
            "anthropic",
            "google.genai",
            "google.generativeai",
            "httpcore",
        ):
            for name in (namespace, namespace + ".client"):
                with self.subTest(name=name):
                    self.assertFalse(self.sdk_handler.accepts(self.record(name)))
                    self.assertFalse(
                        self.sdk_handler.accepts(self.record(name, "INFO"))
                    )
                    for level in ("WARNING", "ERROR", "CRITICAL"):
                        self.assertTrue(
                            self.sdk_handler.accepts(self.record(name, level))
                        )
        self.assertFalse(self.sdk_handler.accepts(self.record("httpx", "DEBUG")))
        self.assertTrue(self.sdk_handler.accepts(self.record("httpx", "INFO")))
        self.assertTrue(self.sdk_handler.accepts(self.record("openai_custom", "DEBUG")))

    def test_provider_payloads_are_hidden_but_other_debug_logs_remain(self):
        for message in ("completion: private response", " Response: private body"):
            for path in (
                "/AstrBot/astrbot/core/provider/sources/openai_source.py",
                r"C:\AstrBot\astrbot\core\provider\sources\anthropic_source.py",
            ):
                with self.subTest(message=message, path=path):
                    self.assertFalse(
                        self.framework_handler.accepts(
                            self.record(path=path, message=message)
                        )
                    )
                    self.assertTrue(
                        self.framework_handler.accepts(
                            self.record(level="ERROR", path=path, message=message)
                        )
                    )
        self.assertTrue(self.framework_handler.accepts(self.record()))
        self.assertTrue(
            self.plugin_handler.accepts(
                self.record(
                    path="/AstrBot/data/plugins/example/main.py",
                    message="completion: ordinary log",
                )
            )
        )

    def test_install_is_idempotent_and_close_uses_original_handlers(self):
        def existing_filter(record):
            return True

        self.framework_handler.addFilter(existing_filter)
        with patch.object(log_guard, "logger", self.api_logger):
            self.guard.install()
        self.assertEqual(len(self.plugin_handler.filters), 1)
        self.assertEqual(len(self.framework_handler.filters), 2)
        self.assertEqual(len(self.sdk_handler.filters), 1)

        with patch.object(log_guard, "logger", types.SimpleNamespace()):
            self.guard.close()
            self.guard.close()
        self.assertEqual(self.plugin_handler.filters, [])
        self.assertEqual(self.sdk_handler.filters, [])
        self.assertEqual(self.framework_handler.filters, [existing_filter])

    def test_overlapping_plugin_instances_keep_the_remaining_guard(self):
        another = log_guard.ProviderLogGuard()
        self.addCleanup(another.close)
        with patch.object(log_guard, "logger", self.api_logger):
            another.install()
        self.guard.close()
        self.assertFalse(self.sdk_handler.accepts(self.record("openai")))
        another.close()
        self.assertTrue(self.sdk_handler.accepts(self.record("openai")))

    def test_plugin_does_not_import_builtin_or_third_party_loggers(self):
        root = Path(__file__).resolve().parents[1]
        for path in [root / "main.py", *root.joinpath("core").rglob("*.py")]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imports = [(node.module or "").split(".", 1)[0]]
                else:
                    continue
                location = f"{path.relative_to(root)}:{node.lineno}"
                self.assertNotIn("logging", imports, location)
                self.assertNotIn("loguru", imports, location)
