import uuid
from collections.abc import AsyncIterator

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

PART_SIZE = 64 * 1024 * 1024  # 64 MiB: low part count for multi-gigabyte files


def client():
    cfg = settings()
    return aioboto3.Session().client(
        "s3",
        endpoint_url=cfg.s3_endpoint_url,
        region_name=cfg.s3_region,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
        config=Config(signature_version="s3v4"),
    )


async def ensure_bucket() -> None:
    async with client() as s3:
        try:
            await s3.head_bucket(Bucket=settings().s3_bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {
                "404",
                "NoSuchBucket",
                "NotFound",
            }:
                raise
            await s3.create_bucket(Bucket=settings().s3_bucket)


async def upload_stream(
    chunks: AsyncIterator[bytes], object_key: str, content_type: str | None
) -> None:
    """Multipart upload without buffering the Telegram file on the API/worker disk."""
    async with client() as s3:
        created = await s3.create_multipart_upload(
            Bucket=settings().s3_bucket,
            Key=object_key,
            **({"ContentType": content_type} if content_type else {}),
        )
        upload_id = created["UploadId"]
        parts: list[dict[str, object]] = []
        buffer = bytearray()
        part_number = 1
        try:
            async for chunk in chunks:
                buffer.extend(chunk)
                while len(buffer) >= PART_SIZE:
                    body = bytes(buffer[:PART_SIZE])
                    del buffer[:PART_SIZE]
                    result = await s3.upload_part(
                        Bucket=settings().s3_bucket,
                        Key=object_key,
                        UploadId=upload_id,
                        PartNumber=part_number,
                        Body=body,
                    )
                    parts.append({"ETag": result["ETag"], "PartNumber": part_number})
                    part_number += 1
            if buffer or not parts:
                result = await s3.upload_part(
                    Bucket=settings().s3_bucket,
                    Key=object_key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=bytes(buffer),
                )
                parts.append({"ETag": result["ETag"], "PartNumber": part_number})
            await s3.complete_multipart_upload(
                Bucket=settings().s3_bucket,
                Key=object_key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception:
            await s3.abort_multipart_upload(
                Bucket=settings().s3_bucket, Key=object_key, UploadId=upload_id
            )
            raise


async def signed_download(object_key: str, filename: str) -> str:
    async with client() as s3:
        return await s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings().s3_bucket,
                "Key": object_key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=3600,
        )


def new_object_key(batch_id: uuid.UUID, file_id: uuid.UUID, filename: str) -> str:
    # Never use the untrusted original name as a path component.
    suffix = filename.rsplit(".", 1)[-1][:16] if "." in filename else "bin"
    return f"batches/{batch_id}/{file_id}.{suffix}"
