from __future__ import annotations

import hashlib
import inspect
from importlib.metadata import version


BABELDOC_VERSION = "0.6.4"
WORD_WIDTH_SOURCE_SHA256 = "cf53ca589c9256fe633071b14a60d473e60c1ac1f037b63f50b8aeca379a01c0"
_PATCH_ID = "zlb-latin-word-width-v1"


def _install_word_width_patch(target_type: type, installed_version: str) -> dict:
    """Adapt the verified 0.6.4 helper without modifying third-party files."""
    if installed_version != BABELDOC_VERSION:
        raise RuntimeError("排版适配仅验证 BabelDOC 0.6.4；引擎版本变化，停止应用补丁")
    original = target_type._get_width_before_next_break_point
    if getattr(original, "_zlb_patch_id", None) == _PATCH_ID:
        return {"patch": _PATCH_ID, "babeldoc_version": installed_version,
                "source_sha256": WORD_WIDTH_SOURCE_SHA256, "already_installed": True}
    try:
        source = inspect.getsource(original).replace("\r\n", "\n")
    except (OSError, TypeError) as exc:
        raise RuntimeError("无法核验 BabelDOC 排版函数源码，停止应用补丁") from exc
    source_hash = hashlib.sha256(source.encode("utf8")).hexdigest()
    if source_hash != WORD_WIDTH_SOURCE_SHA256:
        raise RuntimeError("BabelDOC 排版函数源码与已验证版本不符，停止应用补丁")

    def remaining_word_width(self, typesetting_units, scale):
        # The caller already adds the current glyph's width. The upstream
        # helper includes it a second time, which can wrap within a Latin word.
        if not typesetting_units or typesetting_units[0].can_break_line:
            return 0
        return max(0.0, original(self, typesetting_units, scale)
                   - typesetting_units[0].width * scale)

    remaining_word_width._zlb_patch_id = _PATCH_ID
    target_type._get_width_before_next_break_point = remaining_word_width
    return {"patch": _PATCH_ID, "babeldoc_version": installed_version,
            "source_sha256": source_hash, "already_installed": False}


def install_babeldoc_layout_patch() -> dict:
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting

    return _install_word_width_patch(Typesetting, version("babeldoc"))
