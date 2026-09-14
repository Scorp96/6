import ast
import re
import unittest
from pathlib import Path


class InstallerPythonDependencyClosureV3Tests(unittest.TestCase):
    def test_all_local_python_imports_of_manifest_modules_are_packaged(self):
        root = Path(__file__).resolve().parents[1]
        installer = (root / "install-bridge.ps1").read_text(encoding="utf-8")
        match = re.search(r"\$files\s*=\s*@\((.*?)\)\s*\n", installer, re.S)
        self.assertIsNotNone(match, "installer $files manifest not found")
        manifest = set(re.findall(r"'([^']+)'", match.group(1)))
        python_files = sorted(name for name in manifest if name.endswith(".py"))
        missing = set()
        for name in python_files:
            path = root / name
            self.assertTrue(path.is_file(), f"manifest source missing: {name}")
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
            modules = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules.add(node.module.split(".", 1)[0])
            for module in modules:
                sibling = f"{module}.py"
                if (root / sibling).is_file() and sibling not in manifest:
                    missing.add((name, sibling))
        self.assertEqual([], sorted(missing), f"installer missing local python dependencies: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
