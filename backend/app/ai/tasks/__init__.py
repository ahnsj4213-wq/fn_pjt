"""Task registry (TRD 05 §6). Names are the `{task}` of `POST /v1/llm/{task}`."""
from app.ai.tasks.base import Check, LLMCall, Outcome, TaskInvalid, TaskSpec, run_task
from app.ai.tasks.evaluate import EvaluateAnswer
from app.ai.tasks.followup import FollowupDecide
from app.ai.tasks.questions import QuestionGenerate, ResumeQuestion
from app.ai.tasks.report import CandidateFeedback, CodeReview, ReportSummary
from app.ai.tasks.resume import ResumeExtract

TASKS: dict[str, TaskSpec] = {t.name: t for t in (
    ResumeExtract(), QuestionGenerate(), ResumeQuestion(), FollowupDecide(),
    EvaluateAnswer(), ReportSummary(), CandidateFeedback(), CodeReview())}

LANES = ("RT", "INTERACTIVE", "BATCH")

__all__ = ["TASKS", "LANES", "Check", "LLMCall", "Outcome", "TaskInvalid", "TaskSpec", "run_task"]
