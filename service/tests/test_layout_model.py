import sys
from types import SimpleNamespace

from zotero_local_bilingual import layout_model


def install_fakes(monkeypatch, tmp_path, *, providers=None, fail_coreml=False):
    calls = []
    # The macOS branch is simulated; do not depend on the host OS lock module.
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(
        LOCK_EX=2, LOCK_UN=8, flock=lambda fd, operation: None))

    class Metadata(list):
        def add(self):
            prop = SimpleNamespace(key="", value="")
            self.append(prop)
            return prop

    class Model:
        def __init__(self):
            self.metadata_props = Metadata([
                SimpleNamespace(key="stride", value="32"),
                SimpleNamespace(key="names", value="{0: 'text'}"),
            ])

        def SerializeToString(self):
            return repr([(p.key, p.value) for p in self.metadata_props]).encode()

    class OnnxModel:
        _FIXED_IMGSZ = 1024

        @staticmethod
        def _make_static_model(model, size):
            assert size == 1024
            return model

        def predict(self, image):
            return image

    def session(payload, *, providers):
        calls.append(providers)
        if isinstance(providers[0], tuple) and fail_coreml:
            raise RuntimeError("CoreML compilation failed")
        return SimpleNamespace(providers=providers)

    monkeypatch.setitem(sys.modules, "onnx", SimpleNamespace(load=lambda _: Model()))
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        __version__="1.23.2", get_available_providers=lambda: providers or ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        InferenceSession=session))
    monkeypatch.setitem(sys.modules, "babeldoc.assets.assets", SimpleNamespace(get_doclayout_onnx_model_path=lambda: "model.onnx"))
    upstream = object()
    monkeypatch.setitem(sys.modules, "babeldoc.docvision.doclayout", SimpleNamespace(
        OnnxModel=OnnxModel, DocLayoutModel=SimpleNamespace(load_onnx=lambda: upstream)))
    from zotero_local_bilingual.config import Settings
    monkeypatch.setattr(Settings, "load", lambda: SimpleNamespace(data_dir=tmp_path))
    return calls, upstream, OnnxModel


def test_windows_keeps_upstream_loader(monkeypatch, tmp_path):
    calls, upstream, _ = install_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(layout_model.sys, "platform", "win32")
    assert layout_model.load_layout_model() is upstream
    assert calls == []


def test_macos_cache_retains_prediction_and_reuses_directory(monkeypatch, tmp_path):
    calls, _, original = install_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(layout_model.sys, "platform", "darwin")
    monkeypatch.setattr(layout_model, "version", lambda _: "0.6.4")
    first = layout_model.load_layout_model()
    second = layout_model.load_layout_model()
    assert isinstance(first, original) and first.predict("image") == "image"
    assert first._stride == second._stride == 32
    assert first._names == {0: "text"}
    assert calls[0] == calls[1]
    assert calls[0][1] == "CPUExecutionProvider"
    cache = calls[0][0][1]["ModelCacheDirectory"]
    assert str(tmp_path / "cache" / "coreml") in cache
    assert len(list((tmp_path / "cache" / "coreml").iterdir())) == 1


def test_coreml_failure_falls_back_to_cpu(monkeypatch, tmp_path):
    calls, _, _ = install_fakes(monkeypatch, tmp_path, fail_coreml=True)
    result = layout_model._load_macos_model()
    assert len(calls) == 2 and result.model.providers == ["CPUExecutionProvider"]


def test_no_coreml_uses_cpu_without_creating_cache(monkeypatch, tmp_path):
    calls, _, _ = install_fakes(monkeypatch, tmp_path, providers=["CPUExecutionProvider"])
    layout_model._load_macos_model()
    assert calls == [["CPUExecutionProvider"]]
    assert not (tmp_path / "cache").exists()


def test_unwritable_cache_falls_back_without_losing_model(monkeypatch, tmp_path):
    calls, _, _ = install_fakes(monkeypatch, tmp_path)
    (tmp_path / "cache").write_text("blocked")
    result = layout_model._load_macos_model()
    assert calls == [["CPUExecutionProvider"]]
    assert result._names == {0: "text"}


def test_upstream_upgrade_does_not_apply_unverified_adapter(monkeypatch, tmp_path):
    calls, upstream, _ = install_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(layout_model.sys, "platform", "darwin")
    monkeypatch.setattr(layout_model, "version", lambda _: "0.7.0")
    assert layout_model.load_layout_model() is upstream
    assert calls == []


def test_cache_invalidates_for_weights_runtime_os_and_architecture(monkeypatch):
    monkeypatch.setattr(layout_model.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(layout_model.platform, "mac_ver", lambda: ("14.0", "", ""))
    key = layout_model.coreml_cache_key(b"weights", "1.23.2")
    assert key != layout_model.coreml_cache_key(b"new weights", "1.23.2")
    assert key != layout_model.coreml_cache_key(b"weights", "1.24.0")
    monkeypatch.setattr(layout_model.platform, "machine", lambda: "x86_64")
    assert key != layout_model.coreml_cache_key(b"weights", "1.23.2")
    monkeypatch.setattr(layout_model.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(layout_model.platform, "mac_ver", lambda: ("15.0", "", ""))
    assert key != layout_model.coreml_cache_key(b"weights", "1.23.2")
