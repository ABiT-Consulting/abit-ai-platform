"""Validated, owner-scoped images for ABiT's explicit ChatGPT relay turns."""
import base64
import binascii
import hashlib
from pathlib import Path
import re
import secrets
import shutil

from fastapi import HTTPException

STAGING_ROOT = Path(r"C:\ProgramData\ABiT\OpenWebUI\relay-input-files")
DATA_IMAGE = re.compile(r"data:image/(png|jpeg|webp|gif);base64,([A-Za-z0-9+/]+={0,2})", re.I)
OWNED_FILE = re.compile(r"^/api/v1/files/[^/?#]+/content(?:[?#].*)?$")
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024


async def parse_explicit_user_turn(last, user):
    """Keep text and screenshots from the final user turn; reject task/tool parts."""
    if not isinstance(last, dict) or last.get('role') != 'user':
        raise HTTPException(400, 'Relay requires an explicit user message, not an automated task prompt.')
    content = last.get('content')
    images = []
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list) and 0 < len(content) <= 15:
        texts = []
        total = 0
        for part in content:
            if not isinstance(part, dict):
                raise HTTPException(400, 'Unsupported relay message part.')
            if part.get('type') == 'text' and isinstance(part.get('text'), str):
                texts.append(part['text'])
                continue
            if part.get('type') not in ('image_url', 'input_image'):
                raise HTTPException(400, 'Unsupported relay message part.')
            ref = part.get('image_url')
            url = ref.get('url') if isinstance(ref, dict) else ref
            if not isinstance(url, str):
                raise HTTPException(400, 'Invalid screenshot reference.')
            if OWNED_FILE.fullmatch(url):
                # File-id resolver validates current user's access rights.
                from open_webui.utils.files import get_image_base64_from_url
                url = await get_image_base64_from_url(url, user=user)
            elif not url.startswith('data:image/'):
                raise HTTPException(400, 'Only AIBIT-uploaded screenshots are accepted.')
            match = DATA_IMAGE.fullmatch(url) if isinstance(url, str) else None
            if not match or len(match.group(2)) > MAX_IMAGE_BYTES * 4 // 3 + 8:
                raise HTTPException(400, 'Unsupported image type or size.')
            try:
                data = base64.b64decode(match.group(2), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise HTTPException(400, 'Invalid screenshot data.') from exc
            kind = match.group(1).lower()
            signatures = {
                'png': data.startswith(b'\x89PNG\r\n\x1a\n'),
                'jpeg': data.startswith(b'\xff\xd8\xff'),
                'webp': data.startswith(b'RIFF') and data[8:12] == b'WEBP',
                'gif': data.startswith((b'GIF87a', b'GIF89a')),
            }
            if not signatures[kind] or len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(400, 'Invalid screenshot signature or size.')
            total += len(data)
            if len(images) >= 10 or total > MAX_TOTAL_BYTES:
                raise HTTPException(400, 'Limit is 10 screenshots (40 MB total).')
            images.append(('jpg' if kind == 'jpeg' else kind, data))
        text = '\n'.join(texts).strip()
    else:
        raise HTTPException(400, 'Relay requires explicit user text or screenshot parts.')
    if not text or len(text) > 100000:
        raise HTTPException(400, 'Relay requires nonempty text under 100,000 characters.')
    digest = hashlib.sha256(text.encode())
    for kind, data in images:
        digest.update(kind.encode('ascii'))
        digest.update(hashlib.sha256(data).digest())
    return text, images, digest.hexdigest()


def stage_images(images, job_id):
    """Only decoded images become scoped temporary files, never user paths."""
    if not images:
        return []
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    folder = STAGING_ROOT / (job_id + '-' + secrets.token_hex(8))
    folder.mkdir()
    result = []
    try:
        for index, (kind, data) in enumerate(images, 1):
            name = f'screenshot-{index:02d}.{kind}'
            target = folder / name
            with target.open('xb') as out:
                out.write(data)
            result.append({'name': name, 'path': str(target)})
        return result
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def cleanup_images(attachments, job_id):
    if not attachments:
        return
    folder = Path(attachments[0]['path']).parent
    if folder.parent.resolve() == STAGING_ROOT.resolve() and folder.name.startswith(job_id + '-'):
        shutil.rmtree(folder, ignore_errors=True)
