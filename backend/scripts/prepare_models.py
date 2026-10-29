"""Fills the read-only `models/` folder of the container stack (deploy/docker-compose.yml) on a PC with internet.

    ..\\.venv\\Scripts\\python scripts\\prepare_models.py --out ..\\models        (Windows, from backend/)

  models/llm/qwen3.5-4b.gguf        llm-rt / llm-eval   from the local Ollama store (an Ollama model blob is a GGUF)
  models/embed/bge-m3.gguf          embed               from the local Ollama store (`ollama pull bge-m3` first)
  models/hf/hub/...                 stt                 faster-whisper large-v3-turbo + small (copied from the
                                                        Hugging Face cache when present, otherwise downloaded)
  models/supertonic3/...            tts                 Supertonic (copied from ~/.cache/supertonic3 or downloaded)

The containers only read this folder (internal network, no downloads). Re-running skips what is already there.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

WHISPER = ("large-v3-turbo", "small")


def ollama_blob(ollama_dir: Path, name: str, tag: str) -> Path | None:
    """The GGUF layer of an Ollama model (registry.ollama.ai/library/<name>/<tag> manifest)."""
    manifest = ollama_dir / "manifests" / "registry.ollama.ai" / "library" / name / tag
    if not manifest.is_file():
        return None
    layers = json.loads(manifest.read_text(encoding="utf-8")).get("layers", [])
    model = next((x for x in layers if x.get("mediaType") == "application/vnd.ollama.image.model"), None)
    if model is None:
        return None
    blob = ollama_dir / "blobs" / model["digest"].replace(":", "-")
    return blob if blob.is_file() else None


def copy_file(src: Path, dst: Path) -> None:
    if dst.is_file() and dst.stat().st_size == src.stat().st_size:
        print(f"  ok      {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"  copy    {src} -> {dst} ({src.stat().st_size / 2**30:.2f} GiB)")
    shutil.copyfile(src, dst)


def gguf_from_ollama(ollama_dir: Path, spec: str, dst: Path) -> bool:
    name, _, tag = spec.partition(":")
    blob = ollama_blob(ollama_dir, name, tag or "latest")
    if blob is None:
        print(f"  MISSING {spec} in {ollama_dir} -> run `ollama pull {spec}` and re-run this script")
        return False
    with blob.open("rb") as f:
        if f.read(4) != b"GGUF":
            print(f"  NOT GGUF {blob} (spec {spec})")
            return False
    copy_file(blob, dst)
    return True


def whisper(out: Path) -> bool:
    os.environ.pop("HF_HUB_OFFLINE", None)
    from faster_whisper.utils import _MODELS, download_model

    hub = out / "hf" / "hub"
    user_hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    ok = True
    for size in WHISPER:
        repo = _MODELS.get(size, size)
        folder = "models--" + repo.replace("/", "--")
        if (hub / folder).is_dir():
            print(f"  ok      {hub / folder}")
            continue
        if (user_hub / folder).is_dir():
            print(f"  copy    {user_hub / folder} -> {hub / folder}")
            shutil.copytree(user_hub / folder, hub / folder, symlinks=False)
            continue
        print(f"  download faster-whisper {size} ({repo})")
        try:
            download_model(size, cache_dir=str(hub))
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED  {size}: {type(e).__name__}: {e}")
            ok = False
    return ok


def supertonic(out: Path) -> bool:
    dst = out / "supertonic3"
    if dst.is_dir() and any(dst.iterdir()):
        print(f"  ok      {dst}")
        return True
    src = Path(os.environ.get("SUPERTONIC_CACHE_DIR", Path.home() / ".cache" / "supertonic3"))
    if src.is_dir() and any(src.iterdir()):
        print(f"  copy    {src} -> {dst}")
        shutil.copytree(src, dst)
        return True
    print("  download Supertonic")
    try:
        from supertonic.loader import download_model

        download_model(dst)
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED  Supertonic: {type(e).__name__}: {e}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "models"))
    ap.add_argument("--ollama-dir", default=os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama" / "models")))
    ap.add_argument("--llm", default="qwen3.5:4b")
    ap.add_argument("--embed", default="bge-m3")
    a = ap.parse_args()
    out, ollama_dir = Path(a.out).resolve(), Path(a.ollama_dir)
    print(f"models folder: {out}\nOllama store:  {ollama_dir}")
    results = {
        "llm": gguf_from_ollama(ollama_dir, a.llm, out / "llm" / "qwen3.5-4b.gguf"),
        "embed": gguf_from_ollama(ollama_dir, a.embed, out / "embed" / "bge-m3.gguf"),
        "stt": whisper(out),
        "tts": supertonic(out),
    }
    print("\n" + "  ".join(f"{k}={'OK' if v else 'MISSING'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
