"""Collect redistribution notices from the exact Cargo dependency sources."""

import json
from pathlib import Path
import shutil
import subprocess

REVIEWED = {
    "MIT",
    "MIT OR Apache-2.0",
    "MIT/Apache-2.0",
    "BSD-3-Clause",
    "Unlicense OR MIT",
    "(MIT OR Apache-2.0) AND Unicode-3.0",
    "Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT",
}


def collect(root, stage, rust_target, env):
    meta = json.loads(
        subprocess.check_output(
            [
                "cargo",
                "metadata",
                "--locked",
                "--offline",
                "--filter-platform",
                rust_target,
                "--format-version",
                "1",
            ],
            cwd=root,
            env=env,
            text=True,
        )
    )
    notices = [
        "# Third-party notices",
        "",
        "The official OMP executable is redistributed byte-for-byte under its release LICENSE.",
        "See OMP-LICENSE. Downstream launcher and packer: see LAUNCHER-LICENSE.",
        "The following exact Rust dependency sources supply the additional notices.",
        "",
    ]
    for package in sorted(meta["packages"], key=lambda p: (p["name"], p["version"])):
        if not package["source"]:
            continue
        license = package["license"]
        if license not in REVIEWED:
            raise RuntimeError(f'Unreviewed license: {package["name"]}: {license}')
        source = Path(package["manifest_path"]).parent
        files = [
            p
            for p in source.rglob("*")
            if p.is_file()
            and (
                p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE"))
                or p.name == "copyright"
            )
        ]
        if not files:
            raise RuntimeError(f'Missing notices: {package["name"]}')
        label = f'{package["name"]}-{package["version"]}'
        notices.extend(
            [
                f"## {label}",
                f"License expression: {license}",
                f'Source: {package["repository"] or package["source"]}',
                "",
            ]
        )
        for file in files:
            dest = stage / "licenses/rust" / label / file.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, dest)
    (stage / "licenses/THIRD_PARTY_NOTICES.md").write_text("\n".join(notices) + "\n")
