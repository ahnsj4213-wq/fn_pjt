"""File storage backends (app/core/storage.py): local files and an S3/MinIO bucket with server-side encryption.
The S3 path runs against moto's S3 server (no MinIO needed); MinIO speaks the same API."""
import pytest

from app.core.storage import LocalStorage, S3Storage, set_storage
from tests.fixtures import text_pdf
from tests.helpers import candidate


@pytest.fixture(scope="module")
def s3_endpoint():
    moto_server = pytest.importorskip("moto.server")
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"{host}:{port}"
    server.stop()


async def _roundtrip(st) -> None:
    await st.put("resumes/a.pdf", b"%PDF-1.4 x", "application/pdf")
    assert await st.get("resumes/a.pdf") == b"%PDF-1.4 x"
    await st.copy("resumes/a.pdf", "resumes/b.pdf")
    assert await st.get("resumes/b.pdf") == b"%PDF-1.4 x"
    await st.delete("resumes/a.pdf")
    await st.delete("resumes/a.pdf")  # deleting twice is fine (file_gc retries)
    assert await st.get("resumes/a.pdf") is None
    for bad in ("../etc/passwd", "/abs", "a/../../b", "a\\b", ""):
        with pytest.raises(ValueError):
            await st.put(bad, b"x")


async def test_local_storage(tmp_path):
    await _roundtrip(LocalStorage(tmp_path))


async def test_s3_storage_encrypts_by_default(s3_endpoint):
    st = S3Storage(s3_endpoint, "testing", "testing", "aihire-test")
    await st.ensure_ready()
    await _roundtrip(st)
    enc = st.client.get_bucket_encryption("aihire-test")
    assert enc.rule.sse_algorithm == "AES256"  # SSE-S3 is the bucket default
    stat = st.client.stat_object("aihire-test", "resumes/b.pdf")
    assert stat.metadata.get("x-amz-server-side-encryption") == "AES256"


async def test_resume_upload_goes_to_the_bucket(s3_endpoint, make_client):
    st = S3Storage(s3_endpoint, "testing", "testing", "aihire-upload")
    await st.ensure_ready()
    set_storage(st)
    try:
        cand = make_client()
        await candidate(cand)
        pdf = text_pdf(["김지원 이력서", "커머스랩 백엔드 개발자 2020-2024", "주문 API 응답 지연 개선 경험이 있습니다."])
        r = await cand.post("/api/candidate/resume-files", files={"file": ("cv.pdf", pdf, "application/pdf")})
        assert r.status_code == 201, r.text
        keys = [o.object_name for o in st.client.list_objects("aihire-upload", prefix="resumes/")]
        assert len(keys) == 1 and await st.get(keys[0]) == pdf
    finally:
        set_storage(None)


async def test_files_the_root_worker_makes_belong_to_the_data_dirs_owner(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    from app.core.storage import LocalStorage

    owned = []
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: owned.append((str(path), uid, gid)), raising=False)
    store = LocalStorage(tmp_path)
    real = type(store.root).stat
    monkeypatch.setattr(type(store.root), "stat",
                        lambda self, **kw: SimpleNamespace(st_uid=10010, st_gid=10010) if self == store.root else real(self, **kw))
    await store.put("tts/q.pcm", b"x")
    assert [o[0].rsplit(os.sep, 1)[-1] for o in owned] == ["tts", "q.pcm"] and all(o[1:] == (10010, 10010) for o in owned)
