import os
import re
import uuid
from io import BytesIO

from PIL import Image, UnidentifiedImageError


IMAGE_FORMATS = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class ImageError(ValueError):
    pass


def save_image(data: bytes, filename: str, upload_folder: str) -> str:
    """이미지인지 검증 후 uploads/<id>.<확장자> 로 저장하고 id 를 돌려준다 (주소는 /files/<id>)."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError("이미지는 10MB 이하만 올릴 수 있습니다.")
    try:
        with Image.open(BytesIO(data)) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageError("이미지 파일만 올릴 수 있습니다.") from None
    if fmt not in IMAGE_FORMATS:
        raise ImageError("png, jpg, gif, webp 형식만 지원합니다.")

    file_id = uuid.uuid4().hex
    with open(os.path.join(upload_folder, f"{file_id}.{IMAGE_FORMATS[fmt]}"), "wb") as f:
        f.write(data)
    return file_id


def find_upload(upload_folder: str, file_id: str) -> str | None:
    """id 로 저장된 파일 이름 찾기 (확장자는 저장할 때 정함)."""
    if not re.fullmatch(r"[0-9a-f]{32}", file_id or ""):
        return None
    for ext in IMAGE_FORMATS.values():
        if os.path.exists(os.path.join(upload_folder, f"{file_id}.{ext}")):
            return f"{file_id}.{ext}"
    return None


def alt_text(filename: str) -> str:
    """마크다운 대체텍스트용 이름: 확장자와 []() 문자 제거."""
    return re.sub(r"[\[\]()]", "", os.path.splitext(os.path.basename(filename or ""))[0]) or "image"
