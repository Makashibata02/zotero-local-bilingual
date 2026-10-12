from __future__ import annotations


def parse_glossary(text: str) -> list[tuple[str, str]]:
    entries = {}
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"术语表第 {line_number} 行应为 英文=中文")
        source, target = (v.strip() for v in line.split("=", 1))
        if not source or not target or len(source) > 200 or len(target) > 200:
            raise ValueError(f"术语表第 {line_number} 行为空或超过 200 字符")
        if any(c in source + target for c in "|<>\t"):
            raise ValueError(f"术语表第 {line_number} 行不能含表格分隔符、标签或制表符")
        key = " ".join(source.lower().split())
        if key in entries and entries[key][1] != target:
            raise ValueError(f"术语表第 {line_number} 行与已有译名冲突")
        entries[key] = (source, target)
    if len(entries) > 200:
        raise ValueError("术语表最多 200 条")
    return list(entries.values())


def build_glossaries(text: str, target_language: str = "zh-CN"):
    entries = parse_glossary(text)
    if not entries:
        return []
    from babeldoc.glossary import Glossary, GlossaryEntry
    return [Glossary("User terminology", [GlossaryEntry(a, b, target_language) for a, b in entries])]
