from __future__ import annotations

import io

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.core.errors import ApiError
from app.services.receipt_bundle import ReceiptBundleSource, generate_receipt_bundle


def pdf_bytes(*labels: str) -> bytes:
    writer = PdfWriter()
    for label in labels:
        page = writer.add_blank_page(width=500, height=300)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        ),
                    }
                ),
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 18 Tf 20 150 Td ({label}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def image_bytes(*, size=(200, 500), mode="RGB") -> bytes:
    image = Image.new(mode, size, "white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_bundle_preserves_all_pdf_pages_and_text_in_snapshot_order() -> None:
    invoice = ReceiptBundleSource("invoice.pdf", "pdf", pdf_bytes("Invoice 1"))
    itinerary = ReceiptBundleSource("itinerary.pdf", "pdf", pdf_bytes("Trip 1", "Trip 2"))
    photo = ReceiptBundleSource("taxi.png", "png", image_bytes())
    supporting = ReceiptBundleSource("support.pdf", "pdf", pdf_bytes("Other evidence"))
    result = generate_receipt_bundle([invoice, itinerary, photo, supporting], max_bytes=1_000_000)
    pages = PdfReader(io.BytesIO(result)).pages
    assert len(pages) == 5
    assert [page.extract_text() for page in pages] == [
        "Invoice 1",
        "Trip 1",
        "Trip 2",
        "",
        "Other evidence",
    ]
    assert tuple(pages[0].mediabox) == (0, 0, 500, 300)
    assert float(pages[3].mediabox.width) == pytest.approx(595.2756)
    assert float(pages[3].mediabox.height) == pytest.approx(841.8898)
    assert len(pages[3].images) == 1


def test_distinct_uploaded_files_with_identical_bytes_are_both_included() -> None:
    content = pdf_bytes("Receipt")
    result = generate_receipt_bundle(
        [
            ReceiptBundleSource("a.pdf", "pdf", content),
            ReceiptBundleSource("b.pdf", "pdf", content),
        ],
        max_bytes=1_000_000,
    )
    pages = PdfReader(io.BytesIO(result)).pages
    assert len(pages) == 2
    assert [page.extract_text() for page in pages] == ["Receipt", "Receipt"]


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "P"])
def test_image_evidence_fits_landscape_a4_without_cropping(mode: str) -> None:
    image = image_bytes(size=(1200, 300), mode=mode)
    result = generate_receipt_bundle(
        [ReceiptBundleSource("receipt.png", "png", image)], max_bytes=1_000_000
    )
    page = PdfReader(io.BytesIO(result)).pages[0]
    assert float(page.mediabox.width) == pytest.approx(841.8898)
    assert float(page.mediabox.height) == pytest.approx(595.2756)
    assert page.images[0].image.size == (1200, 300)


def test_corrupt_source_fails_instead_of_omitting_the_evidence() -> None:
    with pytest.raises(ApiError) as caught:
        generate_receipt_bundle(
            [ReceiptBundleSource("行程单.pdf", "pdf", b"%PDF-broken")], max_bytes=1_000_000
        )
    assert caught.value.code == "REIMBURSEMENT_BUNDLE_SOURCE_INVALID"
    assert "行程单.pdf" in caught.value.message


def test_bundle_has_output_size_and_page_limits(monkeypatch) -> None:
    source = ReceiptBundleSource("receipt.pdf", "pdf", pdf_bytes("Receipt", "Details"))
    with pytest.raises(ApiError) as caught:
        generate_receipt_bundle([source], max_bytes=1)
    assert caught.value.code == "REIMBURSEMENT_BUNDLE_TOO_LARGE"
    monkeypatch.setattr("app.services.receipt_bundle._MAX_BUNDLE_PAGES", 1)
    with pytest.raises(ApiError) as caught:
        generate_receipt_bundle([source], max_bytes=1_000_000)
    assert caught.value.code == "REIMBURSEMENT_BUNDLE_TOO_MANY_PAGES"
