"""rubric-v2 (screen spec SC-R07, SC-A08; was rubric-v1 of PRD 6.3). Fixed areas, 1~5 anchor sentences, judged from the text."""
import hashlib
import json

RUBRIC_VERSION = "rubric-v3"

COMMON = ("평가 근거는 답변 텍스트뿐이다. 목소리·억양·말 속도·침묵·말버릇·STT 표기 오류·띄어쓰기·맞춤법은 판단에 쓰지 않는다. "
          "모든 문항에 같은 4항목을 적용한다. 각 점수에는 그 판단을 뒷받침하는 답변 원문 인용을 1개 붙인다.")

# key = LLM JSON key; criterion = app.core.constants.Criterion value
ITEMS = [
    {"key": "expertise", "criterion": "EXPERTISE", "name": "직무 전문성",
     "looks_at": "직무 지식과 기술을 정확하게 쓰는가, 경험이 지원 직무와 이어지는가",
     "levels": {
         5: "지원 직무의 핵심 지식·기술을 정확하게 사용하고, 그것을 언제·왜 썼는지 본인 경험으로 설명하며, 그 경험이 공고의 업무·요건과 어떻게 이어지는지 분명하다. 사실과 다른 기술 설명이 없다.",
         4: "직무 관련 지식·기술을 대체로 정확하게 쓰고 경험과 직무의 연결이 드러나지만, 일부 설명이 얕거나 연결이 한두 곳에서 약하다. 중대한 오류는 없다.",
         3: "직무 관련 용어·기술을 언급하지만 설명이 일반적인 수준에 머물거나, 경험과 지원 직무의 연결이 부분적으로만 드러난다. 사소한 부정확함이 있을 수 있다.",
         2: "직무 지식이 거의 드러나지 않거나 용어를 부정확하게 쓰며, 경험이 지원 직무와 어떻게 이어지는지 거의 드러나지 않는다.",
         1: "직무 관련 지식·기술이 답변에 드러나지 않거나, 명백히 틀린 설명이 답변의 중심이다."}},
    {"key": "logic", "criterion": "LOGIC", "name": "논리성",
     "looks_at": "주장에 이유와 사례가 따라오는가, 앞뒤가 모순되지 않는가",
     "levels": {
         5: "주장마다 이유와 구체 사례가 따라오고 이유→사례→결론이 자연스럽게 이어지며, 답변 안에서 앞뒤 모순이 없다.",
         4: "주된 주장에는 이유와 사례가 있고 모순이 없으나, 일부 주장은 이유나 사례 중 하나가 빠져 있다.",
         3: "주장에 이유는 있으나 사례가 빈약하거나, 이유와 결론 사이에 부분적인 비약이 있다. 모순은 없거나 사소하다.",
         2: "주장이 대부분 이유·사례 없이 나열되거나, 앞뒤 내용이 부분적으로 충돌한다.",
         1: "주장과 근거를 구분할 수 없거나, 답변 안에 명백한 모순이 있다."}},
    {"key": "attitude", "criterion": "ATTITUDE", "name": "표현 및 태도",
     "looks_at": "질문에 적극적으로 나서 답하는가, 확신을 갖고 표현하는가 (자신감을 낮추는 말버릇은 따로 센다)",
     "levels": {
         5: "질문마다 먼저 결론이나 입장을 밝히며 답하고, 본인의 경험과 생각을 확신 있는 표현으로 말한다. 질문 의도에 적극적으로 응한다.",
         4: "대체로 적극적으로 답하고 표현이 확신 있으나, 한두 곳에서 머뭇거리거나 입장을 분명히 하지 않는다.",
         3: "답은 하지만 수동적이거나 \"~인 것 같습니다\"처럼 단정을 피하는 표현이 자주 나온다. 입장이 부분적으로만 드러난다.",
         2: "질문에 소극적으로 응하고 확신 없는 표현이 반복되며 입장을 거의 밝히지 않는다.",
         1: "답을 회피하거나 입장이 전혀 드러나지 않는다."}},
    {"key": "delivery", "criterion": "DELIVERY", "name": "전달력",
     "looks_at": "핵심을 먼저 말하고 순서 있게 정리했는가 (답변 텍스트로만 판단)",
     "levels": {
         5: "답변 앞부분에서 핵심(결론·요지)을 밝히고, 이후 내용이 시간·단계·항목 등 일정한 순서로 정리되어 텍스트만 읽어도 흐름을 바로 따라갈 수 있다.",
         4: "핵심이 앞부분에 드러나고 대체로 순서 있게 정리되었으나, 일부 반복이나 순서 바뀜이 있다.",
         3: "핵심이 답변 중간이나 끝에 가서야 드러나거나, 정리 순서가 부분적으로 흐트러져 있다.",
         2: "핵심을 찾기 어렵고 내용이 순서 없이 이어져 여러 번 읽어야 이해된다.",
         1: "텍스트에서 핵심과 구조를 파악할 수 없다."}},
]
# Sub-items shown in the report (SC-A08). The model scores a·b·c as small integers; labels live here so the prompt, the
# report and the tests agree. STAR (상황·과제·행동·결과) is a 0/1 check per element.
SUB_ITEMS = {
    "expertise": [("a", "개념 정확성", "직무 개념과 기술 설명이 정확한가"), ("b", "실무 적용", "개념을 실제 경험·업무에 적용해 말하는가"),
                  ("c", "설명의 깊이", "원리나 이유까지 설명하는가")],
    "logic": [("a", "주장 명확성", "결론이나 입장이 분명한가"), ("b", "근거 제시", "이유와 사례가 따라오는가"),
              ("c", "논리적 흐름", "순서와 연결이 자연스러운가")],
}
STAR = [("situation", "상황"), ("task", "과제"), ("action", "행동"), ("result", "결과")]
# Coding-test metrics the model judges (ADR-0017). 정확성 is not here: it is the test pass rate, computed by code.
CODING_METRICS = [
    {"key": "efficiency", "name": "효율성", "looks_at": "문제의 입력 범위에 맞는 시간·공간 복잡도의 알고리즘과 자료구조를 썼는가",
     "levels": {
         5: "입력 범위에서 충분히 빠른 알고리즘과 알맞은 자료구조를 쓰고 불필요한 반복·복사가 없다.",
         4: "복잡도는 입력 범위에 맞지만 작은 낭비(불필요한 복사, 중복 계산)가 있다.",
         3: "작은 입력에는 문제없으나 최대 입력에서 느려질 수 있는 부분이 있다.",
         2: "최대 입력에서 제한 시간이나 메모리를 넘길 가능성이 큰 구조다.",
         1: "효율을 판단할 수 있는 풀이가 없거나, 입력 범위에서 끝나기 어려운 구조다."}},
    {"key": "approach", "name": "문제 해결 방식", "looks_at": "요구사항을 어떻게 나누어 풀었는가, 자료구조·알고리즘 이해가 드러나는가, 경계 조건을 다루는가",
     "levels": {
         5: "요구사항을 빠짐없이 단계로 나누어 풀고, 문제에 맞는 자료구조·알고리즘을 고른 이유가 코드 구조에서 드러나며 경계 조건을 다룬다.",
         4: "풀이 방향이 맞고 자료구조·알고리즘 선택도 적절하지만 경계 조건 일부를 놓쳤다.",
         3: "주된 흐름은 맞지만 일부 요구사항이 빠졌거나 문제에 덜 맞는 방식을 썼다.",
         2: "풀이 방향이 부분적으로만 맞고 핵심 요구사항을 놓쳤다.",
         1: "풀이 방향을 찾을 수 없거나, 입력과 무관하게 정해진 답을 출력한다."}},
    {"key": "quality", "name": "코드 품질", "looks_at": "변수 이름, 함수 분리, 읽기 쉬운 구조로 협업하기 좋은 코드인가",
     "levels": {
         5: "이름만으로 역할을 알 수 있고 함수가 알맞게 나뉘어 있으며 중첩과 중복이 적어 바로 읽힌다.",
         4: "대체로 읽기 쉽지만 일부 이름이 모호하거나 조금 더 나눌 수 있다.",
         3: "동작을 따라갈 수 있으나 이름이 모호하거나 한 덩어리로 길다.",
         2: "한 글자 이름, 깊은 중첩, 중복이 많아 읽기 어렵다.",
         1: "구조를 파악하기 어렵다."}},
]
KEYS = [i["key"] for i in ITEMS]
KEY_TO_CRITERION = {i["key"]: i["criterion"] for i in ITEMS}


def render() -> str:
    lines = [COMMON]
    for it in ITEMS:
        lines.append(f"[{it['key']}] {it['name']} — {it['looks_at']}")
        lines += [f"  {s}점: {it['levels'][s]}" for s in (5, 4, 3, 2, 1)]
    for key, subs in SUB_ITEMS.items():
        lines.append(f"[{key}_sub] " + ", ".join(f"{k}={name}({desc})" for k, name, desc in subs))
    lines.append("[star] " + ", ".join(f"{k}={name}" for k, name in STAR))
    return "\n".join(lines)


def render_coding() -> str:
    lines = []
    for it in CODING_METRICS:
        lines.append(f"[{it['key']}] {it['name']} — {it['looks_at']}")
        lines += [f"  {s}점: {it['levels'][s]}" for s in (5, 4, 3, 2, 1)]
    return "\n".join(lines)


def rubric_hash() -> str:
    body = json.dumps({"version": RUBRIC_VERSION, "common": COMMON, "items": ITEMS, "sub": SUB_ITEMS, "star": STAR}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]
