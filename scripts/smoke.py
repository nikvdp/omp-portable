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
    # Chromium's Unix socket path must fit even when the checkout path is long.
    with tempfile.TemporaryDirectory(prefix="omp-smoke-", dir="/tmp") as temp:
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
            print(f"Smoke check {len(checks) + 1}: {' '.join(argv)}", flush=True)
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
        required = {"bin/omp.real", "launcher-bin/omp"}
        assert set(manifest["files"]).issuperset(required)
        assert manifest["target"] == target
        edition = manifest["edition"]
        assert edition in {"lite", "portable"}
        if edition == "lite":
            assert "portable" not in manifest
            assert not any(
                (root / p).exists() for p in ("browser", "helpers", "windows-seed")
            )
        else:
            portable = manifest["portable"]
            assert set(portable) == {"python", "browser", "components"}
            required.update(
                {
                    portable["python"],
                    portable["browser"],
                    "launcher-bin/python",
                    "launcher-bin/python3",
                    "launcher-bin/trafilatura",
                    "launcher-bin/chromium",
                }
            )
            assert set(manifest["files"]).issuperset(required)
            for relative in required:
                path = root / relative
                assert path.is_file() and not path.is_symlink(), relative
            assert not (root / "windows-seed").exists()
            forbidden = {
                "models/",
                "helpers/models/",
                "helpers/yt-dlp",
                "helpers/ffmpeg",
                "helpers/ffprobe",
            }
            assert not any(
                path == item.rstrip("/") or path.startswith(item)
                for path in manifest["files"]
                for item in forbidden
            )

            print("Smoke: bundled Python and standard library", flush=True)
            python = offline.run(
                [
                    str(root / "launcher-bin/python3"),
                    "-c",
                    (
                        "import json, pathlib, sqlite3, sys; "
                        "print(json.dumps({'value': sqlite3.connect(':memory:')."
                        "execute('select 6 * 7').fetchone()[0], "
                        "'stdlib': pathlib.__file__, 'executable': sys.executable}))"
                    ),
                ],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert python.returncode == 0, (python.stdout, python.stderr)
            python_result = json.loads(python.stdout)
            assert python_result["value"] == 42
            assert str(root / "helpers/python") in python_result["stdlib"]
            assert python_result["executable"] == str(root / portable["python"])
            checks.append({"portable_python": python_result})

            python_alias = offline.run(
                [str(root / "launcher-bin/python"), "-c", "print('python-alias-ok')"],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert (
                python_alias.returncode == 0
                and python_alias.stdout.strip() == "python-alias-ok"
            ), (
                python_alias.stdout,
                python_alias.stderr,
            )
            html = (
                "<html><body><nav>discard navigation</nav><article><h1>Portable "
                "Extraction</h1><p>trafilatura-local-sentinel appears in this "
                "meaningful article body. The bundled extractor must identify "
                "the article without downloading anything or relying on a host "
                "Python installation. This paragraph deliberately contains "
                "enough complete prose for normal content-quality thresholds. "
                "A successful result demonstrates HTML parsing, text selection, "
                "Markdown serialization, and all imported runtime dependencies "
                "from the relocated private standard library.</p></article>"
                "</body></html>"
            )
            print("Smoke: extracting local HTML with bundled trafilatura", flush=True)
            extracted = offline.run(
                [
                    str(root / "launcher-bin/trafilatura"),
                    "--output-format",
                    "markdown",
                    "--no-comments",
                ],
                cwd=cwd,
                env=env,
                input=html,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert extracted.returncode == 0, (extracted.stdout, extracted.stderr)
            assert "trafilatura-local-sentinel" in extracted.stdout
            checks.append(
                {
                    "portable_trafilatura": extracted.stdout[:3000],
                    "input": "local HTML on stdin",
                }
            )

            browser_fixture = cwd / "browser.html"
            browser_fixture.write_text(
                "<div id='result'>before</div><script>"
                "document.querySelector('#result').textContent="
                "['browser','rendered','sentinel'].join('-')</script>"
            )
            print("Smoke: rendering JavaScript with bundled Chromium", flush=True)
            command_read, command_write = os.pipe()
            response_read, response_write = os.pipe()
            browser = None
            stderr_path = base / "browser.stderr"
            open_fds = {command_read, command_write, response_read, response_write}
            try:
                guarded_argv, guarded_options = offline.guarded(
                    [
                        str(root / "launcher-bin/chromium"),
                        "--headless",
                        "--disable-gpu",
                        "--no-sandbox",
                        "--disable-background-networking",
                        "--disable-breakpad",
                        "--disable-crash-reporter",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        "--password-store=basic",
                        "--use-mock-keychain",
                        f"--user-data-dir={base / 'browser-profile'}",
                        "--remote-debugging-pipe",
                        "about:blank",
                    ]
                )
                guard_preexec = guarded_options.pop("preexec_fn", None)

                def child_setup():
                    os.dup2(command_read, 3)
                    os.dup2(response_write, 4)
                    if guard_preexec is not None:
                        guard_preexec()

                with stderr_path.open("wb") as browser_stderr:
                    browser = subprocess.Popen(
                        guarded_argv,
                        cwd=cwd,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=browser_stderr,
                        pass_fds=tuple({3, 4, command_read, response_write}),
                        preexec_fn=child_setup,
                        **guarded_options,
                    )
                    os.close(command_read)
                    open_fds.remove(command_read)
                    os.close(response_write)
                    open_fds.remove(response_write)
                    deadline = time.monotonic() + 45
                    response_buffer = bytearray()
                    request_id = 0
                    events = []

                    def diagnostics():
                        return (
                            browser.poll(),
                            stderr_path.read_text(errors="replace"),
                        )

                    def receive():
                        while b"\0" not in response_buffer:
                            remaining = deadline - time.monotonic()
                            assert remaining > 0, ("CDP timeout", diagnostics())
                            assert select.select([response_read], [], [], remaining)[
                                0
                            ], ("CDP timeout", diagnostics())
                            chunk = os.read(response_read, 65536)
                            assert chunk, ("CDP pipe closed", diagnostics())
                            response_buffer.extend(chunk)
                        raw, _, rest = response_buffer.partition(b"\0")
                        response_buffer[:] = rest
                        return json.loads(raw)

                    def cdp(method, params=None, session_id=None):
                        nonlocal request_id
                        request_id += 1
                        request = {
                            "id": request_id,
                            "method": method,
                            "params": params or {},
                        }
                        if session_id is not None:
                            request["sessionId"] = session_id
                        os.write(
                            command_write,
                            json.dumps(request, separators=(",", ":")).encode() + b"\0",
                        )
                        while True:
                            response = receive()
                            if response.get("id") == request_id:
                                assert "error" not in response, (
                                    request,
                                    response,
                                    diagnostics(),
                                )
                                return response["result"]
                            events.append(response)

                    browser_version = cdp("Browser.getVersion")
                    target_id = cdp("Target.createTarget", {"url": "about:blank"})[
                        "targetId"
                    ]
                    session_id = cdp(
                        "Target.attachToTarget",
                        {"targetId": target_id, "flatten": True},
                    )["sessionId"]
                    cdp("Page.enable", session_id=session_id)
                    cdp(
                        "Page.setLifecycleEventsEnabled",
                        {"enabled": True},
                        session_id,
                    )
                    navigation = cdp(
                        "Page.navigate",
                        {"url": browser_fixture.as_uri()},
                        session_id,
                    )
                    loader_id = navigation["loaderId"]
                    while not any(
                        event.get("method") == "Page.lifecycleEvent"
                        and event.get("sessionId") == session_id
                        and event.get("params", {}).get("name") == "load"
                        and event.get("params", {}).get("loaderId") == loader_id
                        for event in events
                    ):
                        events.append(receive())
                    evaluation = cdp(
                        "Runtime.evaluate",
                        {
                            "expression": (
                                "document.querySelector('#result')?.textContent"
                            ),
                            "returnByValue": True,
                        },
                        session_id,
                    )
                    rendered = evaluation.get("result", {}).get("value")
                    assert rendered == "browser-rendered-sentinel", (
                        rendered,
                        diagnostics(),
                    )
                    checks.append(
                        {
                            "portable_browser": "rendered local JavaScript page over CDP pipe",
                            "product": browser_version["product"],
                            "dom": rendered,
                        }
                    )
            finally:
                for fd in open_fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if browser is not None and browser.poll() is None:
                    browser.terminate()
                    try:
                        browser.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        browser.kill()
                        browser.wait()
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
        rpc_argv = [
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
        ]
        commands = [
            {"id": "state", "type": "get_state"},
            {
                "id": "shell",
                "type": "bash",
                "command": "printf portable-shell-sentinel",
            },
        ]
        if edition == "portable":
            commands.append(
                {
                    "id": "browser-env",
                    "type": "bash",
                    "command": 'printf %s "$PUPPETEER_EXECUTABLE_PATH"',
                }
            )
        err = base / "rpc.stderr"
        with (
            err.open("w") as stderr,
            offline.popen(
                rpc_argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                bufsize=1,
            ) as p,
        ):
            try:
                for command in commands:
                    print(f"Smoke: OMP RPC {command['id']}", flush=True)
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
                            if command["id"] == "browser-env":
                                expected_browser = str(root / "launcher-bin/chromium")
                                assert expected_browser in json.dumps(message), message
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
        if edition == "portable":
            print("Smoke: preserving explicit browser override", flush=True)
            override = "/explicit/user/chromium"
            override_env = {**env, "PUPPETEER_EXECUTABLE_PATH": override}
            override_err = base / "rpc-override.stderr"
            with (
                override_err.open("w") as stderr,
                offline.popen(
                    rpc_argv,
                    cwd=cwd,
                    env=override_env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    text=True,
                    bufsize=1,
                ) as p,
            ):
                try:
                    command = {
                        "id": "browser-override",
                        "type": "bash",
                        "command": 'printf %s "$PUPPETEER_EXECUTABLE_PATH"',
                    }
                    p.stdin.write(json.dumps(command) + "\n")
                    p.stdin.flush()
                    deadline = time.monotonic() + 40
                    while True:
                        remaining = deadline - time.monotonic()
                        assert remaining > 0, (
                            "RPC override timeout",
                            override_err.read_text(),
                        )
                        assert select.select([p.stdout], [], [], remaining)[0], (
                            "RPC override timeout",
                            override_err.read_text(),
                        )
                        line = p.stdout.readline()
                        assert line, (
                            "RPC override exited",
                            p.poll(),
                            override_err.read_text(),
                        )
                        try:
                            message = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if message.get("id") == command["id"]:
                            assert message.get("success"), (
                                message,
                                override_err.read_text(),
                            )
                            assert override in json.dumps(message), message
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
    report = args.report or ROOT / "build" / target / edition / "smoke-results.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(checks, indent=2) + "\n")
    print(f"PASS: {len(checks)} real-OMP checks; {report}")


if __name__ == "__main__":
    main()
