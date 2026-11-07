"""Proves the problem bank through the REAL sandbox and grader (ADR-0021): every template's reference program, submitted like a
candidate's code, must pass every public and hidden case of a fresh variant; its starter code must run; a wrong program must fail.

Run inside the worker container (it holds docker.sock and the runner package):
    docker cp tools/verify_coding_bank.py aihire-worker-1:/tmp/ && docker exec aihire-worker-1 python /tmp/verify_coding_bank.py [key ...]
Prints one line per problem and exits non-zero if any fails. Writes nothing to the database.
"""
import asyncio
import random
import sys
import time

from app.coding.grading import grade
from app.coding.reference import reference_program
from app.coding.runner import execute
from app.coding.sandbox_local import verify_and_publish
from app.coding.templates import TEMPLATES

WRONG = "import sys\nprint(sys.stdin.read().count(chr(10)) * 7919 + 13)\n"


async def check(key: str) -> tuple[bool, str]:
    t = TEMPLATES[key]
    inputs = [*t.public, *t.hidden(random.Random(20261105))]
    cases = [{"id": i, "input": x, "expected_output": t.solve(x)} for i, x in enumerate(inputs)]
    t0 = time.monotonic()
    good = await grade(reference_program(key), cases, wall_ms=t.time_limit_ms, mem_mb=t.memory_mb)
    slowest = max((c.time_ms or 0) for c in good.cases)
    bad = await grade(WRONG, cases, wall_ms=t.time_limit_ms, mem_mb=t.memory_mb)
    first = await execute(t.starter, [{"case_id": "0", "stdin": t.public[0]}], wall_ms=t.time_limit_ms, mem_mb=t.memory_mb)
    outcomes = sorted({c.outcome for c in good.cases if not c.passed})
    ok = good.passed == good.total and bad.passed < bad.total and first[0].verdict == "OK"
    return ok, (f"Lv.{t.level} {key:<18} reference {good.passed}/{good.total} slowest {slowest}ms/{t.time_limit_ms}ms | wrong {bad.passed}/{bad.total} "
                f"| starter {first[0].verdict} | {time.monotonic() - t0:.0f}s {outcomes or ''}" + ("" if first[0].verdict == "OK" else f" {first[0].stderr[-160:]}"))


async def main() -> int:
    state = await verify_and_publish()
    print("sandbox", state.get("sandbox_verified"), flush=True)
    failed = []
    for key in sys.argv[1:] or sorted(TEMPLATES, key=lambda k: (TEMPLATES[k].level, k)):
        ok, line = await check(key)
        print(("PASS " if ok else "FAIL ") + line, flush=True)
        if not ok:
            failed.append(key)
    print(f"{len(TEMPLATES) - len(failed) if len(sys.argv) == 1 else '-'} passed, failed: {failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
