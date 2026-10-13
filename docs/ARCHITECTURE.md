# Native Linux/macOS builder

## Platform selection

`config/targets.json` maps supported native hosts to a Rust target triple, exact
upstream asset name, payload container and verification runner. The single
`build.sh` / `scripts/build.py` entry point detects the current process platform,
resolves a data-driven plan, and builds with an explicit Rust target. Each
target and edition have separate staging paths; Cargo tools are target-scoped.
Cross-building is rejected. Optional components also use native smoke checks.

The upstream lock records the release version. Both editions stage the complete
published OMP source package and production dependency graph with a pinned native
Bun runtime. `runtime-bun.lock` fixes package versions and integrity hashes;
`runtime-lock.json` fixes Bun archives for Linux x64/Arm64 and macOS Intel/Apple
silicon. Lifecycle scripts are disabled and builds use a frozen dependency lock.
Portable adds locked Python, trafilatura, and browser payloads. Linux launchers
compile statically against musl; Bun and native packages use the host's glibc.
Windows remains unsupported.

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
hash is recorded. Published OMP source is included without patching or rebuilding.
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
status. PATH and XDG_CACHE_HOME select private helpers and caches; HOME and OMP
user-state selectors retain their incoming values. Portable adds default browser
and Python bytecode-cache locations without replacing nonempty explicit overrides.
OMP runs its published `src/cli.ts` under the private Bun executable. SDK modules,
extensions, and worker hosts share the same on-disk package graph. This avoids
the compiled upstream registry's partial wildcard coverage and duplicate host
singletons. User extensions are neither copied nor rewritten by this builder.

Profile cache candidates from environment/argv are precreated only for safe
names. Upstream owns argv parsing; literal profile-looking tool arguments are
not rejected. No optional cache components are created merely for symmetry.

## Portable components

`config/portable-lock.json` pins archives, wheels, browser builds, Linux runtime
packages, and standalone license notices by version, size, and SHA-256.
`scripts/portable.py` stages them without invoking a runtime package manager.
Archive links are checked for containment and materialized before packing; the
final SFX format still accepts only regular files.

Native multicall launchers provide `python`, `python3`, `trafilatura`, and
`chromium`. Python uses its bundled standard library and wheel installation;
trafilatura invokes its declared console entry point. Bytecode is written outside
the verified payload. The default browser path points to the native Chromium
launcher; a nonempty user `PUPPETEER_EXECUTABLE_PATH` takes precedence.

Linux browser dependencies are resolved against an empty installed-package
database for Ubuntu 24.04, not against the build runner's installed packages.
The builder copies ELF shared libraries, fonts, and copyright notices. It excludes
glibc's libraries so the host C runtime stays paired with its loader.
Only Chromium receives the bundled library path and font configuration.
OMP and Python do not inherit browser library overrides.

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

Both workflows cover Lite and Portable on all four native hosts. The release
workflow polls upstream hourly and passes the same upstream, runtime, package,
and Portable locks to all eight builds. Lite is mandatory: every Lite build must
pass before publication. Portable is best-effort with `continue-on-error`; the publish job
validates the Portable artifacts that exist, records skipped ones, and refuses
to publish only when a Lite artifact is missing or invalid. Reports and
manifests remain internal validation inputs; public assets are executables and
SHA-256 files only. The `omp-v<upstream>` downstream tag carries the packaging
revision so rebuilt releases never overwrite published ones. Publication uses a
draft so interrupted uploads do not expose an incomplete release.

## Upstream and license review

The locks record upstream release metadata, native Bun hashes and sizes, and
production package integrity hashes. The smoke checks exercise configuration,
profiles, symlinked extensions with nested SDK imports, shared host registry
identity, TUI rendering, Python, extraction, and browser launch.
Package-provided licenses and third-party notices remain in the complete payload;
the release LICENSE, pinned Bun license, launcher notices, and Portable component
notices are also included.
Unknown Cargo license expressions fail builds. A full dependency license audit
remains outstanding.
