"""Validate automation attachments and prepare run-time model content.

References are local File IDs, never arbitrary URLs or raw client-provided content.
"""

import base64
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from open_webui.models.automations import AutomationAttachment
from open_webui.models.files import Files
from open_webui.utils.access_control.files import has_access_to_file
from open_webui.utils.files import get_image_base64_from_file_id

MAX_ATTACHMENTS = 10
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_CHARS = 50_000
MAX_TOTAL_DOCUMENT_CHARS = 125_000
IMAGE_TYPES = {"image/png", "image/jpeg"}
DOCUMENT_TYPES = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain", "text/x-markdown"},
    ".csv": {"text/csv", "text/plain", "application/vnd.ms-excel"},
    ".json": {"application/json", "text/plain"},
}


async def resolve_automation_attachments(attachments, user, db=None):
    """Recheck ownership, content type, processing and size on save and every run.

    Returns (user-message file references, vision images, extracted document text).
    """
    if len(attachments) > MAX_ATTACHMENTS:
        raise HTTPException(400, "Automations support at most 10 attachments")

    files, images, documents = [], [], []
    seen = set()
    total_document_chars = 0
    for attachment in attachments:
        reference = AutomationAttachment.model_validate(attachment)
        file_id = reference.id
        if file_id in seen:
            raise HTTPException(400, "Duplicate automation attachment")
        seen.add(file_id)

        file = await Files.get_file_by_id(file_id, db=db)
        if not file or (
            file.user_id != user.id
            and not await has_access_to_file(file_id, "read", user, db=db)
        ):
            raise HTTPException(404, "Automation attachment unavailable")

        meta = file.meta or {}
        content_type = str(meta.get("content_type") or "").lower()
        try:
            size = int(meta.get("size") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid automation attachment size")
        if not 0 < size <= MAX_FILE_BYTES:
            raise HTTPException(400, "Automation attachments must be between 1 byte and 10 MB")

        if content_type in IMAGE_TYPES:
            # Verify bytes, not only the upload's claimed MIME type.
            image = await get_image_base64_from_file_id(file_id, user=user)
            if not image:
                raise HTTPException(404, "Automation attachment unavailable")
            try:
                content = base64.b64decode(image.split(",", 1)[1], validate=True)
                if not 0 < len(content) <= MAX_FILE_BYTES:
                    raise ValueError("Image size limit exceeded")
                with Image.open(BytesIO(content)) as decoded:
                    if decoded.format not in {"PNG", "JPEG"}:
                        raise ValueError("Unsupported image format")
                    actual_type = Image.MIME[decoded.format]
                    decoded.verify()
                image = (
                    f"data:{actual_type};base64,"
                    + base64.b64encode(content).decode("ascii")
                )
            except Exception:
                raise HTTPException(400, "Invalid PNG or JPEG automation image")
            images.append(image)
            kind = "image"
        else:
            extension = Path(file.filename or "").suffix.lower()
            acceptable = DOCUMENT_TYPES.get(extension, set())
            if not acceptable or content_type not in (acceptable | {"", "application/octet-stream"}):
                raise HTTPException(
                    400, "Supported automation files: PDF, DOCX, XLSX, TXT, MD, CSV, JSON, PNG and JPEG"
                )
            data = file.data or {}
            contents = data.get("content")
            if data.get("status") != "completed" or not isinstance(contents, str) or not contents.strip():
                raise HTTPException(400, "Automation document processing is incomplete or failed")
            if len(contents) > MAX_DOCUMENT_CHARS:
                raise HTTPException(400, "Automation document text exceeds 50,000 characters")
            total_document_chars += len(contents)
            if total_document_chars > MAX_TOTAL_DOCUMENT_CHARS:
                raise HTTPException(400, "Automation document text exceeds 125,000 characters total")

            # Display only a sanitized filename; text comes from the server's file parser.
            safe_name = " ".join((file.filename or "Document").split())[:200]
            documents.append(f"[Attached file: {safe_name}]\n{contents}\n[/Attached file]")
            kind = "file"

        files.append({
            "id": file.id,
            "type": kind,
            "url": file.id,
            "name": file.filename,
            "content_type": content_type,
            "size": size,
            "status": "uploaded",
        })
    return files, images, documents


def automation_message_content(prompt, images, documents=None):
    """Keep text-only automations unchanged; attach parsed document text and images."""
    if documents:
        prompt += "\n\n" + "\n\n".join(documents)
    if not images:
        return prompt
    return [
        {"type": "text", "text": prompt},
        *[{"type": "image_url", "image_url": {"url": image}} for image in images],
    ]
