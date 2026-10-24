from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path


def emit(event: dict) -> None:
    # The parent reads a byte pipe. ASCII JSON escapes keep Windows code pages
    # from corrupting Chinese paths or error messages in the process protocol.
    print(json.dumps(event, ensure_ascii=True), flush=True)


def translate(spec_path: Path) -> int:
    started = time.perf_counter()
    from .runtime_assets import configure_assets
    configure_assets()
    from .layout_model import load_layout_model
    from babeldoc.format.pdf import high_level
    from babeldoc.format.pdf.translation_config import (
        TranslationConfig,
        WatermarkOutputMode,
    )
    from babeldoc.progress_monitor import ProgressMonitor
    from .guarded_translator import GuardedTranslator
    from .translation_policy import role_prompt
    from .languages import font_language
    from .quality import audit_tracking
    from .glossary_support import build_glossaries
    from .layout_compat import install_babeldoc_layout_patch

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    api_key = os.environ.pop("ZLB_API_KEY", None)
    free = spec.get("provider", "openai") != "openai"
    if not api_key and not free:
        raise RuntimeError("工作进程没有收到 API Key")

    # Apply deterministic reference protection to both MT and API modes.
    from .free_policy import install_free_document_policy
    install_free_document_policy()
    layout_patch = install_babeldoc_layout_patch()
    emit({"type": "progress", "stage": "load_layout_model", "progress": 1.0})
    layout_model = load_layout_model()
    if free:
        from .free_translate import FreeTranslator
        translator = FreeTranslator(spec)
    else:
        translator = GuardedTranslator(spec, api_key)
    config = TranslationConfig(
        translator=translator,
        glossaries=[] if free else build_glossaries(spec.get("glossary_text", ""), spec.get("target_language", "zh-CN")),
        input_file=spec["source_path"],
        lang_in=spec.get("source_language", "en"),
        lang_out=font_language(spec.get("target_language", "zh-CN")),
        doc_layout_model=layout_model,
        pages=spec.get("pages") or None,
        output_dir=spec["engine_output_dir"],
        working_dir=spec["engine_working_dir"],
        no_dual=True,
        no_mono=False,
        qps=1 if free else spec.get("qps", 2),
        pool_max_workers=1 if free else spec.get("qps", 2),
        term_pool_max_workers=1,
        auto_extract_glossary=False if free else spec.get("auto_extract_glossary", False),
        save_auto_extracted_glossary=False,
        disable_rich_text_translate=spec.get("disable_rich_text_translate", False),
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
        custom_system_prompt=role_prompt(spec.get("custom_system_prompt"), spec.get("target_language", "zh-CN")),
        primary_font_family=spec.get("primary_font_family", "serif"),
        report_interval=0.5,
    )

    def on_progress(**event) -> None:
        if event.get("type") in {
            "progress_start",
            "progress_update",
            "progress_end",
        }:
            emit(
                {
                    "type": "progress",
                    "stage": event.get("stage"),
                    "progress": float(event.get("overall_progress", 0.0)),
                    "stage_progress": float(event.get("stage_progress", 0.0)),
                }
            )

    with ProgressMonitor(
        high_level.get_translation_stage(config),
        progress_change_callback=on_progress,
        report_interval=0.5,
    ) as monitor:
        result = high_level.do_translate(monitor, config)
    translator.save_audit()
    if translator.failure:
        raise RuntimeError(translator.failure)
    quality = audit_tracking(Path(spec["engine_working_dir"]))
    quality["layout_compatibility"] = layout_patch
    quality["reference_blocks_preserved"] = getattr(translator, "reference_blocks", 0)
    quality["author_blocks_preserved"] = getattr(translator, "author_blocks", 0)
    (Path(spec["engine_working_dir"]) / "quality.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf8")
    if quality["empty_paragraphs"] or not quality["tracking_files"]:
        raise RuntimeError(f"完整性检查未通过：{quality['empty_paragraphs']} 个段落为空，或缺少翻译追踪；未交付 PDF。")
    result_path = result.no_watermark_mono_pdf_path or result.mono_pdf_path

    if result_path is None or not Path(result_path).exists():
        raise RuntimeError("BabelDOC 没有生成译文 PDF")
    emit(
        {
            "type": "result",
            "mono_pdf_path": str(Path(result_path).resolve()),
            "usage": translator.snapshot(),
            "quality": quality,
            "engine_seconds": round(time.perf_counter() - started, 3),
        }
    )
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: engine_worker <job-spec.json>")
    try:
        from .worker_lock import worker_lock
        path = Path(sys.argv[1])
        spec = json.loads(path.read_text("utf8"))
        with worker_lock(Path(spec["engine_working_dir"])):
            exit_code = translate(path)
    except Exception as exc:  # noqa: BLE001 - serialize worker failures for parent
        emit(
            {
                "type": "fatal",
                "error": str(exc),
                "traceback": traceback.format_exc(limit=20),
            }
        )
        raise SystemExit(1)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
