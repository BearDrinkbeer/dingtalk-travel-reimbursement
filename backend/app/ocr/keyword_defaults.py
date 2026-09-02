from __future__ import annotations

from app.domain.categories import ExpenseCategory
from app.ocr.types import ReceiptKeywordRule

# These are category-assignment hints, not the structural signals used to
# recognize a train ticket or an invoice. They are seeded into the database so
# administrators can change them without changing application code.
DEFAULT_RECEIPT_KEYWORD_RULES: tuple[ReceiptKeywordRule, ...] = (
    ReceiptKeywordRule("酒店", ExpenseCategory.LODGING),
    ReceiptKeywordRule("宾馆", ExpenseCategory.LODGING),
    ReceiptKeywordRule("住宿", ExpenseCategory.LODGING),
    ReceiptKeywordRule("房费", ExpenseCategory.LODGING),
    ReceiptKeywordRule("客房", ExpenseCategory.LODGING),
    ReceiptKeywordRule("航空", ExpenseCategory.AIRFARE),
    ReceiptKeywordRule("飞机", ExpenseCategory.AIRFARE),
    ReceiptKeywordRule("民航", ExpenseCategory.AIRFARE),
    ReceiptKeywordRule("铁路", ExpenseCategory.RAIL_FARE),
    ReceiptKeywordRule("火车", ExpenseCategory.RAIL_FARE),
    ReceiptKeywordRule("高铁", ExpenseCategory.RAIL_FARE),
    ReceiptKeywordRule("动车", ExpenseCategory.RAIL_FARE),
    ReceiptKeywordRule("出租汽车", ExpenseCategory.LOCAL_TRANSPORT),
    ReceiptKeywordRule("出租车", ExpenseCategory.LOCAL_TRANSPORT),
    ReceiptKeywordRule("公路客运", ExpenseCategory.LOCAL_TRANSPORT),
    ReceiptKeywordRule("道路旅客运输", ExpenseCategory.LOCAL_TRANSPORT),
    ReceiptKeywordRule("汽车客运", ExpenseCategory.LOCAL_TRANSPORT),
    ReceiptKeywordRule("客运服务费", ExpenseCategory.LOCAL_TRANSPORT),
)
