"""One line of the bill of materials."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BomLine:
    category: str
    item: str
    catalog_id: str | None
    qty: float
    unit: str
    remarks: str = ""
