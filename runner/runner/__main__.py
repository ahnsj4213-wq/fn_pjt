"""`python -m runner` (run from the runner/ directory).

  python -m runner                 serve on RUNNER_HOST:RUNNER_PORT (default 127.0.0.1:8400)
  python -m runner --build-image   build the sandbox image from runner/sandbox
  python -m runner --selftest      run SBX-01~14 once and print the result (exit 1 on failure)
"""
import argparse
import asyncio
import json
import pathlib
import subprocess
import sys

from runner.config import RunnerConfig


def build_image(cfg: RunnerConfig) -> int:
    ctx = pathlib.Path(__file__).resolve().parent.parent / "sandbox"
    return subprocess.call([cfg.docker, "build", "-t", cfg.image, str(ctx)])


async def selftest_once(cfg: RunnerConfig) -> int:
    from runner.selftest import RunnerService

    st = await RunnerService(cfg).verify()
    print(json.dumps(st.public(), ensure_ascii=False, indent=2))
    for c in st.checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.id} {c.detail}")
    return 0 if st.verified else 1


def serve(cfg: RunnerConfig) -> None:
    import uvicorn

    from runner.app import create_app

    config = uvicorn.Config(create_app(), host=cfg.host, port=cfg.port, log_level="info", access_log=False)
    # Run on the default loop (Proactor on Windows) so asyncio subprocesses work; uvicorn.run might pick another.
    asyncio.run(uvicorn.Server(config).serve())


def main() -> None:
    p = argparse.ArgumentParser(prog="runner")
    p.add_argument("--build-image", action="store_true")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()
    cfg = RunnerConfig()
    cfg.check()
    if a.build_image:
        sys.exit(build_image(cfg))
    if a.selftest:
        sys.exit(asyncio.run(selftest_once(cfg)))
    serve(cfg)


if __name__ == "__main__":
    main()
