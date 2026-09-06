from __future__ import annotations

import io
import math
from collections.abc import Iterable
from dataclasses import dataclass

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter, Transformation

from app.core.errors import ApiError

RECEIPT_BUNDLE_FILENAME = "票据汇总.pdf"
_A4 = (595.2756, 841.8898)
_MARGIN = 18.0
_MAX_BUNDLE_PAGES = 500


@dataclass(frozen=True, slots=True)
class ReceiptBundleSource:
    file_name: str
    file_type: str
    content: bytes


def generate_receipt_bundle(sources: Iterable[ReceiptBundleSource], *, max_bytes: int) -> bytes:
    """Merge ordered evidence once, preserving PDF vectors and whole image bounds.

    Source ordering is decided from the immutable reimbursement snapshot. That
    snapshot includes each uploaded file ID once even when multiple rows refer
    to it. Distinct uploaded files are retained even if their bytes match.
    Page content is never rebuilt from OCR text.
    """

    writer = PdfWriter()
    try:
        for source in sources:
            try:
                if source.file_type.lower().removeprefix(".") == "pdf":
                    reader = PdfReader(io.BytesIO(source.content), strict=True)
                    if reader.is_encrypted or not reader.pages:
                        raise ValueError("encrypted or empty PDF")
                    if len(writer.pages) + len(reader.pages) > _MAX_BUNDLE_PAGES:
                        raise _too_many_pages()
                    for page in reader.pages:
                        dimensions = (float(page.mediabox.width), float(page.mediabox.height))
                        if any(not math.isfinite(value) or value <= 0 for value in dimensions):
                            raise ValueError("invalid PDF page dimensions")
                        # Preserve content streams, page dimensions and rotation. This
                        # also retains stamps/QR codes without rendering to an image.
                        writer.add_page(page)
                elif source.file_type.lower().removeprefix(".") in {"png", "jpg", "jpeg"}:
                    if len(writer.pages) >= _MAX_BUNDLE_PAGES:
                        raise _too_many_pages()
                    _append_image(writer, source.content)
                else:
                    raise ValueError("unsupported source format")
            except ApiError:
                raise
            except Exception as exc:
                raise ApiError(
                    "REIMBURSEMENT_BUNDLE_SOURCE_INVALID",
                    f"附件“{source.file_name}”无法合并为 PDF，请重新上传",
                    422,
                ) from exc
        if not writer.pages:
            raise ApiError("REIMBURSEMENT_BUNDLE_EMPTY", "请先上传票据或证明材料", 422)
        output = io.BytesIO()
        writer.write(output)
        if output.tell() > max_bytes:
            raise ApiError(
                "REIMBURSEMENT_BUNDLE_TOO_LARGE",
                "票据汇总 PDF 超过附件大小限制，请压缩上传图片后重试",
                413,
            )
        return output.getvalue()
    finally:
        writer.close()


def _append_image(writer: PdfWriter, content: bytes) -> None:
    with Image.open(io.BytesIO(content)) as original:
        oriented = ImageOps.exif_transpose(original)
        if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
            rgba = oriented.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
        else:
            image = oriented.convert("RGB")
        image_pdf = io.BytesIO()
        image.save(image_pdf, format="PDF", resolution=144.0, quality=95, subsampling=0)
        reader = PdfReader(io.BytesIO(image_pdf.getvalue()))
        source = reader.pages[0]
        width, height = _A4 if image.height >= image.width else tuple(reversed(_A4))
        scale = min(
            (width - 2 * _MARGIN) / float(source.mediabox.width),
            (height - 2 * _MARGIN) / float(source.mediabox.height),
        )
        x = (width - float(source.mediabox.width) * scale) / 2
        y = (height - float(source.mediabox.height) * scale) / 2
        target = writer.add_blank_page(width=width, height=height)
        target.merge_transformed_page(source, Transformation().scale(scale).translate(x, y))


def _too_many_pages() -> ApiError:
    return ApiError(
        "REIMBURSEMENT_BUNDLE_TOO_MANY_PAGES",
        f"票据汇总最多支持 {_MAX_BUNDLE_PAGES} 页，请减少附件页数",
        422,
    )
