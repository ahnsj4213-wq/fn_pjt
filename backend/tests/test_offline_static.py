"""AC-22 / SEC-06 static checks (TRD 07 §12-1~4). The decisive run is tools/offline/run.sh (egress-blocked E2E)."""
import json
import re
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.aisvc.offline import offline_env
from app.core.config import Settings, is_private_endpoint

REPO = Path(__file__).resolve().parents[2]

# Cloud AI SDKs that must never be a dependency (TRD 07 §12-2).
DENIED = {"openai", "anthropic", "google-generativeai", "google-genai", "google-cloud-aiplatform", "cohere",
          "mistralai", "langchain-openai", "langchain-anthropic", "groq", "replicate", "together", "@anthropic-ai/sdk",
          "@google/generative-ai", "@google/genai", "@mistralai/mistralai", "cohere-ai"}
_NAME = re.compile(r"^\s*([A-Za-z0-9_.\-@/]+)")


def _python_deps(pyproject: Path) -> set[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    proj = data.get("project", {})
    specs = list(proj.get("dependencies", []))
    for extra in proj.get("optional-dependencies", {}).values():
        specs += extra
    return {m.group(1).split("[")[0].lower() for s in specs if (m := _NAME.match(s))}


def test_ai_endpoints_must_be_local_or_private():
    for url in ("http://127.0.0.1:8200", "http://localhost:11434", "http://10.0.0.5:8200", "http://ai:8200",
                "http://192.168.1.20:8400"):
        assert is_private_endpoint(url), url
    for url in ("https://8.8.8.8/v1", "http://1.1.1.1:8200", "https://"):
        assert not is_private_endpoint(url), url
    with pytest.raises(ValidationError):
        Settings(ai_service_url="https://1.1.1.1/v1")
    with pytest.raises(ValidationError):
        Settings(ollama_url="http://8.8.8.8:11434")


def test_no_cloud_ai_sdk_in_dependencies():
    found = set()
    for pyproject in (REPO / "backend" / "pyproject.toml", REPO / "runner" / "pyproject.toml"):
        if pyproject.exists():
            found |= _python_deps(pyproject) & DENIED
    lock = json.loads((REPO / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    npm = {p.split("node_modules/")[-1] for p in lock.get("packages", {}) if p}
    found |= npm & DENIED
    assert not found, f"cloud AI SDKs must not be dependencies (SEC-06): {sorted(found)}"


_URL = re.compile(r"https?://([^/\s\"'`)<>\\:]+)")
_ALLOWED_HOST = re.compile(r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|[a-z][a-z0-9-]*)$")  # single label = compose service


def _source_files():
    for root, pattern in ((REPO / "backend" / "app", "*.py"), (REPO / "runner" / "runner", "*.py"),
                          (REPO / "frontend" / "src", "*.ts"), (REPO / "frontend" / "src", "*.tsx"),
                          (REPO / "frontend" / "src", "*.css"), (REPO / "frontend" / "index.html", "")):
        files = [root] if root.is_file() else root.rglob(pattern)
        for f in files:
            if ".test." not in f.name:
                yield f


def test_source_has_no_external_hosts():
    """CDNs, web fonts and hard-coded API hosts never ship (frontend/dist is also checked by `npm run check:offline`)."""
    hits = []
    for f in _source_files():
        for host in _URL.findall(f.read_text(encoding="utf-8", errors="replace")):
            if not _ALLOWED_HOST.match(host) and not host.startswith("{"):  # "{...}" = an f-string placeholder
                hits.append(f"{f.relative_to(REPO)}: {host}")
    assert not hits, hits


def test_webrtc_never_uses_ice_servers():
    """aiortc's default is Google STUN (10.2 s to first frame, measured); browsers get an empty list too (TRD 07 §8)."""
    server = (REPO / "backend" / "app" / "voice" / "session.py").read_text(encoding="utf-8")
    assert server.count("RTCPeerConnection(") == server.count("RTCPeerConnection(RTCConfiguration(iceServers=[]))") > 0
    for f in (REPO / "frontend" / "src").rglob("*.ts*"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"new RTCPeerConnection\(([^)]*)\)", text):
            assert "iceServers: []" in m.group(1), f"{f.name}: {m.group(0)}"


def test_model_libraries_run_offline_unless_download_is_allowed():
    env = offline_env(auto_download=False)
    assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1" and env["HF_HUB_DISABLE_TELEMETRY"] == "1"
    dev_first_run = offline_env(auto_download=True)
    assert "HF_HUB_OFFLINE" not in dev_first_run and dev_first_run["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert Settings().model_auto_download is False  # default = offline
