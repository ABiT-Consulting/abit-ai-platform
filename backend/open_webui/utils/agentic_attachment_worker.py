"""Fixed parser entry point, run only inside the context presentation sandbox."""

import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import warnings


def render(data, page, offset):
    result = {'source_sha256': hashlib.sha256(data).hexdigest()}
    if data.startswith(b'%PDF-'):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or not 0 < len(reader.pages) <= 200 or page > len(reader.pages):
            raise ValueError('Unsupported PDF bounds')
        text = reader.pages[page - 1].extract_text() or ''
        if len(text) > 2_000_000 or offset > len(text):
            raise ValueError('PDF text bounds')
        result.update(
            kind='pdf_text',
            page=page,
            pages=len(reader.pages),
            text=text[offset : offset + 12000],
            text_offset=offset,
            next_text_offset=offset + 12000 if offset + 12000 < len(text) else None,
            truncated=offset + 12000 < len(text),
        )
    elif data.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')):
        from PIL import Image

        warnings.simplefilter('error', Image.DecompressionBombWarning)
        Image.MAX_IMAGE_PIXELS = 12_000_000
        original = Image.open(io.BytesIO(data))
        if original.format not in {'PNG', 'JPEG'} or getattr(original, 'n_frames', 1) != 1:
            raise ValueError('Unsupported image')
        original.verify()
        image = Image.open(io.BytesIO(data))
        dimensions = list(image.size)
        if image.width * image.height > 12_000_000:
            raise ValueError('Image pixel bounds')
        image = image.convert('RGB')
        image.thumbnail((1280, 1280))
        preview = b''
        for quality in (80, 65, 45):
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=quality, optimize=False)
            preview = output.getvalue()
            if len(preview) <= 98304:
                break
            image.thumbnail((640, 640))
        if not 0 < len(preview) <= 98304:
            raise ValueError('Image preview bounds')
        result.update(
            kind='image_preview',
            original_dimensions=dimensions,
            preview_dimensions=list(image.size),
            preview_mime='image/jpeg',
            preview_sha256=hashlib.sha256(preview).hexdigest(),
            preview_base64=base64.b64encode(preview).decode(),
            derived_preview=True,
        )
    else:
        text = data.decode('utf-8-sig')
        if '\x00' in text or offset > len(text) or page != 1:
            raise ValueError('Unsupported text bounds')
        result.update(
            kind='text',
            text=text[offset : offset + 12000],
            text_offset=offset,
            next_text_offset=offset + 12000 if offset + 12000 < len(text) else None,
            truncated=offset + 12000 < len(text),
        )
    return result


if __name__ == '__main__':
    sys.path.insert(0, '/parser')
    content = Path('/context/input').read_bytes()
    if not 0 < len(content) <= 2_000_000:
        raise SystemExit(65)
    print(json.dumps(render(content, int(sys.argv[1]), int(sys.argv[2])), ensure_ascii=False, separators=(',', ':')))
