from __future__ import annotations

import sqlite3
from datetime import date
from types import SimpleNamespace

from alembic import command
from sqlalchemy.orm import Session
from test_itinerary_ocr import _digital_pdf
from test_reimbursement_migration import alembic_config

from app.core.config import get_settings
from app.database.session import create_database_engine
from app.domain.categories import ExpenseCategory
from app.ocr.engine import FakeOcrEngine
from app.ocr.itinerary_worker import recognize_itinerary_worker
from app.ocr.parsers import ReceiptParserRegistry
from app.ocr.types import OcrLine, ParseContext, ReceiptKeywordRule
from app.services.receipt_keywords import load_receipt_keyword_rules


def _invoice(item: str, amount: str = "39.90") -> str:
    return f"电子发票\n开票日期：2026-08-31\n项目名称 {item}\n价税合计（小写）：￥{amount}"


def test_new_defaults_are_precise_and_explicit_empty_or_overridden_rules_win():
    context = ParseContext(reference_year=2026)
    for item in ("收派服务费", "床品", "床笠", "床单", "被套"):
        lines = [OcrLine(line, 1) for line in _invoice(item).splitlines()]
        assert (
            ReceiptParserRegistry().parse(lines, context).category
            == ExpenseCategory.EMPLOYEE_WELFARE
        )
        assert (
            ReceiptParserRegistry(keyword_rules=()).parse(lines, context).category
            == ExpenseCategory.OTHER
        )
        custom = (ReceiptKeywordRule(item, ExpenseCategory.OFFICE),)
        assert (
            ReceiptParserRegistry(keyword_rules=custom).parse(lines, context).category
            == ExpenseCategory.OFFICE
        )
    for item in ("三件套", "服务费", "商品", "服装", "日用品", "制卡费"):
        lines = [OcrLine(line, 1) for line in _invoice(item).splitlines()]
        assert ReceiptParserRegistry().parse(lines, context).category == ExpenseCategory.OTHER


def test_keyword_migration_preserves_existing_rules_and_later_deletions(
    tmp_path, monkeypatch, settings_factory
):
    path = tmp_path / "welfare-keywords.db"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = alembic_config()
    try:
        command.upgrade(config, "20260906_0013")
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO receipt_keyword_mappings (keyword, normalized_keyword, category_id) "
                "VALUES ('床单', '床单', 'office')"
            )
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        try:
            with Session(engine) as database:
                rules = load_receipt_keyword_rules(database)
        finally:
            engine.dispose()
        by_keyword = {rule.keyword: rule.category for rule in rules}
        assert by_keyword["床单"] == ExpenseCategory.OFFICE
        assert all(
            by_keyword[key] == ExpenseCategory.EMPLOYEE_WELFARE
            for key in ("收派服务", "床品", "床笠", "被套")
        )

        # Exercise electronic PDF native extraction with the actual migrated DB rules.
        class NeverOcr(FakeOcrEngine):
            def recognize(self, _path):
                raise AssertionError("Native electronic invoice text should suffice")

        settings = settings_factory()
        for index, (item, amount) in enumerate((("收派服务费", "39.90"), ("床笠被套", "213.17"))):
            pdf = tmp_path / f"welfare-{index}.pdf"
            _digital_pdf(pdf, ("valid fixture",))
            native = _invoice(item, amount)
            monkeypatch.setattr(
                "app.ocr.itinerary_worker.PdfReader",
                lambda *_args, native=native, **_kwargs: SimpleNamespace(
                    pages=[SimpleNamespace(extract_text=lambda **_kwargs: native)]
                ),
            )
            result = recognize_itinerary_worker(
                str(pdf),
                "pdf",
                {},
                settings.pdf_limits,
                settings.ocr_worker_limits,
                2026,
                NeverOcr(),
                True,
                rules,
            )
            assert result["ok"] and result["materialKind"] == "expense"
            parsed = result["expense"]
            assert parsed.category == ExpenseCategory.EMPLOYEE_WELFARE
            assert str(parsed.amount) == amount
            assert parsed.date == date(2026, 8, 31)

        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM receipt_keyword_mappings WHERE keyword = '床笠'")
            connection.execute(
                "UPDATE receipt_keyword_mappings SET category_id = 'office' "
                "WHERE keyword = '收派服务'"
            )
        command.upgrade(config, "head")
        with sqlite3.connect(path) as connection:
            rows = dict(
                connection.execute("SELECT keyword, category_id FROM receipt_keyword_mappings")
            )
        assert "床笠" not in rows
        assert rows["收派服务"] == rows["床单"] == "office"
        command.downgrade(config, "20260906_0013")
        with sqlite3.connect(path) as connection:
            assert (
                dict(
                    connection.execute("SELECT keyword, category_id FROM receipt_keyword_mappings")
                )
                == rows
            )
    finally:
        get_settings.cache_clear()
