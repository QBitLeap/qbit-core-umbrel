import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_common", ROOT / "scripts/release_common.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseInputTests(unittest.TestCase):
    def test_unsafe_version_never_reaches_git_docker_or_the_filesystem(self):
        with tempfile.TemporaryDirectory() as name, patch.object(release.subprocess, "check_output") as run:
            root = Path(name)
            for value in ("../release", "$(touch unexpected)", "latest", "1.0.0;echo unsafe"):
                with self.assertRaises(ValueError):
                    release.stage(root, value)
            run.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_changed_source_patch_is_rejected_before_checkout_or_network(self):
        path = ROOT / "scripts/fetch_upstream.py"
        if not path.exists():
            self.skipTest("Core uses a pinned binary release instead of a source patch")
        spec = importlib.util.spec_from_file_location("source_fetch", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as name, patch.object(module.subprocess, "check_output") as run:
            root = Path(name)
            (root / "fix.patch").write_text("changed")
            output = root / "checkout"
            with self.assertRaises(ValueError):
                module.fetch(output, {"repository": "https://github.com/example/source.git", "commit": "a" * 40,
                    "patches": [{"file": "fix.patch", "sha256": "b" * 64}]}, root)
            run.assert_not_called()
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
