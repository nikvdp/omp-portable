# Cross-platform revision validation

This revision adds native macOS Apple silicon/Intel and Linux Arm64 build paths
to the existing Linux x64 builder. It is intended to be cloned and built on the
user's Mac, not mistaken for a Linux-produced, Mac-tested executable.

## Checks in this Linux session

- Eight Rust format/parser tests, including a Mach-O payload section with
  signature bytes after it, invalid command/section bounds, missing section,
  zero-relative-offset payload containers, ELF footer and path checks.
- Twenty-two Python tests: platform detection, four pinned official assets,
  Mac cache selection, wrong-host rejection, plus the original 17 lifecycle
  tests including concurrent first runs, actual mid-extraction SIGKILL/recovery,
  corruption, byte reproducibility, update isolation, signals and terminal use.
- Complete native Linux x64 build through `./build.sh`, followed by real OMP
  tests against the resulting artifact with enforced network restrictions.
- Linux smoke checks cover OMP version/help, configuration reads, speech payload
  absence, read/grep, profile selection, user-state sentinels, update guard,
  private shim, upstream hash equality, agent initialization and bash execution.
- Rust formatting and Clippy with warnings denied.

Mac source/type-check results are recorded in this revision's final validation
entry below. Native macOS linking, codesign behavior and execution still need
an actual Mac; the builder performs all three rather than declaring success
based only on a source check.

## Known Linux sandbox limit

Upstream config writes use an abstract Unix datagram socket as a lock. This host
denies that operation. Both the packaged and untouched official executable
produce the same failure. Normal smoke mode records only that exact reproduced
Linux failure; `--strict-host` rejects it. The Mac path never waives config errors.
The SFX's filesystem flock and normal agent startup work here.

## Native Mac validation supplied

A default Mac `./build.sh` verifies the official asset digest, compiles the native
launcher and packer, embeds the payload into a proper Mach-O section, signs and
verifies the finished executable, then runs the same real-OMP smoke suite with
sandbox-enforced network denial. Reports are saved under `build/<target>/`.
A signature or smoke failure exits unsuccessfully and leaves a diagnostic
candidate rather than publishing it to dist.

The GitHub workflow additionally runs the extraction lifecycle suite on Apple
silicon and Intel runners. It has been supplied but not executed remotely here.

## Remaining product work

Portable/Full optional components and inference tests, Windows seeding/process
handoff, musl runtime dependencies, minimal-host compatibility certification,
full upstream embedded-license audit, notarization/signing identities, release
mirroring and publication remain unimplemented. Lite's ordinary optional
on-demand installation is preserved, but successful optional downloads were
not exercised. No paid model requests were used.

## Final cross-target check results

Both `cargo check --locked --target aarch64-apple-darwin` and the corresponding
`x86_64-apple-darwin` check completed successfully in this session. Rust's Mac
standard libraries were installed locally. Zig's C cross compiler compiled the
zstd dependency into Mac-target object files for these checks. This toolchain
was used only for development validation; Mac users build with Apple's native
compiler and do not need Zig. These were **type checks, not native executable
link/sign/run tests**. The supplied build path performs the remaining checks on
macOS. The native Linux run and all 30 Rust/Python tests passed locally.
