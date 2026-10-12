from __future__ import annotations

import re


def parse_pages(value: str | None, page_count: int) -> list[int]:
    """Return sorted 0-based physical page indexes from a 1-based selection.

    Empty input selects the whole document. Open ranges use the document's
    first or final page: ``-3`` means pages 1 through 3, and ``2-`` means page
    2 through the end. Printed page labels are intentionally not interpreted.
    """
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        raise ValueError("PDF 页数必须为正整数")
    if value is None or (isinstance(value, str) and not value.strip()):
        return list(range(page_count))
    if not isinstance(value, str):
        raise ValueError("页码必须为文本，例如 1-2,5")
    selected: set[int] = set()
    for raw in value.split(","):
        part = raw.strip()
        if re.fullmatch(r"[0-9]+", part):
            start = end = int(part)
        else:
            match = re.fullmatch(r"([0-9]*)\s*-\s*([0-9]*)", part)
            if not match or not any(match.groups()):
                raise ValueError(f"无效页码：{part!r}；请使用 1-2,5 等物理页码")
            start = int(match[1]) if match[1] else 1
            end = int(match[2]) if match[2] else page_count
        if not (1 <= start <= page_count and 1 <= end <= page_count):
            raise ValueError(f"页码超出范围 1-{page_count}：{part}")
        if start > end:
            raise ValueError(f"页码范围不能倒序：{part}")
        selected.update(range(start - 1, end))
    return sorted(selected)
