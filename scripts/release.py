#!/usr/bin/env python3
"""Discover upstream Lite releases and publish only complete verified builds."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True)


def prepare(check_existing):
    lock = json.loads((ROOT / "config/upstream-lock.json").read_text())
    release = json.loads(gh("api", f"repos/{lock['repo']}/releases/latest"))
    tag = release["tag_name"]
    if (
        release["draft"]
        or release["prerelease"]
        or not re.fullmatch(r"v\d+\.\d+\.\d+", tag)
    ):
        raise RuntimeError(f"Unsupported upstream release: {tag}")
    release_tag = f"omp-{tag}"
    needed = True
    if check_existing:
        existing = json.loads(
            gh(
                "api",
                "--paginate",
                "--slurp",
                f"repos/{os.environ['GITHUB_REPOSITORY']}/releases",
            )
        )
        needed = not any(
            r["tag_name"] == release_tag and not r["draft"]
            for page in existing
            for r in page
        )
    if needed:
        assets = {a["name"]: a for a in release["assets"]}
        for item in [lock["license"], *lock["assets"].values()]:
            asset = assets[item["name"]]
            digest = asset.get("digest") or ""
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise RuntimeError(f"Missing GitHub SHA-256: {item['name']}")
            item.update(
                url=asset["browser_download_url"], sha256=digest[7:], size=asset["size"]
            )
        for source in lock["reviewed_sources"]:
            url = source["url"].replace(f"/{lock['tag']}/", f"/{tag}/")
            with urllib.request.urlopen(url, timeout=60) as response:
                digest = hashlib.sha256(response.read()).hexdigest()
            if digest != source["sha256"]:
                raise RuntimeError(
                    f"Review required before releasing {tag}: {source['name']} changed"
                )
            source["url"] = url
        lock.update(tag=tag, release_id=release["id"])
        (ROOT / "config/upstream-lock.json").write_text(
            json.dumps(lock, indent=2) + "\n"
        )
    outputs = f"needed={str(needed).lower()}\ntag={release_tag}\n"
    print(outputs, flush=True)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(outputs)


def publish(dry_run):
    lock = json.loads((ROOT / "config/upstream-lock.json").read_text())
    targets = json.loads((ROOT / "config/targets.json").read_text())
    if set(lock["assets"]) != set(targets):
        raise RuntimeError("Release lock does not cover every supported native target")
    paths = []
    for target in lock["assets"]:
        binary = ROOT / "dist" / f"omp-lite-{lock['tag'].removeprefix('v')}-{target}"
        report = json.loads(binary.with_name(binary.name + ".build.json").read_text())
        manifest = json.loads(
            binary.with_name(binary.name + ".manifest.json").read_text()
        )
        with binary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (
            report["smoke"] != "passed"
            or report["target"] != target
            or report["sha256"] != digest
            or report["size_bytes"] != binary.stat().st_size
            or manifest["target"] != target
            or manifest["edition"] != "lite"
            or manifest["upstream"]["tag"] != lock["tag"]
            or manifest["upstream"]["sha256"] != lock["assets"][target]["sha256"]
        ):
            raise RuntimeError(f"Invalid release artifact: {binary.name}")
        checksum = binary.with_name(binary.name + ".sha256")
        if checksum.read_text() != f"{digest}  {binary.name}\n":
            raise RuntimeError(f"Invalid checksum sidecar: {binary.name}")
        paths.extend(
            [
                binary,
                checksum,
                binary.with_name(binary.name + ".build.json"),
                binary.with_name(binary.name + ".manifest.json"),
            ]
        )
    print(
        f"Verified {len(lock['assets'])} native builds, {len(paths)} release files",
        flush=True,
    )
    if dry_run:
        return
    tag = f"omp-{lock['tag']}"
    repo = os.environ["GITHUB_REPOSITORY"]
    releases = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/releases"))
    existing = next(
        (r for page in releases for r in page if r["tag_name"] == tag), None
    )
    if existing and not existing["draft"]:
        raise RuntimeError(f"Refusing to overwrite published release {tag}")
    if not existing:
        gh(
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--draft",
            "--target",
            os.environ["GITHUB_SHA"],
            "--title",
            f"OMP Lite {lock['tag']}",
            "--notes",
            f"Native Lite builds of https://github.com/{lock['repo']}/releases/tag/{lock['tag']}. "
            "All four targets passed offline smoke and extraction tests. macOS binaries are ad-hoc signed, not notarized. "
            "Portable/Full editions are not included. Restore executable permissions with chmod +x after downloading.",
        )
    gh("release", "upload", tag, "--repo", repo, "--clobber", *map(str, paths))
    gh("release", "edit", tag, "--repo", repo, "--draft=false")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "publish"])
    parser.add_argument("--check-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.check_existing)
    else:
        publish(args.dry_run)
