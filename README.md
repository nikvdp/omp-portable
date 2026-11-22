# Cross-platform OMP single-file builder

Build a native, self-extracting OMP executable **on macOS or Linux**. The builder
detects Apple silicon, Intel Mac, Linux x64, or Linux Arm64 and bundles a pinned
Bun runtime with the complete published OMP package and production dependencies.
OMP source is not patched or rebuilt. Extensions use the same packages as the host.

Lite bundles the OMP runtime. Portable also bundles Python, trafilatura, and a browser.
Neither edition includes model weights or media helper tools.

## Install Lite with Nix

From this checkout:

```sh
nix run . -- --version
nix run .
nix profile install .
```

Or build without installing:

```sh
nix build .
./result/bin/omp --version
```

The flake downloads the newest stable Lite release recorded in
`config/nix-release.json` and unpacks it without executing the downloaded
launcher. The downstream Nix workflow reconciles this pin from the published
assets on `main`, including when no new Lite build is needed.
Bun, OMP source, dependencies, and license notices stay in `/nix/store`.
Startup runs that unpacked copy directly: no first-launch extraction or repeated
portable-payload checksum scan. Nix verifies the downloaded release hash, and
the build verifies the payload and every extracted file before applying fixups.
The install uses a compiler-free build environment: it does not compile OMP or
require GCC/Clang. Python is used only to verify and unpack the release; Linux
also needs ELF patching tools and runtime libraries. Allow disk space for both
the downloaded artifact and its unpacked runtime, plus Nix's dependencies.

Your normal configuration, credentials, sessions, extensions, profiles, and cache
environment remain in use; nothing writes caches into the Nix store. `omp update`
is blocked because Nix owns the installation. Install from the repository to
track its releases, then upgrade with:

```sh
nix profile install 'git+ssh://git@github.com/nikvdp/omp-portable'
nix profile upgrade --refresh omp-portable
```

On macOS inside Herdr, the launcher preserves `omp` as the foreground process
identity, matching OMP's native launcher so Herdr can recognize its integration.

Each flake revision pins the version, release tag, and four asset hashes in
`config/nix-release.json`; `flake.lock` pins Nixpkgs separately. Refreshing the
profile fetches the current `main` pins, not a mutable unchecked download.
Updating only Nixpkgs does not change OMP.

The flake targets Apple silicon/Intel macOS and ARM64/x86-64 Linux. It packages
Lite only: no Python, trafilatura, browser, or model weights are added. Extensions'
own third-party dependencies still belong with those extensions.

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

The first build downloads the pinned Bun runtime and locked OMP packages,
compiles the launcher, embeds the payload, ad-hoc signs the outer Mac executable,
checks its signature, and runs the finished artifact from a temporary directory
with fresh state and outbound network access blocked. No developer signing
account is needed for ad-hoc signing. This is not Apple notarization.

Output is `dist/omp-lite-18.15.0-darwin-arm64` or
`dist/omp-lite-18.15.0-darwin-x64`. `dist/omp-lite` points to the latest successful
native build. Copy the **versioned file** to another Mac of the same architecture.
The destination needs no Rust, Python, Node, Bun, or installed OMP to launch it.
If sharing through a service that strips executable permissions, restore them
with `chmod +x <filename>`. Normal macOS quarantine rules still apply to files
transferred from the Internet.

## Linux

Install Python 3.11+, Rust/Cargo, a C compiler, Git, and on Linux `musl-tools`
(for the static launcher), then use the same `./build.sh` and `./dist/omp-lite`
commands. The builder supports native Linux x64/Arm64 hosts. The Linux launcher
is statically linked against musl. Bundled Bun and native package dependencies
use the host's glibc; their platform requirements apply to the finished artifact.
Portable builds also need `dpkg-deb` to extract the pinned browser libraries.
The Portable browser bundles Ubuntu 24.04 runtime libraries; on older
distributions the browser component may still require a newer userspace, while
OMP itself and bundled Python continue to work.

## What is bundled

| Edition | Bundled content |
| --- | --- |
| Lite | Native Bun, complete OMP package and production dependencies, launcher, manifest, and license notices |
| Portable | Lite plus private Python 3.12, its standard library, trafilatura and its dependencies, and native Chromium |

Portable includes the browser's Linux shared libraries and fonts. The host's
glibc and ELF loader remain in use. All extra downloads are version-, size-, and
SHA-256-pinned in `config/portable-lock.json`. Full remains unimplemented; no
speech, embedding, or other model weights are bundled.

| Native target | Lite verification | Portable verification |
| --- | --- | --- |
| darwin-arm64 | GitHub build and release passed | GitHub build and release passed; local native checks |
| darwin-x64 | GitHub build and release passed | GitHub build and release passed |
| linux-arm64 | GitHub build and release passed | GitHub build and release passed; minimal offline Ubuntu 24.04 check |
| linux-x64 | GitHub build and release passed | GitHub build and release passed |

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
Extensions remain in your normal user or project directories, including symlinked
directories. SDK imports resolve against the bundled package tree, including
wildcard subpaths and nested imports. No host Bun, Node, npm, or global OMP is needed.
Packages owned by an extension must still be installed with that extension;
this builder does not infer or download undeclared third-party dependencies.
All `omp update` variants are intercepted; rebuild against a newer upstream
release to update. Nothing replaces global OMP.

## Inspect and diagnose

```sh
./build.sh --plan                    # no download or build
./build.sh --strict-host             # require upstream config writes too
python3 -m venv /tmp/omp-tests
/tmp/omp-tests/bin/pip install zstandard==0.23.0
/tmp/omp-tests/bin/python -m unittest discover -s tests -v   # after a native build
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

### Performance review for changes

Before changing packaging, extraction, or startup, measure the current finished
executable with a fresh HOME, cached launches, and the private shim. After the
change, repeat on the same machine with comparable conditions and the real
payload. Record before-and-after timings and exercise affected functionality;
passing small fixture tests alone does not establish a usable release.

Investigate and fix material regressions before calling a change complete. Do
not hide them by increasing timeouts, weakening integrity checks, or reporting
only functional success. If a performance tradeoff is genuinely unavoidable,
explain its user impact and get explicit agreement before accepting it.

Smoke checks print timings and build reports retain them for review. Timings are
measurements, not fixed pass/fail cutoffs or app runtime limits. A shippable change
preserves the requested behavior, extension compatibility, integrity checks, and
interrupted-extraction recovery without an unagreed material regression.

The local Linux sandbox denies the abstract Unix socket operation that upstream
uses for configuration-write locks. Normal smoke mode records that exact error
only when the same packaged OMP runtime reproduces it without the launcher.
`--strict-host`, used by the four-target workflow, treats it as a failure.
Mac failures are never waived.

See [docs/VALIDATION.md](docs/VALIDATION.md) for the evidence and limits, and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the macOS container design.

## Automated releases

`.github/workflows/release.yml` (**Release OMP Lite**) publishes on pushes to
`main`, checks for new stable upstream releases hourly at minute 49, and supports
manual dispatch. It builds only Lite. PRs and non-main pushes use the same
latest-upstream preparation and Lite candidate checks without publishing. No
personal access token is needed.

The workflow resolves the latest stable upstream release and checks its published
`engines.bun` range. A compatible pinned Bun is retained; otherwise preparation
selects the latest stable Bun satisfying that range, verifies all four archives
against npm SHA-512 integrity, and records SHA-256, sizes, and license metadata.
An incompatible stable runtime fails preparation before the build matrix starts.
One production dependency lock is generated for all build jobs.
`config/runtime-bun.lock` pins package versions and integrity hashes;
`config/runtime-lock.json` pins native Bun archives. Offline extension and runtime
smoke checks remain mandatory. Optional component versions stay pinned separately.

All four native Lite targets must pass Rust checks, strict offline smoke checks,
and extraction tests before publication. The publisher validates the artifacts
and uploads the executables and checksums to a draft before publishing it.
Neither Nix nor Portable gates Lite publication.

**Update Nix from published Lite** runs separately after the Lite release
workflow. It selects the newest stable published Lite version and packaging
revision and requires all four uploaded binaries and checksums. It updates only
`config/nix-release.json` on `main` using GitHub's binary asset hashes. An
already-published release can repair stale pins without rebuilding; dry runs,
drafts, and incomplete releases cannot advance the pin. The bot uses
`GITHUB_TOKEN`, so its commit does not trigger another push workflow. Branch
protection must allow the bot update.

The Nix workflow checks the reconciled flake in a fresh profile on all four
targets, verifies the version, and runs the flake's installed extension SDK
check. Hourly runs skip the install matrix if the pin is unchanged. PRs and
non-main pushes perform the same checks against their own Lite candidates in a
separate job. Nix failures are visible without holding up Lite or Portable.

`.github/workflows/portable.yml` (**Portable best effort**) runs independently
after a successful Lite release workflow on `main`. It checks out that run's
exact source SHA and downloads its `portable-release-inputs` artifact: the
published release ID/tag plus the resolved upstream, runtime, package, and
Portable locks. Their hashes and the source SHA must match before building.
It never resolves the latest upstream version again. Dry runs and duplicate
Lite releases have no publication handoff, so Portable is skipped.

Each Portable target must pass the same strict build, smoke, and extraction
checks, plus artifact validation, before its binary and checksum are appended to
that specific published release. Portable cannot create or edit a release,
delete assets, replace existing assets, or upload Lite assets. Reruns skip
identical existing Portable files and reject differing ones. Manifests and build
reports stay in workflow artifacts. Build/setup/upload failures are handled at
the step level and listed in the job summary without failing the workflow;
cancellations and GitHub infrastructure failures can still interrupt it.

The current packaging revision produces `omp-v<upstream-version>-r<N>`; bump
`RELEASE_REVISION` (or run `make release`, which does it for you) when a
complete published release would otherwise be skipped. Revision r6 starts the
independent Lite release path; older combined releases and drafts are untouched.
Complete published Lite sets are skipped; interrupted Lite drafts can be retried.

For a GitHub dry run, leave the manual workflow's **publish** checkbox clear.
This builds and uploads Lite workflow artifacts without creating a release or
triggering Portable builds. To retry Portable for an actual publication, rerun
its best-effort workflow while the original Lite handoff artifact is retained.
See [validation evidence](docs/VALIDATION.md) for hosted and local checks.

Avoid changing workflow files on the default branch while a release is building.
GitHub can reject release creation from an older workflow revision with HTTP 403,
even with `contents: write`. If this happens, rerun from current `main`; do not
retarget the release to a commit that was not built.

## Git history

This is an ordinary Git repository. The new bundle contains the original commits
and the cross-platform changes. Export it later with:

```sh
git bundle create omp-portable.bundle --all
```
