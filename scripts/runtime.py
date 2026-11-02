"""Stage the published OMP source graph and a pinned native Bun runtime."""

import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request

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


def registry_metadata(package, version):
    with urllib.request.urlopen(
        f"https://registry.npmjs.org/{package}/{version}", timeout=60
    ) as response:
        return json.load(response)


def bun_satisfies(bun, version, requirement):
    return subprocess.check_output(
        [str(bun), "-e", "console.log(Bun.semver.satisfies(process.argv[1], process.argv[2]))",
         version, requirement], text=True
    ).strip() == "true"


def refresh_bun(tag, bun, cache, acquire):
    """Keep the pin unless upstream requires a newer runtime; lock verified bytes."""
    metadata = registry_metadata("@oh-my-pi/pi-coding-agent", tag.removeprefix("v"))
    requirement = metadata["engines"]["bun"]
    lock = json.loads(LOCK.read_text())
    print(f"OMP {tag} requires Bun {requirement}; pinned {lock['version']}", flush=True)
    if bun_satisfies(bun, lock["version"], requirement):
        return bun
    version = registry_metadata("bun", "latest")["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not bun_satisfies(bun, version, requirement):
        raise RuntimeError(f"Stable Bun {version} does not satisfy upstream {requirement}")
    assets = {}
    for target, old in lock["assets"].items():
        package = old["url"].split("/-/", 1)[0].removeprefix("https://registry.npmjs.org/")
        dist = registry_metadata(package, version)["dist"]
        algorithm, encoded = dist["integrity"].split("-", 1)
        if algorithm != "sha512":
            raise RuntimeError(f"Missing npm SHA-512 integrity: {package}@{version}")
        name = dist["tarball"].rsplit("/", 1)[1]
        print(f"Locking Bun {version}: {target}", flush=True)
        digest = hashlib.sha256()
        integrity = hashlib.sha512()
        size = 0
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
            temporary = Path(output.name)
            try:
                with urllib.request.urlopen(dist["tarball"], timeout=120) as response:
                    while chunk := response.read(1024 * 1024):
                        digest.update(chunk)
                        integrity.update(chunk)
                        size += len(chunk)
                        output.write(chunk)
                if integrity.digest() != base64.b64decode(encoded, validate=True):
                    raise RuntimeError(f"npm integrity mismatch: {name}")
                output.close()
                temporary.replace(cache / name)
            finally:
                temporary.unlink(missing_ok=True)
        assets[target] = {"name": name, "url": dist["tarball"],
                          "sha256": digest.hexdigest(), "size": size}
    license_url = f"https://raw.githubusercontent.com/oven-sh/bun/bun-v{version}/LICENSE.md"
    with urllib.request.urlopen(license_url, timeout=60) as response:
        license_bytes = response.read()
    updated = {"version": version, "assets": assets, "license": {
        "name": f"Bun-LICENSE-{version}.md", "url": license_url,
        "sha256": hashlib.sha256(license_bytes).hexdigest(), "size": len(license_bytes),
    }}
    LOCK.write_text(json.dumps(updated, indent=2) + "\n")
    from platforms import detect_target

    return runtime_bun(detect_target(), cache, acquire)[0]


def install(bun, directory, env, frozen=True):
    command = [str(bun), "install", "--ignore-scripts",
               "--linker=hoisted", "--minimum-release-age=0"]
    if frozen:
        command.extend(["--production", "--frozen-lockfile"])
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
