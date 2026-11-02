#!/usr/bin/env python3
"""Publish mandatory Lite releases and independently append verified Portable assets."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from runtime import runtime_bun, refresh_bun, refresh, LOCK as RUNTIME_LOCK, DEPENDENCIES
from platforms import detect_target

ROOT = Path(__file__).resolve().parents[1]
EDITIONS = ("lite", "portable")
RELEASE_REVISION = 6


def downstream_tag(upstream_tag):
    return f"omp-{upstream_tag}-r{RELEASE_REVISION}"


def release_asset_names(upstream_tag, targets, suffixes=("", ".sha256"), editions=EDITIONS):
    version = upstream_tag.removeprefix("v")
    return {
        f"omp-{edition}-{version}-{target}{suffix}"
        for edition in editions
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
            expected = release_asset_names(tag, lock["assets"], editions=("lite",))
            missing = expected - names
            unexpected = names - release_asset_names(tag, lock["assets"])
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
        lock.update(tag=tag, release_id=release["id"])
        from build import acquire

        cache = ROOT / "build/upstream"
        cache.mkdir(parents=True, exist_ok=True)
        bun, _ = runtime_bun(detect_target(), cache, acquire)
        bun = refresh_bun(tag, bun, cache, acquire)
        refresh(tag, bun, os.environ.copy())
        (ROOT / "config/upstream-lock.json").write_text(
            json.dumps(lock, indent=2) + "\n"
        )
    outputs = f"needed={str(needed).lower()}\ntag={release_tag}\n"
    print(outputs, flush=True)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(outputs)


def validate_artifact(binary, edition, target, lock, runtime_lock, runtime_digest, portable_digest, package_digest):
    report = json.loads(binary.with_name(binary.name + ".build.json").read_text())
    manifest = json.loads(binary.with_name(binary.name + ".manifest.json").read_text())
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    portable = manifest.get("portable")
    if (
        report["smoke"] != "passed"
        or report["target"] != target
        or report["edition"] != edition
        or report["sha256"] != digest
        or report["size_bytes"] != binary.stat().st_size
        or (edition == "portable" and report.get("portable_lock_sha256") != portable_digest)
        or manifest["target"] != target
        or manifest["edition"] != edition
        or manifest["schema"] != 1
        or (edition == "portable" and (
            not isinstance(portable, dict)
            or set(portable) != {"python", "browser", "components"}
            or not portable["components"]
        ))
        or (edition == "lite" and "portable" in manifest)
        or manifest["upstream"]["tag"] != lock["tag"]
        or report.get("runtime_lock_sha256") != runtime_digest
        or report.get("package_lock_sha256") != package_digest
        or manifest["upstream"]["runtime"]["package_lock_sha256"] != package_digest
        or manifest["upstream"]["runtime"]["bun"] != {
            "version": runtime_lock["version"], **runtime_lock["assets"][target],
        }
    ):
        raise RuntimeError(f"Invalid release artifact: {binary.name}")
    checksum = binary.with_name(binary.name + ".sha256")
    if checksum.read_text() != f"{digest}  {binary.name}\n":
        raise RuntimeError(f"Invalid checksum sidecar: {binary.name}")
    return [binary, checksum]


def publication_inputs():
    lock = json.loads((ROOT / "config/upstream-lock.json").read_text())
    targets = json.loads((ROOT / "config/targets.json").read_text())
    if set(lock["assets"]) != set(targets):
        raise RuntimeError("Release lock does not cover every supported native target")
    return (
        lock, json.loads(RUNTIME_LOCK.read_text()),
        hashlib.sha256(RUNTIME_LOCK.read_bytes()).hexdigest(),
        hashlib.sha256(DEPENDENCIES.read_bytes()).hexdigest(),
    )


HANDOFF_LOCKS = (
    "upstream-lock.json", "portable-lock.json", "runtime-lock.json",
    "runtime-package.json", "runtime-bun.lock",
)


def check_handoff():
    handoff = json.loads((ROOT / "config/release.json").read_text())
    if handoff["source_sha"] != os.environ["RELEASE_SOURCE_SHA"]:
        raise RuntimeError("Portable source does not match the successful Lite run")
    if set(handoff["locks"]) != set(HANDOFF_LOCKS):
        raise RuntimeError("Incomplete Lite release lock handoff")
    for name, digest in handoff["locks"].items():
        if hashlib.sha256((ROOT / "config" / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Lite release lock changed: {name}")
    lock = json.loads((ROOT / "config/upstream-lock.json").read_text())
    if handoff["tag"] != downstream_tag(lock["tag"]):
        raise RuntimeError("Lite release tag does not match its upstream lock")
    return handoff


def publish_portable(target, dry_run):
    handoff = check_handoff()
    lock, runtime_lock, runtime_digest, package_digest = publication_inputs()
    if target not in lock["assets"]:
        raise RuntimeError(f"Unsupported Portable target: {target}")
    portable_digest = hashlib.sha256((ROOT / "config/portable-lock.json").read_bytes()).hexdigest()
    binary = ROOT / "dist" / f"omp-portable-{lock['tag'].removeprefix('v')}-{target}"
    paths = validate_artifact(
        binary, "portable", target, lock, runtime_lock, runtime_digest,
        portable_digest, package_digest,
    )
    if dry_run:
        print(f"Verified Portable {target} for {handoff['tag']}", flush=True)
        return
    repo = os.environ["GITHUB_REPOSITORY"]
    published = json.loads(gh("api", f"repos/{repo}/releases/{handoff['release_id']}"))
    if (published["draft"] or published["tag_name"] != handoff["tag"]
            or published["target_commitish"] != handoff["source_sha"]):
        raise RuntimeError("Portable destination is not the original published Lite release")
    expected_lite = release_asset_names(lock["tag"], lock["assets"], editions=("lite",))
    assets = release_assets(repo, handoff["release_id"])
    if not expected_lite.issubset({asset["name"] for asset in assets}):
        raise RuntimeError("Destination release is missing mandatory Lite assets")
    existing = {asset["name"]: asset for asset in assets}
    for path in paths:
        if path.name in existing:
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            if existing[path.name].get("digest") != digest:
                raise RuntimeError(f"Refusing to replace published asset: {path.name}")
    missing = [str(path) for path in paths if path.name not in existing]
    if missing:
        gh("release", "upload", handoff["tag"], "--repo", repo, *missing)


def publish(dry_run):
    lock, runtime_lock, runtime_digest, package_digest = publication_inputs()
    paths = []
    version = lock["tag"].removeprefix("v")
    for target in lock["assets"]:
        binary = ROOT / "dist" / f"omp-lite-{version}-{target}"
        paths.extend(validate_artifact(
            binary, "lite", target, lock, runtime_lock, runtime_digest,
            None, package_digest,
        ))
    print(f"Verified {len(paths) // 2} Lite builds, {len(paths)} public release files", flush=True)
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
            f"OMP Lite {lock['tag']} (packaging r{RELEASE_REVISION})",
            "--notes",
            f"Native Lite builds of https://github.com/{lock['repo']}/releases/tag/{lock['tag']}. "
            "All four Lite targets passed offline smoke and extraction tests. "
            "Portable assets may be appended by a separate best-effort workflow; "
            "only targets passing its checks are uploaded. "
            "macOS binaries are ad-hoc signed, not notarized. Full is not included. "
            "Restore executable permissions with chmod +x after downloading.",
        )
    else:
        if existing["target_commitish"] != os.environ["GITHUB_SHA"]:
            raise RuntimeError("Draft source differs from this build; increment RELEASE_REVISION")
        public_names = release_asset_names(lock["tag"], lock["assets"], editions=("lite",))
        metadata_names = release_asset_names(
            lock["tag"], lock["assets"], (".build.json", ".manifest.json"), editions=("lite",)
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
        verified_names = {p.name for p in paths}
        for asset in draft_assets:
            if asset["name"] in metadata_names or asset["name"] not in verified_names:
                gh(
                    "api",
                    "--method",
                    "DELETE",
                    f"repos/{repo}/releases/assets/{asset['id']}",
                )
    gh("release", "upload", tag, "--repo", repo, "--clobber", *map(str, paths))
    gh("release", "edit", tag, "--repo", repo, "--draft=false")


def write_handoff():
    lock = json.loads((ROOT / "config/upstream-lock.json").read_text())
    tag = downstream_tag(lock["tag"])
    repo = os.environ["GITHUB_REPOSITORY"]
    published = json.loads(gh("api", f"repos/{repo}/releases/tags/{tag}"))
    if (published["draft"] or published["tag_name"] != tag
            or published["target_commitish"] != os.environ["GITHUB_SHA"]):
        raise RuntimeError("Cannot hand off a release from a different source or an unpublished draft")
    handoff = {
        "tag": tag, "release_id": published["id"], "source_sha": os.environ["GITHUB_SHA"],
        "locks": {name: hashlib.sha256((ROOT / "config" / name).read_bytes()).hexdigest()
                  for name in HANDOFF_LOCKS},
    }
    (ROOT / "config/release.json").write_text(json.dumps(handoff, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "publish", "handoff", "check-handoff", "publish-portable"])
    parser.add_argument("--check-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.check_existing)
    elif args.command == "handoff":
        write_handoff()
    elif args.command == "check-handoff":
        check_handoff()
    elif args.command == "publish-portable":
        if not args.target:
            parser.error("publish-portable requires --target")
        publish_portable(args.target, args.dry_run)
    else:
        publish(args.dry_run)
