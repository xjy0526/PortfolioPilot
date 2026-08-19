"""Object storage remains addressable across separate web and worker processes."""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from app.core.principal import Principal
from app.services.research_knowledge import can_access_source_object
from app.storage.local import LocalObjectStorage
from app.storage.s3 import S3CompatibleObjectStorage


@pytest.mark.asyncio
async def test_web_and_worker_instances_share_only_a_durable_storage_uri(
    tmp_path, monkeypatch
):
    shared_root = tmp_path / "shared-object-storage"
    web_workdir = tmp_path / "web-container"
    worker_workdir = tmp_path / "worker-container"
    web_workdir.mkdir()
    worker_workdir.mkdir()

    monkeypatch.chdir(web_workdir)
    web_storage = LocalObjectStorage(shared_root, bucket="research")
    uri = await web_storage.put(
        "ingestion/2026/report.md",
        b"public research evidence",
        content_type="text/markdown",
    )

    monkeypatch.chdir(worker_workdir)
    worker_storage = LocalObjectStorage(shared_root, bucket="research")
    assert await worker_storage.exists(uri) is True
    assert await worker_storage.get(uri) == b"public research evidence"
    assert [item async for item in worker_storage.iter_uris("ingestion")] == [uri]

    await worker_storage.delete(uri)
    assert await web_storage.exists(uri) is False


@pytest.mark.asyncio
async def test_local_storage_rejects_cross_bucket_and_path_traversal(tmp_path):
    storage = LocalObjectStorage(tmp_path, bucket="research")
    with pytest.raises(ValueError, match="unsafe"):
        await storage.put("../secret.txt", b"secret", content_type="text/plain")
    with pytest.raises(ValueError, match="does not belong"):
        await storage.get("local://another-bucket/research.txt")


@pytest.mark.asyncio
async def test_local_development_storage_uses_object_keys(tmp_path):
    storage = LocalObjectStorage(tmp_path, bucket="research")
    stored = await storage.put_object(
        "ingestion/report.md",
        b"local evidence",
        content_type="text/markdown",
    )

    await storage.check_access()
    assert stored.key == "ingestion/report.md"
    assert stored.version == stored.checksum
    assert await storage.get_object(stored.key, version=stored.version) == b"local evidence"
    assert [item async for item in storage.iter_keys("ingestion")] == [stored.key]


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], bytes] = {}
        self.bucket_checks = 0

    def head_bucket(self, *, Bucket: str) -> dict[str, object]:
        self.bucket_checks += 1
        return {"Bucket": Bucket}

    def put_object(self, **kwargs):
        version = "version-1"
        self.objects[(kwargs["Bucket"], kwargs["Key"], version)] = kwargs["Body"]
        return {"VersionId": version}

    def get_object(self, **kwargs):
        version = kwargs.get("VersionId", "version-1")
        object_id = (kwargs["Bucket"], kwargs["Key"], version)
        if object_id not in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )
        content = self.objects[object_id]
        return {"Body": BytesIO(content)}

    def delete_object(self, **kwargs):
        version = kwargs.get("VersionId", "version-1")
        self.objects.pop((kwargs["Bucket"], kwargs["Key"], version), None)
        return {}

    def head_object(self, **kwargs):
        version = kwargs.get("VersionId", "version-1")
        if (kwargs["Bucket"], kwargs["Key"], version) not in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return {}

    def list_objects_v2(self, **kwargs):
        keys = sorted(
            key
            for bucket, key, _version in self.objects
            if bucket == kwargs["Bucket"] and key.startswith(kwargs.get("Prefix", ""))
        )
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}


@pytest.mark.asyncio
async def test_s3_mock_upload_read_delete_and_bucket_access():
    client = _FakeS3Client()
    storage = S3CompatibleObjectStorage(bucket="research", client=client)

    await storage.check_access()
    stored = await storage.put_object(
        "ingestion/report.md",
        b"shared evidence",
        content_type="text/markdown",
    )

    assert client.bucket_checks == 1
    assert stored.key == "ingestion/report.md"
    assert stored.version == "version-1"
    assert await storage.get_object(stored.key, version=stored.version) == b"shared evidence"
    assert await storage.object_exists(stored.key, version=stored.version) is True
    assert [item async for item in storage.iter_keys("ingestion")] == [stored.key]

    await storage.delete_object(stored.key, version=stored.version)
    assert await storage.object_exists(stored.key, version=stored.version) is False
    with pytest.raises(FileNotFoundError):
        await storage.get_object(stored.key, version=stored.version)


def test_source_object_permission_uses_server_principal():
    job = SimpleNamespace(
        object_owner="owner",
        object_permission_groups=["research-team"],
    )

    assert can_access_source_object(
        job,
        Principal("owner", frozenset({"public"}), authenticated=True),
    )
    assert can_access_source_object(
        job,
        Principal("reviewer", frozenset({"research-team"}), authenticated=True),
    )
    assert not can_access_source_object(
        job,
        Principal("outsider", frozenset({"public"}), authenticated=True),
    )
    assert not can_access_source_object(
        job,
        Principal("anonymous", frozenset({"research-team"}), authenticated=False),
    )
