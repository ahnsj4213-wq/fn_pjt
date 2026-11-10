"""Calibration of T5 evaluate_answer against the real local model (ADR-0023, requirement 10 of 2026-11-09).

    docker exec aihire-api-1 python scripts/eval_calibration.py            (the running stack's ai service)
    python scripts/eval_calibration.py --fake                              (checks the script itself, no model)

A fixed set of answers of different length and quality per kind of question, each with the range of 논리성·전문성 a fair
rater would give (written before the run, not fitted to it). The script prints the model's scores, evidence sentences and
comments next to the answer so a person can check that the evidence and the comment match the answer, and counts:
- in range       the score of each expected area falls in its range
- ordered        within one question, a better answer never scores lower on 논리성 or 전문성 than a worse one
- linked         no criterion is UNLINKED (a score of 3+ with no sentence), and every evidence is a sentence of the answer
The model decides the scores; nothing here changes them. Exit code 1 when a check fails. No data is stored.
"""
import argparse
import asyncio
import sys
import time
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import rubric  # noqa: E402
from app.ai.client import AIClient, get_ai_client  # noqa: E402
from app.evaluation.pipeline import median_judgement  # noqa: E402

SAMPLES = 1  # --samples: independent judgements per answer, the median kept (what the pipeline does with eval_samples)


async def judge(ai: AIClient, inp: dict):
    """One answer scored like the pipeline: SAMPLES judgements, per criterion the median one."""
    runs = [await ai.llm("evaluate_answer", inp, lane="BATCH") for _ in range(SAMPLES)]
    ok = [r for r in runs if r.status == "OK"]
    if not ok:
        return runs[-1]
    calls = sum(r.attempts.get("llm_calls", 0) for r in runs)
    return SimpleNamespace(status="OK", output=median_judgement([r.output for r in ok]), attempts={"llm_calls": calls})

JOB = "백엔드 개발자: 주문·결제 API 개발과 운영, 대용량 트래픽 처리, 장애 대응"
IOT = "산업 IoT 데이터 엔지니어: 공장 설비 센서 데이터 수집·전송, 망 단절 시 저장 후 재전송, 시스템 로그·성능 지표 기반 장애 분석"
BRIEF = {"situ": IOT, "fu": IOT}  # the posting each question came from (the two report answers were for an IoT posting)

# (group, rank in group: higher = better answer, area, question, answer, expected {criterion: (lo, hi)})
CASES = [
    ("exp", 3, "PROJECT", "성능 문제를 해결했던 경험을 말씀해 주세요.",
     "주문 조회 API 응답이 평균 3초까지 느려진 문제를 맡았습니다. APM으로 보니 주문 목록 쿼리 하나가 전체 시간의 대부분이었고, "
     "실행 계획에서 주문 테이블 풀 스캔을 확인했습니다. 캐시를 먼저 넣는 방안도 검토했지만 데이터가 자주 바뀌어 원인을 덮을 뿐이라고 판단했고, "
     "조회 조건 순서대로 회원 ID와 주문일 복합 인덱스를 추가했습니다. 배포 후 일주일 동안 모니터링해 평균 응답이 0.3초로 유지되는 것을 확인했습니다.",
     {"LOGIC": (4, 5), "EXPERTISE": (4, 5)}),
    ("exp", 3, "PROJECT", "성능 문제를 해결했던 경험을 말씀해 주세요.",
     "주문 조회가 3초 걸려 실행 계획을 보니 풀 스캔이었고, 캐시는 원인을 덮을 뿐이라 복합 인덱스를 추가해 0.3초로 줄어든 것을 모니터링으로 확인했습니다.",
     {"LOGIC": (4, 5), "EXPERTISE": (4, 5)}),  # short but complete: must not lose for being short
    ("exp", 1, "PROJECT", "성능 문제를 해결했던 경험을 말씀해 주세요.",
     "주문 API가 느려서 인덱스를 추가하고 캐시도 넣고 서버도 두 대 늘렸습니다. 그 뒤로는 빨라졌습니다.",
     {"LOGIC": (2, 3), "EXPERTISE": (2, 3)}),
    ("exp", 0, "PROJECT", "성능 문제를 해결했던 경험을 말씀해 주세요.",
     "그때 정말 열심히 했습니다. 팀원들과 함께 여러 가지를 시도했고 결국 잘 해결됐습니다. 그 경험으로 많이 성장했다고 생각합니다. "
     "앞으로도 문제가 생기면 열심히 해결하겠습니다. 정말 열심히 하는 것이 중요하다고 생각합니다.",
     {"LOGIC": (1, 2), "EXPERTISE": (1, 2)}),  # long but empty: must not win for being long
    ("tech", 2, "JOB_SKILL", "REST와 gRPC의 차이를 설명해 주세요.",
     "가장 큰 차이는 전송 방식입니다. gRPC는 HTTP/2 위에서 프로토콜 버퍼로 직렬화해 메시지가 작고 스트리밍을 지원해서 내부 서비스 간 호출에 유리합니다. "
     "REST는 HTTP와 JSON을 그대로 써서 브라우저나 외부 파트너가 바로 호출하기 쉽습니다. 그래서 외부 공개 API는 REST로, 내부 고빈도 호출은 gRPC로 나눠 씁니다.",
     {"LOGIC": (4, 5), "EXPERTISE": (4, 5)}),
    ("tech", 1, "JOB_SKILL", "REST와 gRPC의 차이를 설명해 주세요.",
     "REST는 많이 쓰는 방식이고 gRPC는 구글이 만든 더 빠른 방식입니다.",
     {"LOGIC": (2, 3), "EXPERTISE": (2, 3)}),
    ("tech", 0, "JOB_SKILL", "REST와 gRPC의 차이를 설명해 주세요.",
     "gRPC는 HTTP 1.0과 XML만 써서 REST보다 느리고, 그래서 요즘은 거의 쓰지 않습니다.",
     {"LOGIC": (1, 3), "EXPERTISE": (1, 2)}),  # confident but wrong: 전문성 low
    ("situ", 1, "JOB_SKILL",
     "공장 망 단절 시 72시간치 데이터를 저장하고 복구 후 순서대로 재전송하는 방식을 유지하면서, 현재 3시간에서 1시간으로 복구 완료 시간을 개선해야 한다면 어떤 데이터를 우선적으로 처리하시겠습니까?",
     "먼저 재전송 대기 데이터를 이상 이벤트와 일반 측정값으로 나누겠습니다. 설비 고장이나 온도 이상 같은 이벤트는 복구 판단에 바로 쓰이기 때문에 먼저 보내고, "
     "일반 측정값은 압축해서 배치로 보내 전송량을 줄이겠습니다. 순서 보장이 필요한 설비별 데이터는 설비 단위로 병렬 전송하고, 재전송 중 걸린 시간을 측정해 1시간 안에 끝나는지 확인하겠습니다.",
     {"LOGIC": (4, 5), "EXPERTISE": (3, 5)}),
    ("situ", 0, "JOB_SKILL",
     "공장 망 단절 시 72시간치 데이터를 저장하고 복구 후 순서대로 재전송하는 방식을 유지하면서, 현재 3시간에서 1시간으로 복구 완료 시간을 개선해야 한다면 어떤 데이터를 우선적으로 처리하시겠습니까?",
     "먼저 설비 고장 이력 온도 진동 등 주요 센서의 이상 데이터 이벤트로그를 우선 전송하고 일반적인 센서 측정 데이터는 배치 처리와 압축을 통해 전송 효율을 높이겠습니다.",
     {"LOGIC": (2, 3), "EXPERTISE": (3, 4)}),  # the report that scored 100: what to do, no why or check
    ("fu", 0, "PROJECT",
     "시스템 로그와 CPU 메모리 품질 성능을 분석하여 장애 원인을 파악할 때, 실제 데이터 수집 과정에서 늦게 도착하거나 빠진 데이터를 어떻게 처리하셨습니까?",
     "로그나 성능 지표가 늦게 수집되거나 누락되면 장애 발생 시점을 잘못 판단할 수 있기 때문에 데이터의 실제 레슨 시간과 수집 시간을 구분하는 것이 중요하다고 생각합니다. "
     "이번 수집이 지연된 데이터는 실제 발생 시간 기준으로 정리를 분석하고, 보고하는 이전 로그나 다른 모니터링 지표와 비교의 원인을 확인하겠습니다.",
     {"LOGIC": (2, 4), "EXPERTISE": (2, 4)}),  # the report that scored 20 on a real answer
    ("beh", 1, "COLLABORATION", "팀원과 의견이 다를 때 어떻게 하시나요?",
     "먼저 서로 판단 기준이 무엇인지부터 맞춥니다. 의견이 갈리는 이유는 대개 목표나 제약을 다르게 보고 있어서이기 때문입니다. "
     "지난 프로젝트에서 캐시 도입을 두고 의견이 갈렸을 때도 응답 시간 목표와 데이터 정합성 중 무엇이 우선인지 먼저 합의했고, 그 기준으로 정하니 결론이 빨리 났습니다.",
     {"LOGIC": (4, 5), "EXPERTISE": (3, 5)}),
    ("beh", 0, "COLLABORATION", "팀원과 의견이 다를 때 어떻게 하시나요?",
     "그냥 다른 사람 의견에 맞춰 줍니다.",
     {"LOGIC": (1, 2)}),
    ("design", 1, "JOB_SKILL", "초당 10만 건의 로그를 수집하는 시스템을 설계한다면 어떻게 구성하시겠습니까?",
     "먼저 유실 허용 여부와 조회 지연 요구를 확인하겠습니다. 수집단은 각 서버의 에이전트가 Kafka로 보내게 하고, 직접 DB에 쓰는 방식은 쓰기 폭주에 약해서 제외합니다. "
     "Kafka 파티션을 서비스별로 나눠 처리량을 늘리고, 소비자가 배치로 저장소에 적재합니다. 저장소는 검색이 필요하면 Elasticsearch, 장기 보관은 오브젝트 스토리지로 나누고, "
     "부하 테스트로 초당 10만 건에서 지연과 유실이 없는지 확인하겠습니다.",
     {"LOGIC": (4, 5), "EXPERTISE": (4, 5)}),
    ("design", 0, "JOB_SKILL", "초당 10만 건의 로그를 수집하는 시스템을 설계한다면 어떻게 구성하시겠습니까?",
     "Kafka를 쓰고 Elasticsearch에 저장하겠습니다.",
     {"LOGIC": (1, 3), "EXPERTISE": (2, 3)}),  # one option, no comparison or reason
]


async def run(ai: AIClient) -> bool:
    rows = []
    for group, rank, area, question, answer, expect in CASES:
        kind = rubric.question_kind(area, question)
        t = time.perf_counter()
        r = await judge(ai, {"question": question, "question_type": area, "question_kind": kind, "job_brief": BRIEF.get(group, JOB),
                                             "answer": answer, "is_no_answer": False})
        rows.append((group, rank, kind, answer, expect, r, time.perf_counter() - t))
    ok_range = ok_link = 0
    total_range = total_link = 0
    for group, rank, kind, answer, expect, r, sec in rows:
        print(f"\n== {group}#{rank} {kind} ({sec:.1f}s, {r.attempts.get('llm_calls', 0)} calls) {answer[:70]}…")
        if r.status != "OK":
            print(f"   status {r.status}")
            total_range += len(expect)
            continue
        found = (r.output.get("detail") or {}).get("found")
        if found:
            print("   found " + " ".join(f"{k}={v}" for k, v in found.items()))
        for crit, v in r.output["items"].items():
            q = v.get("quote")
            # linked: not UNLINKED, and evidence (when there is one) is a sentence of the answer; 내용 부족 has nothing to point at
            linked = v["evidence_status"] != "UNLINKED" and (not q or answer[q["start"]:q["end"]] == q["text"])
            total_link += 1
            ok_link += bool(linked)
            mark = ""
            if crit in expect:
                lo, hi = expect[crit]
                good = lo <= v["score"] <= hi
                total_range += 1
                ok_range += good
                mark = f" expect {lo}~{hi} {'OK' if good else 'MISS'}"
            ev = f"[{q['sent']}] {q['text'][:50]}" if q else v["evidence_status"]
            print(f"   {crit:9} {v['score']}{mark:16} {ev} | {v.get('comment', '')}")
    order_bad = []
    by_group: dict = {}
    for group, rank, _k, _a, _e, r, _s in rows:
        if r.status == "OK":
            by_group.setdefault(group, []).append((rank, r.output["items"]))
    for group, items in by_group.items():
        items.sort(key=lambda x: x[0])
        for (ra, a), (rb, b) in zip(items, items[1:]):
            if ra == rb:  # equally good answers (the same content, short and long): no order between them
                continue
            for crit in ("LOGIC", "EXPERTISE"):
                if b[crit]["score"] < a[crit]["score"]:
                    order_bad.append(f"{group}: #{rb} {crit} {b[crit]['score']} < #{ra} {a[crit]['score']}")
    print(f"\nin range {ok_range}/{total_range}, linked {ok_link}/{total_link}, order violations {len(order_bad)}")
    for x in order_bad:
        print("  ", x)
    return ok_range == total_range and ok_link == total_link and not order_bad


async def spread(ai: AIClient, n: int) -> bool:
    """The same answer scored n times: the largest difference per criterion. More than 1 point apart is reported as unstable."""
    unstable = 0
    for group, rank, area, question, answer, _expect in CASES:
        kind = rubric.question_kind(area, question)
        inp = {"question": question, "question_type": area, "question_kind": kind, "job_brief": BRIEF.get(group, JOB),
               "answer": answer, "is_no_answer": False}
        runs = [await judge(ai, inp) for _ in range(n)]
        scores = {c: [r.output["items"][c]["score"] for r in runs if r.status == "OK"] for c in ("LOGIC", "EXPERTISE", "ATTITUDE", "DELIVERY")}
        line = "  ".join(f"{c[:5]} {'/'.join(map(str, v))}" for c, v in scores.items())
        bad = [c for c, v in scores.items() if v and max(v) - min(v) > 1]
        unstable += len(bad)
        print(f"{group}#{rank:<2} {line}{'  UNSTABLE ' + ','.join(bad) if bad else ''}")
    print(f"\nunstable criteria (spread > 1 point): {unstable}")
    return unstable == 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="use FakeAIClient (checks the script, not the model)")
    ap.add_argument("--samples", type=int, default=1, help="judgements per answer, the median kept (the pipeline's eval_samples)")
    ap.add_argument("--repeat", type=int, default=1, help="score every answer N times and print the spread (consistency)")
    args = ap.parse_args()
    if args.fake:
        from app.ai.fake import FakeAIClient

        ai: AIClient = FakeAIClient()
    else:
        ai = get_ai_client()  # the client the running stack uses (ai service or direct llama.cpp lanes)
    global SAMPLES
    SAMPLES = max(1, args.samples)
    ok = asyncio.run(run(ai)) if args.repeat <= 1 else asyncio.run(spread(ai, args.repeat))
    print("\nALL CHECKS PASS" if ok else "\nSOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
