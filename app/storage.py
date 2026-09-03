from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from .config import settings


class LocalMediaStorage:
    """Durable local adapter used until the S3/MinIO storage phase.

    Route code deals exclusively in opaque keys, so replacing this with an S3
    adapter will not change the project or guide persistence contract.
    """

    def __init__(self, root: Path = settings.media_dir):
        self.root = root.resolve()

    def path_for(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("Invalid media key")
        return candidate

    async def put(self, key: str, content: bytes) -> None:
        target = self.path_for(key)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            temporary.write_bytes(content)
            temporary.replace(target)

        await asyncio.to_thread(write)

    async def delete(self, key: str) -> None:
        target = self.path_for(key)

        def remove() -> None:
            if target.exists():
                target.unlink()

        await asyncio.to_thread(remove)


media_storage = LocalMediaStorage()
