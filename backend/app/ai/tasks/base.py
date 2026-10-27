"""Task framework: a TaskSpec owns its prompt, output schema and validator; `run_task` drives the
format-retry / regeneration loop against any `LLMCall` (the real gateway in app.aisvc, or a fake).

Retry budget (TRD 05 §5.3):
- format: JSON parse / schema / range errors are re-asked with the error appended (EVL-03), up to `format_retries`
- regen: validator findings (quote mismatch, P-8, overlap ...) are re-asked once per kind (`regen_limits`), then the
  validator runs in `final` mode and applies the fallback (근거 부족, sentence removal, ...)
"""
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from app.ai.textrules import FENCE_RULE, new_nonce

LLMCall = Callable[[list[dict], dict, dict], Awaitable[tuple[str, dict]]]


class TaskInvalid(Exception):
    """The output cannot be repaired: the call ends with status INVALID."""


@dataclass
class Check:
    output: dict | None = None
    feedback: dict[str, str] = field(default_factory=dict)  # kind -> message asking for a regeneration
    flags: dict = field(default_factory=dict)


@dataclass
class Outcome:
    status: str  # OK | INVALID
    output: dict
    attempts: dict
    flags: dict
    usage: dict
    error: str | None = None


def ollama_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema with $refs inlined and length/title noise removed (Ollama `format` grammar)."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items()
                    if k not in ("title", "maxLength", "minLength", "maxItems", "minItems", "description", "default")}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(raw)


class TaskSpec:
    name: ClassVar[str]
    version: ClassVar[str]
    lane: ClassVar[str]
    temperature: ClassVar[float] = 0.0
    num_predict: ClassVar[int] = 500
    format_retries: ClassVar[int] = 2
    regen_limits: ClassVar[dict[str, int]] = {}
    out_model: ClassVar[type[BaseModel]]
    system: ClassVar[str]

    # ── hooks ──
    def preflight(self, inp: dict) -> tuple[dict, dict] | None:
        """Rule-based (output, flags) without calling the LLM (e.g. 답변 없음 / 매우 짧은 답변)."""
        return None

    def split(self, inp: dict) -> list[dict]:
        return [inp]

    def merge(self, outputs: list[dict], flags: list[dict]) -> dict:
        return outputs[0]

    def build(self, inp: dict, nonce: str) -> tuple[str, dict]:
        raise NotImplementedError

    def check(self, inp: dict, parsed: BaseModel, ctx: dict, final: bool) -> Check:
        return Check(output=parsed.model_dump())

    # ── derived ──
    def system_prompt(self) -> str:
        return f"{self.system}\n\n{FENCE_RULE}\n출력은 지정된 JSON 형식만 쓴다. 합격·불합격을 판단하거나 추천하지 않는다."

    def schema(self) -> dict:
        return ollama_schema(self.out_model)

    def options(self) -> dict:
        return {"temperature": self.temperature, "num_predict": self.num_predict, "seed": 42}

    def prompt_hash(self) -> str:
        body = json.dumps({"system": self.system_prompt(), "schema": self.schema()}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()[:16]

    @property
    def full_version(self) -> str:
        return f"{self.version}+{self.prompt_hash()}"


def _add_usage(total: dict, usage: dict) -> None:
    for k, v in (usage or {}).items():
        if isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + v


async def run_task(spec: TaskSpec, inp: dict, call: LLMCall) -> Outcome:
    pre = spec.preflight(inp)
    if pre is not None:
        out, pre_flags = pre
        return Outcome("OK", out, {"format": 0, "regen": 0, "llm_calls": 0}, pre_flags, {})
    usage: dict = {}
    attempts = {"format": 0, "regen": 0, "llm_calls": 0}
    outputs, flag_list = [], []
    for part in spec.split(inp):
        out = await _run_one(spec, part, call, usage, attempts)
        if out.status != "OK":
            return Outcome(out.status, {}, attempts, out.flags, usage, out.error)
        outputs.append(out.output)
        flag_list.append(out.flags)
    flags: dict = {}
    for f in flag_list:
        for k, v in f.items():
            flags[k] = (flags.get(k, 0) + v) if isinstance(v, int) and not isinstance(v, bool) else (flags.get(k) or v)
    return Outcome("OK", spec.merge(outputs, flag_list), attempts, flags, usage)


async def _run_one(spec: TaskSpec, inp: dict, call: LLMCall, usage: dict, attempts: dict) -> Outcome:
    nonce = new_nonce()
    user, ctx = spec.build(inp, nonce)
    messages = [{"role": "system", "content": spec.system_prompt()}, {"role": "user", "content": user}]
    schema, options = spec.schema(), spec.options()
    fmt = 0
    regen: dict[str, int] = {}
    while True:
        content, u = await call(messages, schema, options)
        attempts["llm_calls"] += 1
        _add_usage(usage, u)
        try:
            parsed = spec.out_model.model_validate(json.loads(content))
        except (ValueError, ValidationError) as e:
            fmt += 1
            attempts["format"] += 1
            if fmt > spec.format_retries:
                return Outcome("INVALID", {}, attempts, {}, usage, f"format: {type(e).__name__}")
            messages += [{"role": "assistant", "content": content[:4000]},
                         {"role": "user", "content": "출력이 JSON 스키마·값 범위에 맞지 않는다. 오류: "
                                                     f"{_short_error(e)}. 같은 형식으로 다시 출력하라."}]
            continue
        try:
            chk = spec.check(inp, parsed, ctx, final=False)
            if chk.feedback:
                kinds = [k for k in chk.feedback if regen.get(k, 0) < spec.regen_limits.get(k, 0)]
                if kinds:
                    for k in kinds:
                        regen[k] = regen.get(k, 0) + 1
                    attempts["regen"] += 1
                    messages += [{"role": "assistant", "content": content[:4000]},
                                 {"role": "user", "content": "\n".join(chk.feedback[k] for k in chk.feedback)}]
                    continue
                chk = spec.check(inp, parsed, ctx, final=True)
        except TaskInvalid as e:
            return Outcome("INVALID", {}, attempts, {}, usage, str(e))
        if chk.output is None:
            return Outcome("INVALID", {}, attempts, chk.flags, usage, "validator rejected output")
        return Outcome("OK", chk.output, attempts, chk.flags, usage)


def _short_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['type']}" for err in e.errors()[:5])
    return type(e).__name__
