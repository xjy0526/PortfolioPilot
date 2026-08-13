"""Object storage remains addressable across separate web and worker processes."""
from __future__ import annotations

import pytest

from app.storage.local import LocalObjectStorage


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
