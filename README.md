# omp-portable

Linux x64 Lite milestone: a single native self-extracting executable containing
an unmodified official OMP binary. No installed OMP, Bun, Node, Python, tar,
or zstd command is required to launch it. Python and Rust are build tools only.

This is **not the complete multi-platform Portable/Full release mirror**.
Only Linux x64 Lite is implemented. No scheduled publication is enabled.

## Build

Prerequisites: Linux x64, Rust/Cargo, a C compiler (for the zstd library),
Python 3.11+, Git and HTTPS access to GitHub/crates.io.

```sh
python3 scripts/build-lite.py
python3 -m unittest discover -s tests -v
python3 scripts/smoke.py dist/omp-lite-18.2.6-linux-x64
```

The checked-in upstream lock pins the reviewed release, binary digest, license,
and relevant source files. Unknown tags cannot silently inherit reviewed
assumptions. Review and update the lock before adopting another version.
`Cargo.lock` pins the downstream dependency graph.

## Run

```sh
chmod +x omp-lite-18.2.6-linux-x64
./omp-lite-18.2.6-linux-x64 --version
./omp-lite-18.2.6-linux-x64
```

First launch verifies and streams the payload into
`${XDG_CACHE_HOME:-$HOME/.cache}/omp-portable/dist/<payload-sha256>/`.
Later launches validate and reuse that directory. Each launch checks the
compressed payload and all immutable components; this trades some startup I/O
for corruption detection. It never loads the whole payload into memory.

Normal HOME, configuration, credentials, sessions, and plugins remain visible.
The launcher changes only PATH and XDG_CACHE_HOME for its child. Subprocesses
inherit that private cache root. The private native `omp` command always starts
this distribution. All `omp update` variants are intercepted, including
`--plugins` and `--check`. Download a newer downstream artifact to update.

Optional OMP features not bundled in Lite can still download/install their
normal dependencies on first use. Lite contains no optional model weights,
browser, Python, yt-dlp or trafilatura. Embedded upstream native code may extract
into the normal OMP home. Lite is not an offline edition for optional features.

If immutable extracted files are corrupt, launch fails closed. Remove only the
reported distribution cache and retry; do not remove your normal `.omp` home.
Private runtime caches inside that distribution may contain on-demand downloads.

## Scope and export

See [docs/VALIDATION.md](docs/VALIDATION.md) for actual tests and limitations,
and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the format and roadmap.
This repository uses local commits and can be exported with history:

```sh
git bundle create omp-portable.bundle --all
git clone omp-portable.bundle omp-portable
```
