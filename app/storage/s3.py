"""S3-compatible object storage for production deployments."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import quote, unquote, urlparse


class S3CompatibleObjectStorage:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str = "",
        region: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
    ) -> None:
        import boto3

        self.bucket = bucket
        kwargs: dict[str, str] = {}
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
        normalized = _safe_key(key)
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=normalized,
            Body=content,
            ContentType=content_type,
        )
        return f"s3://{self.bucket}/{quote(normalized, safe='/')}"

    async def get(self, uri: str) -> bytes:
        key = self._key(uri)

        def read() -> bytes:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()

        return await asyncio.to_thread(read)

    async def delete(self, uri: str) -> None:
        await asyncio.to_thread(
            self.client.delete_object,
            Bucket=self.bucket,
            Key=self._key(uri),
        )

    async def exists(self, uri: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            await asyncio.to_thread(
                self.client.head_object,
                Bucket=self.bucket,
                Key=self._key(uri),
            )
        except ClientError as exc:
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status == 404:
                return False
            raise
        return True

    async def iter_uris(self, prefix: str = "") -> AsyncIterator[str]:
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
                key = str(item["Key"])
                yield f"s3://{self.bucket}/{quote(key, safe='/')}"
            if not response.get("IsTruncated"):
                return
            continuation = str(response["NextContinuationToken"])

    def _key(self, uri: str) -> str:
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or parsed.netloc != self.bucket:
            raise ValueError("Object URI does not belong to this S3 bucket")
        return _safe_key(unquote(parsed.path.lstrip("/")))


def _safe_key(value: str) -> str:
    normalized = str(value).strip().strip("/")
    if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError("Object key is empty or unsafe")
    return normalized
