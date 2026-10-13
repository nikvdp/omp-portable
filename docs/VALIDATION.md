# Validation

The current builder packages OMP 18.10.3 with its complete production dependency
tree and a private Bun runtime. Portable adds Python, trafilatura, and a native
browser. Full and model payloads are not implemented. The older hosted results
below describe previously published builds, not the current extension fix.

## Startup performance and shipping contract

Packaging revision 4 was not acceptable: the complete package tree exposed a
per-file disk flush and unbuffered manifest parsing, and a roughly 145-second
local cold start was allowed through by raising the smoke timeout. A user then
reported extraction lasting more than five minutes on Linux. A functional pass
did not establish usable startup performance.

The corrected launcher removes per-file flushes, buffers manifest parsing,
reuses the verified manifest, copies through one 128 KiB buffer, and enables
CPU-dispatched SHA-256 acceleration. Four bounded workers overlap independent
file checks. Every payload and extracted-file checksum remains checked on every launch.
The cache does not promise full power-loss durability; corrupt caches fail closed.

Shippable means the finished target/edition executable passes all existing
offline functionality, extension imports and shared registry checks, corruption
rejection, concurrent extraction, and killed-extraction recovery **and**:

- Fresh-HOME `--version`: at most 30 seconds including extraction and verification.
- Three cached `--version` launches: at most 3 seconds each.
- Private `omp` shim `--version`: at most 3 seconds.

`scripts/smoke.py` enforces these limits in every native build and prints timings.
Build reports retain startup evidence; `scripts/release.py` rejects missing,
slow, or incomplete timing evidence even when `smoke` says `passed`. Do not raise
limits or timeouts to hide a regression. Changing the shipping contract requires
explicit user agreement, not a convenient CI adjustment. A fast small fixture
does not substitute for timing the real release payload.

On local macOS ARM64, the final Lite artifact measured 11.111 seconds cold,
0.732/0.715/0.638 seconds cached, and 0.853 seconds for the private shim. All 24
offline checks, 23 lifecycle/platform tests, 8 Rust tests, Clippy, and formatting
passed. Throwaway publication checks accepted the exact timing boundaries and
rejected missing evidence, slow cold startup, one slow cached sample, and a slow
private shim. These are native CI bounds, not guarantees for every filesystem.
Linux ARM64 Lite, built and run in a local Ubuntu 24.04 container, measured
3.677 seconds cold and 0.390/0.373/0.375 seconds cached. Its private shim took
0.249 seconds; all 24 strict offline checks and 23 lifecycle/platform tests passed.
Linux amd64 and Intel macOS performance still require native hosted verification.
The final macOS ARM64 Portable artifact measured 22.006 seconds cold,
1.180/1.166/1.129 seconds cached, and 0.910 seconds for the private shim. Its 29
strict offline checks passed, including Python, HTML extraction, Chromium,
extension imports, and explicit browser override preservation.

## Extension runtime verification

The macOS ARM64 Lite artifact passed 20 strict offline smoke checks, including
a symlinked extension with nested SDK imports, wildcard TUI imports, and access
to the live host registry. All 22 lifecycle/platform tests passed after adapting
the launcher fixtures to the source-entry contract. The final Portable artifact
passed 25 strict offline checks, including Python, HTML extraction, Chromium,
and extension imports.

The actual `side.ts` and `session-injector.ts` extensions ran against the packaged
runtime on macOS ARM64 and Linux amd64. A scripted offline provider triggered
the real `fork_task` tool; its child inherited the conversation, called `yield`,
and delivered its result back to the parent. Linux used an Ubuntu 24.04 amd64
container with networking disabled. This Linux check exercised the runtime,
not a finished Linux self-extracting artifact.

All three reported extensions loaded together on macOS ARM64 and Linux amd64,
including `model-ladder` and its SQLite state under an isolated HOME. The finished
macOS Lite artifact also passed the real fork/yield scenario and started an agent
through `/side`. No paid model requests were used. Intel macOS and Linux ARM64
execution of this packaging change has not been verified locally.

Clippy and Python syntax checks passed. Workflow syntax passed with ShellCheck
disabled; the normal run reports existing SC2193 warnings for matrix expressions
in both workflows. Throwaway publication fixtures accepted the complete Lite
set and rejected a stale production dependency lock without remote writes.

## Hosted Lite builds and publication

All four native Lite targets passed GitHub verification and release:

- [Four-platform verification](https://github.com/nikvdp/omp-portable/actions/runs/35490959363).
- [Build and publication](https://github.com/nikvdp/omp-portable/actions/runs/35490966175).
- [Published Lite 18.2.6 release](https://github.com/nikvdp/omp-portable/releases/tag/omp-v18.2.6).

The Mac packaging fix disables temporary-path-dependent linker UUIDs. The
corruption test changes the actual embedded payload and re-signs the Mac fixture
so it exercises the distribution's digest check. All 22 lifecycle/platform tests
passed on the hosted platforms. The published release now contains four
executables and four SHA-256 files; its build reports and manifests were removed.

## Hosted combined release

The [eight-build release workflow](https://github.com/nikvdp/omp-portable/actions/runs/35500466819)
passed for Lite and Portable on Linux ARM64/x64 and macOS ARM64/x64.
It published [OMP 18.2.6 packaging revision 2](https://github.com/nikvdp/omp-portable/releases/tag/omp-v18.2.6-r2)
from commit `6c44c349a2acac1b80f82e9994d2f54f9a3a722c`.
All 16 assets were uploaded: eight executables and eight SHA-256 files, with no
build reports or manifests. Each downloaded checksum matched GitHub's digest of
the corresponding executable.

The hosted Linux fix puts smoke state under a short private directory in `/tmp`.
Long checkout-relative temporary paths exceeded Chromium's Unix socket pathname
limit. Network guards and browser protocol assertions were unchanged.

After all eight builds passed, account billing blocked the publisher from
starting. Making the repository public allowed the publisher-only rerun to
complete using the existing verified artifacts and the workflow's own token.

## Local Portable verification

Portable was built and exercised on macOS ARM64 and Linux ARM64:

- Rust formatting, Clippy with warnings denied, and eight Rust tests passed.
- All 22 existing Python lifecycle/platform tests passed on both hosts.
- Each Portable artifact passed 24 strict offline checks. These cover the existing
  OMP commands, profiles, config writes, update protection, private shim, and RPC
  execution, plus bundled Python, HTML extraction, browser JavaScript rendering,
  browser-path selection, and preservation of an explicit browser override.
- The Mac build verified the finished ad-hoc signature. The browser smoke command
  uses a separate profile and Puppeteer's automation settings, including no
  first-run setup, background networking, or real keychain access.
- The Linux preparation/build/upload jobs passed under `act` on a native ARM64
  Docker engine. The local runner image used Ubuntu 24.04 and Rust 1.98.1.
  Publication was disabled. The uploaded executable and internal sidecars were
  recovered locally, and the executable's SHA-256 matched its build report.

Every build smoke run proves its network guard is active before testing OMP.
Linux denies Internet socket creation with seccomp; macOS uses `sandbox-exec`.
Fresh HOME and an empty host-tool PATH prevent use of installed Python or browsers.

### Minimal Linux host

The recovered Linux Portable executable also ran in an unmodified `ubuntu:24.04`
ARM64 container with `--network none`. No packages were installed in that
container, and the check first confirmed that Python, Chromium, and trafilatura
were absent from PATH. The executable then:

1. Extracted and launched official OMP.
2. Ran bundled Python with working SQLite and SSL modules.
3. Extracted a local HTML article through bundled trafilatura.
4. Rendered JavaScript through bundled headless Chromium.
5. Answered a DevTools protocol request through the native launcher's inherited
   pipes, the transport OMP uses for browser control.

The minimal image emits desktop DBus/GSettings diagnostics; the headless rendering
and protocol checks passed. This verifies the Ubuntu 24.04 headless runtime, not a
complete desktop session or arbitrary Linux distribution compatibility.

The Linux runtime lock includes packages already installed on a typical build
runner: it was resolved against an empty dpkg state. The builder stages shared
libraries, fonts, and copyright notices without installing the packages. glibc
libraries are excluded from the browser search path to keep the host loader and
C runtime paired.

## Release gates and remaining verification

`actionlint` passed both workflows. Throwaway publication checks verified the
binary/checksum-only public asset list and that internal reports and manifests
remain required. Missing artifacts, failed smoke reports, and mismatched
checksums reject publication. Fixture checks are not evidence of native
execution on additional platforms.

The hourly workflow is active on the default branch at minute 49. Its actual
discovery code recognized the live complete release and emitted `needed=false`,
preventing duplicate builds. A synthetic next-version check selected a new
upstream tag and updated all four asset pins; changed integration source bytes
correctly stopped preparation without modifying the lock. These checks verify
discovery behavior, not a future upstream build. A clock-triggered scheduled run
had not yet been observed when this evidence was recorded.

Full, Windows, musl, signing identities/notarization, and a full dependency license
audit remain outside the implemented scope.
Lite retains upstream's on-demand optional downloads; successful optional downloads
were not tested here.

## Restricted-host config writes

Some Linux sandboxes deny the abstract Unix socket upstream uses for config-write
locks. Normal smoke mode records this limitation only when the same packaged OMP
runtime reproduces the error without the launcher. `--strict-host` rejects it.
The native Mac and local ARM64 workflow checks used strict mode; no config-write
failure was waived.
