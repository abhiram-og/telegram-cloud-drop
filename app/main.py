from contextlib import asynccontextmanager
from datetime import UTC, datetime
from html import escape
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.db import Session, engine
from app.models import Base, Batch, BatchState, StoredFile
from app.services import add_file, close_batch, start_batch
from app.storage import signed_download
from app.telegram import send_message


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fine for an MVP. Replace with Alembic migrations before multi-instance production deploys.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="Telegram File Links", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


def attachment(message: dict) -> tuple[str, str, str | None, int | None] | None:
    for key in ("document", "video", "audio", "animation"):
        source = message.get(key)
        if source:
            return (
                source["file_id"],
                source.get("file_name") or f"telegram-{source['file_unique_id']}",
                source.get("mime_type"),
                source.get("file_size"),
            )
    return None


@app.post("/telegram/webhook")
async def webhook(
    request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)
):
    if x_telegram_bot_api_secret_token != settings().telegram_webhook_secret:
        raise HTTPException(401, "invalid Telegram webhook secret")
    update = await request.json()
    message = update.get("message") or update.get("channel_post")
    if not message:
        return {"ok": True}
    chat_id = message["chat"]["id"]
    command_parts = (message.get("text") or "").split()
    text = command_parts[0].lower() if command_parts else ""
    async with Session.begin() as session:
        if text == "/batch":
            batch = await start_batch(session, chat_id)
            await send_message(
                chat_id, "Batch started. Forward your files, then send /finish."
            )
            return {"ok": True, "batch": str(batch.id)}
        if text in {"/finish", "/done"}:
            batch = await close_batch(session, chat_id)
            if not batch:
                await send_message(chat_id, "No open batch. Send /batch first.")
            else:
                await send_message(
                    chat_id,
                    "Batch received. I’ll send one link when every file is ready.",
                )
            return {"ok": True}
        received = attachment(message)
        if not received:
            await send_message(
                chat_id,
                "Forward a document, video, audio, or animation. Use /batch and /finish for multiple files.",
            )
            return {"ok": True}
        file_id, name, mime, size = received
        try:
            batch = await add_file(session, chat_id, file_id, name, mime, size)
        except ValueError as exc:
            await send_message(chat_id, str(exc))
            return {"ok": True}
        await send_message(chat_id, f"Received {name}. Preparing your download link…")
    return {"ok": True, "batch": str(batch.id)}


async def public_batch(token: UUID) -> Batch:
    async with Session() as session:
        batch = await session.scalar(select(Batch).where(Batch.token == token))
        if (
            not batch
            or batch.state != BatchState.ready
            or not batch.expires_at
            or batch.expires_at < datetime.now(UTC)
        ):
            raise HTTPException(404, "This link is unavailable or has expired.")
        return batch


@app.get("/d/{token}", response_class=HTMLResponse)
async def download_page(token: UUID):
    batch = await public_batch(token)
    async with Session() as session:
        files = (
            await session.scalars(
                select(StoredFile)
                .where(StoredFile.batch_id == batch.id)
                .order_by(StoredFile.created_at)
            )
        ).all()
    rows = "".join(
        f'<li><a href="/d/{token}/files/{item.id}">{escape(item.original_name)}</a></li>'
        for item in files
    )
    return HTMLResponse(
        f"<!doctype html><title>Download files</title><h1>Download files</h1><p>{len(files)} file(s). Links expire with this share.</p><ul>{rows}</ul>"
    )


@app.get("/d/{token}/files/{file_id}")
async def download_file(token: UUID, file_id: UUID):
    batch = await public_batch(token)
    async with Session() as session:
        item = await session.scalar(
            select(StoredFile).where(
                StoredFile.id == file_id, StoredFile.batch_id == batch.id
            )
        )
    if not item or not item.object_key:
        raise HTTPException(404, "File unavailable")
    return RedirectResponse(
        await signed_download(item.object_key, item.original_name), status_code=307
    )
