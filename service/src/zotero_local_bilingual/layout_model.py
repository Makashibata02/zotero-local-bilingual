"""Keep the upstream Windows loader; reuse CoreML compilation on macOS."""
from __future__ import annotations

import ast
import hashlib
import logging
import platform
import sys
import threading
from importlib.metadata import version

logger = logging.getLogger(__name__)


def coreml_cache_key(model_bytes: bytes, ort_version: str) -> str:
    # ORT does not invalidate CoreML caches when weights change. Include the
    # actual static graph, runtime, OS and architecture, including Rosetta.
    identity = "|".join(("zlb-coreml-v1", ort_version, platform.machine(), platform.mac_ver()[0]))
    return hashlib.sha256(identity.encode() + b"\0" + model_bytes).hexdigest()


def load_layout_model():
    from babeldoc.docvision.doclayout import DocLayoutModel

    if sys.platform != "darwin" or version("babeldoc") != "0.6.4":
        return DocLayoutModel.load_onnx()
    return _load_macos_model()


def _load_macos_model():
    import onnx
    import onnxruntime as ort
    from babeldoc.assets.assets import get_doclayout_onnx_model_path
    from babeldoc.docvision.doclayout import OnnxModel
    from .config import Settings

    # Retain BabelDOC 0.6.4's preprocessing, prediction, labels and box scaling.
    # Only session creation differs: it can reuse the compiled CoreML graph.
    result = OnnxModel.__new__(OnnxModel)
    result.model_path = get_doclayout_onnx_model_path()
    model = onnx.load(result.model_path)
    metadata = {item.key: item.value for item in model.metadata_props}
    result._stride = ast.literal_eval(metadata["stride"])
    result._names = ast.literal_eval(metadata["names"])
    result.lock = threading.Lock()

    if "CoreMLExecutionProvider" not in ort.get_available_providers():
        result.model = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"])
        return result

    model = OnnxModel._make_static_model(model, OnnxModel._FIXED_IMGSZ)
    payload = model.SerializeToString()
    key = coreml_cache_key(payload, ort.__version__)
    cache = Settings.load().data_dir / "cache" / "coreml" / key
    # Use a content key instead of a path: an installation moves from staging
    # into runtimes after preflight, and both must reuse the same compiled model.
    prop = model.metadata_props.add()
    prop.key, prop.value = "CACHE_KEY", key[:48]
    payload = model.SerializeToString()
    try:
        import fcntl
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Two queued PDFs may start together. Only one may populate this cache;
        # inference itself runs outside the lock and remains independently cancellable.
        with (cache / ".compile.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                result.model = ort.InferenceSession(payload, providers=[
                    ("CoreMLExecutionProvider", {"ModelCacheDirectory": str(cache)}),
                    "CPUExecutionProvider",
                ])
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except Exception:
        logger.warning("CoreML cache/session unavailable; using CPU for this worker", exc_info=True)
        result.model = ort.InferenceSession(payload, providers=["CPUExecutionProvider"])
    return result
