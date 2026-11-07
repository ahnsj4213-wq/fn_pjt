"""Problem levels Lv.0~5 (ADR-0021): what each level is, how long it gets, what the code review expects.

The score itself does not change by level: it stays passed / total over public + hidden tests (6.5, scoring.coding_score).
A level changes the sandbox run limit, the test's time limit and what the reviewer model is told to expect.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class LevelPolicy:
    level: int
    label: str
    difficulty: str  # 하 | 중 | 상 (the older three-step column)
    minutes: int  # the test's time limit at this level; the posting's own limit is the ceiling
    run_ms: int  # one run in the sandbox
    expected: str  # what a good submission looks like (told to the reviewer, shown to the recruiter)


LEVELS: dict[int, LevelPolicy] = {p.level: p for p in (
    LevelPolicy(0, "입문", "하", 15, 2000, "입력을 읽어 조건문과 반복문으로 정확히 처리한다. 효율은 보지 않는다."),
    LevelPolicy(1, "기초", "하", 20, 2000, "문자열·배열·해시 같은 기본 자료구조를 알맞게 고른다. 빈 입력 같은 경계 조건을 처리한다."),
    LevelPolicy(2, "초급", "중", 30, 3000, "스택·큐·정렬·투 포인터를 써서 O(n log n) 안에 푼다."),
    LevelPolicy(3, "중급", "중", 40, 4000, "BFS·DFS·최단 경로·기본 DP를 입력 크기에 맞게 고른다. O(n²) 풀이는 큰 입력에서 시간을 넘길 수 있다."),
    LevelPolicy(4, "고급", "상", 50, 5000, "상태 설계가 필요한 DP·그래프·트리 자료구조를 쓴다. 재귀 깊이와 메모리까지 고려한다."),
    LevelPolicy(5, "심화", "상", 60, 6000, "구간 자료구조·비트마스크 DP·강한 연결 요소·최대 유량을 구현하고 복잡도를 설명할 수 있다."),
)}
