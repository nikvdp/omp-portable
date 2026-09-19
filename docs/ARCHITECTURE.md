# Linux x64 Lite architecture

## Scope

This implements the first OMP milestone and the Linux subset of the generic
self-extracting executable milestone. Portable, Full, Windows, macOS, musl,
and automated release mirroring are intentionally not advertised or enabled.
The user-facing launcher is Rust; Python is build/test orchestration only.

## File format version 1

All integers are little-endian. The last 64 bytes contain:

| Offset | Length | Meaning |
| --- | --- | --- |
| 0 | 8 | `OMPSFX01` |
| 8 | 4 | Format version, 1 |
| 12 | 4 | Manifest schema, 1 |
| 16 | 8 | Payload byte offset |
| 24 | 8 | Compressed payload length |
| 32 | 32 | SHA-256 of compressed payload |

Before the footer: native launcher, then a zstd-compressed tar stream.
The footer bounds must exactly match the file length. Extraction hashes the
bounded compressed stream, seeks back, and streams zstd/tar into a temporary
directory. Hash buffers are 128 KiB; no whole-payload allocation is used.

Version 1 permits regular files only. Absolute paths, parent traversal,
symlinks, hardlinks, duplicate entries, unlisted files and reserved READY paths
are rejected. Headers and file records specify normalized executable modes.
The packer produces a sorted archive with zero timestamps/owner IDs. The same
stub and staging bytes produce identical artifacts.

## Extraction and launch

A per-digest `flock` serializes extraction. The cache is mode 0700. Stale
temporary directories for that digest are cleaned under the lock. Component
hashes and executable permissions are checked, files are synchronized, READY
is written, then the temporary directory is atomically renamed. The lock is
released before executing OMP. Failed extraction never creates a valid cache.
Interrupted packer output uses a process-specific partial filename.

Reuse verifies READY and immutable file hashes. Corrupt existing distributions
fail closed; they are not automatically deleted because their mutable caches
may contain user-triggered downloads. SHA-256 detects corruption; this is not
a signed artifact or a defense against a malicious process running as the user.

The same native stub without its payload is installed as `launcher-bin/omp`.
It derives the distribution root from its executable path, validates components,
then directly executes `bin/omp.real`. It never resolves host `omp`.
Both entry points block the first upstream `update` subcommand, including when
preceded by profile selectors. No update variant is passed through in v1.
An explicit direct invocation of `bin/omp.real` bypasses this wrapper contract.

Unix process replacement preserves working directory, arguments, standard
streams, terminal and signals. The environment overlay changes only PATH and
XDG_CACHE_HOME. It preserves HOME and all OMP data/config/state selectors.
No helpers are bundled in Lite, so only the launcher directory is prepended.

Profile cache candidates from environment and argv are precreated if their
names are safe. Upstream retains authority over argument parsing; literal
`--profile` tool arguments are never rejected by the wrapper. This can create
an unused empty candidate cache, but never changes user profile state. Lite
has no model tree to share between profiles. Explicit custom agent-directory
settings retain upstream's own cache-resolution behavior.

## Exact upstream review: v18.2.6

`config/upstream-lock.json` records GitHub's release ID, size, asset digest and
hashes of reviewed source files. The builder verifies these before packing:

- `dirs.ts`: XDG cache selection on Linux/macOS requires the appropriate app
  or profile cache directory. Agent paths flatten under XDG; the handoff's
  illustrative directory tree must not be used as a permanent contract.
- `profile-bootstrap.ts`: profile switches can be literal tool arguments;
  registered subcommands retain their own flag parsing.
- `update-cli.ts`: updater can resolve the active launcher through PATH;
  all updater variants are blocked by this milestone.
- `loader-state.js`: embedded native addons extract to the ordinary OMP
  native namespace; XDG_DATA_HOME must not be reassigned by this launcher.

The official OMP executable is copied byte-for-byte; no recompilation or binary
patch is performed. Its release license and notices from locked Rust dependency
sources are included. New/unreviewed dependency license expressions fail builds.
Upstream's complete compiled third-party dependency audit remains upstream-owned;
this milestone is not a claim that its release LICENSE enumerates every embedded
third-party notice. Review that inventory before public redistribution at scale.

## Next milestones

1. Run strict tests on a clean ordinary Linux host and a minimal container;
   audit upstream's complete binary notice inventory before public releases.
2. Expand Lite to native platform runners and implement Windows seeding/handoff.
3. Add Portable components individually, with real offline inference tests.
4. Add Full registry discovery, supported-model filtering and deterministic budget.
5. Add missing-release discovery, resumable drafts and complete-matrix publication.

The checked-in workflow verifies Linux Lite only. It has read-only permissions
and does not publish. No GitHub repository or workflow run was created remotely
in this session.
