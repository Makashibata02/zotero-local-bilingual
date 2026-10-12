"""Remove a signed URL in an unused third-party documentation example."""
from pathlib import Path
import re


def sanitize_examples(site: Path) -> int:
    path=site/'skimage/data/_fetchers.py'
    text=path.read_text('utf8')
    pattern=r"(    >>> url = 'https://images-wixmp-[^'\n?]+)\?token=eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(')"
    updated,count=re.subn(pattern,r'\1\2',text)
    if count>1:
        raise ValueError('Unexpected signed examples; review before packaging')
    if count:
        path.write_text(updated,'utf8')
    return count
