"""Sandbox entry point (read-only, owned by root inside the image).

Frame on stdin:  b"<code length in bytes>\n" + <code bytes> + <program stdin bytes>
The launcher reads exactly the header and the code with unbuffered os.read, writes /tmp/main.py and execs
`python -I -B /tmp/main.py`. Whatever is left on fd 0 becomes the program's stdin, untouched.
No bind mounts are needed, so no host path is exposed (TRD 07 13.2).
"""
import os
import sys

MAX_CODE_BYTES = 64 * 1024
BAD_FRAME = 70  # EX_SOFTWARE


def _fail(msg: str) -> None:
    os.write(2, ("launcher: " + msg + "\n").encode())
    os._exit(BAD_FRAME)


def _read_exact(n: int) -> bytes:
    parts, left = [], n
    while left > 0:
        chunk = os.read(0, min(left, 65536))
        if not chunk:
            _fail("truncated code frame")
        parts.append(chunk)
        left -= len(chunk)
    return b"".join(parts)


def main() -> None:
    header = b""
    while True:
        c = os.read(0, 1)
        if not c:
            _fail("missing header")
        if c == b"\n":
            break
        header += c
        if len(header) > 8:
            _fail("header too long")
    if not header.isdigit():
        _fail("bad header")
    n = int(header)
    if n > MAX_CODE_BYTES:
        _fail("code too large")
    code = _read_exact(n)
    fd = os.open("/tmp/main.py", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    view = memoryview(code)
    while view:
        view = view[os.write(fd, view):]
    os.close(fd)
    os.execv(sys.executable, [sys.executable, "-I", "-B", "/tmp/main.py"])


if __name__ == "__main__":
    main()
