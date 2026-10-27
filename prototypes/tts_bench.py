"""Spike: Supertonic (ONNX, CPU) Korean TTS speed for pre-generating main-question audio."""
import inspect, time
from pathlib import Path
import supertonic
from supertonic import TTS

OUT = Path(__file__).parent / "audio"
QUESTIONS = [
    "최근 프로젝트에서 성능 문제를 해결한 경험을 말씀해 주세요.",
    "이력서에 적힌 주문 처리 시스템 개선 프로젝트에서 본인이 맡은 역할과 결과를 구체적으로 설명해 주세요.",
    "팀원과 의견이 달랐을 때 어떻게 해결했는지 사례를 들어 말씀해 주세요.",
]
print("supertonic", getattr(supertonic, "__version__", "?"))
t0 = time.perf_counter(); tts = TTS(); print(f"load {time.perf_counter()-t0:.1f}s")
print("synthesize sig:", inspect.signature(tts.synthesize))
styles = [n for n in dir(tts) if "style" in n.lower()]
print("style attrs:", styles)
style = tts.get_voice_style(voice_name=tts.voice_style_names[0]) if hasattr(tts, "voice_style_names") else None
for i, q in enumerate(QUESTIONS):
    t1 = time.perf_counter()
    wav, dur = tts.synthesize(q, voice_style=style, lang="ko")
    el = time.perf_counter() - t1
    path = OUT / f"tts_supertonic_{i}.wav"; tts.save_audio(wav, str(path))
    d = float(dur[0]) if hasattr(dur, "__len__") else float(dur)
    print(f"q{i}: chars={len(q)} audio={d:.1f}s synth={el:.2f}s rtf={el/d:.3f} -> {path.name}")
