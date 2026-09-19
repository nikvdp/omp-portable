#!/usr/bin/env python3
"""Build and verify a native single-file OMP Lite for this Linux or Mac host."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from licenses import collect
from platforms import ROOT, TARGETS, detect_target, cargo_environment
from packaging import binaries, package


def sha(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def acquire(url, path, expected, size=None):
    if not path.exists() or sha(path) != expected:
        tmp = path.with_name(path.name + ".download")
        req = urllib.request.Request(
            url, headers={"User-Agent": "omp-portable-builder"}
        )
        # macOS framework Python sometimes lacks installed CA certificates.
        # curl uses the host certificate store; keep TLS verification enabled.
        if shutil.which("curl"):
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--retry",
                    "3",
                    "--connect-timeout",
                    "30",
                    "--max-time",
                    "900",
                    url,
                    "--output",
                    str(tmp),
                ],
                check=True,
            )
        else:
            with urllib.request.urlopen(req, timeout=120) as src, tmp.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
        if sha(tmp) != expected:
            tmp.unlink()
            raise RuntimeError(f"Hash mismatch: {url}")
        tmp.replace(path)
    if size is not None and path.stat().st_size != size:
        raise RuntimeError(f"Size mismatch: {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=sorted(TARGETS),
        help="Defaults to the native host; cross-building is rejected",
    )
    parser.add_argument(
        "--edition",
        choices=["lite"],
        default="lite",
        help="Lite preserves normal optional on-demand installs",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print resolved build plan without downloading or compiling",
    )
    parser.add_argument(
        "--skip-compile",
        action="store_true",
        help="Reuse explicitly target-scoped compiled tools",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Create artifact without running it; recorded as unverified",
    )
    parser.add_argument(
        "--strict-host",
        action="store_true",
        help="Also require upstream config writes on this host",
    )
    args = parser.parse_args(argv)
    host = detect_target()
    target = args.target or host
    if target != host:
        parser.error(
            f"Build {target} on a native {target} host; this process is {host}"
        )
    lock = json.loads((ROOT / "config/upstream-lock.json").read_text())
    asset = lock["assets"][target]
    cfg = TARGETS[target]
    policy = tomllib.loads((ROOT / "config/editions.toml").read_text())[args.edition]
    plan = {
        "target": target,
        "rust_target": cfg["rust"],
        "edition": args.edition,
        "upstream_tag": lock["tag"],
        "asset": asset["name"],
        "sha256": asset["sha256"],
        "container": cfg["container"],
        "max_sfx_bytes": policy["max_sfx_bytes"],
    }
    print(json.dumps(plan, indent=2), flush=True)
    if args.plan:
        return
    env = cargo_environment()
    for tool in ["cargo", "rustc", "cc", "git"]:
        if not shutil.which(tool, path=env["PATH"]):
            parser.error(f"Missing build tool: {tool}. See README.md prerequisites.")
    if target.startswith("darwin-"):
        for tool in ["/usr/bin/codesign", "/usr/bin/sandbox-exec"]:
            if not Path(tool).exists():
                parser.error(f"Required macOS build/test tool missing: {tool}")
        subprocess.run(["xcrun", "--find", "clang"], check=True)
    if (
        any(
            policy[k]
            for k in ("browser", "speech_default", "mnemopi", "yt_dlp", "trafilatura")
        )
        or policy["local_models"] != "none"
    ):
        raise RuntimeError("Lite policy unexpectedly enables optional components")
    build = ROOT / "build" / target
    cache = ROOT / "build/upstream"
    stage = build / "stage"
    build.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    (build / "build-plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    for item in [asset, lock["license"], *lock["reviewed_sources"]]:
        acquire(item["url"], cache / item["name"], item["sha256"], item.get("size"))
    if not args.skip_compile:
        subprocess.run(
            ["cargo", "build", "--locked", "--release", "--target", cfg["rust"]],
            cwd=ROOT,
            env=env,
            check=True,
        )
    bins = binaries(target)
    # Keep a standalone unembedded shim before macOS's final cargo rustc pass.
    for name in ["sfx-stub", "sfx-pack"]:
        if not (bins / name).is_file():
            raise RuntimeError(f"Missing {bins/name}; remove --skip-compile")
    if stage.exists():
        shutil.rmtree(stage)
    for folder in ("bin", "launcher-bin", "licenses"):
        (stage / folder).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cache / asset["name"], stage / "bin/omp.real")
    shutil.copyfile(bins / "sfx-stub", stage / "launcher-bin/omp")
    if target.startswith("darwin-"):
        # Re-sign the renamed shim; never alter the official OMP executable.
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                "--timestamp=none",
                str(stage / "launcher-bin/omp"),
            ],
            check=True,
        )
    shutil.copyfile(cache / "LICENSE", stage / "licenses/OMP-LICENSE")
    shutil.copyfile(ROOT / "LICENSE", stage / "licenses/LAUNCHER-LICENSE")
    for path in ("bin/omp.real", "launcher-bin/omp"):
        (stage / path).chmod(0o755)
    collect(ROOT, stage, cfg["rust"], env)
    files = {
        p.relative_to(stage).as_posix(): {
            "sha256": sha(p),
            "size": p.stat().st_size,
            "executable": bool(p.stat().st_mode & 0o111),
        }
        for p in sorted(stage.rglob("*"))
        if p.is_file()
    }
    manifest = {
        "schema": 1,
        "edition": args.edition,
        "target": target,
        "upstream": {
            "repo": lock["repo"],
            "tag": lock["tag"],
            "release_id": lock["release_id"],
            "asset": asset["name"],
            "sha256": asset["sha256"],
            "reviewed_sources": lock["reviewed_sources"],
        },
        "builder": {
            "repo_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "dirty": bool(
                subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
            ),
            "workflow_run": os.getenv("GITHUB_RUN_ID"),
            "rustc": subprocess.check_output(
                ["rustc", "--version"], env=env, text=True
            ).strip(),
            "container": cfg["container"],
            "signing": "ad-hoc" if target.startswith("darwin-") else "unsigned",
        },
        "files": files,
    }
    (stage / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    output = (
        ROOT / "dist" / f"omp-{args.edition}-{lock['tag'].removeprefix('v')}-{target}"
    )
    candidate = build / "candidate" / output.name
    package(stage, candidate, target, policy["max_sfx_bytes"], env=env)
    result = {
        "artifact": str(candidate),
        "target": target,
        "sha256": sha(candidate),
        "size_bytes": candidate.stat().st_size,
        "smoke": "not-run",
    }
    resultfile = candidate.with_name(candidate.name + ".build.json")
    resultfile.write_text(json.dumps(result, indent=2) + "\n")
    if not args.skip_smoke:
        cmd = [
            sys.executable,
            str(ROOT / "scripts/smoke.py"),
            str(candidate),
            "--report",
            str(build / "smoke-results.json"),
        ]
        if args.strict_host:
            cmd.append("--strict-host")
        try:
            subprocess.run(cmd, cwd=ROOT, env=env, check=True)
        except subprocess.CalledProcessError:
            result["smoke"] = "failed"
            resultfile.write_text(json.dumps(result, indent=2) + "\n")
            print(f"Smoke failed; candidate retained at {candidate}", file=sys.stderr)
            raise
        result["smoke"] = "passed"
        resultfile.write_text(json.dumps(result, indent=2) + "\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    candidate.replace(output)
    result["artifact"] = str(output)
    output.with_name(output.name + ".build.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    output.with_name(output.name + ".sha256").write_text(
        f"{result['sha256']}  {output.name}\n"
    )
    shutil.copyfile(
        stage / "manifest.json", output.with_name(output.name + ".manifest.json")
    )
    alias = output.parent / "omp-lite"
    if not args.skip_smoke and alias.is_symlink():
        alias.unlink()
    if not args.skip_smoke and not alias.exists():
        alias.symlink_to(output.name)
    print(f"\nBuilt {output}\nRun: ./dist/omp-lite --version\nSmoke: {result['smoke']}")


if __name__ == "__main__":
    main()
