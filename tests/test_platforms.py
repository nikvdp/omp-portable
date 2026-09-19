import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from platforms import detect_target, cache_base, TARGETS


class Platforms(unittest.TestCase):
    def test_native_selection(self):
        for system, machine, target in [
            ("Darwin", "arm64", "darwin-arm64"),
            ("Darwin", "x86_64", "darwin-x64"),
            ("Linux", "aarch64", "linux-arm64"),
            ("Linux", "x86_64", "linux-x64"),
        ]:
            self.assertEqual(detect_target(system, machine), target)

    def test_unknown_host_fails(self):
        with self.assertRaisesRegex(RuntimeError, "Unsupported native host"):
            detect_target("Windows", "AMD64")

    def test_mac_cache_does_not_follow_subprocess_xdg(self):
        self.assertEqual(
            cache_base("/Users/me", "darwin-arm64", "/other/cache"),
            Path("/Users/me/Library/Caches/omp-portable/dist"),
        )
        self.assertEqual(
            cache_base("/home/me", "linux-x64", "/other/cache"),
            Path("/other/cache/omp-portable/dist"),
        )

    def test_each_target_has_pinned_official_binary(self):
        lock = json.loads((ROOT / "config/upstream-lock.json").read_text())
        self.assertEqual(set(lock["assets"]), set(TARGETS))
        for name, cfg in TARGETS.items():
            asset = lock["assets"][name]
            self.assertEqual(asset["name"], cfg["asset"])
            self.assertEqual(len(bytes.fromhex(asset["sha256"])), 32)
            self.assertGreater(asset["size"], 1000000)
            self.assertIn("/" + lock["tag"] + "/", asset["url"])

    def test_cross_build_rejected_before_download(self):
        import build

        with patch("build.detect_target", return_value="linux-x64"), patch(
            "build.acquire"
        ) as fetch:
            with self.assertRaises(SystemExit):
                build.main(["--target", "darwin-arm64"])
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
