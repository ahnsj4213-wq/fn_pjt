"""Spike: run hostile snippets inside the hardened container and check every one is contained.

Each case = (name, code, predicate on (exit_code, stdout, stderr, elapsed, timed_out)) that must hold
for the sandbox to count as contained. Run: python escape_tests.py
"""
import subprocess
import time
import uuid

IMAGE = "aihire-sandbox-spike:latest"
WALL_TIMEOUT = 5.0
OUT_LIMIT = 64 * 1024

FLAGS = [
    "--network", "none",
    "--read-only",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--pids-limit", "64",
    "--memory", "256m", "--memory-swap", "256m",
    "--cpus", "1",
    "--user", "10001:10001",
    "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
    "--ulimit", "nofile=64:64",
    "--ulimit", "fsize=8388608:8388608",
    "--ipc", "none",
    "--env", "PYTHONDONTWRITEBYTECODE=1",
]


def run(code: str):
    name = f"sbx-{uuid.uuid4().hex[:12]}"
    cmd = ["docker", "run", "--rm", "-i", "--name", name, *FLAGS, IMAGE]
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timed_out = False
    try:
        out, err = p.communicate(code.encode(), timeout=WALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        subprocess.run(["docker", "kill", name], capture_output=True)
        out, err = p.communicate()
    return p.returncode, out[:OUT_LIMIT].decode(errors="replace"), err[-2000:].decode(errors="replace"), time.perf_counter() - t0, timed_out


CASES = [
    ("hello world runs", "print('ok')", lambda rc, o, e, t, to: rc == 0 and o.strip() == "ok"),
    ("network blocked (tcp to public IP)",
     "import socket\ntry:\n socket.create_connection(('1.1.1.1',53),timeout=2); print('LEAK')\nexcept OSError as x: print('blocked', x)",
     lambda rc, o, e, t, to: "LEAK" not in o and "blocked" in o),
    ("network blocked (host gateway)",
     "import socket\ntry:\n socket.create_connection(('host.docker.internal',11434),timeout=2); print('LEAK')\nexcept OSError as x: print('blocked', x)",
     lambda rc, o, e, t, to: "LEAK" not in o),
    ("root fs read-only",
     "try:\n open('/usr/lib/x','w'); print('LEAK')\nexcept OSError as x: print('ro', x)",
     lambda rc, o, e, t, to: "LEAK" not in o),
    ("tmp is noexec",
     "import os,stat\nopen('/tmp/a.sh','w').write('#!/bin/sh\\necho LEAK')\nos.chmod('/tmp/a.sh',0o755)\nimport subprocess\ntry:\n print(subprocess.run(['/tmp/a.sh'],capture_output=True,text=True).stdout)\nexcept OSError as x: print('noexec', x)",
     lambda rc, o, e, t, to: "LEAK" not in o),
    ("non-root, no capabilities",
     "import os\nprint(os.getuid())\nprint([l for l in open('/proc/self/status') if l.startswith('CapEff')][0].split()[1])",
     lambda rc, o, e, t, to: o.split()[:2] == ["10001", "0000000000000000"]),
    ("no host secrets in env",
     "import os\nprint(sorted(k for k in os.environ))",
     lambda rc, o, e, t, to: all(s not in o for s in ("SECRET", "TOKEN", "PASSWORD", "DATABASE_URL"))),
    ("fork bomb contained by pids limit",
     "import os\nn=0\ntry:\n while True:\n  if os.fork()==0:\n   import time; time.sleep(30)\n  n+=1\nexcept OSError as x:\n print('pids-limited after', n)",
     lambda rc, o, e, t, to: "pids-limited" in o or to),
    ("memory bomb killed",
     "b=[]\nwhile True: b.append(bytearray(32*1024*1024))",
     lambda rc, o, e, t, to: rc != 0),
    ("infinite loop killed by wall timeout", "while True: pass", lambda rc, o, e, t, to: to and t < WALL_TIMEOUT + 3),
    ("tmpfs size capped",
     "try:\n f=open('/tmp/big','wb')\n [f.write(b'0'*1048576) for _ in range(64)]\n f.flush(); print('LEAK')\nexcept OSError as x: print('capped', x)",
     lambda rc, o, e, t, to: "LEAK" not in o),
    ("output flood truncated by runner", "print('A'*10_000_000)", lambda rc, o, e, t, to: len(o) <= OUT_LIMIT),
]


def main() -> None:
    failures = 0
    for name, code, ok in CASES:
        rc, o, e, t, to = run(code)
        good = ok(rc, o, e, t, to)
        failures += not good
        print(f"[{'PASS' if good else 'FAIL'}] {name}: rc={rc} timed_out={to} {t:.2f}s out={o.strip()[:90]!r}")
    print(f"{len(CASES) - failures}/{len(CASES)} contained")


if __name__ == "__main__":
    main()
