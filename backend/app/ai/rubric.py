"""rubric-v4 (2026-11-09, ADR-0023; was rubric-v3 of SC-R07/SC-A08, rubric-v1 of PRD 6.3). The same four areas and 1~5 points.

What changed from v3:
- 논리성 is the thinking and judging process (problem, cause, grounds of the judgement, alternatives, action, checking the result,
  and whether cause and effect hold together), not "a reason follows a claim". 전문성 is whether the job knowledge and the way it
  is applied are correct. "Why" belongs to 논리성 only, so the two areas no longer rise and fall together.
- What 논리성 and 전문성 need depends on the kind of question (QUESTION_KINDS): an experience question needs a result that was
  checked, a knowledge question needs the principle and its conditions, a behaviour question needs no result. Elements are
  weighed, never counted: a short answer that has what the question needs scores high.
- Every level has an example (one scene for all four areas: a slow API), so the model compares with something concrete.
The model decides every score; code only checks the form and that the evidence sentence exists (evaluate.py).

rubric-v5 (2026-11-09, user directive "예시 유사도가 아니라 루브릭 요소와 답변의 의미로"):
- The examples are reference cases of a level. Their wording, technologies and length are not conditions of the score, and what
  they mention (an index, a slow API) is never taken as said by the candidate (evaluate.py also checks the comments for it).
- 논리성 per kind: experience = situation, judgement, action, result; knowledge = concept, reason, consistency; design =
  requirements, alternatives, trade-off. 육하원칙 is a tool to see whether needed context is there, never a checklist.
- 표현 및 태도 and 전달력 are read per kind too, and every area against the candidate's level (LEVEL_VIEW): a new graduate's
  school or team project counts as experience; the bar for correctness does not move.
"""
import hashlib
import json
import re

RUBRIC_VERSION = "rubric-v5"

COMMON = ("평가 근거는 답변 텍스트뿐이다. 목소리·억양·말 속도·침묵·말버릇·STT 표기 오류·띄어쓰기·맞춤법은 판단에 쓰지 않는다. "
          "4개 항목은 서로 독립이다: 한 항목이 높다고 다른 항목을 함께 올리거나 내리지 않고, 항목마다 그 항목의 기준만 본다. "
          "답변 길이로 판단하지 않는다: 짧아도 질문이 요구하는 요소를 갖추면 높은 점수, 길어도 요소가 없거나 같은 말을 반복하면 낮은 점수다. "
          "질문 유형마다 필요한 요소가 다르다(아래 '이 질문에서 보는 것'). 요소의 개수를 세지 말고, 필요한 요소가 얼마나 정확하고 서로 이어지는지 본다. "
          "짧더라도 질문에 정확하고 충분하게 답했으면 5점도 줄 수 있다. "
          "점수별 '참고 사례'는 그 수준이 어떤 모습인지 보여 주는 예일 뿐이다: 사례와 문장·표현·기술·길이가 비슷한지로 점수를 정하지 않고, "
          "점수별 기준 문장과 답변의 의미를 비교한다. 사례에 나온 내용(기술 이름, 수치, 행동)을 지원자가 말한 것으로 보지 않는다. "
          "판단과 코멘트는 답변 원문에 실제로 있는 사실만 근거로 한다. "
          "육하원칙(누가·언제·어디서·무엇을·어떻게·왜)은 답을 이해하는 데 필요한 맥락이 빠졌는지 확인하는 도구일 뿐, 모든 답변이 갖춰야 할 목록이 아니다.")

# key = LLM JSON key; criterion = app.core.constants.Criterion value. `examples` are short answer pieces about one scene (an API
# that became slow), so the five levels of one area can be told apart at a glance.
ITEMS = [
    {"key": "expertise", "criterion": "EXPERTISE", "name": "직무 전문성",
     "looks_at": "직무 지식·기술 자체가 정확한가, 그것을 어떤 도구·방법·조건으로 적용하는지 구체적이고 맞는가, 지원 직무와 맞는가. "
                 "왜 그 방법을 골랐는지 같은 판단 과정은 논리성에서 보고 여기서는 보지 않는다",
     "levels": {
         5: "질문에 필요한 직무 지식·기술을 정확하게 쓰고, 적용 방법(도구·절차·수치·제약 조건)이 구체적이며 직무 요건과 맞는다. 틀린 설명이 없다.",
         4: "지식·기술이 정확하고 적용 방법도 드러나지만 일부 설명이 일반적이거나 조건이 빠져 있다. 중대한 오류는 없다.",
         3: "맞는 기술·방법을 골라 무엇에 쓰는지는 말하지만, 어떻게 적용하는지(도구·절차·조건)는 일반론에 머문다. 사소한 부정확함이 있을 수 있다.",
         2: "기술·용어의 이름만 나열하고 무엇에 어떻게 쓰는지가 없거나, 일부를 부정확하게 쓴다.",
         1: "직무 지식·기술이 답변에 전혀 없거나(다짐·일반론뿐), 답변의 중심이 되는 설명이 틀렸다."},
     "examples": {
         5: "\"실행 계획에서 주문 테이블 풀 스캔을 확인하고, 조회 조건 순서대로 (회원ID, 주문일) 복합 인덱스를 만들었습니다.\"",
         4: "\"실행 계획으로 풀 스캔을 확인하고 조회 조건 컬럼에 인덱스를 추가했습니다.\"",
         3: "\"느린 조회 쿼리에 인덱스를 추가해 속도를 개선했습니다.\" (맞는 방법이지만 어떻게 정했는지는 없음)",
         2: "\"인덱스도 넣고 캐시도 넣고 서버도 늘렸습니다.\" (기술 이름만 나열)",
         1: "\"인덱스는 많을수록 무조건 빨라지니 모든 컬럼에 걸었습니다.\" (핵심이 틀림) / \"개발자들이 알아서 고쳐 줬습니다.\" (지식 없음)"}},
    {"key": "logic", "criterion": "LOGIC", "name": "논리성",
     "looks_at": "사고·판단 과정: 문제 상황, 원인 분석, 판단 근거, 대안 검토, 실행 과정, 결과 검증이 질문에 필요한 만큼 있고, 원인→판단→행동→결과의 "
                 "인과가 앞뒤 모순 없이 이어지는가. 무엇이 필요한지는 질문 유형이 정한다(경험: 상황·판단·행동·결과, 기술: 개념·이유·일관성, 설계: 요구사항·대안·트레이드오프). "
                 "육하원칙은 필요한 맥락이 빠졌는지 확인할 때만 쓰고 개수를 세지 않는다. 지식이 맞는지는 전문성에서 본다",
     "levels": {
         5: "이 질문 유형에 필요한 요소(아래 '이 질문에서 보는 것')를 모두 갖추고, 왜 그렇게 판단했는지와 무엇을 했는지(할지)와 그 결과가 "
            "하나의 인과로 이어지며 모순이 없다.",
         4: "필요한 요소 대부분이 있고 인과가 일관되지만, 한 요소가 빠지거나 얕다.",
         3: "결론과 이유는 있으나 판단 근거가 추측·일반론이거나, 필요한 요소 둘 이상이 빠졌거나, 단계 사이에 비약이 있다.",
         2: "무엇을 했는지(할지)는 있으나 왜 그렇게 했는지가 거의 없이 나열되거나, 앞뒤 내용이 부분적으로 충돌한다.",
         1: "무엇을 왜 했는지 모두 알 수 없거나(다짐·일반론뿐), 명백한 모순이 있거나, 질문과 무관한 답이다."},
     "examples": {
         5: "\"응답이 3초로 느려져 로그를 보니 주문 조회만 느렸고, 인덱스 누락을 의심했습니다. 캐시도 검토했지만 원인을 덮을 뿐이라 인덱스를 추가했고, "
            "배포 후 응답 시간이 0.3초로 돌아온 것을 모니터링으로 확인했습니다.\"",
         4: "\"주문 조회만 느려 인덱스 누락으로 판단해 추가했고, 이후 응답 시간이 정상으로 돌아왔습니다.\" (대안 검토 없음)",
         3: "\"쿼리가 느린 것 같아서 인덱스를 추가했더니 나아졌습니다.\" (원인 판단이 추측)",
         2: "\"인덱스도 추가하고 캐시도 넣고 서버도 늘렸습니다.\" (행동 나열, 이유 없음)",
         1: "\"그때그때 상황에 맞게 잘 처리했습니다.\" (판단 과정 없음)"}},
    {"key": "attitude", "criterion": "ATTITUDE", "name": "표현 및 태도",
     "looks_at": "질문에 적극적으로 나서 답하는가, 본인의 입장과 경험을 확신 있게 표현하는가 (자신감을 낮추는 말버릇은 따로 센다)",
     "levels": {
         5: "먼저 입장이나 결론을 밝히며 답하고, 본인의 경험과 생각을 확신 있는 표현으로 말한다. 질문 의도에 적극적으로 응한다.",
         4: "대체로 적극적이고 확신 있으나, 한두 곳에서 머뭇거리거나 입장을 분명히 하지 않는다.",
         3: "답은 하지만 수동적이거나 \"~인 것 같습니다\"처럼 단정을 피하는 표현이 자주 나온다. 입장이 부분적으로만 드러난다.",
         2: "소극적으로 응하고 확신 없는 표현이 반복되며 입장을 거의 밝히지 않는다.",
         1: "답을 회피하거나 입장이 전혀 드러나지 않는다."},
     "examples": {
         5: "\"제가 원인을 찾겠다고 먼저 나섰고, 인덱스 추가가 맞는 판단이었다고 생각합니다.\"",
         4: "\"원인을 찾아 인덱스를 추가했습니다. 다만 다른 방법이 더 나았을지는 확신이 없습니다.\"",
         3: "\"아마 인덱스 문제였던 것 같고, 그렇게 했던 것 같습니다.\"",
         2: "\"잘은 모르겠지만… 팀에서 하자는 대로 한 것 같습니다.\"",
         1: "\"잘 모르겠습니다. 넘어가도 될까요.\""}},
    {"key": "delivery", "criterion": "DELIVERY", "name": "전달력",
     "looks_at": "핵심을 먼저 말하고 순서 있게 정리했는가 (답변 텍스트로만 판단, 짧은 답변도 핵심이 앞에 있고 순서가 분명하면 높은 점수)",
     "levels": {
         5: "답변 앞부분에서 핵심(결론·요지)을 밝히고, 이후 내용이 시간·단계·항목 등 일정한 순서로 정리되어 텍스트만 읽어도 흐름을 바로 따라갈 수 있다.",
         4: "핵심이 앞부분에 드러나고 대체로 순서 있게 정리되었으나, 일부 반복이나 순서 바뀜이 있다.",
         3: "핵심이 답변 중간이나 끝에 가서야 드러나거나, 정리 순서가 부분적으로 흐트러져 있다.",
         2: "핵심을 찾기 어렵고 내용이 순서 없이 이어져 여러 번 읽어야 이해된다.",
         1: "텍스트에서 핵심과 구조를 파악할 수 없다."},
     "examples": {
         5: "\"원인은 인덱스 누락이었습니다. 먼저 느린 쿼리를 찾고, 다음으로 인덱스를 추가했고, 마지막으로 응답 시간을 확인했습니다.\"",
         4: "\"원인은 인덱스 누락이었습니다. 쿼리를 찾고… 아까 말씀드린 것처럼 인덱스를 추가했고 확인했습니다.\" (일부 반복)",
         3: "\"쿼리를 보고, 로그도 보고, 여러 가지를 해 봤는데 결국 인덱스가 원인이었습니다.\" (핵심이 끝에)",
         2: "\"서버도 보고 인덱스도 하고 그 전에 로그를 봤고 캐시는 나중에…\" (순서 없음)",
         1: "\"그게, 그러니까, 여러 가지가 있어서…\""}},
]

# The kind of question decides what 논리성 and 전문성 need (requirement 2·9 of 2026-11-09). Code picks the kind from the area and the
# question's wording (`question_kind`); it is an input to the model, never a score.
QUESTION_KINDS = {
    "EXPERIENCE": {"name": "경험",
                   "logic": "상황 → 판단(왜 그 방법을 택했는가, 검토한 대안이 있으면 함께) → 본인이 한 행동 → 결과와 그 확인. 결과나 그 확인이 없으면 4점 이하.",
                   "expertise": "실제로 쓴 기술·방법이 정확하고, 본인이 어떻게 적용했는지가 구체적인가.",
                   "others": "태도: 본인이 한 일과 판단을 자기 말로 분명히 말하는가. 전달력: 무엇을 했고 어떻게 됐는지가 앞에서 드러나는가."},
    "BEHAVIOR": {"name": "행동·가치관",
                 "logic": "입장(어떻게 행동하는가) → 그 이유가 되는 판단 기준 → 구체적인 행동 방식이나 실제 사례. 결과 검증과 대안 검토는 필수가 아니다.",
                 "expertise": "기술 지식을 묻지 않는 질문이다. 기술 용어가 없다고 낮추지 않고, 말한 행동이 지원 직무의 실제 업무 방식·역할과 맞게 이어지는지 본다. "
                              "직무와 무관한 일반론이면 3점, 직무 맥락에 정확히 이어지면 4~5점.",
                 "others": "태도: 자기 입장을 솔직하고 분명하게 밝히는가. 전달력: 입장이 먼저 나오고 이유·사례가 뒤따르는가."},
    "TECHNICAL": {"name": "기술 지식",
                  "logic": "개념(정의·답) → 그렇게 되는 이유나 원리 → 앞뒤 설명의 일관성(조건·비교가 앞의 설명과 맞는가). 경험 사례, 결과 검증, 대안 검토는 필수가 아니다. "
                           "정확한 정의와 이유가 한두 문장으로 모순 없이 이어지면 4~5점을 줄 수 있다.",
                  "expertise": "개념과 원리가 정확한가, 한계·조건을 아는가, 틀린 설명이 없는가가 중심이다.",
                  "others": "태도: 아는 것을 단정적으로 말하는가(모르는 부분을 모른다고 밝히는 것은 감점이 아니다). 전달력: 정의·답이 먼저 나오는가. 개인 경험 이야기가 없다고 낮추지 않는다."},
    "DESIGN": {"name": "설계",
               "logic": "요구사항·제약 파악 → 대안(둘 이상의 선택지, 또는 다른 방법을 배제한 이유) → 트레이드오프(무엇을 얻고 무엇을 포기하는가)와 선택 근거. "
                       "왜 그 구성을 골랐는지가 전혀 없으면 2점 이하. 검증·확장 방법은 있으면 좋지만 필수가 아니다.",
               "expertise": "구성 요소와 기술 선택이 요구에 맞는가, 규모·장애·보안 같은 조건을 정확하게 다루는가.",
               "others": "태도: 선택을 분명히 밝히고 그 한계도 인정하는가. 전달력: 결론(선택한 구성)이 먼저 나오고 근거가 뒤따르는가."},
    "SITUATION": {"name": "상황 대응",
                  "logic": "상황 파악 → 우선순위와 그 판단 근거 → 실행 단계 → 결과 확인 또는 실패 시 대안. 무엇을 할지만 말하고 왜 그렇게 하는지가 없으면 2점, 이유는 있으나 확인 방법이 없으면 3~4점.",
                  "expertise": "제시한 조치가 기술·업무적으로 타당하고 실제로 실행 가능한가.",
                  "others": "태도: 할 일을 주저 없이 정하는가. 전달력: 우선순위대로 순서 있게 말하는가."},
}
_DESIGN = re.compile(r"설계|아키텍처|구조를|구성하시|구성하겠|구축하시|시스템을 만든다면")
_PAST = re.compile(r"했던|하셨|했습니까|했나요|하였|경험|사례|있으신|있습니까|있나요|맡았|맡으셨")
_HYPOTHETICAL = re.compile(r"[라다]면|경우|상황에서|때 어떻게|때는 어떻게|하시겠|하겠습니까")


def question_kind(area: str | None, text: str, has_code: bool = False) -> str:
    """The kind of question from its area and wording (code, deterministic). Live coding is a knowledge question."""
    t = text or ""
    if has_code:
        return "TECHNICAL"
    if _DESIGN.search(t):
        return "DESIGN"
    if _PAST.search(t) and not re.search(r"[라다]면\s*어떻게", t):
        return "EXPERIENCE"
    if area not in ("JOB_SKILL", "PROJECT"):  # 인성·지원 동기·협업: how one acts and why, even when asked as "~할 때 어떻게"
        return "BEHAVIOR"
    if _HYPOTHETICAL.search(t):
        return "SITUATION"
    if area == "PROJECT":
        return "EXPERIENCE"
    return "TECHNICAL"


# How the candidate's level (app/questions/level.py career_level, from work periods only) reads every area. It changes what counts
# as experience and how much depth is expected, never the bar for correctness, and it is not a score.
LEVEL_VIEW = {
    "NEW": ("신입(실무 경력 1년 미만)", "학교·개인·팀 프로젝트, 인턴, 교육 과정의 경험도 실무 경험과 같은 무게로 본다. 운영 규모·실무 경력이 없다는 이유로 "
            "낮추지 않는다. 기초 개념을 정확히 설명하면 전문성이 높다."),
    "JUNIOR": ("주니어(경력 1~5년)", "실제 업무에서 본인이 맡은 부분과 적용 방법이 드러나는지 본다."),
    "SENIOR": ("경력자(5년 이상)", "적용 방법에 더해 규모·운영·책임 범위와 판단의 근거가 드러나는지 본다."),
}

# Words that only the reference cases bring in (the slow-API scene). A comment that names one the answer does not have took the
# example for the answer (rubric-v5, requirement 6): asked again once, then the comment is dropped (the score stays).
EXAMPLE_TERMS = ("인덱스", "풀 스캔", "실행 계획", "복합 인덱스", "주문 테이블", "주문 조회", "캐시", "응답 시간", "0.3초", "3초", "모니터링")


def render_kind(kind: str, level: str | None = None) -> str:
    k = QUESTION_KINDS.get(kind, QUESTION_KINDS["BEHAVIOR"])
    out = f"질문 유형: {k['name']}\n이 질문에서 보는 것\n- 논리성: {k['logic']}\n- 전문성: {k['expertise']}\n- {k['others']}"
    if level in LEVEL_VIEW:
        name, view = LEVEL_VIEW[level]
        out += f"\n지원자 수준: {name}. {view} 이 수준은 기준의 해석에만 쓰고, 점수를 일괄로 올리거나 내리지 않는다."
    return out


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
        lines += [f"  {s}점: {it['levels'][s]} (참고 사례: {it['examples'][s]})" for s in (5, 4, 3, 2, 1)]
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
    body = json.dumps({"version": RUBRIC_VERSION, "common": COMMON, "items": ITEMS, "sub": SUB_ITEMS, "star": STAR, "kinds": QUESTION_KINDS,
                       "levels": LEVEL_VIEW},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]
