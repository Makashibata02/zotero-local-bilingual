"""Deterministic bibliography preservation on the parsed document, before threads."""
from __future__ import annotations
import re

REFERENCE_HEADING = re.compile(r"^(?:(?:\d+|[IVX]+)[.)]?\s+)?(?:references|bibliography|literature cited)\s*[:.]?$", re.I)
APPENDIX_HEADING = re.compile(r"^(?:appendix|appendices|supplementary material)\b", re.I)

def simple_formula(text):
    return (len(text) < 160 and bool(re.search(r"[=<>≤≥±∑∫]",text))
            and not re.search(r"[A-Za-z]{4,}",text)
            and bool(re.fullmatch(r"[\w\s=<>≤≥±∑∫+*/^().,\-{}\\\[\]|:]+",text)))


def install_free_document_policy():
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, PbarContext
    from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly
    if getattr(ILTranslator, "_zlb_policy_installed", False):
        return
    original_llm = ILTranslatorLLMOnly.translate
    original_translate = ILTranslator.translate
    original_paragraph = ILTranslator.translate_paragraph
    original_pre = ILTranslator.pre_translate_paragraph

    def mark(self, docs):
        in_references = False
        self._zlb_preserved = set()
        reference_count = 0
        author_count = 0
        for page in docs.page:
            # Parser reading order, not translation executor priority.
            for paragraph in page.pdf_paragraph:
                text = (paragraph.unicode or "").strip()
                if REFERENCE_HEADING.fullmatch(text):
                    in_references = True
                elif APPENDIX_HEADING.match(text) and (paragraph.layout_label == "title" or len(text) < 100):
                    in_references = False
                if in_references:
                    self._zlb_preserved.add(id(paragraph))
                    reference_count += 1
                elif simple_formula(text):
                    self._zlb_preserved.add(id(paragraph))
                elif paragraph.layout_label in {"author", "authors"} or (len(text) < 1200 and not text.lower().startswith("abstract") and re.search(r"(?:Senior Member|Fellow|Member),?\s*IEEE", text)):
                    self._zlb_preserved.add(id(paragraph))
                    author_count += 1
        self.translate_engine.reference_blocks = reference_count
        self.translate_engine.author_blocks = author_count

    def translate(self, docs):
        mark(self, docs)
        return original_translate(self, docs)

    def llm(self, docs):
        mark(self.il_translator, docs)
        return original_llm(self, docs)

    def paragraph(self, paragraph, page, pbar=None, tracker=None, *args, **kwargs):
        if id(paragraph) in getattr(self, "_zlb_preserved", set()):
            with PbarContext(pbar):
                if tracker:
                    tracker.set_pdf_unicode(paragraph.unicode)
                    tracker.set_input(paragraph.unicode)
                    tracker.set_output(paragraph.unicode)
            return
        return original_paragraph(self, paragraph, page, pbar, tracker, *args, **kwargs)

    def pre(self, paragraph, tracker, *args, **kwargs):
        if id(paragraph) in getattr(self, "_zlb_preserved", set()):
            tracker.set_pdf_unicode(paragraph.unicode)
            tracker.set_input(paragraph.unicode)
            tracker.set_output(paragraph.unicode)
            return None, None
        text, prepared = original_pre(self, paragraph, tracker, *args, **kwargs)
        # Explicitly account for formula-only/short/vertical blocks kept unchanged.
        if text is None and tracker and getattr(tracker, "input", None):
            tracker.set_output(tracker.input)
        return text, prepared

    ILTranslator._zlb_policy_installed = True
    ILTranslatorLLMOnly.translate = llm
    ILTranslator.translate = translate
    ILTranslator.translate_paragraph = paragraph
    ILTranslator.pre_translate_paragraph = pre
