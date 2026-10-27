"""Final-submission grading (6.5, COD-04·06). Every public + hidden case runs once in the sandbox; the worker
compares stdout with the expected output here, outside the runner (C8).

Comparison: line endings unified, trailing whitespace of each line and trailing blank lines ignored; everything
else must match exactly. Empty code is not executed and scores 0 (6.5).
"""
from dataclasses import dataclass
from decimal import Decimal

from app.coding import runner
from app.evaluation.scoring import coding_score


def normalize_output(s: str) -> str:
    lines = s.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).rstrip("\n")


@dataclass
class CaseGrade:
    testcase_id: str
    passed: bool
    outcome: str  # PASS | WRONG_ANSWER | RUNTIME_ERROR | TIMEOUT | MEMORY_LIMIT | OUTPUT_LIMIT | NOT_RUN
    time_ms: int | None


@dataclass
class Grade:
    cases: list[CaseGrade]
    compile_ok: bool
    passed: int
    total: int
    score: Decimal


async def grade(code: str, testcases: list[dict], *, wall_ms: int, mem_mb: int) -> Grade:
    """testcases: [{id, input, expected_output}] in ordinal order."""
    total = len(testcases)
    if not code.strip():
        cases = [CaseGrade(str(t["id"]), False, "NOT_RUN", None) for t in testcases]
        return Grade(cases, False, 0, total, coding_score(0, total))
    results = await runner.execute(code, [{"case_id": str(t["id"]), "stdin": t["input"]} for t in testcases],
                                   wall_ms=wall_ms, mem_mb=mem_mb)
    by_id = {r.case_id: r for r in results}
    cases = []
    for t in testcases:
        r = by_id[str(t["id"])]
        if r.verdict != "OK":
            outcome = r.verdict
        elif normalize_output(r.stdout) == normalize_output(t["expected_output"]):
            outcome = "PASS"
        else:
            outcome = "WRONG_ANSWER"
        cases.append(CaseGrade(str(t["id"]), outcome == "PASS", outcome, r.time_ms))
    compile_ok = not any("SyntaxError" in r.stderr or "IndentationError" in r.stderr for r in results)
    passed = sum(c.passed for c in cases)
    return Grade(cases, compile_ok, passed, total, coding_score(passed, total))
