"""Encrypted original files in the isolated local-development namespace."""
from starlette.concurrency import run_in_threadpool
from auth.local_dev import object_path


def storage_path(user_id: str, document_id: str, version_id: str) -> str:
    return f"{user_id}/{document_id}/{version_id}.enc"


async def upload_encrypted(path: str, data: bytes) -> None:
    def save():
        target = object_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
    await run_in_threadpool(save)


async def download_encrypted(path: str) -> bytes:
    return await run_in_threadpool(object_path(path).read_bytes)


async def delete_from_storage(path: str) -> None:
    await run_in_threadpool(object_path(path).unlink, missing_ok=True)
