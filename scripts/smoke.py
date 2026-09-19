#!/usr/bin/env python3
"""Exercise the finished SFX with fresh state and a kernel network guard."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time
import offline
import sys
from platforms import detect_target, cache_base

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("artifact", type=Path)
    ap.add_argument(
        "--strict-host",
        action="store_true",
        help="Require upstream config writes to work too",
    )
    ap.add_argument("--report", type=Path, help="Write structured test evidence here")
    args = ap.parse_args()
    target = detect_target()
    lock = json.loads((ROOT / "config/upstream-lock.json").read_text())
    version = lock["tag"].removeprefix("v")
    (ROOT / "build").mkdir(exist_ok=True)
    guard_probe = offline.run(
        [
            sys.executable,
            "-c",
            "import socket; s=socket.socket(); s.connect(('127.0.0.1',9))",
        ],
        capture_output=True,
        text=True,
    )
    assert guard_probe.returncode != 0 and "PermissionError" in guard_probe.stderr, (
        "Network guard was not enforced",
        guard_probe.stderr,
    )
    artifact = args.artifact.resolve()
    checks = []
    with tempfile.TemporaryDirectory(prefix="real-smoke-", dir=ROOT / "build") as temp:
        base = Path(temp)
        home = base / "home"
        home.mkdir()
        cwd = base / "project"
        cwd.mkdir()
        tools = base / "path"
        tools.mkdir()
        tmp = base / "tmp"
        tmp.mkdir()
        exe = base / "relocated-omp"
        shutil.copy2(artifact, exe)
        env = {
            "HOME": str(home),
            "PATH": str(tools),
            "TMPDIR": str(tmp),
            "TERM": "dumb",
            "NO_COLOR": "1",
        }

        def run(*argv, expected=0):
            p = offline.run(
                [str(exe), *argv],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert p.returncode == expected, (argv, p.returncode, p.stdout, p.stderr)
            checks.append(
                {"command": list(argv), "exit": p.returncode, "stdout": p.stdout[:3000]}
            )
            return p.stdout

        assert version in run("--version")
        assert "COMMANDS" in run("--help")
        assert "off" in run("config", "get", "memory.backend")
        speech = json.loads(run("setup", "speech", "--check", "--json", expected=1))
        assert speech and all(not entry["ready"] for entry in speech.values())
        assert str(home / ".omp/agent") in run("config", "path")
        (cwd / "fixture.txt").write_text("portable-search-sentinel\n")
        assert "portable-search-sentinel" in run(
            "grep", "portable-search-sentinel", "fixture.txt"
        )
        assert "portable-search-sentinel" in run("read", "fixture.txt")
        # Existing configuration and intentional user state must remain visible.
        config = home / ".omp/agent/config.yml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("memory:\n  backend: mnemopi\n")
        assert "mnemopi" in run("config", "get", "memory.backend")
        state = home / ".omp/agent/sessions/sentinel"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("keep-session")
        plugin = home / ".omp/agent/plugins/sentinel"
        plugin.parent.mkdir(parents=True, exist_ok=True)
        plugin.write_text("keep-plugin")
        host = tools / "omp"
        host.write_text("#!/bin/sh\necho HOST-OMP-INVOKED >&2\nexit 99\n")
        host.chmod(0o755)
        host_hash = hashlib.sha256(host.read_bytes()).hexdigest()
        for cmd in [
            ("update",),
            ("update", "--plugins"),
            ("--profile", "work", "update"),
        ]:
            assert "portable OMP build" in run(*cmd)
        assert hashlib.sha256(host.read_bytes()).hexdigest() == host_hash
        assert (
            state.read_text() == "keep-session" and plugin.read_text() == "keep-plugin"
        )
        config.write_text("memory:\n  backend: off\n")
        assert str(home / ".omp/profiles/work/agent") in run(
            "--profile", "work", "config", "path"
        )
        env["PI_PROFILE"] = "legacy"
        assert str(home / ".omp/profiles/legacy/agent") in run("config", "path")
        env["OMP_PROFILE"] = ""
        assert str(home / ".omp/agent") in run("config", "path")
        del env["OMP_PROFILE"]
        del env["PI_PROFILE"]
        root = next(cache_base(home, target).glob("*/READY")).parent
        manifest = json.loads((root / "manifest.json").read_text())
        write = offline.run(
            [str(exe), "config", "set", "memory.backend", "off"],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if write.returncode:
            assert target.startswith("linux-"), write.stderr
            direct = offline.run(
                [str(root / "bin/omp.real"), "config", "set", "memory.backend", "off"],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert (
                direct.returncode == write.returncode
                and "Failed to acquire native file lock" in write.stderr
                and "Operation not permitted" in write.stderr
                and direct.stderr == write.stderr
            ), (write.stderr, direct.stderr)
            checks.append(
                {
                    "limitation": "Upstream config writes fail because this host denies abstract Unix socket locks",
                    "packaged_stderr": write.stderr,
                    "unwrapped_stderr": direct.stderr,
                }
            )
            assert not args.strict_host, write.stderr
        else:
            checks.append(
                {"command": ["config", "set", "memory.backend", "off"], "exit": 0}
            )
        with (root / "bin/omp.real").open("rb") as f:
            assert (
                hashlib.file_digest(f, "sha256").hexdigest()
                == manifest["upstream"]["sha256"]
            )
        assert set(manifest["files"]).issuperset({"bin/omp.real", "launcher-bin/omp"})
        assert not any(
            (root / p).exists() for p in ("browser", "helpers", "windows-seed")
        )
        # Direct private shim is the same distribution, even beside a fake host omp.
        p = offline.run(
            [str(root / "launcher-bin/omp"), "--version"],
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert p.returncode == 0 and version in p.stdout, (p.stdout, p.stderr)
        checks.append(
            {
                "command": ["private-shim", "--version"],
                "exit": p.returncode,
                "stdout": p.stdout,
            }
        )
        # Agent initialization and shell execution through upstream's actual RPC interface.
        # /bin/sh is an ordinary OS facility, not a bundled language runtime.
        (tools / "sh").symlink_to("/bin/sh")
        (tools / "bash").symlink_to("/bin/bash")
        env["SHELL"] = "/bin/bash"
        env["OPENAI_API_KEY"] = "offline-test-placeholder-never-sent"
        err = base / "rpc.stderr"
        with err.open("w") as stderr, offline.popen(
            [
                str(exe),
                "--mode",
                "rpc",
                "--no-session",
                "--no-lsp",
                "--no-extensions",
                "--no-skills",
                "--no-rules",
                "--no-title",
                "--tools",
                "read,edit,write,bash",
            ],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            bufsize=1,
        ) as p:
            try:
                for command in [
                    {"id": "state", "type": "get_state"},
                    {
                        "id": "shell",
                        "type": "bash",
                        "command": "printf portable-shell-sentinel",
                    },
                ]:
                    p.stdin.write(json.dumps(command) + "\n")
                    p.stdin.flush()
                    deadline = time.monotonic() + 40
                    while True:
                        remaining = deadline - time.monotonic()
                        assert remaining > 0, ("RPC timeout", err.read_text())
                        assert select.select([p.stdout], [], [], remaining)[0], (
                            "RPC timeout",
                            err.read_text(),
                        )
                        line = p.stdout.readline()
                        assert line, ("RPC exited", p.poll(), err.read_text())
                        try:
                            message = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if message.get("id") == command["id"]:
                            assert message.get("success"), (message, err.read_text())
                            if command["id"] == "shell":
                                assert "portable-shell-sentinel" in json.dumps(message)
                            if command["id"] == "state":
                                data = message["data"]
                                message = {
                                    **message,
                                    "data": {
                                        k: data.get(k)
                                        for k in (
                                            "sessionId",
                                            "isStreaming",
                                            "messageCount",
                                        )
                                    },
                                }
                            checks.append({"rpc": command, "response": message})
                            break
            finally:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        checks.append(
            {
                "network": (
                    "macOS sandbox denies Internet outbound connections"
                    if target.startswith("darwin-")
                    else "Linux seccomp denies Internet sockets"
                ),
                "state": "fresh HOME and PATH without OMP/Bun/Node/Python/browser; shell added only for shell test",
            }
        )
    report = args.report or ROOT / "build" / target / "smoke-results.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(checks, indent=2) + "\n")
    print(f"PASS: {len(checks)} real-OMP checks; {report}")


if __name__ == "__main__":
    main()
