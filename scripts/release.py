#!/usr/bin/env python3
"""Discover upstream releases and publish only complete verified native builds."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
EDITIONS = ("lite", "portable")
RELEASE_REVISION = 2


def downstream_tag(upstream_tag):
    return f"omp-{upstream_tag}-r{RELEASE_REVISION}"


def release_asset_names(upstream_tag, targets, suffixes=("", ".sha256")):
    version = upstream_tag.removeprefix("v")
    return {
        f"omp-{edition}-{version}-{target}{suffix}"
        for edition in EDITIONS
        for target in targets
        for suffix in suffixes
    }


def release_assets(repo, release_id):
    pages = json.loads(
        gh(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/releases/{release_id}/assets?per_page=100",
        )
    )
    return [asset for page in pages for asset in page]


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
    release_tag = downstream_tag(tag)
    needed = True
    if check_existing:
        repo = os.environ["GITHUB_REPOSITORY"]
        pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/releases"))
        published = next(
            (
                item
                for page in pages
                for item in page
                if item["tag_name"] == release_tag and not item["draft"]
            ),
            None,
        )
        if published:
            assets = release_assets(repo, published["id"])
            names = {asset["name"] for asset in assets}
            expected = release_asset_names(tag, lock["assets"])
            missing = expected - names
            unexpected = names - expected
            if missing or unexpected:
                raise RuntimeError(
                    f"Published release {release_tag} has the wrong asset set; "
                    "increment RELEASE_REVISION instead of overwriting it: "
                    f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
                )
            needed = False
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
    portable_lock_sha256 = hashlib.sha256(
        (ROOT / "config/portable-lock.json").read_bytes()
    ).hexdigest()
    paths = []
    version = lock["tag"].removeprefix("v")
    for edition in EDITIONS:
        for target in lock["assets"]:
            binary = ROOT / "dist" / f"omp-{edition}-{version}-{target}"
            report = json.loads(
                binary.with_name(binary.name + ".build.json").read_text()
            )
            manifest = json.loads(
                binary.with_name(binary.name + ".manifest.json").read_text()
            )
            portable = manifest.get("portable")
            with binary.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if (
                report["smoke"] != "passed"
                or report["target"] != target
                or report["edition"] != edition
                or report["sha256"] != digest
                or report["size_bytes"] != binary.stat().st_size
                or (
                    edition == "portable"
                    and report.get("portable_lock_sha256") != portable_lock_sha256
                )
                or manifest["target"] != target
                or manifest["edition"] != edition
                or manifest["schema"] != 1
                or (
                    edition == "portable"
                    and (
                        not isinstance(portable, dict)
                        or set(portable) != {"python", "browser", "components"}
                        or not portable["components"]
                    )
                )
                or (edition == "lite" and "portable" in manifest)
                or manifest["upstream"]["tag"] != lock["tag"]
                or manifest["upstream"]["sha256"] != lock["assets"][target]["sha256"]
            ):
                raise RuntimeError(f"Invalid release artifact: {binary.name}")
            checksum = binary.with_name(binary.name + ".sha256")
            if checksum.read_text() != f"{digest}  {binary.name}\n":
                raise RuntimeError(f"Invalid checksum sidecar: {binary.name}")
            paths.extend([binary, checksum])
    print(
        f"Verified {len(EDITIONS) * len(lock['assets'])} native builds, "
        f"{len(paths)} public release files",
        flush=True,
    )
    if dry_run:
        return
    tag = downstream_tag(lock["tag"])
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
            f"OMP Lite + Portable {lock['tag']} (packaging r{RELEASE_REVISION})",
            "--notes",
            f"Native Lite and Portable builds of https://github.com/{lock['repo']}/releases/tag/{lock['tag']}. "
            "All eight edition/target builds passed offline smoke and extraction tests. "
            "macOS binaries are ad-hoc signed, not notarized. Full is not included. "
            "Restore executable permissions with chmod +x after downloading.",
        )
    else:
        public_names = release_asset_names(lock["tag"], lock["assets"])
        metadata_names = release_asset_names(
            lock["tag"], lock["assets"], (".build.json", ".manifest.json")
        )
        draft_assets = release_assets(repo, existing["id"])
        unexpected = [
            asset["name"]
            for asset in draft_assets
            if asset["name"] not in public_names | metadata_names
        ]
        if unexpected:
            raise RuntimeError(
                f"Refusing to publish draft {tag} with unexpected assets: "
                f"{sorted(unexpected)}"
            )
        for asset in draft_assets:
            if asset["name"] in metadata_names:
                gh(
                    "api",
                    "--method",
                    "DELETE",
                    f"repos/{repo}/releases/assets/{asset['id']}",
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
