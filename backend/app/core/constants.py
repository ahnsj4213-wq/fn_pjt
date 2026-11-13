"""Values fixed by the PRD. They are code constants, not settings, so they cannot drift silently.

Every value cites its PRD source; tests/test_constants.py pins them.
"""
from enum import StrEnum

# Interview composition (screen spec SC-A06, SC-R07; replaces the fixed 10-slot order of PRD 6.1).
# A posting's settings hold a question plan of 1~10 slots [{area, text}]; an empty text is filled by the bank/AI.
MAIN_QUESTION_MAX = 10


class Area(StrEnum):
    PERSONALITY = "PERSONALITY"      # 인성
    MOTIVATION = "MOTIVATION"        # 지원 동기
    JOB_SKILL = "JOB_SKILL"          # 직무 기술
    PROJECT = "PROJECT"              # 프로젝트 경험
    COLLABORATION = "COLLABORATION"  # 협업


INTRO_QUESTION = "1분 정도로 간단하게 자기소개를 해 주십시오."
# Default ten questions (2026-11-04): 인성 2 (the first is the self-introduction), 지원 동기 1, 직무 3 (the first is live coding in
# 개발·데이터·AI), 경험 3 (asked from the resume), 협업 1. A posting can set its own plan of 1~10 (SC-R07).
DEFAULT_PLAN: tuple[tuple[Area, str | None], ...] = (
    (Area.PERSONALITY, INTRO_QUESTION), (Area.PERSONALITY, None), (Area.MOTIVATION, None),
    (Area.JOB_SKILL, None), (Area.JOB_SKILL, None), (Area.JOB_SKILL, None),
    (Area.PROJECT, None), (Area.PROJECT, None), (Area.PROJECT, None),
    (Area.COLLABORATION, None),
)
# The first job question is live coding in these families (SC-A06); elsewhere it is an ordinary spoken question.

# Timing (SC-A06, SC-R07). The main answer limit and think time come from the posting settings.
THINK_SEC_CHOICES = (5, 10, 20, 30)
ANSWER_SEC_CHOICES = (30, 60, 90, 120)
DEFAULT_THINK_SEC = 10
DEFAULT_ANSWER_SEC = 60
FOLLOWUP_THINK_SEC = 5
FOLLOWUP_ANSWER_LIMIT_SEC = 45
CODE_ANSWER_LIMIT_SEC = 180
WARNING_REMAINING_SEC = 10
REPLAY_MAX = 1  # 질문 다시 듣기: once per question
# The interviewer interjects once when a speech answer is still going at 2/3 of its limit; then at most 30 s remain.
INTERJECT_AT_FRACTION = 2 / 3
INTERJECT_REMAINING_SEC = 30
INTERJECT_TEXT = "말씀 중에 죄송합니다. 답변이 길어지고 있으니 핵심만 30초 안에 정리해 주십시오."
GAZE_SUSPECT_COUNT = 3  # 3 or more gaze warnings are recorded as suspected cheating (SC-A06)

# Follow-ups: at most 1 per main question; the total cap is a posting setting (0~4, default 2).
MAX_FOLLOWUPS_PER_MAIN = 1
MAX_FOLLOWUPS_TOTAL = 4
DEFAULT_FOLLOWUP_MAX = 2
FOLLOWUP_DECISION_TIMEOUT_SEC = 30.0  # ADR-0019: a guard against a stuck model, not a quality rule (a real answer takes 5~7 s)

# RES-02
RESUME_MAX_BYTES = 10 * 1024 * 1024

# USR-06
SESSION_IDLE_SECONDS = 2 * 60 * 60

# EVL-06 / 8.2: one automatic regeneration, 10-minute attempt limit
EVAL_MAX_ATTEMPTS = 2
EVAL_ATTEMPT_TIMEOUT_SEC = 10 * 60


class Criterion(StrEnum):
    EXPERTISE = "EXPERTISE"      # 직무 전문성
    LOGIC = "LOGIC"              # 논리성
    ATTITUDE = "ATTITUDE"        # 표현 및 태도
    DELIVERY = "DELIVERY"        # 전달력


CRITERIA: tuple[Criterion, ...] = tuple(Criterion)

# 6.4 default weights
DEFAULT_WEIGHTS_NO_CODING = {"expertise": 35, "logic": 25, "attitude": 20, "delivery": 20}
DEFAULT_WEIGHTS_CODING = {"expertise": 30, "logic": 20, "attitude": 15, "delivery": 15, "coding": 20}
WEIGHT_TOTAL = 100

# Fixed copy (exact PRD wording)
CAPACITY_FULL_MESSAGE = "이미 많은 면접자들이 면접 중입니다. 추후 다시 요청해주세요."  # INT-13
RECRUITER_AI_NOTICE = "AI 결과는 참고 자료이며 최종 결정은 담당자가 합니다"  # ADM-03
CANDIDATE_FEEDBACK_NOTICE = "이 피드백은 AI가 만든 참고 자료입니다"  # RPT-04

# Python is the only coding-test language in the MVP (X-10)
CODING_LANGUAGES = ("python",)

# Embedding size of BGE-M3 (and qwen3-embedding:0.6b); company_knowledge.embedding is vector(1024) (migration 0028).
EMBED_DIM = 1024
