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
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Network guard supports Linux x86-64 only")
    # Check syscall architecture, reject x32 ABI, block io_uring (which could
    # submit socket operations), and deny socket(AF_INET/AF_INET6/AF_PACKET).
    # Socketpair can only create AF_UNIX sockets on this platform.
    ins = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, 0xC000003E),
        (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
        (0x35, 0, 1, 0x40000000),
        (0x06, 0, 0, 0x00050000 | errno.EPERM),
        (0x15, 0, 1, 425),
        (0x06, 0, 0, 0x00050000 | errno.EPERM),
        (0x15, 0, 5, 41),
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
