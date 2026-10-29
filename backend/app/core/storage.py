"""File storage: resumes (PDF), answer audio (FLAC), pre-synthesized question audio (PCM).

Keys are relative paths such as `resumes/<hex>.pdf`, `answers/<turn>.flac`, `tts/<question>.pcm`; the same keys go
into `file_deletions` (retention, withdrawal). Two backends:

  local  files under `data_dir` (development without containers, tests)
  s3     a MinIO bucket (deploy/docker-compose.yml). Objects are encrypted at rest by the server (SSE-S3 with the
         MinIO KMS key, set as the bucket default by `ensure_ready`), and the endpoint must be private (SEC-06).

The MinIO SDK is synchronous; calls run in a worker thread so the event loop (WebRTC) is never blocked.
"""
import asyncio
import io
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    async def get(self, key: str) -> bytes | None: ...
    async def delete(self, key: str) -> None: ...
    async def copy(self, src: str, dst: str) -> None: ...


def _safe(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
        raise ValueError(f"bad storage key: {key!r}")
    return key


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        return self.root / _safe(key)

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, data)

    async def get(self, key: str) -> bytes | None:
        p = self._path(key)
        return await asyncio.to_thread(p.read_bytes) if p.is_file() else None

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def copy(self, src: str, dst: str) -> None:
        data = await self.get(src)
        if data is None:
            raise FileNotFoundError(src)
        await self.put(dst, data)


class S3Storage:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, *, secure: bool = False,
                 encrypt: bool = True) -> None:
        from minio import Minio

        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self.bucket, self.encrypt = bucket, encrypt

    def _ensure(self) -> None:
        from minio.sseconfig import Rule, SSEConfig

        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
        if self.encrypt:
            self.client.set_bucket_encryption(self.bucket, SSEConfig(Rule.new_sse_s3_rule()))

    async def ensure_ready(self) -> None:
        await asyncio.to_thread(self._ensure)

    def _sse(self):
        from minio.sse import SseS3

        return SseS3() if self.encrypt else None

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await asyncio.to_thread(self.client.put_object, self.bucket, _safe(key), io.BytesIO(data), len(data),
                                content_type=content_type, sse=self._sse())

    async def get(self, key: str) -> bytes | None:
        from minio.error import S3Error

        def read() -> bytes | None:
            try:
                resp = self.client.get_object(self.bucket, _safe(key))
            except S3Error as e:
                if e.code in ("NoSuchKey", "NoSuchObject"):
                    return None
                raise
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        return await asyncio.to_thread(read)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.remove_object, self.bucket, _safe(key))  # missing key: no error

    async def copy(self, src: str, dst: str) -> None:
        from minio.commonconfig import CopySource

        await asyncio.to_thread(self.client.copy_object, self.bucket, _safe(dst), CopySource(self.bucket, _safe(src)),
                                sse=self._sse())


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "s3":
            _storage = S3Storage(s.s3_endpoint, s.s3_access_key.get_secret_value(), s.s3_secret_key.get_secret_value(),
                                 s.s3_bucket, secure=s.s3_secure, encrypt=s.s3_encrypt)
        else:
            _storage = LocalStorage(Path(s.data_dir))
    return _storage


def set_storage(storage: Storage | None) -> None:
    """Tests swap the backend (None resets to the configured one)."""
    global _storage
    _storage = storage


async def ensure_ready() -> None:
    """Startup: create the bucket and its default encryption (no-op for local files)."""
    st = get_storage()
    if isinstance(st, S3Storage):
        await st.ensure_ready()
