import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release


class Publication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = self.root / "config"
        config.mkdir()
        (self.root / "dist").mkdir()
        self.lock = {"tag": "v1.2.3", "assets": {"linux-x64": {}}}
        self.runtime = {"version": "1.4.4", "assets": {"linux-x64": {"sha256": "runtime"}}}
        for name, value in {
            "upstream-lock.json": self.lock,
            "targets.json": {"linux-x64": {}},
            "runtime-lock.json": self.runtime,
            "portable-lock.json": {},
        }.items():
            (config / name).write_text(json.dumps(value))
        self.dependencies = config / "runtime-bun.lock"
        self.dependencies.write_text("locked dependencies")
        self.runtime_path = config / "runtime-lock.json"
        for name, value in {
            "ROOT": self.root, "RUNTIME_LOCK": self.runtime_path,
            "DEPENDENCIES": self.dependencies,
        }.items():
            patcher = patch.object(release, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.lite = self.artifact("lite")
        self.portable = self.artifact("portable")

    def artifact(self, edition):
        binary = self.root / "dist" / f"omp-{edition}-1.2.3-linux-x64"
        binary.write_bytes(f"{edition} executable".encode())
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        package_digest = hashlib.sha256(self.dependencies.read_bytes()).hexdigest()
        report = {
            "smoke": "passed", "target": "linux-x64", "edition": edition,
            "sha256": digest, "size_bytes": binary.stat().st_size,
            "runtime_lock_sha256": hashlib.sha256(self.runtime_path.read_bytes()).hexdigest(),
            "package_lock_sha256": package_digest,
        }
        manifest = {
            "schema": 1, "target": "linux-x64", "edition": edition,
            "upstream": {"tag": "v1.2.3", "runtime": {
                "package_lock_sha256": package_digest,
                "bun": {"version": "1.4.4", **self.runtime["assets"]["linux-x64"]},
            }},
        }
        if edition == "portable":
            report["portable_lock_sha256"] = hashlib.sha256(
                (self.root / "config/portable-lock.json").read_bytes()
            ).hexdigest()
            manifest["portable"] = {"python": "python", "browser": "browser", "components": ["browser"]}
        for suffix, value in ((".build.json", report), (".manifest.json", manifest)):
            binary.with_name(binary.name + suffix).write_text(json.dumps(value))
        binary.with_name(binary.name + ".sha256").write_text(f"{digest}  {binary.name}\n")
        return binary

    def corrupt(self, binary, case):
        if case == "missing binary":
            binary.unlink()
        elif case == "missing report":
            binary.with_name(binary.name + ".build.json").unlink()
        elif case == "bad manifest JSON":
            binary.with_name(binary.name + ".manifest.json").write_text("{")
        elif case == "bad report shape":
            binary.with_name(binary.name + ".build.json").write_text("[]")
        elif case == "bad checksum":
            binary.with_name(binary.name + ".sha256").write_text("wrong")
        elif case == "failed smoke":
            path = binary.with_name(binary.name + ".build.json")
            report = json.loads(path.read_text())
            report["smoke"] = "failed"
            path.write_text(json.dumps(report))

    def publish_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            release.publish(True)
        return output.getvalue()

    def test_valid_artifacts_are_included(self):
        self.assertIn("Verified 1 Lite and 1 Portable builds, 4 public release files", self.publish_output())

    def test_portable_failure_does_not_block_lite(self):
        for case in ("missing binary", "missing report", "bad manifest JSON", "bad report shape",
                     "bad checksum", "failed smoke"):
            with self.subTest(case=case):
                self.artifact("portable")
                self.corrupt(self.portable, case)
                output = self.publish_output()
                self.assertIn("Verified 1 Lite and 0 Portable builds, 2 public release files", output)
                self.assertIn("Skipping best-effort", output)

    def test_same_lite_failures_block_publication(self):
        for case in ("missing binary", "missing report", "bad manifest JSON", "bad report shape",
                     "bad checksum", "failed smoke"):
            with self.subTest(case=case):
                self.artifact("lite")
                self.corrupt(self.lite, case)
                with self.assertRaises((OSError, ValueError, TypeError, RuntimeError)):
                    self.publish_output()

    def test_missing_portable_lock_does_not_block_lite(self):
        (self.root / "config/portable-lock.json").unlink()
        self.assertIn("Verified 1 Lite and 0 Portable builds", self.publish_output())

    def test_skipped_portable_is_removed_from_resumed_draft(self):
        self.corrupt(self.portable, "bad checksum")
        draft = {"tag_name": release.downstream_tag("v1.2.3"), "id": 10, "draft": True}
        assets = [{"id": 20, "name": self.portable.name},
                  {"id": 21, "name": self.portable.name + ".sha256"}]
        with patch.object(release, "gh", return_value=json.dumps([[draft]])) as gh, \
             patch.object(release, "release_assets", return_value=assets), \
             patch.dict(os.environ, {"GITHUB_REPOSITORY": "local/test"}), \
             contextlib.redirect_stdout(io.StringIO()):
            release.publish(False)
        deleted = [call.args[-1] for call in gh.call_args_list if "DELETE" in call.args]
        self.assertEqual(deleted, ["repos/local/test/releases/assets/20", "repos/local/test/releases/assets/21"])
        upload = next(call.args for call in gh.call_args_list if call.args[:2] == ("release", "upload"))
        self.assertIn(str(self.lite), upload)
        self.assertNotIn(str(self.portable), upload)


class RuntimeSelection(unittest.TestCase):
    def test_semver_uses_upstream_engine_range(self):
        import runtime

        bun = shutil.which("bun")
        if not bun:
            self.skipTest("Bun unavailable")
        self.assertFalse(runtime.bun_satisfies(bun, "1.3.14", ">=1.4.4"))
        self.assertTrue(runtime.bun_satisfies(bun, "1.4.4", ">=1.4.4"))
        self.assertFalse(runtime.bun_satisfies(bun, "1.4.4-canary.1", ">=1.4.4"))

    def test_incompatible_stable_runtime_does_not_change_lock(self):
        import runtime

        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "runtime.json"
            original = '{"version":"1.3.14"}'
            lock.write_text(original)
            with patch.object(runtime, "LOCK", lock), \
                 patch.object(runtime, "registry_metadata", side_effect=[
                     {"engines": {"bun": ">=1.5.0"}}, {"version": "1.4.4"},
                 ]), patch.object(runtime, "bun_satisfies", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "does not satisfy"):
                    runtime.refresh_bun("v1.2.3", Path("bun"), Path(tmp), None)
            self.assertEqual(lock.read_text(), original)


    def test_corrupt_runtime_download_does_not_change_lock(self):
        import base64
        import runtime

        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "runtime.json"
            original = json.dumps({"version": "1.3.14", "assets": {"linux-x64": {
                "url": "https://registry.npmjs.org/@oven/bun-linux-x64-baseline/-/old.tgz",
            }}})
            lock.write_text(original)
            integrity = base64.b64encode(hashlib.sha512(b"expected archive").digest()).decode()
            with patch.object(runtime, "LOCK", lock), \
                 patch.object(runtime, "registry_metadata", side_effect=[
                     {"engines": {"bun": ">=1.4.4"}}, {"version": "1.4.4"},
                     {"dist": {"tarball": "https://registry.npmjs.org/new.tgz",
                               "integrity": "sha512-" + integrity}},
                 ]), patch.object(runtime, "bun_satisfies", side_effect=[False, True]), \
                 patch.object(runtime.urllib.request, "urlopen", return_value=io.BytesIO(b"corrupt")):
                with self.assertRaisesRegex(RuntimeError, "integrity mismatch"):
                    runtime.refresh_bun("v1.2.3", Path("bun"), Path(tmp), None)
            self.assertEqual(lock.read_text(), original)
            self.assertEqual({path.name for path in Path(tmp).iterdir()}, {"runtime.json"})


if __name__ == "__main__":
    unittest.main()
