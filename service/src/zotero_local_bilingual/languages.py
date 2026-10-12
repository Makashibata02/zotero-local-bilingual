"""Canonical language identifiers, provider codes and font regions."""
LANGUAGES = {
    "zh-CN": ("简体中文", "Simplified Chinese", "zh-CN", "zh-Hans", "zh"),
    "zh-TW": ("繁體中文", "Traditional Chinese", "zh-TW", "zh-Hant", "zh-TW"),
    "ja": ("日本語", "Japanese", "ja-JP", "ja", "ja"),
    "ko": ("한국어", "Korean", "ko-KR", "ko", "ko"),
    "fr": ("Français", "French", "fr", "fr", "fr"),
    "de": ("Deutsch", "German", "de", "de", "de"),
    "es": ("Español", "Spanish", "es", "es", "es"),
}
RULE_VERSION = "academic-030-v2"

def font_language(target: str) -> str:
    return LANGUAGES[target][2]

def capabilities() -> dict:
    import json
    from pathlib import Path
    evidence = json.loads(Path(__file__).with_name("language_validation.json").read_text("utf8"))
    return {"languages": [{"id": key, "name": value[0]} for key, value in LANGUAGES.items()],
            "providers": {"openai": {key: {"supported": True, "status": "API 支持；质量取决于模型"} for key in LANGUAGES},
                          "bing": evidence.get("bing", {}), "tencent": evidence.get("tencent", {})}, "output_modes": ["dual_and_mono", "dual", "mono"]}
