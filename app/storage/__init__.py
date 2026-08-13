"""Object storage exports."""

from app.storage.base import ObjectStorage
from app.storage.factory import build_object_storage
from app.storage.local import LocalObjectStorage
from app.storage.s3 import S3CompatibleObjectStorage

__all__ = [
    "LocalObjectStorage",
    "ObjectStorage",
    "S3CompatibleObjectStorage",
    "build_object_storage",
]
