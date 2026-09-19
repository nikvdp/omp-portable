# Native Linux/macOS builder

## Platform selection

`config/targets.json` maps supported native hosts to a Rust target triple, exact
upstream asset name, payload container and verification runner. The single
`build.sh` / `scripts/build.py` entry point detects the current process platform,
resolves a data-driven plan, and builds with an explicit Rust target. Each
platform has its own staging and Cargo output paths. Cross-building is rejected;
optional components will also need native build/test environments.

The upstream lock retains one reviewed release and four official binary hashes:
Linux x64/Arm64 and macOS Intel/Apple silicon. Assets and reviewed upstream source
files are verified before use. The official executable remains byte-identical.
The current payload edition is Lite. Portable/Full component prewarming and
Windows/musl support remain separate unfinished work, not aliases for Lite.

## Payload format

A compressed tar stream is followed by a 64-byte footer. Integer fields are
little-endian:

| Offset | Size | Meaning |
| --- | --- | --- |
| 0 | 8 | `OMPSFX01` |
| 8 | 4 | Format version 1 |
| 12 | 4 | Manifest schema 1 |
| 16 | 8 | Payload offset relative to the selected container |
| 24 | 8 | Compressed byte length |
| 32 | 32 | Compressed payload SHA-256 |

**Linux:** the container is the entire ELF file, with launcher, compressed data,
then fixed footer at EOF. This preserves the original Linux artifact format.

**macOS:** the native linker creates `__OMP,__payload` from the compressed data
and footer. The footer's relative offset is zero. The launcher reads Mach-O's
bounded load-command table to locate that exact section, reads its footer, then
streams from the section's absolute file offset. It never scans executable bytes
for magic markers. Both supported Mac targets use thin little-endian Mach-O 64.

Embedding inside a regular, read-only Mach-O section lets the normal linker and
`codesign` account for the payload. Appending arbitrary data after an existing
Mac signature would not provide this guarantee. The outer launcher is ad-hoc
signed **after** linking; the builder runs `codesign --verify --strict` before
smoke testing. Signature bytes may follow the payload section. The loader uses
section bounds, not physical EOF, on Mac. Synthetic signed-tail, missing-section,
malformed-command and out-of-bounds fixtures exercise the parser on Linux.

The embedded Mac launcher is linked in a separate Cargo target directory so it
cannot become the next build's shim and recursively accumulate old payloads.
`launcher-bin/omp` is the small standalone launcher; it is signed before its
hash is recorded. Upstream OMP is never re-signed or otherwise modified.
The format permits lazy virtual-memory mapping of the payload but extraction
reads through bounded file streams; it does not allocate the archive in RAM.

## Extraction and execution

A per-digest flock serializes first extraction. O_NOFOLLOW uses libc's native
constant rather than a Linux-specific integer. Cache bases are:

- macOS: HOME/Library/Caches/omp-portable/dist
- Linux: XDG_CACHE_HOME/omp-portable/dist, or HOME/.cache/omp-portable/dist

Files stream into a temporary directory; hashes, permissions and the complete
manifest file set are checked before READY and an atomic rename. An interrupted
extraction is recoverable. Reuse validates compressed and extracted bytes.
Only regular files are accepted: traversal, absolute paths, duplicate records,
links and unmanifested files fail. The packer normalizes tar owners/timestamps.
Existing corrupt distribution caches fail closed rather than deleting mutable
on-demand runtime downloads. Digests detect corruption, not malicious same-user
modification; public signing identities/notarization are not implemented.

The native shim derives the distribution from its own path and never resolves
host `omp`. Outer and inner entry points block every `update` variant. Unix exec
preserves arguments, current directory, terminal, streams, signals and exit
status. The environment overlay changes PATH and XDG_CACHE_HOME only; HOME and
OMP user-state selectors retain their incoming values. Embedded OMP native
addons may extract into the ordinary native namespace as upstream intends.

Profile cache candidates from environment/argv are precreated only for safe
names. Upstream owns argv parsing; literal profile-looking tool arguments are
not rejected. No optional cache components are created merely for symmetry.

## Builder verification

Normal builds pack into a candidate path and run the finished artifact from a
new directory with fresh HOME and a reduced PATH. A successful candidate is
promoted into dist; a failed smoke test retains its candidate and leaves the
previous dist executable untouched. Reports distinguish failure and explicit
`--skip-smoke` from a passed build. The distribution convenience link is updated
only after a passed build. Checksums include the final macOS signature bytes.

Linux blocks Internet socket creation with seccomp (native x64/Arm64 syscall
numbers). macOS uses sandbox-exec to deny Internet outbound connections while
allowing local worker sockets. Every smoke run first proves that a loopback
connection receives a permission denial; missing or ineffective network guards
fail the test. Dead proxies are not used as evidence of offline operation.

The verification workflow runs all four native hosts, including codesigning and
real OMP smoke tests on both Macs. The release workflow polls upstream hourly,
passes one resolved lock to all four builds, and publishes only after every
build and the complete-artifact checksum gate pass. Publication uses a draft
release so interrupted uploads do not expose an incomplete release.

## Upstream and license review

The lock records release ID, hashes/sizes and reviewed source files covering
cache/profile resolution, updater routing, embedded addon extraction and Linux
file locking. New tags can reuse that review only when all reviewed source hashes
remain identical; a changed file stops release preparation for human review.
Release LICENSE, the downstream
license, and notices from locked Cargo dependency sources are included. Unknown
Cargo license expressions fail builds. A full audit of every dependency compiled
inside upstream's binary remains a requirement before public release publication.
