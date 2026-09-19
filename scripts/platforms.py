"""Native platform selection shared by the builder and test harness."""

import json
import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = json.loads((ROOT / "config/targets.json").read_text())


def detect_target(system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
        machine
    )
    family = {"Linux": "linux", "Darwin": "darwin"}.get(system)
    name = f"{family}-{arch}"
    if name not in TARGETS:
        raise RuntimeError(
            f'Unsupported native host: {system}/{machine}. Supported: {", ".join(TARGETS)}'
        )
    return name


def cache_base(home, target=None, xdg=None):
    target = target or detect_target()
    if target.startswith("darwin-"):
        return Path(home) / "Library/Caches/omp-portable/dist"
    return Path(xdg or Path(home) / ".cache") / "omp-portable/dist"


def cargo_environment():
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(ROOT / "target")
    cargo_bin = Path.home() / ".cargo/bin"
    if cargo_bin.is_dir():
        env["PATH"] = str(cargo_bin) + os.pathsep + env.get("PATH", "")
    return env
