# Cross-platform OMP single-file builder

Build a native, self-extracting OMP executable **on macOS or Linux**. The builder
automatically detects Apple silicon, Intel Mac, Linux x64, or Linux Arm64 and
bundles the matching official OMP release. It does not rebuild or modify OMP.

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

## What is bundled

The currently implemented **Lite edition** contains official OMP, the native
extractor/update-protecting launcher, manifest and license notices. Ordinary
optional OMP features retain their normal on-demand dependency installation.
A bundled browser, speech models and embedding models belong to the planned
Portable/Full editions and are not claimed by this build.

| Build host | Native output | Verification in this session |
| --- | --- | --- |
| Apple silicon Mac | darwin-arm64 | Rust cross-target type check passed; native run still needed |
| Intel Mac | darwin-x64 | Rust cross-target type check passed; native run still needed |
| Linux x64 | linux-x64 | Full local build and offline smoke tests |
| Linux Arm64 | linux-arm64 | Implemented; native run still needed |

Windows and musl need their own launcher/runtime work and are rejected clearly.
One portable file is produced **per operating system and architecture**.

## Runtime behavior

First launch verifies and streams the embedded archive into a private cache:

- macOS: `$HOME/Library/Caches/omp-portable/dist/<payload-sha256>/`
- Linux: `${XDG_CACHE_HOME:-$HOME/.cache}/omp-portable/dist/<payload-sha256>/`

Subsequent launches verify and reuse it. Normal HOME, configuration, credentials,
sessions and plugins remain visible. Only child PATH and XDG_CACHE_HOME are
changed. Subprocesses inherit the private cache; the private `omp` shim always
starts this distribution. All `omp update` variants are intercepted; rebuild
with a reviewed newer upstream lock to update. Nothing replaces global OMP.

## Inspect and diagnose

```sh
./build.sh --plan                    # no download or build
./build.sh --strict-host             # require upstream config writes too
python3 -m unittest discover -s tests -v   # after a native build
```

Each build writes `build/<target>/build-plan.json`,
`build/<target>/smoke-results.json`, plus per-artifact `.build.json`,
`.manifest.json` and `.sha256` sidecars under `dist/`.

A failed smoke test makes the build command fail. The candidate is
left under `build/<target>/candidate/` for diagnosis and its `.build.json` says
`smoke: failed`; existing files under `dist/` are preserved. `--skip-smoke` explicitly skips
verification and is never used by the native verification workflow.
`--skip-compile` reuses target-scoped tools; normal builds do not need either flag.

The local Linux sandbox denies the abstract Unix socket operation that upstream
uses for configuration-write locks. Normal smoke mode records that exact error
only when the unwrapped official binary reproduces it. `--strict-host`, used by
the four-target workflow, treats it as a failure. Mac failures are never waived.

See [docs/VALIDATION.md](docs/VALIDATION.md) for the evidence and limits, and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the macOS container design.
The native Mac workflow is supplied but was not run from this Linux session.

## Git history

This is an ordinary Git repository. The new bundle contains the original commits
and the cross-platform changes. Export it later with:

```sh
git bundle create omp-portable.bundle --all
```
