"""Runner without Docker: hardened container arguments (TRD 07 §13.2), the inspect check that refuses a tampered
container (SBX-01), and the HTTP contract (token, no expected outputs, no execution before verification).
The sandbox itself is exercised by `python -m runner --selftest` on a machine with Docker."""
import time

import httpx
import pytest

from runner import config as C
from runner.app import create_app
from runner.config import RunnerConfig
from runner.sandbox import CaseResult, Limits, create_args, validate_inspect
from runner.selftest import RunnerService

MiB = 1024 * 1024


def flag_values(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_create_args_match_the_hardened_profile():
    cfg = RunnerConfig()
    args = create_args(cfg, "c1", "i1", Limits(2000, 256))
    assert flag_values(args, "--network") == ["none"] and "--read-only" in args
    assert flag_values(args, "--cap-drop") == ["ALL"] and "--cap-add" not in args and "--privileged" not in args
    assert flag_values(args, "--security-opt") == ["no-new-privileges"]
    assert flag_values(args, "--user") == ["10001:10001"]
    assert flag_values(args, "--pids-limit") == ["64"]
    assert flag_values(args, "--memory") == ["256m"] and flag_values(args, "--memory-swap") == ["256m"]
    assert flag_values(args, "--tmpfs") == ["/tmp:rw,noexec,nosuid,nodev,size=16m"]
    assert set(flag_values(args, "--ulimit")) == {"nofile=64:64", f"fsize={8 * MiB}:{8 * MiB}", "core=0:0", "cpu=3:3"}
    assert flag_values(args, "--ipc") == ["none"] and flag_values(args, "--log-driver") == ["none"]
    assert not {"-v", "--volume", "--mount", "--device", "--gpus"} & set(args)  # nothing from the host
    assert args[-1] == cfg.image


def compliant_inspect(limits: Limits) -> dict:
    mem = limits.mem_mb * MiB
    return {"HostConfig": {
        "NetworkMode": "none", "ReadonlyRootfs": True, "CapDrop": ["ALL"], "CapAdd": None,
        "SecurityOpt": ["no-new-privileges"], "Privileged": False, "PidsLimit": C.PIDS_LIMIT, "Memory": mem,
        "MemorySwap": mem, "NanoCpus": 1_000_000_000, "Binds": None, "Tmpfs": {"/tmp": C.TMPFS.split(":", 1)[1]},
        "Devices": [], "DeviceRequests": None, "LogConfig": {"Type": "none"}, "IpcMode": "none", "PidMode": "",
        "UsernsMode": "", "UTSMode": "",
        "Ulimits": [{"Name": "nofile", "Soft": C.NOFILE, "Hard": C.NOFILE},
                    {"Name": "fsize", "Soft": C.FSIZE_BYTES, "Hard": C.FSIZE_BYTES},
                    {"Name": "core", "Soft": 0, "Hard": 0}]},
        "Config": {"User": C.SANDBOX_UID}, "Mounts": []}


@pytest.mark.parametrize(("path", "value", "problem"), [
    (("HostConfig", "NetworkMode"), "bridge", "network not none"),
    (("HostConfig", "Privileged"), True, "privileged"),
    (("HostConfig", "CapAdd"), ["SYS_ADMIN"], "cap-add present"),
    (("HostConfig", "SecurityOpt"), ["no-new-privileges", "seccomp=unconfined"], "unconfined security profile"),
    (("HostConfig", "Binds"), ["/var/run/docker.sock:/var/run/docker.sock"], "bind mounts / volumes present"),
    (("HostConfig", "DeviceRequests"), [{"Driver": "nvidia"}], "devices / gpus present"),
    (("HostConfig", "PidMode"), "host", "host namespace shared"),
    (("Config", "User"), "root", "user not 10001"),
])
def test_validate_inspect_refuses_tampered_containers(path, value, problem):
    limits = Limits(2000, 256)
    info = compliant_inspect(limits)
    assert validate_inspect(info, limits, RunnerConfig()) == []
    info[path[0]][path[1]] = value
    assert problem in validate_inspect(info, limits, RunnerConfig())


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, Limits]] = []

    async def run_case(self, code: str, stdin: bytes, limits: Limits) -> CaseResult:
        self.calls.append((code, stdin, limits))
        return CaseResult("OK", stdout=stdin.decode()[::-1], exit_code=0, time_ms=7)


@pytest.fixture
def service():
    svc = RunnerService(RunnerConfig())
    svc.executor = FakeExecutor()
    return svc


def client(app, token: str | None = C.DEFAULT_TOKEN) -> httpx.AsyncClient:
    headers = {"X-Runner-Token": token} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner", headers=headers)


async def test_execute_requires_token_and_verification(service, monkeypatch):
    app = create_app(service, run_selftest=False)
    body = {"code": "print(1)", "cases": [{"case_id": "1", "stdin": "abc"}]}
    async with client(app, token=None) as c:
        assert (await c.post("/v1/execute", json=body)).status_code == 401
    async with client(app, token="wrong") as c:
        assert (await c.get("/v1/health")).status_code == 401
    async with client(app) as c:
        r = await c.post("/v1/execute", json=body)
        assert r.status_code == 503 and r.json()["error"]["code"] == "SANDBOX_UNVERIFIED"  # never before self-test
        assert (await c.get("/v1/health")).json()["sandbox_verified"] is False
        assert service.executor.calls == []

    async def valid() -> bool:
        return True

    monkeypatch.setattr(service, "ensure_valid", valid)
    service.state.status, service.state.verified_at, service.state.fingerprint = "VERIFIED", time.time(), "fp1"
    async with client(app) as c:
        r = await c.post("/v1/execute", json={**body, "limits": {"wall_ms": 99_999, "mem_mb": 256}})
        assert r.status_code == 422  # wall time outside the allowed range
        r = await c.post("/v1/execute", json=body)
        assert r.status_code == 200
        assert r.json() == {"results": [{"case_id": "1", "verdict": "OK", "stdout": "cba", "stderr": "", "exit_code": 0,
                                         "time_ms": 7}], "sandbox_fingerprint": "fp1"}
        assert (await c.get("/v1/health")).json()["sandbox_verified"] is True


async def test_execute_never_accepts_expected_outputs_or_oversized_input(service):
    app = create_app(service, run_selftest=False)
    async with client(app) as c:
        r = await c.post("/v1/execute", json={"code": "print(1)", "cases": [{"case_id": "1", "stdin": "", "expected": "1"}]})
        assert r.status_code == 422  # C8: answers never reach the runner
        r = await c.post("/v1/execute", json={"code": "x" * (C.MAX_CODE_BYTES + 1), "cases": [{"case_id": "1"}]})
        assert r.status_code == 422
        r = await c.post("/v1/execute", json={"code": "print(1)", "cases": []})
        assert r.status_code == 422
