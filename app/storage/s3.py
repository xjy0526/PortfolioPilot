"""S3-compatible object storage for production deployments."""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote, unquote, urlparse

from app.storage.base import StoredObject


class S3CompatibleObjectStorage:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str = "",
        region: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
        force_path_style: bool = False,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        if client is not None:
            self.client = client
            return

        import boto3
        from botocore.config import Config

        kwargs: dict[str, Any] = {
            "config": Config(
                connect_timeout=3,
                read_timeout=3,
                retries={"max_attempts": 1, "mode": "standard"},
                s3={"addressing_style": "path" if force_path_style else "auto"},
            )
        }
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if region:
            kwargs["region_name"] = region
        if access_key_id:
            kwargs["aws_access_key_id"] = access_key_id
        if secret_access_key:
            kwargs["aws_secret_access_key"] = secret_access_key
        self.client = boto3.client("s3", **kwargs)

    async def put(self, key: str, content: bytes, *, content_type: str) -> str:
        stored = await self.put_object(key, content, content_type=content_type)
        return self._uri(stored.key)

    async def put_object(
        self,
        key: str,
        content: bytes,
        *,
        content_type: str,
    ) -> StoredObject:
        normalized = _safe_key(key)
        response = await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=normalized,
            Body=content,
            ContentType=content_type,
        )
        return StoredObject(
            key=normalized,
            checksum=hashlib.sha256(content).hexdigest(),
            content_type=content_type,
            version=str(response.get("VersionId") or ""),
        )

    async def get(self, uri: str) -> bytes:
        return await self.get_object(self._key_from_reference(uri))

    async def get_object(self, key: str, *, version: str = "") -> bytes:
        from botocore.exceptions import ClientError

        kwargs = {"Bucket": self.bucket, "Key": _safe_key(key)}
        if version:
            kwargs["VersionId"] = version

        def read() -> bytes:
            response = self.client.get_object(**kwargs)
            return response["Body"].read()

        try:
            return await asyncio.to_thread(read)
        except ClientError as exc:
            if _is_not_found(exc):
                raise FileNotFoundError(key) from exc
            raise

    async def delete(self, uri: str) -> None:
        await self.delete_object(self._key_from_reference(uri))

    async def delete_object(self, key: str, *, version: str = "") -> None:
        kwargs = {"Bucket": self.bucket, "Key": _safe_key(key)}
        if version:
            kwargs["VersionId"] = version
        await asyncio.to_thread(self.client.delete_object, **kwargs)

    async def exists(self, uri: str) -> bool:
        return await self.object_exists(self._key_from_reference(uri))

    async def object_exists(self, key: str, *, version: str = "") -> bool:
        from botocore.exceptions import ClientError

        kwargs = {"Bucket": self.bucket, "Key": _safe_key(key)}
        if version:
            kwargs["VersionId"] = version
        try:
            await asyncio.to_thread(self.client.head_object, **kwargs)
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise
        return True

    async def iter_keys(self, prefix: str = "") -> AsyncIterator[str]:
        continuation: str | None = None
        while True:
            kwargs: dict[str, object] = {
                "Bucket": self.bucket,
                "Prefix": _safe_key(prefix) if prefix else "",
            }
            if continuation:
                kwargs["ContinuationToken"] = continuation
            response = await asyncio.to_thread(self.client.list_objects_v2, **kwargs)
            for item in response.get("Contents", []):
                yield str(item["Key"])
            if not response.get("IsTruncated"):
                return
            continuation = str(response["NextContinuationToken"])

    async def iter_uris(self, prefix: str = "") -> AsyncIterator[str]:
        async for key in self.iter_keys(prefix):
            yield self._uri(key)

    async def check_access(self) -> None:
        await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)

    def _key(self, uri: str) -> str:
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or parsed.netloc != self.bucket:
            raise ValueError("Object URI does not belong to this S3 bucket")
        return _safe_key(unquote(parsed.path.lstrip("/")))

    def _key_from_reference(self, value: str) -> str:
        if value.startswith("s3://"):
            return self._key(value)
        return _safe_key(value)

    def _uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{quote(_safe_key(key), safe='/')}"


def _safe_key(value: str) -> str:
    normalized = str(value).strip().strip("/")
    if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError("Object key is empty or unsafe")
    return normalized


def _is_not_found(exc: Exception) -> bool:
    from botocore.exceptions import ClientError

    if not isinstance(exc, ClientError):
        return False
    status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return status == 404 or code in {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}
