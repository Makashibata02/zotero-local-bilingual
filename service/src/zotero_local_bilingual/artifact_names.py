import re

def artifact_name(metadata: dict, title: str, target: str, kind: str, profile: dict) -> str:
    """Keep a short internal path while exposing a bibliographic download name."""
    label = metadata.get("citation_label") or title or metadata.get("filename", "PDF")
    model = profile.get("model") or profile.get("provider", "translator")
    clean = lambda value: re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    suffix = f"_{target}_{kind}_{clean(model)[:36]}.pdf"
    # Zotero attachments and temp viewer copies must also work without long-path support.
    return clean(label)[:max(30, 170-len(suffix))].rstrip(" .") + suffix
