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
            bucket=configuration.object_storage_bucket,
            endpoint_url=configuration.s3_endpoint_url,
            region=configuration.s3_region,
            access_key_id=configuration.s3_access_key_id,
            secret_access_key=configuration.s3_secret_access_key,
            force_path_style=configuration.S3_FORCE_PATH_STYLE,
        )
    root = Path(configuration.OBJECT_STORAGE_LOCAL_ROOT).expanduser()
    if not root.is_absolute():
        root = BASE_DIR / root
    return LocalObjectStorage(root, bucket=configuration.object_storage_bucket)
