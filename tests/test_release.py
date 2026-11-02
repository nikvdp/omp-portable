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
        (config / "runtime-package.json").write_text('{}')
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

    def test_lite_publishes_without_portable_inputs(self):
        self.lock["repo"] = "local/upstream"
        (self.root / "config/upstream-lock.json").write_text(json.dumps(self.lock))
        self.corrupt(self.portable, "bad checksum")
        (self.root / "config/portable-lock.json").unlink()
        with patch.object(release, "gh", return_value="[[]]") as gh, \
             patch.dict(os.environ, {"GITHUB_REPOSITORY": "local/test", "GITHUB_SHA": "abc123"}), \
             contextlib.redirect_stdout(io.StringIO()):
            release.publish(False)
        upload = next(call.args for call in gh.call_args_list if call.args[:2] == ("release", "upload"))
        self.assertEqual(upload[6:], (str(self.lite), str(self.lite) + ".sha256"))

    def test_same_lite_failures_block_publication(self):
        for case in ("missing binary", "missing report", "bad manifest JSON", "bad report shape",
                     "bad checksum", "failed smoke"):
            with self.subTest(case=case):
                self.artifact("lite")
                self.corrupt(self.lite, case)
                with patch.object(release, "gh") as gh:
                    with self.assertRaises((OSError, ValueError, TypeError, RuntimeError)):
                        release.publish(False)
                    gh.assert_not_called()

    def handoff(self):
        value = {
            "tag": release.downstream_tag("v1.2.3"), "release_id": 10,
            "source_sha": "abc123",
            "locks": {name: hashlib.sha256((self.root / "config" / name).read_bytes()).hexdigest()
                      for name in release.HANDOFF_LOCKS},
        }
        (self.root / "config/release.json").write_text(json.dumps(value))
        return value

    def destination(self):
        return {"id": 10, "tag_name": release.downstream_tag("v1.2.3"),
                "draft": False, "target_commitish": "abc123"}

    def lite_assets(self):
        return [{"name": self.lite.name}, {"name": self.lite.name + ".sha256"}]

    def test_portable_appends_without_mutating_lite(self):
        self.handoff()
        with patch.object(release, "gh", return_value=json.dumps(self.destination())) as gh, \
             patch.object(release, "release_assets", return_value=self.lite_assets()), \
             patch.dict(os.environ, {"GITHUB_REPOSITORY": "local/test", "RELEASE_SOURCE_SHA": "abc123"}):
            release.publish_portable("linux-x64", False)
        writes = [call.args for call in gh.call_args_list if call.args[:1] != ("api",)]
        self.assertEqual(writes, [("release", "upload", release.downstream_tag("v1.2.3"),
                                 "--repo", "local/test", str(self.portable), str(self.portable) + ".sha256")])

    def test_portable_rejects_invalid_artifacts_before_remote_writes(self):
        self.handoff()
        for case in ("missing binary", "missing report", "bad manifest JSON", "bad report shape",
                     "bad checksum", "failed smoke"):
            with self.subTest(case=case):
                self.artifact("portable")
                self.corrupt(self.portable, case)
                with patch.dict(os.environ, {"RELEASE_SOURCE_SHA": "abc123"}), \
                     patch.object(release, "gh") as gh:
                    with self.assertRaises((OSError, ValueError, TypeError, RuntimeError)):
                        release.publish_portable("linux-x64", False)
                    gh.assert_not_called()

    def test_portable_rejects_changed_locks_source_and_tag(self):
        for case in ("source", "tag", *release.HANDOFF_LOCKS):
            with self.subTest(case=case):
                handoff = self.handoff()
                if case == "source":
                    handoff["source_sha"] = "different"
                elif case == "tag":
                    handoff["tag"] = "omp-v9.9.9-r6"
                else:
                    handoff["locks"][case] = "wrong hash"
                (self.root / "config/release.json").write_text(json.dumps(handoff))
                with patch.dict(os.environ, {"RELEASE_SOURCE_SHA": "abc123"}), \
                     patch.object(release, "gh") as gh:
                    with self.assertRaises(RuntimeError):
                        release.publish_portable("linux-x64", False)
                    gh.assert_not_called()

    def test_portable_rejects_wrong_destination_or_missing_lite(self):
        self.handoff()
        for changes, assets in (({"draft": True}, self.lite_assets()),
                                ({"tag_name": "wrong"}, self.lite_assets()),
                                ({"target_commitish": "wrong"}, self.lite_assets()), ({}, [])):
            with self.subTest(changes=changes, assets=assets), \
                 patch.object(release, "gh", return_value=json.dumps(self.destination() | changes)) as gh, \
                 patch.object(release, "release_assets", return_value=assets), \
                 patch.dict(os.environ, {"GITHUB_REPOSITORY": "local/test", "RELEASE_SOURCE_SHA": "abc123"}):
                with self.assertRaises(RuntimeError):
                    release.publish_portable("linux-x64", False)
                self.assertTrue(all(call.args[0] == "api" for call in gh.call_args_list))

    def test_portable_rerun_preserves_existing_assets(self):
        self.handoff()
        sidecar = self.portable.with_name(self.portable.name + ".sha256")
        assets = self.lite_assets() + [
            {"name": path.name, "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in (self.portable, sidecar)
        ]
        with patch.object(release, "gh", return_value=json.dumps(self.destination())) as gh, \
             patch.object(release, "release_assets", return_value=assets), \
             patch.dict(os.environ, {"GITHUB_REPOSITORY": "local/test", "RELEASE_SOURCE_SHA": "abc123"}):
            release.publish_portable("linux-x64", False)
            self.assertTrue(all(call.args[0] == "api" for call in gh.call_args_list))
            assets[-1]["digest"] = "sha256:changed"
            with self.assertRaisesRegex(RuntimeError, "Refusing to replace"):
                release.publish_portable("linux-x64", False)
            self.assertTrue(all(call.args[0] == "api" for call in gh.call_args_list))

    def test_lite_publication_handoff_uses_resolved_inputs(self):
        self.lock["repo"] = "local/upstream"
        (self.root / "config/upstream-lock.json").write_text(json.dumps(self.lock))
        def api(*args):
            if args[:3] == ("api", "--paginate", "--slurp"):
                return "[[]]"
            if args == ("api", f"repos/local/test/releases/tags/{release.downstream_tag('v1.2.3')}"):
                return json.dumps(self.destination())
            return ""
        with patch.object(release, "gh", side_effect=api) as gh, \
             patch.dict(os.environ, {"GITHUB_REPOSITORY": "local/test", "GITHUB_SHA": "abc123"}), \
             contextlib.redirect_stdout(io.StringIO()):
            release.publish(False)
            release.write_handoff()
        handoff = json.loads((self.root / "config/release.json").read_text())
        self.assertEqual(handoff["source_sha"], "abc123")
        self.assertEqual(handoff["release_id"], 10)
        (self.root / "config/upstream-lock.json").write_text('{"tag":"v9.9.9"}')
        with patch.dict(os.environ, {"RELEASE_SOURCE_SHA": "abc123"}):
            with self.assertRaisesRegex(RuntimeError, "lock changed"):
                release.check_handoff()
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
