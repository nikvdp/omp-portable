# Cross-platform OMP single-file builder

Build a native, self-extracting OMP executable **on macOS or Linux**. The builder
automatically detects Apple silicon, Intel Mac, Linux x64, or Linux Arm64 and
bundles the matching official OMP release. It does not rebuild or modify OMP.

Lite bundles OMP only. Portable also bundles Python, trafilatura, and a browser.
Neither edition includes model weights or media helper tools.

## Build on your Mac

Clone the new bundle into a new directory (it contains the original history):

```sh
git clone omp-portable-cross-platform.bundle omp-portable
cd omp-portable
```

You need Python **3.11+**, Rust/Cargo, and Apple's command-line developer tools.
If you already have these, skip installation. With Homebrew:

```sh
xcode-select --install   # only if Apple's command-line tools are missing
brew install python rust
```

Finish the Apple tools installation before building. Homebrew is just a way to
install build prerequisites; it is not used by the resulting executable.
Rust installed through rustup also works; the builder checks `~/.cargo/bin`.
Then run:

```sh
./build.sh
./dist/omp-lite --version
./dist/omp-lite
```

To build Portable instead, run:

```sh
./build.sh --edition portable
./dist/omp-portable --version
./dist/omp-portable
```

**That is the same command on Apple silicon and Intel.** No architecture flag
is required. Use an ordinary native terminal/Python on Apple silicon; a Python
running through Rosetta identifies itself as Intel and builds the Intel target.

The first build downloads the pinned official OMP binary and Rust dependencies,
compiles the launcher, embeds the payload, ad-hoc signs the outer Mac executable,
checks its signature, and runs the finished artifact from a temporary directory
with fresh state and outbound network access blocked. No developer signing
account is needed for ad-hoc signing. This is not Apple notarization.

Output is `dist/omp-lite-18.2.6-darwin-arm64` or
`dist/omp-lite-18.2.6-darwin-x64`. `dist/omp-lite` points to the latest successful
native build. Copy the **versioned file** to another Mac of the same architecture.
The destination needs no Rust, Python, Node, Bun, or installed OMP to launch it.
If sharing through a service that strips executable permissions, restore them
with `chmod +x <filename>`. Normal macOS quarantine rules still apply to files
transferred from the Internet.

## Linux

Install Python 3.11+, Rust/Cargo, a C compiler and Git, then use the same
`./build.sh` and `./dist/omp-lite` commands. The builder supports native glibc
Linux x64/Arm64 hosts. It does not label musl/Alpine supported.
Portable builds also need `dpkg-deb` to extract the pinned browser libraries.
The Portable Linux runtime baseline is Ubuntu 24.04 with glibc 2.39 or newer;
other Linux distributions have not been verified.

## What is bundled

| Edition | Bundled content |
| --- | --- |
| Lite | Official OMP, native launcher, manifest, and license notices |
| Portable | Lite plus private Python 3.12, its standard library, trafilatura and its dependencies, and native Chromium |

Portable includes the browser's Linux shared libraries and fonts. The host's
glibc and ELF loader remain in use. All extra downloads are version-, size-, and
SHA-256-pinned in `config/portable-lock.json`. Full remains unimplemented; no
speech, embedding, or other model weights are bundled.

| Native target | Lite verification | Portable verification |
| --- | --- | --- |
| darwin-arm64 | GitHub build and release passed | Local native build and offline smoke checks |
| darwin-x64 | GitHub build and release passed | Configured; native verification still needed |
| linux-arm64 | GitHub build and release passed | Local `act` workflow and minimal offline Ubuntu 24.04 container |
| linux-x64 | GitHub build and release passed | Configured; native verification still needed |

Windows and musl need their own launcher/runtime work and are rejected clearly.
One portable file is produced **per operating system and architecture**.

## Runtime behavior

First launch verifies and streams the embedded archive into a private cache:

- macOS: `$HOME/Library/Caches/omp-portable/dist/<payload-sha256>/`
- Linux: `${XDG_CACHE_HOME:-$HOME/.cache}/omp-portable/dist/<payload-sha256>/`

Subsequent launches verify and reuse it. Normal HOME, configuration, credentials,
sessions and plugins remain visible. Child PATH and XDG_CACHE_HOME select the
private helpers and cache; the private `omp` shim always starts this distribution.
Portable sets a default `PUPPETEER_EXECUTABLE_PATH` and Python bytecode cache
location, preserving nonempty explicit overrides. Browser libraries and font
settings apply only to the browser process, not OMP or Python.
All `omp update` variants are intercepted; rebuild with a reviewed newer upstream
lock to update. Nothing replaces global OMP.

## Inspect and diagnose

```sh
./build.sh --plan                    # no download or build
./build.sh --strict-host             # require upstream config writes too
python3 -m unittest discover -s tests -v   # after a native build
```

Each build writes `build/<target>/<edition>/build-plan.json`,
`build/<target>/<edition>/smoke-results.json`, plus per-artifact `.build.json`,
`.manifest.json` and `.sha256` sidecars under `dist/`. Public GitHub releases
contain only the versioned executables and `.sha256` files.

A failed smoke test makes the build command fail. The candidate is
left under `build/<target>/<edition>/candidate/` for diagnosis and its `.build.json`
says `smoke: failed`; existing files under `dist/` are preserved. `--skip-smoke` explicitly skips
verification and is never used by the native verification workflow.
`--skip-compile` reuses target-scoped tools; normal builds do not need either flag.

The local Linux sandbox denies the abstract Unix socket operation that upstream
uses for configuration-write locks. Normal smoke mode records that exact error
only when the unwrapped official binary reproduces it. `--strict-host`, used by
the four-target workflow, treats it as a failure. Mac failures are never waived.

See [docs/VALIDATION.md](docs/VALIDATION.md) for the evidence and limits, and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the macOS container design.

## Automated releases

`.github/workflows/release.yml` checks the latest stable upstream release hourly
and can also be run from Actions using **Release OMP Lite and Portable**. Push the workflow
to the repository's default branch and enable Actions. No personal access token
is needed on GitHub; only the publication job receives `contents: write`.

The workflow resolves upstream asset hashes from GitHub and checks the reviewed
source files against the checked-in lock, including Python, browser, and
extraction integration code. If a reviewed file changes, the run fails with
`Review required`; review and update the lock before retrying. Optional component
versions stay pinned separately. The workflow tracks the latest stable OMP
release, not every historical release.

All eight builds—Lite and Portable on four native platforms—must pass Rust
checks, strict offline smoke tests, and extraction tests before publication.
The final job validates binaries, checksums, manifests, build reports, and the
Portable dependency-lock digest. Manifests and reports stay in workflow artifacts;
only eight executables and eight `.sha256` files are uploaded to the release.
Publication uses a draft so incomplete uploads aren't published.

The current packaging revision produces `omp-v<upstream-version>-r2`. Existing
Lite-only `omp-v<upstream-version>` releases remain unchanged and do not prevent
the new combined release. Complete published revision-2 releases are skipped;
interrupted drafts can be retried.

For a GitHub dry run, leave the manual workflow's **publish** checkbox clear.
This builds and uploads workflow artifacts without creating a release.
Lite has passed all four hosted runners and publication. Portable has passed
local macOS ARM64 and Linux ARM64 checks; the eight-build GitHub workflow and
Portable x64 builds still need their first run. See the validation document for
the exact local evidence.

## Git history

This is an ordinary Git repository. The new bundle contains the original commits
and the cross-platform changes. Export it later with:

```sh
git bundle create omp-portable.bundle --all
```
