"""Linux x86-64 process-tree network guard, installed before exec.

Deny Internet sockets at the kernel syscall boundary. Unix sockets remain
available to native workers. No proxy variables are used as a substitute.
"""

import ctypes
import errno
import platform


class Filter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint),
    ]


class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]


def block_network():
    if platform.system() != "Linux" or platform.machine() not in (
        "x86_64",
        "aarch64",
        "arm64",
    ):
        raise RuntimeError("Network guard supports Linux x64/Arm64 only")
    # Check syscall architecture, reject x32 ABI, block io_uring (which could
    # submit socket operations), and deny socket(AF_INET/AF_INET6/AF_PACKET).
    # Socketpair can only create AF_UNIX sockets on this platform.
    arm = platform.machine() in ("aarch64", "arm64")
    audit_arch = 0xC00000B7 if arm else 0xC000003E
    socket_syscall = 198 if arm else 41
    ins = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, audit_arch),
        (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
        (0x35, 0, 1, 0x40000000),
        (0x06, 0, 0, 0x00050000 | errno.EPERM),
        (0x15, 0, 1, 425),
        (0x06, 0, 0, 0x00050000 | errno.EPERM),
        (0x15, 0, 5, socket_syscall),
        (0x20, 0, 0, 16),
        (0x15, 2, 0, 2),
        (0x15, 1, 0, 10),
        (0x15, 0, 1, 17),
        (0x06, 0, 0, 0x00050000 | errno.EPERM),
        (0x06, 0, 0, 0x7FFF0000),
    ]
    buf = (Filter * len(ins))(*(Filter(*x) for x in ins))
    prog = Program(len(ins), buf)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.byref(prog), 0, 0):
        raise OSError(ctypes.get_errno(), "cannot install mandatory offline guard")


def guarded(command):
    """Return argv and Popen options with native enforced network restrictions."""
    if platform.system() == "Darwin":
        from pathlib import Path

        sandbox = Path("/usr/bin/sandbox-exec")
        if not sandbox.exists():
            raise RuntimeError(
                "macOS sandbox-exec is unavailable; cannot certify an offline smoke test"
            )
        # Limit Internet outbound connections, leaving local Unix worker sockets
        # available. The smoke harness first checks a denied loopback connection.
        policy = '(version 1) (allow default) (deny network-outbound (remote ip "*:*"))'
        return [str(sandbox), "-p", policy, *map(str, command)], {}
    return list(map(str, command)), {"preexec_fn": block_network}


def run(command, **kwargs):
    import subprocess

    argv, options = guarded(command)
    return subprocess.run(argv, **options, **kwargs)


def popen(command, **kwargs):
    import subprocess

    argv, options = guarded(command)
    return subprocess.Popen(argv, **options, **kwargs)
