from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Batch, BatchState, StoredFile


async def active_batch(session: AsyncSession, chat_id: int) -> Batch | None:
    return await session.scalar(
        select(Batch)
        .where(Batch.owner_chat_id == chat_id, Batch.state == BatchState.collecting)
        .order_by(Batch.created_at.desc())
        .limit(1)
    )


async def start_batch(session: AsyncSession, chat_id: int) -> Batch:
    existing = await active_batch(session, chat_id)
    if existing:
        return existing
    batch = Batch(owner_chat_id=chat_id)
    session.add(batch)
    await session.flush()
    return batch


async def close_batch(session: AsyncSession, chat_id: int) -> Batch | None:
    batch = await active_batch(session, chat_id)
    if not batch:
        return None
    batch.state = BatchState.finalizing
    batch.expires_at = datetime.now(UTC) + timedelta(
        hours=settings().download_link_ttl_hours
    )
    return batch


async def add_file(
    session: AsyncSession,
    chat_id: int,
    file_id: str,
    name: str,
    mime: str | None,
    size: int | None,
) -> Batch:
    batch = await active_batch(session, chat_id)
    single = batch is None
    if single:
        batch = await start_batch(session, chat_id)
    assert batch
    file_count = await session.scalar(
        select(func.count())
        .select_from(StoredFile)
        .where(StoredFile.batch_id == batch.id)
    )
    if file_count >= settings().max_batch_files:
        raise ValueError(
            f"A batch can contain at most {settings().max_batch_files} files"
        )
    # The worker marks one-file batches ready and delivers them; a user batch remains collecting until /finish.
    item = StoredFile(
        batch_id=batch.id,
        telegram_file_id=file_id,
        original_name=name,
        mime_type=mime,
        size_bytes=size,
    )
    session.add(item)
    if single:
        batch.state = BatchState.finalizing
        batch.expires_at = datetime.now(UTC) + timedelta(
            hours=settings().download_link_ttl_hours
        )
    return batch
