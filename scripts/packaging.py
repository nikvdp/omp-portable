"""Pack native Linux executables or linker-embedded, signed macOS executables."""

from pathlib import Path
import shutil
import subprocess
import tempfile
from platforms import ROOT, TARGETS, cargo_environment


def binaries(target):
    return ROOT / "target" / TARGETS[target]["rust"] / "release"


def package(stage, output, target, limit, *, env=None, stub=None, packer=None):
    env = env or cargo_environment()
    bins = binaries(target)
    stub, packer = Path(stub or bins / "sfx-stub"), Path(packer or bins / "sfx-pack")
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Never destroy the previous verified artifact if a new build fails.
    with tempfile.TemporaryDirectory(prefix="pack-", dir=output.parent) as tmp:
        tmp = Path(tmp)
        candidate = tmp / output.name
        if TARGETS[target]["container"] == "elf-overlay":
            subprocess.run(
                [str(packer), str(stub), str(stage), str(candidate), str(limit)],
                check=True,
                env=env,
            )
        else:
            payload = tmp / "omp.payload"
            subprocess.run(
                [str(packer), "--payload", str(stage), str(payload), str(limit)],
                check=True,
                env=env,
            )
            # Build from the same stub source but let Apple's linker place the
            # payload inside Mach-O. Its signature is then over the entire file.
            link_args = []
            for value in [
                # The linker UUID varies with the temporary payload path.
                "-no_uuid",
                "-sectcreate",
                "__OMP",
                "__payload",
                str(payload),
                "-segprot",
                "__OMP",
                "r",
                "r",
            ]:
                link_args.extend(["-C", "link-arg=-Xlinker", "-C", f"link-arg={value}"])
            subprocess.run(
                [
                    "cargo",
                    "rustc",
                    "--locked",
                    "--release",
                    "--target",
                    TARGETS[target]["rust"],
                    "--target-dir",
                    str(ROOT / "target/embedded"),
                    "-p",
                    "sfx-stub",
                    "--",
                    *link_args,
                ],
                cwd=ROOT,
                env=env,
                check=True,
            )
            shutil.copyfile(
                ROOT / "target/embedded" / TARGETS[target]["rust"] / "release/sfx-stub",
                candidate,
            )
            candidate.chmod(0o755)
            subprocess.run(
                [
                    "/usr/bin/codesign",
                    "--force",
                    "--sign",
                    "-",
                    "--identifier",
                    "org.omp-portable.launcher",
                    "--timestamp=none",
                    str(candidate),
                ],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/codesign", "--verify", "--strict", str(candidate)],
                check=True,
            )
        if candidate.stat().st_size >= limit:
            raise RuntimeError(f"Finished artifact exceeds {limit} byte budget")
        candidate.replace(output)
