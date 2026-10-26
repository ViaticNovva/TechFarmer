"""Native-pixel image operations; previews alone are reduced in size."""
from __future__ import annotations

from io import BytesIO
import warnings
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from .safety import LIMITS, UserError, CropBudgetExceeded

FORMATS = {"PNG": ("image/png", {".png"}), "JPEG": ("image/jpeg", {".jpg", ".jpeg"}), "WEBP": ("image/webp", {".webp"})}


def decode(raw: bytes, mime: str | None = None, suffix: str | None = None, *, reference=False, generated=False) -> tuple[Image.Image, dict]:
    maximum = LIMITS.pixels * 5 if generated else LIMITS.reference_bytes if reference else LIMITS.image_bytes
    if not raw or len(raw) > maximum:
        raise UserError(f"Image must be nonempty and no larger than {maximum // (1024 * 1024)} MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as check:
                fmt, (width, height) = check.format, check.size
                if fmt not in FORMATS:
                    raise UserError("Only still PNG, JPEG and WebP images are supported.")
                expected_mime, suffixes = FORMATS[fmt]
                if (mime is not None and mime.lower() != expected_mime) or (suffix is not None and suffix.lower() not in suffixes):
                    raise UserError("Attachment filename, content type and actual image format must agree.")
                if width < 1 or height < 1 or max(width, height) > LIMITS.side or width * height > LIMITS.pixels:
                    raise UserError("Image exceeds the 20-megapixel or 16000-pixel side decode limit. Nothing was downscaled to bypass it.")
                if getattr(check, "n_frames", 1) != 1:
                    raise UserError("Animated images are not supported.")
                check.verify()
            with Image.open(BytesIO(raw)) as loaded:
                loaded.load()
                result = ImageOps.exif_transpose(loaded).convert("RGBA")
                result.info.clear()
                return result, {"format": fmt, "width": result.width, "height": result.height,
                                "encoded_width": width, "encoded_height": height,
                                "orientation": "EXIF-transposed pixel coordinates; original bytes preserved"}
    except UserError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError,
            OSError, ValueError, SyntaxError, OverflowError):
        raise UserError("Image is corrupt, unsupported or too large to decode safely.") from None


def png(image: Image.Image, byte_limit: int | None = None) -> bytes:
    class BoundedBuffer(BytesIO):
        def write(self, content):
            if byte_limit is not None and self.tell() + len(content) > byte_limit:
                raise CropBudgetExceeded("This page exceeds its native-crop encoding budget. No detections were discarded; reduce framing/context or raise the documented page_crop_mib budget if safe.")
            return super().write(content)
    with BoundedBuffer() as output:
        image.save(output, format="PNG", optimize=False)
        return output.getvalue()


def crop_native(page: Image.Image, box, shape: str = "rectangle") -> Image.Image:
    if len(box) != 4 or any(type(v) is not int for v in box):
        raise UserError("Crop requires an integer native-pixel xyxy rectangle.")
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= page.width and 0 <= y1 < y2 <= page.height):
        raise UserError("Crop rectangle is outside the original page.")
    if shape not in {"rectangle", "ellipse", "circle"}:
        raise UserError("Shape must be rectangle, ellipse or circle.")
    result = page.crop(box).convert("RGBA")
    if shape != "rectangle":
        if shape == "circle" and result.width != result.height:
            raise UserError("Circle crops require a square native-pixel box.")
        mask = Image.new("L", result.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, result.width - 1, result.height - 1), fill=255)
        from PIL import ImageChops
        result.putalpha(ImageChops.multiply(result.getchannel("A"), mask))
    result.info.clear()
    return result


def preview(image: Image.Image, max_side: int = 900) -> bytes:
    copy = image.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return png(copy)


def contact_sheet(items: list[tuple[str, Image.Image]], columns: int = 3) -> bytes:
    width, height = 270, 270
    count = max(1, len(items))
    sheet = Image.new("RGB", (columns * width, ((count + columns - 1) // columns) * height), "#17251f")
    draw = ImageDraw.Draw(sheet)
    for index, (title, source) in enumerate(items):
        x, y = (index % columns) * width, (index // columns) * height
        image = source.convert("RGBA")
        image.thumbnail((width - 16, height - 42), Image.Resampling.LANCZOS)
        sheet.paste(image, (x + (width - image.width) // 2, y + 6), image)
        draw.text((x + 8, y + height - 30), title[:35], fill="white")
    return png(sheet)


def suggest_panels(page: Image.Image) -> list[list[int]]:
    """White-gutter heuristic only; returned xyxy boxes need human review."""
    small = page.convert("RGBA")
    small.thumbnail((600, 600), Image.Resampling.BOX)
    width, height = small.size
    pixels = list(small.getdata())
    white = [a < 20 or (min(r, g, b) >= 240 and max(r, g, b) - min(r, g, b) < 16) for r, g, b, a in pixels]
    minimum = max(12, round(min(width, height) * 0.08))
    boxes = []

    def lines(box, axis):
        x, y, w, h = box
        count, across = (w, h) if axis == 0 else (h, w)
        result, start = [], None
        for k in range(count + 1):
            amount = sum(white[(y + j if axis == 0 else y + k) * width + (x + k if axis == 0 else x + j)] for j in range(across)) if k < count else 0
            blank = k < count and amount / across >= 0.985
            if blank and start is None:
                start = k
            if not blank and start is not None:
                result.append((start, k))
                start = None
        return result

    def split(box, depth=0):
        x, y, w, h = box
        if min(w, h) < minimum:
            return
        choices = [(float(b - a) / (w if axis == 0 else h), axis, a, b)
                   for axis in (0, 1) for a, b in lines(box, axis)
                   if b - a >= 2 and a >= minimum and (w if axis == 0 else h) - b >= minimum]
        if depth >= 5 or not choices:
            boxes.append(box)
            return
        _, axis, a, b = max(choices)
        if axis == 0:
            split((x, y, a, h), depth + 1)
            split((x + b, y, w - b, h), depth + 1)
        else:
            split((x, y, w, a), depth + 1)
            split((x, y + b, w, h - b), depth + 1)

    split((0, 0, width, height))
    if len(boxes) < 2:
        return []
    return [[round(x * page.width / width), round(y * page.height / height),
             round((x + w) * page.width / width), round((y + h) * page.height / height)]
            for x, y, w, h in sorted(boxes, key=lambda box: (box[1], box[0]))]
