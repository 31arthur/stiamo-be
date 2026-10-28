import asyncio
from pathlib import Path

from app.application.ports import FileStorage


class LocalFileStorage(FileStorage):
    """Files on a local disk (a mounted volume in production). Swap for object storage by implementing FileStorage."""

    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()

    def _resolve(self, path: str) -> Path:
        target = (self._root / path).resolve()
        if self._root not in target.parents:
            raise ValueError("path escapes the storage directory")
        return target

    async def put(self, path: str, data: bytes) -> None:
        def write() -> None:
            target = self._resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(target)  # never leave a half-written file under the real name

        await asyncio.to_thread(write)

    async def read(self, path: str) -> bytes:
        return await asyncio.to_thread(self._resolve(path).read_bytes)

    async def delete(self, path: str) -> None:
        await asyncio.to_thread(self._resolve(path).unlink, True)
