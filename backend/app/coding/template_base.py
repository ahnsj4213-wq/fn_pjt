"""What one coding problem template holds (see templates.py for how templates become per-application variants)."""
import random
from collections.abc import Callable
from dataclasses import dataclass, field

BLOG = "https://lovelyunsh.tistory.com/"
# Every problem here is original work. "reference" says which public write-up the algorithm idea was looked at for; no statement,
# test or solution is copied from it, so there is no upstream problem and `upstream_url` stays empty.
ORIGINAL_NOTE = "자체 제작 문제. 알고리즘 유형만 참고했고 문제 원문·풀이·테스트는 가져오지 않았다."


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    difficulty: str  # 하 | 중 | 상
    statement: str
    public: tuple[str, ...]  # PUBLIC example inputs (fixed, shown to the candidate)
    solve: Callable[[str], str]
    hidden: Callable[[random.Random], list[str]]  # HIDDEN inputs: fixed edge cases first, then seeded random ones
    time_limit_ms: int = 2000
    memory_mb: int = 256
    notes: tuple[str, ...] = ()  # explanation of each public example, same order as `public`
    level: int = 1  # Lv.0~5 (levels.py)
    tags: tuple[str, ...] = ()  # algorithm tags: recruiter side only, they would be a hint to the candidate
    starter: str = ""  # Python 3 code the editor opens with
    expected: tuple[str, ...] = ()  # the public outputs worked out by hand: checks `solve` independently
    families: tuple[str, ...] = ()  # job families it suits best; empty = any
    source: dict = field(default_factory=lambda: source_of())


def source_of(*urls: str, idea: str = "") -> dict:
    return {"origin": "ORIGINAL", "upstream_url": None, "reference_urls": [BLOG, *urls], "reference_idea": idea, "note": ORIGINAL_NOTE}


def statement(story: str, task: str, inp: str, out: str, limits: tuple[str, ...]) -> str:
    """Enough to write the program from: the situation, exactly what to compute, both formats and every limit."""
    return (f"### 문제 상황\n{story}\n\n### 해야 할 일\n{task}\n\n### 입력\n{inp}\n\n### 출력\n{out}\n\n### 제한사항\n"
            + "\n".join(f"- {x}" for x in limits) + "\n")


def starter(args: str, parse: tuple[str, ...], default: str = "0", show: str = "print(solution({args}))",
            reader: str = "data = sys.stdin.read().split()") -> str:
    """The input is already read for the candidate: they fill in `solution`."""
    body = "\n".join(f"    {x}" for x in (reader, *parse, show.format(args=args)))
    return (f"import sys\n\n\ndef solution({args}):\n    # 여기에 풀이를 작성하세요.\n    answer = {default}\n    return answer\n\n\n"
            f"def main():\n{body}\n\n\nmain()\n")


def lines_starter(args: str, parse: tuple[str, ...], default: str = "0", show: str = "print(solution({args}))") -> str:
    return starter(args, parse, default, show, reader='rows = sys.stdin.read().split("\\n")')
