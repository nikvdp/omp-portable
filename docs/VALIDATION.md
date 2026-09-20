# Validation

The current scope is Lite and Portable for OMP 18.2.6. Portable adds private
Python, trafilatura, and a native browser. Full and model payloads are not
implemented. No paid model requests were used in these checks.

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
remain required. Missing artifacts, failed smoke reports, mismatched checksums,
and changed reviewed upstream source bytes reject publication. Fixture checks
are not evidence of native execution on additional platforms.

The new workflow requires Lite and Portable on all four platforms before publishing
`omp-v<upstream-version>-r2`. Portable x64 builds and the complete eight-build
GitHub publication path have not run yet. Portable changes remain local; no
Portable release was published.

Full, Windows, musl, signing identities/notarization, and a full audit of licenses
inside the official upstream OMP binary remain outside the implemented scope.
Lite retains upstream's on-demand optional downloads; successful optional downloads
were not tested here.

## Restricted-host config writes

Some Linux sandboxes deny the abstract Unix socket upstream uses for config-write
locks. Normal smoke mode records this limitation only when the untouched official
binary reproduces the same error. `--strict-host` rejects it. The native Mac and
local ARM64 workflow checks used strict mode; no config-write failure was waived.
