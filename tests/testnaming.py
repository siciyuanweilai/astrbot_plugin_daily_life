from __future__ import annotations

import re
import unittest
from collections import defaultdict
from pathlib import Path


_WORD_NAME = re.compile(r"^[a-z][a-z0-9]*$")
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_CORE_ROOT = _PLUGIN_ROOT / "core"


class ProductionModuleNamingTests(unittest.TestCase):
    def test_package_and_module_names_are_unique_single_words(self):
        packages: dict[str, list[Path]] = defaultdict(list)
        modules: dict[str, list[Path]] = defaultdict(list)

        for path in _CORE_ROOT.rglob("*"):
            if "__pycache__" in path.parts:
                continue
            if path.is_dir():
                packages[path.name].append(path)
                self.assertRegex(
                    path.name,
                    _WORD_NAME,
                    f"生产包目录必须使用单个小写单词：{path.relative_to(_PLUGIN_ROOT)}",
                )
                continue
            if path.suffix != ".py" or path.name == "__init__.py":
                continue
            modules[path.stem].append(path)
            self.assertRegex(
                path.stem,
                _WORD_NAME,
                f"生产模块必须使用单个小写单词：{path.relative_to(_PLUGIN_ROOT)}",
            )

        duplicate_modules = {
            name: paths for name, paths in modules.items() if len(paths) > 1
        }
        package_module_conflicts = {
            name: packages[name] + modules[name]
            for name in packages.keys() & modules.keys()
        }
        self.assertEqual(
            duplicate_modules,
            {},
            "生产模块 basename 必须全局唯一",
        )
        self.assertEqual(
            package_module_conflicts,
            {},
            "生产包目录与模块不能同名",
        )


if __name__ == "__main__":
    unittest.main()
