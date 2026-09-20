"""Stage the immutable Python, trafilatura, and browser Portable payload."""

import json
import filecmp
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "config/portable-lock.json"


def _archive_path(name, prefix=None):
    if "\\" in name:
        raise RuntimeError(f"Unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise RuntimeError(f"Unsafe archive path: {name!r}")
    parts = path.parts
    if prefix is not None:
        if not parts or parts[0] != prefix:
            raise RuntimeError(f"Archive entry is outside {prefix!r}: {name!r}")
        parts = parts[1:]
    return PurePosixPath(*parts) if parts else None


def _link_path(link, target, prefix=None, hard=False):
    if "\\" in target:
        raise RuntimeError(f"Unsafe archive link: {target!r}")
    raw = PurePosixPath(target)
    if raw.is_absolute():
        raise RuntimeError(f"Escaping archive link: {target!r}")
    if hard:
        resolved = _archive_path(target, prefix)
        if resolved is None:
            raise RuntimeError(f"Invalid archive hard link: {target!r}")
        return resolved
    parts = list(link.parent.parts)
    for part in raw.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise RuntimeError(f"Escaping archive link: {target!r}")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise RuntimeError(f"Invalid archive link: {target!r}")
    return PurePosixPath(*parts)


def _output(root, relative):
    return root.joinpath(*relative.parts)


def _write_file(root, relative, source, mode):
    output = _output(root, relative)
    if os.path.lexists(output):
        raise RuntimeError(f"Duplicate archive entry: {relative}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as destination:
        shutil.copyfileobj(source, destination, 1024 * 1024)
    output.chmod(mode & 0o777 or 0o644)


def _materialize_links(root, links, allow_missing=False):
    pending = dict(links)
    while pending:
        progress = False
        for relative, target_relative in list(pending.items()):
            output = _output(root, relative)
            target = _output(root, target_relative)
            if not target.exists():
                continue
            if target.is_dir() and any(
                other != relative
                and (other == target_relative or target_relative in other.parents)
                for other in pending
            ):
                continue
            if target == output or target in output.parents:
                raise RuntimeError(
                    f"Recursive archive link: {relative} -> {target_relative}"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                shutil.copytree(target, output, copy_function=shutil.copy2)
            elif target.is_file() and not target.is_symlink():
                shutil.copy2(target, output)
            else:
                raise RuntimeError(f"Invalid archive link target: {target_relative}")
            del pending[relative]
            progress = True
        if not progress:
            if allow_missing:
                return
            unresolved = ", ".join(f"{a} -> {b}" for a, b in pending.items())
            raise RuntimeError(f"Missing or cyclic archive links: {unresolved}")


def _extract_tar(archive, destination, prefix):
    links = {}
    with tarfile.open(archive, "r:*") as source:
        for member in source:
            relative = _archive_path(member.name.rstrip("/"), prefix)
            if relative is None:
                continue
            output = _output(destination, relative)
            if member.isdir():
                if os.path.lexists(output) and not output.is_dir():
                    raise RuntimeError(f"Duplicate archive entry: {relative}")
                output.mkdir(parents=True, exist_ok=True)
                output.chmod(member.mode & 0o777 or 0o755)
            elif member.isfile():
                stream = source.extractfile(member)
                if stream is None:
                    raise RuntimeError(f"Cannot read archive entry: {member.name}")
                with stream:
                    _write_file(destination, relative, stream, member.mode)
            elif member.issym() or member.islnk():
                if os.path.lexists(output) or relative in links:
                    raise RuntimeError(f"Duplicate archive entry: {relative}")
                links[relative] = _link_path(
                    relative, member.linkname, prefix, hard=member.islnk()
                )
            else:
                raise RuntimeError(f"Unsupported archive entry type: {member.name}")
    _materialize_links(destination, links)


def _extract_zip(archive, destination, prefix):
    links = {}
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            relative = _archive_path(member.filename.rstrip("/"), prefix)
            if relative is None:
                continue
            output = _output(destination, relative)
            mode = member.external_attr >> 16
            if member.is_dir():
                if os.path.lexists(output) and not output.is_dir():
                    raise RuntimeError(f"Duplicate archive entry: {relative}")
                output.mkdir(parents=True, exist_ok=True)
                output.chmod(mode & 0o777 or 0o755)
            elif stat.S_ISLNK(mode):
                if os.path.lexists(output) or relative in links:
                    raise RuntimeError(f"Duplicate archive entry: {relative}")
                target = source.read(member).decode("utf-8")
                links[relative] = _link_path(relative, target)
            elif stat.S_IFMT(mode) in (0, stat.S_IFREG):
                with source.open(member) as stream:
                    _write_file(destination, relative, stream, mode)
            else:
                raise RuntimeError(f"Unsupported archive entry type: {member.filename}")
    _materialize_links(destination, links)


def _install_wheel(archive, site_packages):
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            relative = _archive_path(member.filename.rstrip("/"))
            if relative is None:
                continue
            parts = relative.parts
            if parts[0].endswith(".data"):
                if len(parts) < 3 or parts[1] not in ("purelib", "platlib"):
                    continue
                relative = PurePosixPath(*parts[2:])
            mode = member.external_attr >> 16
            output = _output(site_packages, relative)
            if member.is_dir():
                if os.path.lexists(output) and not output.is_dir():
                    raise RuntimeError(f"Wheel path collision: {relative}")
                output.mkdir(parents=True, exist_ok=True)
            elif stat.S_ISLNK(mode):
                raise RuntimeError(f"Wheel contains a link: {member.filename}")
            elif stat.S_IFMT(mode) in (0, stat.S_IFREG):
                with source.open(member) as stream:
                    _write_file(site_packages, relative, stream, mode)
            else:
                raise RuntimeError(f"Unsupported wheel entry type: {member.filename}")


def _materialize_existing_links(root):
    links = {}
    for path in sorted(root.rglob("*")):
        if not path.is_symlink():
            continue
        relative = PurePosixPath(path.relative_to(root).as_posix())
        target = os.readlink(path)
        path.unlink()
        if PurePosixPath(target).is_absolute():
            target = target.lstrip("/")
            links[relative] = _archive_path(target)
        else:
            links[relative] = _link_path(relative, target)
    _materialize_links(root, links, allow_missing=True)


def _stage_linux_runtime(items, cache, acquire, browser_root, licenses, env):
    dpkg_deb = shutil.which("dpkg-deb", path=env.get("PATH"))
    if not dpkg_deb:
        raise RuntimeError("Linux Portable staging requires dpkg-deb")
    archives = [_acquire(cache, acquire, item) for item in items]
    system_libraries = set()
    for item, archive in zip(items, archives):
        if item["package"] == "libc6":
            # Keep the host's C runtime paired with its ELF loader.
            with subprocess.Popen(
                [dpkg_deb, "--fsys-tarfile", str(archive)],
                env=env,
                stdout=subprocess.PIPE,
            ) as process:
                with tarfile.open(fileobj=process.stdout, mode="r|") as members:
                    system_libraries.update(
                        PurePosixPath(member.name).name for member in members
                    )
                if process.wait():
                    raise RuntimeError("Could not inspect pinned libc6 package")
    with tempfile.TemporaryDirectory(prefix="browser-runtime-", dir=cache) as tmp:
        runtime = Path(tmp)
        for archive in archives:
            subprocess.run(
                [dpkg_deb, "--extract", str(archive), str(runtime)],
                env=env,
                check=True,
            )
        _materialize_existing_links(runtime)

        library_root = browser_root / "lib"
        library_root.mkdir()
        for source in sorted(runtime.rglob("*")):
            if (
                not source.is_file()
                or source.name in system_libraries
                or not (source.name.endswith(".so") or ".so." in source.name)
            ):
                continue
            with source.open("rb") as library:
                if library.read(4) != b"\x7fELF":
                    continue
            output = library_root / source.name
            if output.exists():
                if not filecmp.cmp(source, output, shallow=False):
                    raise RuntimeError(
                        f"Linux runtime library collision: {source.name}"
                    )
                continue
            shutil.copy2(source, output)

        font_root = browser_root / "fonts"
        font_root.mkdir()
        for source in sorted(runtime.rglob("*")):
            if not source.is_file() or source.suffix.lower() not in (
                ".otf",
                ".ttc",
                ".ttf",
            ):
                continue
            output = font_root / source.name
            if output.exists() and not filecmp.cmp(source, output, shallow=False):
                raise RuntimeError(f"Linux runtime font collision: {source.name}")
            if not output.exists():
                shutil.copy2(source, output)

        notice_root = licenses / "linux-runtime"
        notice_root.mkdir(parents=True)
        for item in items:
            source = runtime / "usr/share/doc" / item["package"] / "copyright"
            if not source.is_file():
                raise RuntimeError(f"Missing Ubuntu copyright: {item['package']}")
            name = f"{item['package']}-{item['version']}.copyright".replace("/", "_")
            shutil.copyfile(source, notice_root / name)

    (browser_root / "fonts.conf").write_text(
        '<?xml version="1.0"?>\n'
        "<fontconfig>\n"
        '  <dir prefix="relative">fonts</dir>\n'
        '  <cachedir prefix="xdg">fontconfig</cachedir>\n'
        "</fontconfig>\n"
    )


def _copy_python_notices(python_root, licenses):
    notice_root = licenses / "python-packages"
    for metadata in sorted(python_root.rglob("*.dist-info")):
        for source in sorted(metadata.rglob("*")):
            if not source.is_file():
                continue
            upper = source.name.upper()
            if not upper.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                continue
            output = notice_root / metadata.name / source.relative_to(metadata)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)


def _acquire(cache, acquire, item):
    path = cache / item["name"]
    acquire(item["url"], path, item["sha256"], item["size"])
    return path


def _component(section, artifact):
    metadata = {key: value for key, value in section.items() if key != "targets"}
    metadata["artifact"] = artifact
    return metadata


def stage_portable(stage: Path, target: str, cache: Path, acquire, env: dict) -> dict:
    """Provision a fully local Portable runtime under an existing stage tree."""
    lock = json.loads(LOCK.read_text())
    runtime_items = lock.get("linux_runtime", {}).get("targets", {}).get(target, [])
    if lock.get("schema") != 1:
        raise RuntimeError(f"Unsupported Portable lock schema: {lock.get('schema')!r}")
    try:
        python_artifact = lock["python"]["targets"][target]
        wheels = lock["packages"]["targets"][target]
        browser_artifact = lock["browser"]["targets"][target]
    except KeyError as error:
        raise RuntimeError(f"Portable target is not locked: {target}") from error

    cache.mkdir(parents=True, exist_ok=True)
    python_archive = _acquire(cache, acquire, python_artifact)
    python_root = stage / "helpers/python"
    python_root.mkdir(parents=True)
    _extract_tar(python_archive, python_root, python_artifact["strip_prefix"])

    site_packages = python_root / lock["python"]["site_packages"]
    site_packages.mkdir(parents=True, exist_ok=True)
    for wheel in wheels:
        wheel_artifact = {**wheel, "name": wheel["filename"]}
        wheel_path = _acquire(cache, acquire, wheel_artifact)
        _install_wheel(wheel_path, site_packages)
    _copy_python_notices(python_root, stage / "licenses")

    browser_archive = _acquire(cache, acquire, browser_artifact)
    browser_root = stage / "browser"
    browser_root.mkdir(parents=True)
    _extract_zip(browser_archive, browser_root, browser_artifact["strip_prefix"])
    if runtime_items:
        _stage_linux_runtime(
            runtime_items,
            cache,
            acquire,
            browser_root,
            stage / "licenses",
            env,
        )
    about = browser_root / "ABOUT"
    if about.is_file():
        shutil.copyfile(about, stage / "licenses/CHROMIUM-ABOUT")
    for notice in lock["notices"]:
        notice_path = _acquire(cache, acquire, notice)
        shutil.copyfile(notice_path, stage / "licenses" / notice["name"])
    python_license = python_root / "LICENSE"
    if python_license.is_file():
        shutil.copyfile(python_license, stage / "licenses/PYTHON-RUNTIME-LICENSE")

    python_executable = python_root / "bin/python3"
    browser_executable = browser_root / browser_artifact["executable"]
    for executable in (python_executable, browser_executable):
        if not executable.is_file() or executable.is_symlink():
            raise RuntimeError(
                f"Portable executable is not a regular file: {executable}"
            )
        executable.chmod(executable.stat().st_mode | 0o755)
    links = [path for path in stage.rglob("*") if path.is_symlink()]
    if links:
        raise RuntimeError(f"Portable stage contains links: {links[0]}")

    shutil.copyfile(LOCK, stage / "licenses/PORTABLE-COMPONENTS.json")
    browser_path = browser_executable.relative_to(stage).as_posix()
    return {
        "python": "helpers/python/bin/python3",
        "browser": browser_path,
        "components": {
            "python": _component(lock["python"], python_artifact),
            "python_packages": wheels,
            "browser": _component(lock["browser"], browser_artifact),
            "notices": lock["notices"],
            "linux_runtime": runtime_items,
        },
    }
