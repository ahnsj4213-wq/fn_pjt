"""Values fixed by the PRD. They are code constants, not settings, so they cannot drift silently.

Every value cites its PRD source; tests/test_constants.py pins them.
"""
from enum import StrEnum

# 6.1 Main question composition
MAIN_QUESTION_COUNT = 10


class Slot(StrEnum):
    PERSONALITY = "PERSONALITY"
    RESUME = "RESUME"
    COMPANY = "COMPANY"
    JOB = "JOB"


# Order is fixed: personality -> resume -> company -> job (6.1). Index 0 is seq 1.
SLOT_ORDER: tuple[Slot, ...] = (
    Slot.PERSONALITY, Slot.PERSONALITY,
    Slot.RESUME, Slot.RESUME,
    Slot.COMPANY, Slot.COMPANY,
    Slot.JOB, Slot.JOB, Slot.JOB, Slot.JOB,
)
CUSTOM_QUESTION_MAX = 1  # X-09: replaces one job question

# INT-06 / X-02 answer windows
MAIN_ANSWER_LIMIT_SEC = 60
FOLLOWUP_ANSWER_LIMIT_SEC = 45
WARNING_REMAINING_SEC = 10

# 6.2 follow-ups
MAX_FOLLOWUPS_PER_MAIN = 1
MAX_FOLLOWUPS_TOTAL = 4
FOLLOWUP_DECISION_TIMEOUT_SEC = 5.0

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
    SPECIFICITY = "SPECIFICITY"  # 구체성·관련성
    DELIVERY = "DELIVERY"        # 전달력


CRITERIA: tuple[Criterion, ...] = tuple(Criterion)

# 6.4 default weights
DEFAULT_WEIGHTS_NO_CODING = {"expertise": 35, "logic": 25, "specificity": 25, "delivery": 15}
DEFAULT_WEIGHTS_CODING = {"expertise": 30, "logic": 20, "specificity": 20, "delivery": 10, "coding": 20}
WEIGHT_TOTAL = 100

# Fixed copy (exact PRD wording)
CAPACITY_FULL_MESSAGE = "이미 많은 면접자들이 면접 중입니다. 추후 다시 요청해주세요."  # INT-13
RECRUITER_AI_NOTICE = "AI 결과는 참고 자료이며 최종 결정은 담당자가 합니다"  # ADM-03
CANDIDATE_FEEDBACK_NOTICE = "이 피드백은 AI가 만든 참고 자료입니다"  # RPT-04

# Python is the only coding-test language in the MVP (X-10)
CODING_LANGUAGES = ("python",)
