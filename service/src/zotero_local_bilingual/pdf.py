from __future__ import annotations

import hashlib
import json
from pathlib import Path
from . import __version__

import pymupdf


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_pdf(path: Path) -> int:
    with pymupdf.open(path) as document:
        if document.page_count < 1:
            raise ValueError("PDF 没有页面")
        return document.page_count


def combine_side_by_side(
    source_path: Path,
    translated_path: Path,
    output_path: Path,
    report_path: Path,
    gap_points: float = 12.0,
    selected_pages: list[int] | None = None,
) -> dict:
    source = pymupdf.open(source_path)
    translated = pymupdf.open(translated_path)
    try:
        if source.page_count != translated.page_count:
            raise ValueError(
                f"原文与译文页数不一致：{source.page_count} != {translated.page_count}"
            )
        indexes = list(range(source.page_count)) if selected_pages is None else list(selected_pages)
        if not indexes:
            raise ValueError("所选页码不能为空")
        if any(not isinstance(index, int) or isinstance(index, bool)
               or not 0 <= index < source.page_count for index in indexes):
            raise ValueError("所选页码必须为原文范围内的 0-based 整数索引")
        if len(set(indexes)) != len(indexes):
            raise ValueError("所选页码不能重复")
        output = pymupdf.open()
        pages: list[dict] = []
        for output_index, index in enumerate(indexes):
            left = source[index]
            right = translated[index]
            left_rect = left.rect
            right_rect = right.rect
            height = max(left_rect.height, right_rect.height)
            width = left_rect.width + gap_points + right_rect.width
            page = output.new_page(width=width, height=height)
            left_target = pymupdf.Rect(0, 0, left_rect.width, left_rect.height)
            right_target = pymupdf.Rect(
                left_rect.width + gap_points,
                0,
                width,
                right_rect.height,
            )
            page.show_pdf_page(left_target, source, index, keep_proportion=False)
            page.show_pdf_page(right_target, translated, index, keep_proportion=False)
            pages.append(
                {
                    "physical_page": index + 1,
                    "output_page": output_index + 1,
                    "source_size_pt": [left_rect.width, left_rect.height],
                    "translated_size_pt": [right_rect.width, right_rect.height],
                    "output_size_pt": [width, height],
                    "source_rotation": left.rotation,
                    "translated_rotation": right.rotation,
                    "scaled": False,
                }
            )
        metadata = output.metadata
        metadata.update(
            {
                "title": f"Bilingual - {source_path.stem}",
                "producer": f"Zotero Local Bilingual {__version__}",
            }
        )
        output.set_metadata(metadata)
        output.save(output_path, garbage=4, deflate=True)
        output.close()
    finally:
        source.close()
        translated.close()

    report = {
        "status": "layout_baseline",
        "layout": "left_original_right_translation",
        "gap_points": gap_points,
        "page_count": len(pages),
        "pages": pages,
        "quality": {
            "automatic_overlap_detection": "not_implemented",
            "minimum_body_font_check": "not_implemented",
            "manual_review_required": True,
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
