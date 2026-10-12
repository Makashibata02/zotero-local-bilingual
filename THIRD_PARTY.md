# Third-party components

- BabelDOC 0.6.4, AGPL-3.0-or-later: https://github.com/funstory-ai/BabelDOC
- PyMuPDF, AGPL/commercial dual license: https://pymupdf.readthedocs.io/
- FastAPI, MIT: https://github.com/fastapi/fastapi
- Uvicorn, BSD-3-Clause: https://github.com/encode/uvicorn
- esbuild, MIT: https://github.com/evanw/esbuild

The distributed plugin and local service do not include Immersive Translate source code,
branding, credentials, or translation service endpoints. The Zotero bootstrap shape follows
Zotero's public plugin documentation. Investigation copies were local development evidence and are not included in this repository or release.


- REA / rea-agents 6.3.0, MIT: https://github.com/morluto/rea . Used as an analysis tool
  via an isolated npm cache; not a runtime dependency and not included in the XPI.
- Immersive Translate Zotero plugin (AGPL-3.0 repository):
  https://github.com/immersive-translate/zotero-immersivetranslate . Its installed XPI and
  public source were inspected for architecture and parameter evidence. No source was
  copied into our plugin implementation; investigation copies are not distributed.
- BabelDOC layout adaptation: service/src/zotero_local_bilingual/layout_compat.py checks
  BabelDOC 0.6.4 and a verified function digest before installing a runtime width correction.
  Third-party files on disk are not patched. Existing BabelDOC licensing still applies.


## Standalone runtime

Project code is licensed under AGPL-3.0-or-later (see LICENSE). BabelDOC 0.6.4 and PyMuPDF retain their upstream licenses. Runtime wheels are installed from the hash-locked runtime-requirements.txt; their original dist-info license directories remain in the engine. Python's LICENSE.txt remains beside the portable interpreter. runtime.json lists exact installed dependency versions; upstream source for each is obtainable via its corresponding PyPI release page, and the release source bundle includes the lockfile and build recipe.

Fonts, CMaps, DocLayout-YOLO model and tokenizer data are taken from the BabelDOC 0.6.4 asset list, without modification. The assets/resources.json manifest records each file size and SHA-256. docs/font-license-inventory.json records copyright and license notices embedded in each bundled font (including OFL notices and URLs). CMap support derives from BabelDOC's bundled pdfminer code and its retained MIT LICENSE. DocLayout-YOLO upstream: https://github.com/opendatalab/DocLayout-YOLO (AGPL-3.0). Asset upstream: https://github.com/funstory-ai/BabelDOC-Assets.

Bing and Tencent experimental adapters are independently implemented HTTP clients for their public web translation pages. No Immersive Translate code, keys, cloud services, or REA executable is included. They do not confer any provider guarantee of indefinite free access. No Google or CNKI adapter is shipped.


## Distribution-only documentation sanitation

The scikit-image grass-image documentation example contains a signed URL. The build recipe removes only its JWT query parameter from that unused example; image-processing code and license notices remain intact. Eight non-secret dependency scanner matches (type annotations, enum expressions, generated descriptors, a public OAuth client ID, and a truncated documentation placeholder) are classified by exact path, line, and full-file SHA-256 in docs/dependency-scan-exceptions.json. A dependency change invalidates the classification; no dependency directory is broadly exempted.
