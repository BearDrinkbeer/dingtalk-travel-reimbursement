from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.errors import ApiError


@dataclass(frozen=True, slots=True)
class PdfLimits:
    render_dpi: int
    max_render_pixels: int
    max_dimension: int
    max_xobjects: int
    max_embedded_pixels: int
    max_content_streams: int = 512
    max_resource_depth: int = 8
    max_decoded_page_bytes: int = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PdfInspection:
    text: str
    layout_text: str
    render_width: int
    render_height: int
    embedded_images: int
    embedded_pixels: int
    page_count: int = 1


def _resolved(value: Any) -> Any:
    get_object = getattr(value, "get_object", None)
    return get_object() if callable(get_object) else value


def _raw_get(mapping: Any, key: str, default: Any = None) -> Any:
    raw_get = getattr(mapping, "raw_get", None)
    if callable(raw_get):
        try:
            return raw_get(key)
        except KeyError:
            return default
    return mapping.get(key, default)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _too_complex(message: str = "PDF 资源结构过于复杂") -> ApiError:
    return ApiError("PDF_TOO_COMPLEX", message, 400)


def _decoded_page_bytes(page: Any, limits: PdfLimits) -> int:
    """Decode bounded page content inside the resource-limited worker."""

    raw_contents = _raw_get(page, "/Contents")
    if raw_contents is None:
        return 0
    pending = [raw_contents]
    visited: set[tuple[object, ...]] = set()
    decoded = 0
    streams = 0
    while pending:
        raw = pending.pop()
        try:
            resolved = _resolved(raw)
        except (PdfReadError, OSError, TypeError, ValueError, KeyError, RecursionError) as exc:
            raise _too_complex("PDF 页面内容结构无效") from exc
        if isinstance(resolved, list | tuple):
            pending.extend(resolved)
            continue
        identity = _object_identity(raw, resolved)
        if identity in visited:
            continue
        visited.add(identity)
        get_data = getattr(resolved, "get_data", None)
        if not callable(get_data):
            raise _too_complex("PDF 页面内容结构无效")
        streams += 1
        if streams > limits.max_content_streams:
            raise _too_complex("PDF 页面内容流过多")
        try:
            data = get_data()
        except (PdfReadError, OSError, TypeError, ValueError, KeyError, MemoryError) as exc:
            raise _too_complex("PDF 页面内容解码失败") from exc
        decoded += len(data)
        if decoded > limits.max_decoded_page_bytes:
            raise _too_complex("PDF 页面解码内容超过限制")
    return decoded


def _object_identity(raw_object: Any, resolved_object: Any) -> tuple[object, ...]:
    """Prefer stable indirect identities; direct objects still need cycle protection."""

    idnum = getattr(raw_object, "idnum", None)
    generation = getattr(raw_object, "generation", None)
    pdf = getattr(raw_object, "pdf", None)
    if isinstance(idnum, int) and isinstance(generation, int):
        return ("indirect", id(pdf), idnum, generation)
    return ("direct", id(resolved_object))


@dataclass(slots=True)
class _ResourceInspection:
    limits: PdfLimits
    visited: set[tuple[object, ...]]
    active: set[tuple[object, ...]]
    xobjects: int = 0
    images: int = 0
    pixels: int = 0

    def inspect_resources(self, raw_resources: Any, *, depth: int) -> None:
        if raw_resources is None:
            return
        if depth > self.limits.max_resource_depth:
            raise _too_complex("PDF 资源嵌套层级超过限制")
        try:
            resources = _resolved(raw_resources)
        except (PdfReadError, OSError, TypeError, ValueError, KeyError, RecursionError) as exc:
            raise _too_complex("PDF 资源结构无效") from exc
        if not hasattr(resources, "get"):
            raise _too_complex("PDF 资源结构无效")
        raw_xobjects = _raw_get(resources, "/XObject")
        if raw_xobjects is None:
            return
        try:
            xobjects = _resolved(raw_xobjects)
        except (PdfReadError, OSError, TypeError, ValueError, KeyError, RecursionError) as exc:
            raise _too_complex("PDF 内嵌对象结构无效") from exc
        if not hasattr(xobjects, "values"):
            raise _too_complex("PDF 内嵌对象结构无效")
        try:
            values = list(xobjects.values())
        except (PdfReadError, OSError, TypeError, ValueError, KeyError, RecursionError) as exc:
            raise _too_complex("PDF 内嵌对象结构无效") from exc
        for raw_object in values:
            self.inspect_xobject(raw_object, depth=depth)

    def inspect_xobject(self, raw_object: Any, *, depth: int) -> None:
        if depth > self.limits.max_resource_depth:
            raise _too_complex("PDF 资源嵌套层级超过限制")
        try:
            pdf_object = _resolved(raw_object)
        except (TypeError, ValueError, KeyError, RecursionError) as exc:
            raise _too_complex("PDF 内嵌对象结构无效") from exc
        if not hasattr(pdf_object, "get"):
            raise _too_complex("PDF 内嵌对象结构无效")

        identity = _object_identity(raw_object, pdf_object)
        if identity in self.active:
            raise _too_complex("PDF 资源包含循环引用")
        if identity in self.visited:
            return
        self.visited.add(identity)
        self.active.add(identity)
        self.xobjects += 1
        if self.xobjects > self.limits.max_xobjects:
            raise _too_complex("PDF 内嵌对象过多")
        try:
            subtype = str(pdf_object.get("/Subtype", ""))
            if subtype == "/Image":
                self._inspect_image(pdf_object, depth=depth)
            elif subtype == "/Form":
                resources = _raw_get(pdf_object, "/Resources")
                if resources is None:
                    raise _too_complex("PDF 表单资源结构无效")
                self.inspect_resources(resources, depth=depth + 1)
        finally:
            self.active.discard(identity)

    def _inspect_image(self, pdf_object: Any, *, depth: int) -> None:
        width = _positive_int(pdf_object.get("/Width"))
        height = _positive_int(pdf_object.get("/Height"))
        if width is None or height is None:
            raise _too_complex("PDF 内嵌图片尺寸无效")
        self.images += 1
        if width > self.limits.max_dimension or height > self.limits.max_dimension:
            raise ApiError("PDF_PAGE_TOO_LARGE", "PDF 内嵌图片尺寸超过限制", 400)
        self.pixels += width * height
        if self.pixels > self.limits.max_embedded_pixels:
            raise _too_complex("PDF 内嵌图片总像素超过限制")

        # Soft masks and explicit image masks are image XObjects too. A colour-key
        # mask is an array and deliberately does not enter resource traversal.
        for key in ("/SMask", "/Mask"):
            raw_mask = _raw_get(pdf_object, key)
            if raw_mask is None:
                continue
            try:
                mask = _resolved(raw_mask)
            except (PdfReadError, OSError, TypeError, ValueError, KeyError, RecursionError) as exc:
                raise _too_complex("PDF 图片遮罩结构无效") from exc
            if hasattr(mask, "get"):
                self.inspect_xobject(raw_mask, depth=depth + 1)
            elif key == "/SMask":
                raise _too_complex("PDF 图片遮罩结构无效")


def inspect_single_page_pdf(
    path: Path,
    limits: PdfLimits,
    *,
    extract_text: bool,
    max_pages: int = 1,
) -> PdfInspection:
    """Validate cheap PDF metadata without decoding image streams."""

    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise ApiError("ENCRYPTED_PDF_UNSUPPORTED", "不支持加密 PDF", 400)
        page_count = len(reader.pages)
        if page_count < 1 or page_count > max_pages:
            code = "MULTI_PAGE_PDF_UNSUPPORTED" if page_count > 1 else "INVALID_PDF"
            message = "每个 PDF 必须只包含一张票据" if page_count > 1 else "PDF 没有有效页面"
            if max_pages > 1 and page_count > max_pages:
                code, message = "PDF_PAGE_LIMIT_EXCEEDED", f"行程单 PDF 最多支持 {max_pages} 页"
            raise ApiError(code, message, 400)
        page = reader.pages[0]
        width_points = float(page.mediabox.width)
        height_points = float(page.mediabox.height)
        if width_points <= 0 or height_points <= 0:
            raise ApiError("INVALID_PDF", "PDF 页面尺寸无效", 400)
        render_width = max(1, round(width_points * limits.render_dpi / 72))
        render_height = max(1, round(height_points * limits.render_dpi / 72))
        if (
            render_width > limits.max_dimension
            or render_height > limits.max_dimension
            or render_width * render_height > limits.max_render_pixels
        ):
            raise ApiError("PDF_PAGE_TOO_LARGE", "PDF 页面渲染尺寸超过限制", 400)

        resources = _raw_get(page, "/Resources")
        _decoded_page_bytes(page, limits)
        resource_inspection = _ResourceInspection(limits, set(), set())
        resource_inspection.inspect_resources(resources, depth=0)

        # Supporting itineraries may span pages, but every page is checked
        # before storage/printing; OCR itself retains the one-receipt limit.
        for extra_page in list(reader.pages)[1:]:
            width = float(extra_page.mediabox.width)
            height = float(extra_page.mediabox.height)
            extra_width = round(width * limits.render_dpi / 72)
            extra_height = round(height * limits.render_dpi / 72)
            if width <= 0 or height <= 0:
                raise ApiError("INVALID_PDF", "PDF 页面尺寸无效", 400)
            if (
                extra_width > limits.max_dimension
                or extra_height > limits.max_dimension
                or extra_width * extra_height > limits.max_render_pixels
            ):
                raise ApiError("PDF_PAGE_TOO_LARGE", "PDF 页面渲染尺寸超过限制", 400)
            _decoded_page_bytes(extra_page, limits)
            resource_inspection.inspect_resources(_raw_get(extra_page, "/Resources"), depth=0)

        text = ""
        layout_text = ""
        if extract_text:
            pypdf_logger = logging.getLogger("pypdf")
            previous_level = pypdf_logger.level
            try:
                pypdf_logger.setLevel(logging.ERROR)
                try:
                    text = page.extract_text() or ""
                except (PdfReadError, OSError, ValueError, TypeError, KeyError, OverflowError):
                    # The text layer is only an optimization. Some otherwise
                    # renderable electronic tickets contain non-standard font
                    # descriptors, so leave text empty and let image OCR handle
                    # them after the strict metadata/resource checks above.
                    text = ""
                if text:
                    try:
                        layout_text = page.extract_text(extraction_mode="layout") or ""
                    except (PdfReadError, OSError, ValueError, TypeError, KeyError, OverflowError):
                        # Layout text is supplemental route evidence. The
                        # validated plain text remains usable if it cannot be
                        # reconstructed safely.
                        layout_text = ""
            finally:
                pypdf_logger.setLevel(previous_level)
        return PdfInspection(
            text=text,
            layout_text=layout_text,
            render_width=render_width,
            render_height=render_height,
            embedded_images=resource_inspection.images,
            embedded_pixels=resource_inspection.pixels,
            page_count=len(reader.pages),
        )
    except ApiError:
        raise
    except RecursionError as exc:
        raise _too_complex("PDF 资源嵌套层级超过限制") from exc
    except (PdfReadError, OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ApiError("INVALID_PDF", "PDF 文件损坏或格式无效", 400) from exc
