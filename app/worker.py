import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select

from app.config import settings
from app.db import Session, engine
from app.models import Base, Batch, BatchState, FileState, StoredFile
from app.storage import ensure_bucket, new_object_key, upload_stream
from app.telegram import get_file_path, send_message


async def local_chunks(path: str) -> AsyncIterator[bytes]:
    handle = await asyncio.to_thread(open, path, "rb")
    try:
        while data := await asyncio.to_thread(handle.read, 8 * 1024 * 1024):
            yield data
    finally:
        await asyncio.to_thread(handle.close)


async def remote_chunks(file_path: str) -> AsyncIterator[bytes]:
    url = f"{settings().telegram_file_url}/{file_path.lstrip('/')}"
    async with (
        httpx.AsyncClient(timeout=None) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        async for part in response.aiter_bytes(8 * 1024 * 1024):
            yield part


async def claim_file() -> StoredFile | None:
    async with Session.begin() as session:
        item = await session.scalar(
            select(StoredFile)
            .where(StoredFile.state == FileState.pending)
            .order_by(StoredFile.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if item:
            item.state = FileState.transferring
            item.attempts += 1
            await session.flush()
            return item
    return None


async def mark_ready(item: StoredFile, object_key: str) -> None:
    async with Session.begin() as session:
        db_item = await session.get(StoredFile, item.id, with_for_update=True)
        assert db_item
        db_item.object_key, db_item.state, db_item.error = (
            object_key,
            FileState.ready,
            None,
        )


async def mark_failed(item: StoredFile, error: Exception) -> None:
    async with Session.begin() as session:
        db_item = await session.get(StoredFile, item.id, with_for_update=True)
        if db_item:
            db_item.state, db_item.error = FileState.failed, str(error)[:4000]


async def deliver_ready_batches() -> None:
    async with Session.begin() as session:
        batches = (
            await session.scalars(
                select(Batch)
                .where(Batch.state == BatchState.finalizing)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for batch in batches:
            states = (
                await session.scalars(
                    select(StoredFile.state).where(StoredFile.batch_id == batch.id)
                )
            ).all()
            if not states:
                continue
            if any(state == FileState.failed for state in states):
                batch.state = BatchState.failed
                await send_message(
                    batch.owner_chat_id,
                    "One or more files failed to transfer. Please forward the failed file again.",
                )
            elif all(state == FileState.ready for state in states):
                batch.state, batch.delivered_at = BatchState.ready, datetime.now(UTC)
                await send_message(
                    batch.owner_chat_id,
                    f"Your download link is ready: {settings().app_base_url.rstrip('/')}/d/{batch.token}",
                )


async def transfer(item: StoredFile) -> None:
    try:
        file_path = await get_file_path(item.telegram_file_id)
        object_key = new_object_key(item.batch_id, item.id, item.original_name)
        # Local Bot API returns an absolute path; mount that directory in this worker.
        chunks = (
            local_chunks(file_path)
            if settings().telegram_local_files and Path(file_path).is_absolute()
            else remote_chunks(file_path)
        )
        await upload_stream(chunks, object_key, item.mime_type)
        await mark_ready(item, object_key)
    except (BotoCoreError, ClientError, httpx.HTTPError, OSError, RuntimeError) as exc:
        await mark_failed(item, exc)


async def main() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ensure_bucket()
    while True:
        item = await claim_file()
        if item:
            await transfer(item)
        await deliver_ready_batches()
        await asyncio.sleep(0 if item else settings().worker_poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
