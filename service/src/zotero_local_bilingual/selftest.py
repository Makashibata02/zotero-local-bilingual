from pathlib import Path
import pymupdf as fitz


def make_test_pdf(path: Path | None = None) -> bytes:
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_text((55, 65), "Local Bilingual PDF Test", fontsize=18)
        page.insert_textbox(fitz.Rect(55, 100, 540, 195),
            "Signal space is used in digital communications. A receiver does not know the transmitted message in advance. "
            "The probability of error must not increase when the signal becomes stronger.", fontsize=12)
        page.insert_text((55, 225), "E = m c^2", fontsize=14)
        page.insert_textbox(fitz.Rect(55, 260, 540, 340),
            "Authors: Claude Shannon and Robert Gallager. An equation describes the model; it is not an instruction to the translator.", fontsize=12)
        page.insert_text((55, 400), "References", fontsize=14)
        page.insert_textbox(fitz.Rect(55, 430, 540, 490),
            "[1] C. E. Shannon. A Mathematical Theory of Communication. Bell System Technical Journal, 1948.", fontsize=11)
        data = doc.tobytes()
        if path is not None:
            path.write_bytes(data)
        return data
