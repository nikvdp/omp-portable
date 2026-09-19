import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "target/release/sfx-stub"
PACK = ROOT / "target/release/sfx-pack"


def sha(p):
    with open(p, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Lifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(dir=ROOT / "build")
        cls.base = Path(cls.work.name)
        cls.stage = cls.base / "stage"
        (cls.stage / "bin").mkdir(parents=True)
        (cls.stage / "launcher-bin").mkdir()
        subprocess.run(
            [
                "cc",
                "-O2",
                str(ROOT / "tests/fixture.c"),
                "-o",
                str(cls.stage / "bin/omp.real"),
            ],
            check=True,
        )
        shutil.copy2(STUB, cls.stage / "launcher-bin/omp")
        cls.write_manifest()
        cls.artifact = cls.base / "fixture"
        cls.pack(cls.artifact)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    @classmethod
    def write_manifest(cls):
        files = {
            str(p.relative_to(cls.stage)): {
                "size": p.stat().st_size,
                "sha256": sha(p),
                "executable": True,
            }
            for p in cls.stage.rglob("*")
            if p.is_file() and p.name != "manifest.json"
        }
        (cls.stage / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "edition": "lite",
                    "target": "linux-x64",
                    "upstream": {"fixture": True},
                    "builder": {},
                    "files": files,
                }
            )
        )

    @classmethod
    def pack(cls, out, limit=1990000000):
        return subprocess.run(
            [str(PACK), str(STUB), str(cls.stage), str(out), str(limit)],
            capture_output=True,
            text=True,
            check=limit > 100,
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=self.base)
        self.dir = Path(self.tmp.name)
        self.home = self.dir / "home"
        self.home.mkdir()
        self.cwd = self.dir / "unrelated"
        self.cwd.mkdir()
        self.env = {
            "HOME": str(self.home),
            "PATH": str(self.dir / "empty"),
            "TMPDIR": str(self.dir),
        }
        Path(self.env["PATH"]).mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_sfx(self, *args, exe=None, **kw):
        return subprocess.run(
            [str(exe or self.artifact), *args],
            env=self.env,
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=20,
            **kw,
        )

    def root(self):
        return next((self.home / ".cache/omp-portable/dist").glob("*/READY")).parent

    def test_extract_reuse_arguments_state(self):
        for key in [
            "PI_CONFIG_DIR",
            "PI_CODING_AGENT_DIR",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
        ]:
            self.env[key] = str(self.dir / key)
        p = self.run_sfx("hello world", "--", "--profile", "../../literal")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("arg=hello world", p.stdout)
        self.assertIn("arg=../../literal", p.stdout)
        self.assertIn(f"cwd={self.cwd}", p.stdout)
        for k, v in self.env.items():
            if k not in ["PATH", "TMPDIR"]:
                self.assertIn(f"{k}={v}", p.stdout)
        root = self.root()
        inode = (root / "bin/omp.real").stat().st_ino
        self.assertEqual(self.run_sfx().returncode, 0)
        self.assertEqual(inode, (root / "bin/omp.real").stat().st_ino)

    def test_concurrent_first_run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.run_sfx(), range(16)))
        self.assertTrue(
            all(p.returncode == 0 for p in results), [p.stderr for p in results]
        )
        self.assertEqual(
            len(list((self.home / ".cache/omp-portable/dist").glob("*/READY"))), 1
        )

    def test_updates_and_host_shim(self):
        host = Path(self.env["PATH"]) / "omp"
        host.write_text("#!/bin/sh\nexit 99\n")
        host.chmod(0o755)
        before = sha(host)
        for args in [
            ("update",),
            ("update", "--plugins"),
            ("--profile", "work", "update", "--check"),
            ("--profile=work", "update"),
        ]:
            p = self.run_sfx(*args)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("portable OMP build", p.stdout)
        self.assertEqual(sha(host), before)
        p = self.run_sfx("child")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("portable OMP build", p.stdout)
        self.assertEqual(sha(host), before)

    def test_profiles(self):
        self.env["OMP_PROFILE"] = "work"
        self.assertEqual(self.run_sfx().returncode, 0)
        self.assertTrue((self.root() / "xdg-cache/omp/profiles/work").is_dir())
        self.assertEqual(self.run_sfx("--profile=other").returncode, 0)
        self.assertTrue((self.root() / "xdg-cache/omp/profiles/other").is_dir())

    def test_literal_profile_is_not_rejected(self):
        p = self.run_sfx("grep", "--profile", "../../file")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("arg=../../file", p.stdout)

    def test_input_and_exit(self):
        self.assertEqual(self.run_sfx("exit").returncode, 37)
        self.assertEqual(
            self.run_sfx("input", input="hello stdin\n").stdout, "hello stdin\n"
        )

    def test_signal_handoff(self):
        with subprocess.Popen(
            [str(self.artifact), "wait"],
            env=self.env,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as p:
            self.assertTrue(select.select([p.stdout], [], [], 10)[0])
            self.assertEqual(p.stdout.readline().strip(), "waiting")
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(timeout=5), -signal.SIGTERM)

    def test_tty(self):
        master, slave = pty.openpty()
        try:
            p = subprocess.run(
                [str(self.artifact)],
                env=self.env,
                cwd=self.cwd,
                stdin=slave,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("tty=1", p.stdout)
        finally:
            os.close(master)
            os.close(slave)

    def test_payload_corruption_even_with_cache(self):
        self.assertEqual(self.run_sfx().returncode, 0)
        bad = self.dir / "bad"
        shutil.copy2(self.artifact, bad)
        with bad.open("r+b") as f:
            f.seek(-64, 2)
            footer = f.read()
            off = struct.unpack_from("<Q", footer, 16)[0]
            f.seek(off + 10)
            b = f.read(1)
            f.seek(off + 10)
            f.write(bytes([b[0] ^ 1]))
        p = self.run_sfx(exe=bad)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("SHA-256 mismatch", p.stderr)

    def test_cached_corruption(self):
        self.run_sfx()
        (self.root() / "bin/omp.real").write_bytes(b"bad")
        p = self.run_sfx()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("component mismatch", p.stderr)

    def test_truncated_footer(self):
        bad = self.dir / "bad"
        shutil.copy2(self.artifact, bad)
        with bad.open("r+b") as f:
            f.truncate(bad.stat().st_size - 1)
        self.assertNotEqual(self.run_sfx(exe=bad).returncode, 0)

    def test_size_budget(self):
        p = self.pack(self.dir / "too-big", 100)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((self.dir / "too-big").exists())
        self.assertFalse((self.dir / "too-big.partial").exists())

    def test_stale_interrupted_extraction(self):
        self.run_sfx()
        root = self.root()
        shutil.rmtree(root)
        stale = root.with_name(root.name + ".tmp.12345")
        stale.mkdir()
        (stale / "partial").write_text("unfinished")
        p = self.run_sfx()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(stale.exists())
        self.assertTrue((root / "READY").exists())

    def test_cache_symlink_rejected(self):
        self.run_sfx()
        root = self.root()
        shutil.rmtree(root)
        root.symlink_to(self.cwd, target_is_directory=True)
        self.assertNotEqual(self.run_sfx().returncode, 0)

    def test_killed_extraction_recovers(self):
        big = self.dir / "big-stage"
        shutil.copytree(self.stage, big)
        pad = big / "padding"
        pad.write_bytes(b"X" * (64 * 1024 * 1024))
        manifest = json.loads((big / "manifest.json").read_text())
        manifest["files"]["padding"] = {
            "size": pad.stat().st_size,
            "sha256": sha(pad),
            "executable": False,
        }
        (big / "manifest.json").write_text(json.dumps(manifest))
        artifact = self.dir / "large"
        subprocess.run(
            [str(PACK), str(STUB), str(big), str(artifact), "1990000000"],
            check=True,
            capture_output=True,
        )
        p = subprocess.Popen(
            [str(artifact)],
            env=self.env,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 10
            detected = False
            while time.monotonic() < deadline and p.poll() is None:
                if list(
                    (self.home / ".cache/omp-portable/dist").glob("*.tmp.*/padding")
                ):
                    p.send_signal(signal.SIGSTOP)
                    detected = True
                    break
                time.sleep(0.001)
            self.assertTrue(detected, "could not interrupt active extraction")
            p.kill()
            p.wait(timeout=5)
            self.assertFalse(
                list((self.home / ".cache/omp-portable/dist").glob("*/READY"))
            )
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            p.stdout.close()
            p.stderr.close()
        result = self.run_sfx(exe=artifact)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root() / "READY").exists())
        self.assertFalse(list(self.root().parent.glob("*.tmp.*")))

    def test_pack_rejects_traversal_and_symlink(self):
        stage = self.dir / "unsafe-stage"
        shutil.copytree(self.stage, stage)
        manifest = json.loads((stage / "manifest.json").read_text())
        manifest["files"]["../escape"] = manifest["files"]["bin/omp.real"]
        (stage / "manifest.json").write_text(json.dumps(manifest))
        result = subprocess.run(
            [str(PACK), str(STUB), str(stage), str(self.dir / "bad"), "1990000000"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe payload path", result.stderr)
        del manifest["files"]["../escape"]
        (stage / "manifest.json").write_text(json.dumps(manifest))
        (stage / "bin/omp.real").unlink()
        (stage / "bin/omp.real").symlink_to(self.stage / "bin/omp.real")
        result = subprocess.run(
            [str(PACK), str(STUB), str(stage), str(self.dir / "bad"), "1990000000"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_deterministic_packing(self):
        second = self.dir / "second"
        self.pack(second)
        self.assertEqual(sha(second), sha(self.artifact))


if __name__ == "__main__":
    unittest.main()
