# Local validation

Tested in this Work Mode session on Linux x86-64 with the official OMP v18.2.6
binary and Rust 1.98.1. This is a tested Linux Lite milestone, not public-v1
acceptance for the full proposed product.

## Passed

- Three Rust format tests: footer roundtrip, overflow/bounds and path rejection.
- Seventeen integration tests: first extraction/reuse, arguments/working
  directory/state environment, 16 concurrent launches, outer and inner updater
  guards, fake host OMP coexistence, profile caches, literal profile arguments,
  standard input, exact exit status, signal handoff, terminal visibility,
  payload corruption even with valid cache, extracted-file corruption,
  truncated footer, size budget, stale temporary cleanup, actual SIGKILL during
  extraction with successful recovery, invalid paths/symlinks and deterministic
  packing. Several tests cover multiple related assertions.
- Clippy with warnings denied and Rust formatting.
- Finished artifact relocated outside staging, fresh HOME, empty dependency PATH,
  and a kernel seccomp network guard inherited by child processes.
- Actual OMP version/help, default memory backend, configuration location and
  sentinel configuration reads, native grep and read tools, named profiles,
  legacy profile variable, empty canonical profile overriding the legacy value,
  session/plugin sentinel preservation, update interception, private shim launch,
  embedded upstream executable digest equality, and upstream confirmation that
  both optional speech models are absent.
- Actual agent initialization through its newline-delimited JSON control
  interface and a successful bash `printf` command. A dummy model credential
  allows initialization only; no provider call is sent, and Internet sockets
  are denied by the kernel.

`build/smoke-results.json` contains the actual command outputs and control
responses. The exported source archive includes a copy under `validation/`.

## Confirmed environment limit

`omp config set memory.backend off` fails here with:

> Failed to acquire native file lock ... Operation not permitted (os error 1)

This reproduces with the byte-identical upstream binary, without the portable
launcher and without the additional offline guard. Exact-tag upstream
`crates/pi-natives/src/file_lock/linux.rs` implements locks by binding abstract
Unix datagram sockets. A direct Python abstract socket bind also receives
`PermissionError` in this sandbox. Ordinary filesystem `flock` works, so the
SFX's own extraction lock is unaffected.

Configuration reads and agent initialization work. Configuration mutation and
other upstream operations relying on that socket lock cannot be certified here.
The smoke script records the matching wrapped/unwrapped failure explicitly.
`--strict-host` rejects it; the GitHub verification workflow uses strict mode.
No workaround or upstream modification is shipped.

## Not yet certified

- Clean minimal-container compatibility, an older glibc baseline or other Linux
  distributions. This host has standard system libraries; a reduced PATH is
  not a full container image. The launcher dynamically uses libc/libgcc;
  upstream also uses ordinary libc/libm/libdl/libpthread.
- Successful provider-backed read/edit/write conversation, interactive terminal
  UI exercise, authentication/login or paid model traffic.
- Real plugin execution/session resume; sentinel tests check coexistence and
  preservation only.
- Successful on-demand installation of an optional dependency/model. No wrapper
  flag disables installation, but a successful download was not tested.
- Portable/Full components, other operating systems/architectures, complete
  upstream embedded-license inventory, signing, scheduled builds or publication.

The test harness fails on unexpected failures. The known lock limitation is
recognized only when wrapped and unwrapped upstream produce matching errors;
it is not a blanket suppression of configuration errors.
