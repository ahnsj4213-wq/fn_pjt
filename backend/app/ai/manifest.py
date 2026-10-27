"""Version manifest (EVL-08, TRD 05 §11): everything that can change an AI result, hashed as canonical JSON."""
import hashlib
import json

from app.ai import rubric
from app.ai.tasks import TASKS
from app.ai.textrules import RULE_VERSIONS
from app.evaluation.quotes import RULE_VERSION as QUOTE_RULE_VERSION


def build_manifest(*, llm: dict, stt: dict, tts: dict, embed: dict) -> dict:
    return {
        "llm": llm,
        "prompts": {name: spec.full_version for name, spec in sorted(TASKS.items())},
        "task_options": {name: spec.options() for name, spec in sorted(TASKS.items())},
        "rubric": f"{rubric.RUBRIC_VERSION}+{rubric.rubric_hash()}",
        "rules": {"quote": QUOTE_RULE_VERSION, **RULE_VERSIONS},
        "stt": stt,
        "tts": tts,
        "embed": embed,
    }


def manifest_hash(body: dict) -> str:
    canon = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()
