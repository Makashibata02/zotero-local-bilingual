from __future__ import annotations

import json

import pymupdf
import pytest
from zotero_local_bilingual.pdf import combine_side_by_side


def make_pdf(path, width: float, height: float, text: str) -> None:
    document = pymupdf.open()
    page = document.new_page(width=width, height=height)
    page.insert_text((36, 48), text, fontsize=12)
    document.save(path)
    document.close()


def test_combine_side_by_side_keeps_page_dimensions(tmp_path):
    source = tmp_path / "source.pdf"
    translated = tmp_path / "translated.pdf"
    output = tmp_path / "bilingual.pdf"
    report = tmp_path / "report.json"
    make_pdf(source, 612, 792, "English source")
    make_pdf(translated, 612, 792, "Chinese translation")

    combine_side_by_side(source, translated, output, report)

    with pymupdf.open(output) as result:
        assert result.page_count == 1
        assert result[0].rect.width == 1236
        assert result[0].rect.height == 792
        assert "English source" in result[0].get_text()
        assert "Chinese translation" in result[0].get_text()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["page_count"] == 1
    assert payload["pages"][0]["scaled"] is False


def test_combine_rejects_page_count_mismatch(tmp_path):
    source = tmp_path / "source.pdf"
    translated = tmp_path / "translated.pdf"
    make_pdf(source, 612, 792, "one")
    make_pdf(translated, 612, 792, "one")
    document = pymupdf.open(translated)
    document.new_page(width=612, height=792)
    document.save(tmp_path / "translated-two.pdf")
    document.close()

    import pytest

    with pytest.raises(ValueError, match="页数不一致"):
        combine_side_by_side(
            source,
            tmp_path / "translated-two.pdf",
            tmp_path / "output.pdf",
            tmp_path / "report.json",
        )


def make_numbered_pdf(path, prefix):
    with pymupdf.open() as document:
        for number in range(1, 5):
            page = document.new_page(width=612 + number, height=792)
            page.insert_text((36, 48), f"{prefix} page {number}", fontsize=12)
        document.save(path)


def test_combine_selection_pairs_original_indexes_and_records_output_order(tmp_path):
    source = tmp_path / "source.pdf"
    translated = tmp_path / "translated.pdf"
    output = tmp_path / "bilingual.pdf"
    report_path = tmp_path / "report.json"
    make_numbered_pdf(source, "English")
    make_numbered_pdf(translated, "Translated")
    report = combine_side_by_side(source, translated, output, report_path,
                                  selected_pages=[1, 3])
    with pymupdf.open(output) as result:
        assert result.page_count == 2
        for output_index, original_number in enumerate([2, 4]):
            text = result[output_index].get_text()
            assert f"English page {original_number}" in text
            assert f"Translated page {original_number}" in text
            assert "page 1" not in text and "page 3" not in text
            assert result[output_index].rect.width == 2 * (612 + original_number) + 12
    assert report["page_count"] == 2
    assert [p["physical_page"] for p in report["pages"]] == [2, 4]
    assert [p["output_page"] for p in report["pages"]] == [1, 2]


def test_combine_default_still_includes_every_page(tmp_path):
    source = tmp_path / "source.pdf"
    translated = tmp_path / "translated.pdf"
    output = tmp_path / "bilingual.pdf"
    make_numbered_pdf(source, "English")
    make_numbered_pdf(translated, "Translated")
    report = combine_side_by_side(source, translated, output, tmp_path / "report.json")
    assert report["page_count"] == 4
    with pymupdf.open(output) as result:
        assert result.page_count == 4
        assert "Translated page 4" in result[3].get_text()


@pytest.mark.parametrize("selection", [[], [-1], [4], [1, 1], [True], ["1"]])
def test_combine_rejects_invalid_indexes_before_writing_output(tmp_path, selection):
    source = tmp_path / "source.pdf"
    translated = tmp_path / "translated.pdf"
    output = tmp_path / "bilingual.pdf"
    make_numbered_pdf(source, "English")
    make_numbered_pdf(translated, "Translated")
    with pytest.raises(ValueError):
        combine_side_by_side(source, translated, output, tmp_path / "report.json",
                             selected_pages=selection)
    assert not output.exists()
