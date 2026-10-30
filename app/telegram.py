from typing import Any

import httpx

from app.config import settings


async def telegram(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings().telegram_bot_url}/{method}", json=payload
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
    if not response.is_success:
        # Do not call raise_for_status here: httpx includes the bot-token URL in its error text.
        raise RuntimeError(
            f"Telegram {method} failed: {body.get('description', f'HTTP {response.status_code}')}"
        )
    if not body.get("ok"):
        raise RuntimeError(body.get("description", "Telegram request failed"))
    return body["result"]


async def send_message(
    chat_id: int, text: str, keyboard: list[list[dict[str, str]]] | None = None
) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    await telegram("sendMessage", payload)


async def get_file_path(file_id: str) -> str:
    result = await telegram("getFile", {"file_id": file_id})
    path = result.get("file_path")
    if not path:
        raise RuntimeError("Telegram did not return a file path")
    return path
