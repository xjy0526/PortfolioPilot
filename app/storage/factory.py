"""Object storage construction from validated application settings."""
from __future__ import annotations

from pathlib import Path

from app.storage.base import ObjectStorage
from app.storage.local import LocalObjectStorage
from app.storage.s3 import S3CompatibleObjectStorage
from config import BASE_DIR, Settings, settings


def build_object_storage(configuration: Settings = settings) -> ObjectStorage:
    if configuration.OBJECT_STORAGE_BACKEND == "s3":
        return S3CompatibleObjectStorage(
            bucket=configuration.OBJECT_STORAGE_BUCKET,
            endpoint_url=configuration.OBJECT_STORAGE_ENDPOINT_URL,
            region=configuration.OBJECT_STORAGE_REGION,
            access_key_id=configuration.OBJECT_STORAGE_ACCESS_KEY_ID,
            secret_access_key=configuration.OBJECT_STORAGE_SECRET_ACCESS_KEY,
        )
    root = Path(configuration.OBJECT_STORAGE_LOCAL_ROOT).expanduser()
    if not root.is_absolute():
        root = BASE_DIR / root
    return LocalObjectStorage(root, bucket=configuration.OBJECT_STORAGE_BUCKET)
