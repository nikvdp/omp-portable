import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest

import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extract_lite import extract


class ExtractLite(unittest.TestCase):
    def test_gnu_long_name_ending_at_directory_boundary(self):
        # The GNU header's truncated name ends in '/', but its NUL type is a
        # regular file, not a V7 directory. More members must still be read.
        name = "runtime/" + "a" * 90 + "/file.ts"
        entry = "runtime/node_modules/@oh-my-pi/pi-coding-agent/src/cli.ts"
        files = {name: b"long-name file", entry: b"source entry"}
        manifest = {
            "schema": 1, "edition": "lite", "target": "linux-x64",
            "upstream": {"tag": "v1.0.0", "runtime": {"entry": entry}},
            "files": {
                path: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "executable": False}
                for path, data in files.items()
            },
        }
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.GNU_FORMAT) as archive:
            for path, data in {"manifest.json": json.dumps(manifest).encode(), **files}.items():
                member = tarfile.TarInfo(path)
                member.type = tarfile.AREGTYPE
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        payload = zstandard.ZstdCompressor().compress(stream.getvalue())
        footer = struct.pack("<8sIIQQ32s", b"OMPSFX01", 1, 1, 4, len(payload), hashlib.sha256(payload).digest())
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "release"
            source.write_bytes(b"\x7fELF" + payload + footer)
            destination = Path(tmp) / "unpacked"
            extract(source, destination, "linux-x64", "1.0.0")
            for path, data in files.items():
                self.assertEqual((destination / path).read_bytes(), data)

    def test_payload_corruption_is_rejected(self):
        payload = b"not the expected payload"
        footer = struct.pack("<8sIIQQ32s", b"OMPSFX01", 1, 1, 4, len(payload), b"\0" * 32)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "release"
            source.write_bytes(b"\x7fELF" + payload + footer)
            with self.assertRaisesRegex(ValueError, "payload checksum mismatch"):
                extract(source, Path(tmp) / "unpacked", "linux-x64", "1.0.0")


if __name__ == "__main__":
    unittest.main()
