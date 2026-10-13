"""Stage the published OMP source graph and a pinned native Bun runtime."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from platforms import ROOT
from portable import _extract_tar

LOCK = ROOT / "config/runtime-lock.json"
PACKAGE = ROOT / "config/runtime-package.json"
DEPENDENCIES = ROOT / "config/runtime-bun.lock"
ENTRY = "runtime/node_modules/@oh-my-pi/pi-coding-agent/src/cli.ts"


def runtime_bun(target, cache, acquire):
    lock = json.loads(LOCK.read_text())
    asset = lock["assets"][target]
    archive = cache / asset["name"]
    acquire(asset["url"], archive, asset["sha256"], asset["size"])
    destination = cache / f"bun-{lock['version']}-{target}"
    # Re-extract verified bytes rather than trusting a mutable build cache.
    with tempfile.TemporaryDirectory(dir=cache, prefix="bun-") as tmp:
        _extract_tar(archive, Path(tmp), "package")
        destination.mkdir(exist_ok=True)
        shutil.copy2(Path(tmp) / "bin/bun", destination / "bun")
    bun = destination / "bun"
    bun.chmod(0o755)
    return bun, asset


def install(bun, directory, env, frozen=True):
    command = [str(bun), "install", "--production", "--ignore-scripts",
               "--linker=hoisted", "--minimum-release-age=0"]
    if frozen:
        command.append("--frozen-lockfile")
    else:
        command.extend(["--lockfile-only", "--save-text-lockfile", "--os=*", "--cpu=*"])
    print("Installing locked OMP production dependency graph" if frozen else
          "Resolving OMP dependency lock for all native targets", flush=True)
    subprocess.run(command, cwd=directory, env=env, check=True)


def refresh(tag, bun, env):
    package = {"private": True, "dependencies": {"@oh-my-pi/pi-coding-agent": tag.removeprefix("v")}}
    with tempfile.TemporaryDirectory(prefix="omp-lock-") as tmp:
        directory = Path(tmp)
        (directory / "package.json").write_text(json.dumps(package, indent=2) + "\n")
        install(bun, directory, env, frozen=False)
        shutil.copyfile(directory / "bun.lock", DEPENDENCIES)
    PACKAGE.write_text(json.dumps(package, indent=2) + "\n")


def stage_runtime(stage, target, cache, acquire, env, tag):
    package = json.loads(PACKAGE.read_text())
    if package["dependencies"]["@oh-my-pi/pi-coding-agent"] != tag.removeprefix("v"):
        raise RuntimeError("OMP package lock does not match upstream tag; run release.py prepare")
    cache.mkdir(parents=True, exist_ok=True)
    bun, asset = runtime_bun(target, cache, acquire)
    runtime = stage / "runtime"
    runtime.mkdir()
    shutil.copyfile(PACKAGE, runtime / "package.json")
    shutil.copyfile(DEPENDENCIES, runtime / "bun.lock")
    install(bun, runtime, env)
    # Materialize contained package links because the payload accepts regular files only.
    modules = runtime / "node_modules"
    for link in sorted(modules.rglob("*")):
        if not link.is_symlink():
            continue
        target_path = link.resolve(strict=True)
        if not target_path.is_relative_to(modules.resolve()):
            raise RuntimeError(f"Package link escapes runtime: {link}")
        link.unlink()
        if target_path.is_dir():
            shutil.copytree(target_path, link)
        else:
            shutil.copy2(target_path, link)
    entry = stage / ENTRY
    if not entry.is_file():
        raise RuntimeError(f"Published OMP package is missing source entry: {ENTRY}")
    license_asset = json.loads(LOCK.read_text())["license"]
    acquire(license_asset["url"], cache / license_asset["name"],
            license_asset["sha256"], license_asset["size"])
    shutil.copyfile(cache / license_asset["name"], stage / "licenses/Bun-LICENSE.md")
    shutil.copy2(bun, stage / "bin/omp.real")
    return {
        "entry": ENTRY,
        "bun": {"version": json.loads(LOCK.read_text())["version"], **asset},
        "package_lock_sha256": hashlib.sha256(DEPENDENCIES.read_bytes()).hexdigest(),
    }
