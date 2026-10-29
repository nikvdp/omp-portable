"""Unpack a verified Lite release without executing its launcher."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import sys
import tarfile

import zstandard


class PayloadTarInfo(tarfile.TarInfo):
    @classmethod
    def frombuf(cls, buf, encoding, errors):
        member = super().frombuf(buf, encoding, errors)
        # Rust tar uses NUL for regular files. A truncated GNU long-name
        # header can end in '/', which Python otherwise mistakes for a directory.
        if buf[156:157] == tarfile.AREGTYPE and member.isdir():
            member.type = tarfile.AREGTYPE
        return member


class PayloadReader:
    def __init__(self, source, length):
        self.source = source
        self.remaining = length

    def read(self, size):
        chunk = self.source.read(min(size, self.remaining))
        self.remaining -= len(chunk)
        return chunk


def payload_region(source):
    source.seek(0, 2)
    size = source.tell()
    source.seek(0)
    if source.read(4) != b"\xcf\xfa\xed\xfe":
        return 0, size
    source.seek(16)
    count, command_bytes = struct.unpack("<II", source.read(8))
    if command_bytes > 1024 * 1024 or 32 + command_bytes > size:
        raise ValueError("Invalid Mach-O command bounds")
    position = 32
    region = None
    for _ in range(count):
        source.seek(position)
        command, length = struct.unpack("<II", source.read(8))
        if length < 8 or position + length > 32 + command_bytes:
            raise ValueError("Invalid Mach-O command")
        if command == 0x19:
            if length < 72:
                raise ValueError("Truncated Mach-O segment")
            source.seek(position + 64)
            sections = struct.unpack("<I", source.read(4))[0]
            if 72 + sections * 80 > length:
                raise ValueError("Invalid Mach-O section count")
            source.seek(position + 72)
            for _ in range(sections):
                section = source.read(80)
                if (section[:16].rstrip(b"\0"), section[16:32].rstrip(b"\0")) != (
                    b"__payload", b"__OMP"
                ):
                    continue
                section_size, offset = struct.unpack_from("<QI", section, 40)
                if region is not None or offset < 32 + command_bytes or offset + section_size > size:
                    raise ValueError("Invalid Mach-O payload bounds")
                region = offset, section_size
        position += length
    if region is None:
        raise ValueError("Missing Mach-O payload section")
    return region


def extract(archive, destination, target, version):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with open(archive, "rb") as source:
        origin, size = payload_region(source)
        if size < 64:
            raise ValueError("Truncated SFX footer")
        source.seek(origin + size - 64)
        magic, format_version, schema, offset, length, expected = struct.unpack(
            "<8sIIQQ32s", source.read(64)
        )
        if magic != b"OMPSFX01" or (format_version, schema) != (1, 1):
            raise ValueError("Unsupported SFX format")
        if not length or offset + length != size - 64:
            raise ValueError("Invalid SFX payload bounds")
        source.seek(origin + offset)
        digest = hashlib.sha256()
        remaining = length
        while remaining:
            chunk = source.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError("Truncated SFX payload")
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.digest() != expected:
            raise ValueError("SFX payload checksum mismatch")
        source.seek(origin + offset)
        with zstandard.ZstdDecompressor().stream_reader(PayloadReader(source, length)) as decoded:
            with tarfile.open(fileobj=decoded, mode="r|", tarinfo=PayloadTarInfo) as members:
                first = members.next()
                if first is None or first.name != "manifest.json" or not first.isfile() or first.size > 8 * 1024 * 1024:
                    raise ValueError("Missing or invalid payload manifest")
                manifest = json.load(members.extractfile(first))
                if (manifest["schema"], manifest["edition"], manifest["target"], manifest["upstream"]["tag"]) != (
                    1, "lite", target, "v" + version
                ):
                    raise ValueError("Payload does not match the requested Lite release")
                entry = "runtime/node_modules/@oh-my-pi/pi-coding-agent/src/cli.ts"
                if manifest["upstream"]["runtime"]["entry"] != entry or entry not in manifest["files"]:
                    raise ValueError("Missing OMP source runtime")
                seen = set()
                for member in members:
                    # Streaming iteration also yields the first member again.
                    if member is first:
                        continue
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or not path.parts or any(p in ("", ".", "..") for p in member.name.split("/")):
                        raise ValueError(f"Unsafe payload path: {member.name}")
                    if not member.isfile() or member.name in seen or member.name not in manifest["files"]:
                        raise ValueError(f"Unexpected payload member: {member.name}")
                    record = manifest["files"][member.name]
                    if member.size != record["size"]:
                        raise ValueError(f"Payload size mismatch: {member.name}")
                    seen.add(member.name)
                    output = destination / path
                    output.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with members.extractfile(member) as stream, output.open("xb") as sink:
                        while chunk := stream.read(1024 * 1024):
                            digest.update(chunk)
                            sink.write(chunk)
                    if digest.hexdigest() != record["sha256"]:
                        raise ValueError(f"Payload checksum mismatch: {member.name}")
                    output.chmod(0o755 if record["executable"] else 0o644)
                    if len(seen) % 5000 == 0:
                        print(f"Unpacked and verified {len(seen)}/{len(manifest['files'])} files", flush=True)
                if seen != set(manifest["files"]):
                    raise ValueError("Incomplete payload")
    print(f"Unpacked and verified {len(seen)} Lite files", flush=True)


if __name__ == "__main__":
    extract(*sys.argv[1:])
