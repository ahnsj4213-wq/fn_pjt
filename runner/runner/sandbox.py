"""Run one test case in one fresh, hardened container via the Docker CLI (no SDK dependency).

create -> inspect (SBX-01: refuse to start if the config is not exactly the hardened one) -> start -a -i with a
framed stdin -> read stdout/stderr up to fixed caps from the stream -> kill on wall time or output cap ->
inspect exit state -> rm -f. Expected outputs never reach this module (C8).
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from runner import config as C
from runner.config import RunnerConfig

MiB = 1024 * 1024

VERDICTS = ("OK", "RUNTIME_ERROR", "TIMEOUT", "MEMORY_LIMIT", "OUTPUT_LIMIT", "SANDBOX_ERROR")


@dataclass
class Limits:
    wall_ms: int = C.DEFAULT_WALL_MS
    mem_mb: int = C.DEFAULT_MEM_MB

    def clamped(self) -> Limits:
        lo, hi = C.WALL_MS_RANGE
        mlo, mhi = C.MEM_MB_RANGE
        return Limits(min(max(self.wall_ms, lo), hi), min(max(self.mem_mb, mlo), mhi))


@dataclass
class CaseResult:
    verdict: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    time_ms: int = 0
    # internal diagnostics (not returned to callers)
    config_problems: list[str] = field(default_factory=list)
    detail: str = ""


async def run_cmd(*args: str, input_bytes: bytes | None = None, timeout: float = 30.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(input_bytes), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "timeout"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def create_args(cfg: RunnerConfig, name: str, instance: str, limits: Limits) -> list[str]:
    cpu_sec = math.ceil(limits.wall_ms / 1000) + 1
    args = [
        "create", "-i", "--name", name,
        "--label", C.SANDBOX_LABEL, "--label", f"aihire.runner={instance}",
        "--network", "none",
        "--read-only",
        "--tmpfs", C.TMPFS,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", C.SANDBOX_UID,
        "--pids-limit", str(C.PIDS_LIMIT),
        "--memory", f"{limits.mem_mb}m", "--memory-swap", f"{limits.mem_mb}m",
        "--cpus", C.CPUS,
        "--ulimit", f"nofile={C.NOFILE}:{C.NOFILE}",
        "--ulimit", f"fsize={C.FSIZE_BYTES}:{C.FSIZE_BYTES}",
        "--ulimit", "core=0:0",
        "--ulimit", f"cpu={cpu_sec}:{cpu_sec}",
        "--ipc", "none",
        "--log-driver", "none",
        "--hostname", "sandbox",
        "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONIOENCODING=utf-8",
    ]
    if cfg.runtime:
        args += ["--runtime", cfg.runtime]
    if cfg.cpuset:
        args += ["--cpuset-cpus", cfg.cpuset]
    return args + [cfg.image]


def validate_inspect(info: dict[str, Any], limits: Limits, cfg: RunnerConfig) -> list[str]:
    """SBX-01: the created container must match the hardened config exactly. Returns problems (empty = OK)."""
    p: list[str] = []
    hc = info.get("HostConfig") or {}
    mem = limits.mem_mb * MiB

    def need(cond: bool, msg: str) -> None:
        if not cond:
            p.append(msg)

    need(hc.get("NetworkMode") == "none", "network not none")
    need(hc.get("ReadonlyRootfs") is True, "rootfs not read-only")
    need("ALL" in [c.upper() for c in (hc.get("CapDrop") or [])], "cap-drop ALL missing")
    need(not hc.get("CapAdd"), "cap-add present")
    sec = hc.get("SecurityOpt") or []
    need(any(s.startswith("no-new-privileges") for s in sec), "no-new-privileges missing")
    need(not any("unconfined" in s for s in sec), "unconfined security profile")
    need(hc.get("Privileged") is False, "privileged")
    need(hc.get("PidsLimit") == C.PIDS_LIMIT, "pids limit")
    need(hc.get("Memory") == mem and hc.get("MemorySwap") == mem, "memory/swap limit")
    need(hc.get("NanoCpus") == 1_000_000_000, "cpus")
    need(not hc.get("Binds") and not info.get("Mounts"), "bind mounts / volumes present")
    need((hc.get("Tmpfs") or {}).get("/tmp") == C.TMPFS.split(":", 1)[1], "tmpfs /tmp options")
    need(not hc.get("Devices") and not hc.get("DeviceRequests"), "devices / gpus present")
    need((hc.get("LogConfig") or {}).get("Type") == "none", "log driver not none")
    need(hc.get("IpcMode") == "none", "ipc not none")
    need(hc.get("PidMode", "") == "" and hc.get("UsernsMode", "") != "host" and hc.get("UTSMode", "") == "",
         "host namespace shared")
    need((info.get("Config") or {}).get("User") == C.SANDBOX_UID, "user not 10001")
    ul = {u.get("Name"): (u.get("Soft"), u.get("Hard")) for u in (hc.get("Ulimits") or [])}
    need(ul.get("nofile") == (C.NOFILE, C.NOFILE), "nofile ulimit")
    need(ul.get("fsize") == (C.FSIZE_BYTES, C.FSIZE_BYTES), "fsize ulimit")
    need(ul.get("core") == (0, 0), "core ulimit")
    if cfg.runtime:
        need(hc.get("Runtime") == cfg.runtime, f"runtime not {cfg.runtime}")
    return p


class SandboxExecutor:
    def __init__(self, cfg: RunnerConfig, on_config_violation: Callable[[list[str]], None] | None = None) -> None:
        self.cfg = cfg
        self.instance = uuid.uuid4().hex[:12]
        self._sem = asyncio.Semaphore(cfg.concurrency)
        self._on_violation = on_config_violation
        self.active = 0
        self.max_active = 0
        self.max_buffered = 0          # largest output buffer ever held (bounded by the caps)
        self.last_inspect: dict[str, Any] | None = None

    async def _docker(self, *args: str, timeout: float = 30.0) -> tuple[int, str, str]:
        return await run_cmd(self.cfg.docker, *args, timeout=timeout)

    async def run_case(self, code: str, stdin: bytes, limits: Limits) -> CaseResult:
        limits = limits.clamped()
        async with self._sem:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                return await self._run(code, stdin, limits)
            finally:
                self.active -= 1

    async def _run(self, code: str, stdin: bytes, limits: Limits) -> CaseResult:
        import json

        name = f"sbx-{uuid.uuid4().hex[:16]}"
        rc, out, err = await self._docker(*create_args(self.cfg, name, self.instance, limits))
        if rc != 0:
            return CaseResult("SANDBOX_ERROR", detail="create failed: " + err.strip()[:300])
        cid = out.strip()
        try:
            rc, out, err = await self._docker("inspect", cid)
            if rc != 0:
                return CaseResult("SANDBOX_ERROR", detail="inspect failed")
            info = json.loads(out)[0]
            self.last_inspect = info
            problems = validate_inspect(info, limits, self.cfg)
            if problems:
                if self._on_violation:
                    self._on_violation(problems)
                return CaseResult("SANDBOX_ERROR", config_problems=problems, detail="config violation")
            return await self._start_and_collect(cid, code.encode(), stdin, limits)
        finally:
            await self._docker("rm", "-f", cid, timeout=30)

    async def _start_and_collect(self, cid: str, code: bytes, stdin: bytes, limits: Limits) -> CaseResult:
        import json

        payload = str(len(code)).encode() + b"\n" + code + stdin
        t0 = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            self.cfg.docker, "start", "-a", "-i", cid,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        exceeded = asyncio.Event()
        out_buf, err_buf = bytearray(), bytearray()

        async def feed() -> None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(payload)
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # program exited before reading all input

        async def pump(stream: asyncio.StreamReader | None, buf: bytearray, cap: int) -> None:
            assert stream is not None
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return
                room = cap - len(buf)
                if room > 0:
                    buf += chunk[:room]
                    self.max_buffered = max(self.max_buffered, len(buf))
                if len(chunk) > room:
                    exceeded.set()  # keep draining (discarding) so the CLI can exit after the kill

        feeder = asyncio.create_task(feed())
        pumps = asyncio.gather(pump(proc.stdout, out_buf, C.STDOUT_CAP), pump(proc.stderr, err_buf, C.STDERR_CAP))
        waiter = asyncio.create_task(proc.wait())
        overflow = asyncio.create_task(exceeded.wait())
        timed_out = False
        done, _ = await asyncio.wait({waiter, overflow}, timeout=limits.wall_ms / 1000,
                                     return_when=asyncio.FIRST_COMPLETED)
        if not done:
            timed_out = True
        if not waiter.done():
            await self._docker("kill", cid, timeout=15)
        try:
            await asyncio.wait_for(waiter, timeout=15)
            await asyncio.wait_for(pumps, timeout=15)
        except TimeoutError:
            proc.kill()
        finally:
            overflow.cancel()
            feeder.cancel()
        elapsed = int((time.perf_counter() - t0) * 1000)

        rc, out, _ = await self._docker("inspect", "--format", "{{json .State}}", cid)
        state = json.loads(out) if rc == 0 and out.strip() else {}
        exit_code = state.get("ExitCode")
        res = CaseResult("RUNTIME_ERROR", out_buf.decode(errors="replace"), err_buf.decode(errors="replace"),
                         exit_code, elapsed)
        if exceeded.is_set():
            res.verdict = "OUTPUT_LIMIT"
        elif timed_out:
            res.verdict = "TIMEOUT"
        elif state.get("OOMKilled"):
            res.verdict = "MEMORY_LIMIT"
        elif not state or state.get("Error") or not state.get("StartedAt", "").strip("0-:TZ."):
            res.verdict, res.detail = "SANDBOX_ERROR", "container did not run: " + str(state.get("Error", ""))[:200]
        elif exit_code == 0:
            res.verdict = "OK"
        return res

    async def leftover_containers(self) -> list[str]:
        rc, out, _ = await self._docker("ps", "-a", "-q", "--filter", f"label=aihire.runner={self.instance}")
        return out.split() if rc == 0 else ["<docker ps failed>"]
