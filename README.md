# Telegram File Links

Forward a file from Telegram to the bot and receive a private, expiring download link.

The service uses FastAPI, PostgreSQL, and S3-compatible object storage. A separate worker transfers each Telegram file to storage using a 64 MiB multipart stream. It does **not** buffer a multi-gigabyte file in RAM or make a temporary full-file copy.

## User flow

| What the user sends | Bot result |
| --- | --- |
| One forwarded attachment | One link to a download page containing that file |
| `/batch`, then several forwarded attachments, then `/finish` | One link to a download page containing all batch files |

The batch page provides a download link per file. That is deliberate: a 20 GB archive requires an expensive second copy and forces the recipient to download everything. A ZIP worker can be added later if required.

## Important Telegram requirement

Run a **local Telegram Bot API server**. Telegram's hosted Bot API can download files only up to 20 MB. The local server returns a local absolute `file_path` and allows unlimited downloads. Mount its file directory into the `worker` container at the same absolute path. Telegram itself limits an individual uploaded file to 2 GB, or 4 GB when the sender has Premium; total batch size can be much larger.

`docker-compose.yml` runs the local Bot API server as a dedicated container. Its persistent volume is mounted read-only in the transfer worker, so the worker can stream Telegram's local file paths into object storage.

## Setup

1. Copy `.env.example` to `.env` and replace every `change-me`/`replace-me` value.
2. Configure an S3-compatible bucket. Cloudflare R2, Backblaze B2, and AWS S3 are supported. The deployment Compose file is configured for an external storage provider and does not start a storage container.
3. Set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in `.env`. The Compose setup starts the local Bot API container and shares its file directory read-only with the worker. Keep `TELEGRAM_LOCAL_FILES=true`.
4. Set `APP_BASE_URL` to the public HTTPS URL of this service.
5. Start the application and worker:

   ```sh
   docker compose up --build
   ```

   For a no-domain test URL, start a Cloudflare Quick Tunnel too: `docker compose --profile quick-tunnel up -d`. Run `docker compose logs cloudflared` to find the generated `https://…trycloudflare.com` URL, set it as `APP_BASE_URL`, then restart `api` and `worker`.

6. Register the webhook after the app is publicly reachable:

   ```sh
   curl -X POST "$TELEGRAM_API_BASE_URL/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
     -d "url=$APP_BASE_URL/telegram/webhook" \
     -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
   ```

## Production checklist

- Put FastAPI behind HTTPS (for example, Caddy or a managed load balancer).
- Use long random secrets and a private S3 bucket. The app creates one-hour presigned object URLs only after the share page validates the batch token.
- Set a lifecycle policy in storage to delete objects after the same retention period as `DOWNLOAD_LINK_TTL_HOURS`; the current service expires access links but does not yet delete the objects.
- Use Alembic migrations before running more than one API/worker replica.
- Monitor disk on the local Bot API server and object-storage transfer failures.
