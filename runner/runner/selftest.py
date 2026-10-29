"""Sandbox self-test SBX-01~14 (TRD 07 13.4) and the verification state / fingerprint (13.5).

Every probe goes through SandboxExecutor.run_case — the same path as candidate code. The sandbox counts as
verified only when all checks pass and the fingerprint (runner version, image ID, engine version, runtime,
limits hash) is unchanged from before to after the run.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from runner import __version__
from runner import config as C
from runner.config import RunnerConfig
from runner.sandbox import Limits, SandboxExecutor, create_args, run_cmd, validate_inspect

FAST = Limits(wall_ms=10_000, mem_mb=256)
SHORT = Limits(wall_ms=2_000, mem_mb=256)

# ── probe programs (run as candidate code) ─────────────────────────────────────────────────────────────
P_IDENTITY = """import os
st = {l.split(':')[0]: l.split(':', 1)[1].strip() for l in open('/proc/self/status')}
print(os.getuid(), os.getgid(), st['CapEff'], st.get('NoNewPrivs'), st.get('Seccomp'))
"""
P_NETWORK = """import socket
ifs = sorted(l.split(':')[0].strip() for l in open('/proc/net/dev').readlines()[2:])
leaks = []
for host, port in [('1.1.1.1', 53), ('8.8.8.8', 443), ('172.17.0.1', 2375), ('192.168.65.254', 11434),
                   ('10.0.0.1', 5432), ('192.168.0.1', 80)]:
    try:
        socket.create_connection((host, port), timeout=1).close(); leaks.append('OPEN:' + host)
    except OSError:
        pass
for name in ('host.docker.internal', 'gateway.docker.internal', 'example.com'):
    try:
        socket.getaddrinfo(name, 80); leaks.append('DNS:' + name)
    except OSError:
        pass
print('IF', ','.join(ifs)); print('LEAKS', ';'.join(leaks) or 'none')
"""
P_FILESYSTEM = """import os, subprocess, errno
leaks = []
for p in ('/x', '/usr/x', '/etc/x', '/opt/sandbox/launcher.py'):
    try:
        open(p, 'a'); leaks.append('W:' + p)
    except OSError:
        pass
open('/tmp/a.sh', 'w').write('#!/bin/sh\\necho EXEC\\n'); os.chmod('/tmp/a.sh', 0o755)
try:
    leaks.append('X:' + subprocess.run(['/tmp/a.sh'], capture_output=True, text=True).stdout.strip())
except OSError:
    pass
mb, err = 0, 'none'
try:
    for i in range(4):
        with open('/tmp/f%d' % i, 'wb') as f:
            for _ in range(6):
                f.write(b'0' * 1048576); f.flush(); mb += 1
except OSError as e:
    err = errno.errorcode.get(e.errno, str(e.errno))
print('LEAKS', ';'.join(leaks) or 'none'); print('TMPFS', mb, err)
"""
P_FORK = """import os
n = 0
try:
    while True:
        if os.fork() == 0:
            import time; time.sleep(30); os._exit(0)
        n += 1
except OSError:
    print('pids-limited after', n)
"""
P_MEMORY = "b = []\nwhile True:\n    b.append(bytearray(32 * 1024 * 1024))\n"
P_LOOP = "while True:\n    pass\n"
P_SLEEP = "import time\ntime.sleep(100)\n"
P_FLOOD = "import sys\nwhile True:\n    sys.stdout.write('A' * 65536)\n"
P_HOSTINFO = """import os, json
mi = open('/proc/self/mountinfo').read()
bad = [m for m in ('/mnt/host', '/run/desktop/mnt', 'docker.sock', 'big21', 'final_test') if m in mi]
sock = any(os.path.exists(p) for p in ('/var/run/docker.sock', '/run/docker.sock'))
print(json.dumps({'env': sorted(os.environ), 'bad': bad, 'sock': sock, 'dev': sorted(os.listdir('/dev'))}))
"""
P_PRIV = """import os, ctypes, errno, platform
libc = ctypes.CDLL(None, use_errno=True)
res = {}
def code(e): return errno.errorcode.get(e, str(e))
# libc unshare(2) directly: os.unshare exists only from Python 3.12 (the sandbox runs 3.11).
for name, flag in (('unshare_user', 0x10000000), ('unshare_mnt', 0x00020000)):
    res[name] = 'ALLOWED' if libc.unshare(flag) == 0 else code(ctypes.get_errno())
os.makedirs('/tmp/m', exist_ok=True)
r = libc.mount(b'none', b'/tmp/m', b'tmpfs', 0, None)
res['mount'] = 'ALLOWED' if r == 0 else code(ctypes.get_errno())
try:
    os.setuid(0); res['setuid0'] = 'ALLOWED'
except OSError as e:
    res['setuid0'] = code(e.errno)
if platform.machine() == 'x86_64':
    r = libc.syscall(250, 0, -3, 0)  # keyctl(KEYCTL_GET_KEYRING_ID, KEY_SPEC_SESSION_KEYRING)
    res['keyctl'] = 'ALLOWED' if r >= 0 else code(ctypes.get_errno())
print(res)
"""
P_HIDDEN = """import os, sys
data = sys.stdin.read().strip()
if data.startswith('KEEP:'):
    print('stored'); raise SystemExit
needle = ('SBX11' + '-CANARY-' + data).encode()
hits = []
for blob_name, blob in (('environ', '\\n'.join(os.environ.values()).encode()), ('argv', ' '.join(sys.argv).encode())):
    if needle in blob: hits.append(blob_name)
for p in ('/proc/self/cmdline', '/proc/1/cmdline', '/proc/self/environ'):
    try:
        if needle in open(p, 'rb').read(): hits.append(p)
    except OSError:
        pass
for root, dirs, files in os.walk('/'):
    dirs[:] = [d for d in dirs if not (root == '/' and d in ('proc', 'sys', 'dev'))]
    for f in files:
        p = os.path.join(root, f)
        try:
            if os.path.isfile(p) and os.path.getsize(p) < 2_000_000 and needle in open(p, 'rb').read():
                hits.append(p)
        except OSError:
            pass
print('FOUND' if hits else 'NOTFOUND', hits[:5])
"""
P_SLEEP1 = "import time\ntime.sleep(1)\nprint('done')\n"
P_HELLO = "print('ok')\n"
P_ECHO = "import sys\nsys.stdout.write(sys.stdin.read())\n"
P_RAISE = "1 / 0\n"

ALLOWED_EXTRA_ENV = {"HOSTNAME", "HOME", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING"}
SECRET_WORDS = ("SECRET", "TOKEN", "PASSWORD", "DATABASE", "AIHIRE", "RUNNER")
FORBIDDEN_DEV = ("sda", "nvme", "nvidia", "kvm", "dri", "mem", "kmsg", "fuse")


@dataclass
class Check:
    id: str
    ok: bool
    detail: str


@dataclass
class VerificationState:
    status: str = "UNVERIFIED"          # UNVERIFIED | VERIFYING | VERIFIED | FAILED
    fingerprint: str | None = None
    fingerprint_parts: dict = field(default_factory=dict)
    verified_at: float | None = None    # epoch seconds
    runtime: str | None = None
    checks: list[Check] = field(default_factory=list)
    reason: str = ""

    @property
    def verified(self) -> bool:
        return self.status == "VERIFIED"

    def public(self) -> dict:
        return {
            "sandbox_verified": self.verified,
            "status": self.status,
            "fingerprint": self.fingerprint,
            "verified_at": datetime.fromtimestamp(self.verified_at, UTC).isoformat() if self.verified_at else None,
            "runtime": self.runtime,
            "failed_checks": [c.id for c in self.checks if not c.ok],
            "reason": self.reason,
        }


def limits_hash(cfg: RunnerConfig) -> str:
    probe = create_args(cfg, "NAME", "INSTANCE", Limits())
    return hashlib.sha256(json.dumps(probe).encode()).hexdigest()[:16]


async def fingerprint(cfg: RunnerConfig) -> tuple[str, dict]:
    rc1, image_id, _ = await run_cmd(cfg.docker, "image", "inspect", "--format", "{{.Id}}", cfg.image)
    rc2, engine, _ = await run_cmd(cfg.docker, "version", "--format", "{{.Server.Version}}")
    runtime = cfg.runtime
    if not runtime:
        _, runtime, _ = await run_cmd(cfg.docker, "info", "--format", "{{.DefaultRuntime}}")
    parts = {
        "runner_version": __version__,
        "image_id": image_id.strip() if rc1 == 0 else "MISSING",
        "engine_version": engine.strip() if rc2 == 0 else "UNKNOWN",
        "runtime": runtime.strip() or "UNKNOWN",
        "limits_hash": limits_hash(cfg),
    }
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
    return "sha256:" + digest, parts


async def image_env_keys(cfg: RunnerConfig) -> set[str]:
    rc, out, _ = await run_cmd(cfg.docker, "image", "inspect", "--format", "{{json .Config.Env}}", cfg.image)
    return {e.split("=", 1)[0] for e in json.loads(out)} if rc == 0 else set()


async def run_selftest(ex: SandboxExecutor) -> list[Check]:
    cfg = ex.cfg
    checks: list[Check] = []

    async def check(cid: str, fn: Callable[[], Awaitable[tuple[bool, str]]]) -> None:
        try:
            ok, detail = await fn()
        except Exception as e:  # noqa: BLE001 - a crashing probe is a failed check
            ok, detail = False, f"{type(e).__name__}: {e}"
        checks.append(Check(cid, ok, detail[:300]))

    async def sbx01():
        r = await ex.run_case(P_HELLO, b"", FAST)
        info = ex.last_inspect or {}
        tampered = json.loads(json.dumps(info))
        tampered.setdefault("HostConfig", {})["NetworkMode"] = "bridge"
        tampered["HostConfig"]["Binds"] = ["/var/run/docker.sock:/var/run/docker.sock"]
        rejects = len(validate_inspect(tampered, FAST, cfg)) >= 2
        return (r.verdict == "OK" and not r.config_problems and rejects,
                f"verdict={r.verdict} problems={r.config_problems} validator_rejects_tampered={rejects}")

    async def sbx02():
        r = await ex.run_case(P_IDENTITY, b"", FAST)
        return r.stdout.split() == ["10001", "10001", "0000000000000000", "1", "2"], r.stdout.strip()

    async def sbx03():
        r = await ex.run_case(P_NETWORK, b"", FAST)
        return ("IF lo\n" in r.stdout and "LEAKS none" in r.stdout), r.stdout.strip().replace("\n", " | ")

    async def sbx04():
        r = await ex.run_case(P_FILESYSTEM, b"", FAST)
        lines = dict(l.split(" ", 1) for l in r.stdout.strip().splitlines() if " " in l)
        mb, err = (lines.get("TMPFS", "0 none").split() + ["none"])[:2]
        ok = lines.get("LEAKS") == "none" and err == "ENOSPC" and int(mb) <= 16
        return ok, r.stdout.strip().replace("\n", " | ")

    async def sbx05():
        r = await ex.run_case(P_FORK, b"", Limits(wall_ms=8_000))
        after = await ex.run_case(P_HELLO, b"", FAST)
        ok = "pids-limited" in r.stdout and r.verdict in ("OK", "TIMEOUT") and after.verdict == "OK"
        return ok, f"{r.stdout.strip()} verdict={r.verdict} {r.time_ms}ms; next run={after.verdict}"

    async def sbx06():
        r = await ex.run_case(P_MEMORY, b"", Limits(wall_ms=10_000, mem_mb=256))
        return r.verdict == "MEMORY_LIMIT", f"verdict={r.verdict} exit={r.exit_code} {r.time_ms}ms"

    async def sbx07():
        a = await ex.run_case(P_LOOP, b"", SHORT)
        b = await ex.run_case(P_SLEEP, b"", SHORT)
        ok = a.verdict == b.verdict == "TIMEOUT" and a.time_ms < 6_000 and b.time_ms < 6_000
        return ok, f"loop={a.verdict}/{a.time_ms}ms sleep={b.verdict}/{b.time_ms}ms"

    async def sbx08():
        ex.max_buffered = 0
        r = await ex.run_case(P_FLOOD, b"", FAST)
        ok = (r.verdict == "OUTPUT_LIMIT" and len(r.stdout.encode()) <= C.STDOUT_CAP
              and ex.max_buffered <= C.STDOUT_CAP and r.time_ms < 6_000)
        return ok, f"verdict={r.verdict} kept={len(r.stdout)}B max_buffered={ex.max_buffered}B {r.time_ms}ms"

    async def sbx09():
        r = await ex.run_case(P_HOSTINFO, b"", FAST)
        d = json.loads(r.stdout)
        allowed = await image_env_keys(cfg) | ALLOWED_EXTRA_ENV
        extra = [k for k in d["env"] if k not in allowed]
        secretish = [k for k in d["env"] if k not in allowed and any(w in k.upper() for w in SECRET_WORDS)]
        bad_dev = [x for x in d["dev"] if x.startswith(FORBIDDEN_DEV)]
        ok = not extra and not secretish and not d["bad"] and not d["sock"] and not bad_dev
        return ok, f"extra_env={extra} host_paths={d['bad']} sock={d['sock']} bad_dev={bad_dev}"

    async def sbx10():
        r = await ex.run_case(P_PRIV, b"", FAST)
        return ("ALLOWED" not in r.stdout and r.verdict == "OK"), r.stdout.strip()

    async def sbx11():
        rnd = secrets.token_hex(8)
        keep, probe = await asyncio.gather(
            ex.run_case(P_HIDDEN, f"KEEP:SBX11-CANARY-{rnd}".encode(), FAST),
            ex.run_case(P_HIDDEN, rnd.encode(), Limits(wall_ms=15_000)))
        ok = keep.verdict == "OK" and probe.verdict == "OK" and probe.stdout.startswith("NOTFOUND")
        return ok, f"other case: {probe.stdout.strip()[:120]} ({probe.verdict})"

    async def sbx13():
        ex.max_active = 0
        rs = await asyncio.gather(*(ex.run_case(P_SLEEP1, b"", FAST) for _ in range(cfg.concurrency + 2)))
        ok = ex.max_active <= cfg.concurrency and all(r.verdict == "OK" for r in rs)
        return ok, f"max_active={ex.max_active} limit={cfg.concurrency} verdicts={[r.verdict for r in rs]}"

    async def sbx14():
        a = await ex.run_case(P_HELLO, b"", FAST)
        b = await ex.run_case(P_ECHO, "hello\n세계\n".encode(), FAST)
        c = await ex.run_case(P_RAISE, b"", FAST)
        ok = (a.verdict == "OK" and a.stdout == "ok\n" and b.verdict == "OK" and b.stdout == "hello\n세계\n"
              and c.verdict == "RUNTIME_ERROR" and c.exit_code == 1 and "ZeroDivisionError" in c.stderr)
        return ok, f"hello={a.verdict} echo={b.verdict} raise={c.verdict}/{c.exit_code}"

    async def sbx12():
        left = await ex.leftover_containers()
        return not left, f"leftover containers={left}"

    for cid, fn in (("SBX-01", sbx01), ("SBX-02", sbx02), ("SBX-03", sbx03), ("SBX-04", sbx04), ("SBX-05", sbx05),
                    ("SBX-06", sbx06), ("SBX-07", sbx07), ("SBX-08", sbx08), ("SBX-09", sbx09), ("SBX-10", sbx10),
                    ("SBX-11", sbx11), ("SBX-13", sbx13), ("SBX-14", sbx14), ("SBX-12", sbx12)):
        await check(cid, fn)
    return sorted(checks, key=lambda c: c.id)


class RunnerService:
    """Holds the executor and the verification state; decides whether execution may be exposed."""

    def __init__(self, cfg: RunnerConfig | None = None) -> None:
        self.cfg = cfg or RunnerConfig()
        self.state = VerificationState()
        self.executor = SandboxExecutor(self.cfg, on_config_violation=self._violation)
        self._lock = asyncio.Lock()
        self._fp_checked_at = 0.0

    def _violation(self, problems: list[str]) -> None:
        self.state.status, self.state.reason = "FAILED", "config violation: " + "; ".join(problems)

    async def verify(self) -> VerificationState:
        async with self._lock:
            self.state.status, self.state.reason = "VERIFYING", ""
            fp_before, parts = await fingerprint(self.cfg)
            checks = await run_selftest(self.executor)
            fp_after, _ = await fingerprint(self.cfg)
            st = VerificationState(fingerprint=fp_after, fingerprint_parts=parts, runtime=parts["runtime"],
                                   checks=checks)
            failed = [c.id for c in checks if not c.ok]
            if failed:
                st.status, st.reason = "FAILED", "failed: " + ",".join(failed)
            elif fp_before != fp_after:
                st.status, st.reason = "FAILED", "fingerprint changed during self-test"
            elif self.cfg.env == "prod" and parts["runtime"] not in self.cfg.approved_runtimes:
                st.status, st.reason = "FAILED", f"runtime {parts['runtime']} not approved for prod"
            else:
                st.status, st.verified_at = "VERIFIED", time.time()
            self.state = st
            self._fp_checked_at = time.monotonic()
            return st

    async def ensure_valid(self) -> bool:
        """Verified, not older than 24 h, and fingerprint unchanged (re-checked at most every 60 s)."""
        st = self.state
        if not st.verified:
            return False
        if st.verified_at is None or time.time() - st.verified_at > C.VERIFICATION_TTL_SEC:
            st.status, st.reason = "UNVERIFIED", "verification expired"
            return False
        if time.monotonic() - self._fp_checked_at > C.FINGERPRINT_CACHE_SEC:
            fp, _ = await fingerprint(self.cfg)
            self._fp_checked_at = time.monotonic()
            if fp != st.fingerprint:
                st.status, st.reason = "UNVERIFIED", "fingerprint changed"
                return False
        return True
