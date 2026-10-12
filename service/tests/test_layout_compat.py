from __future__ import annotations

import ast
import copy
import logging
import statistics
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace

import pytest

from zotero_local_bilingual.layout_compat import _install_word_width_patch


@pytest.fixture
def layout_type():
    # Exercise the actual installed helper and caller without loading models or
    # constructing an API client. Original line positions preserve source checks.
    spec = find_spec("babeldoc")
    source_path = Path(next(iter(spec.submodule_search_locations))) / "format/pdf/document_il/midend/typesetting.py"
    source = ast.parse(source_path.read_text("utf8"))
    original_class = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "Typesetting")
    methods = [node for node in original_class.body if isinstance(node, ast.FunctionDef)
               and node.name in {"_get_width_before_next_break_point", "_layout_typesetting_units"}]
    for method in methods:
        method.returns = None
        for argument in method.args.args:
            argument.annotation = None
    tree = ast.Module(body=[ast.ClassDef(name="LayoutProbe", bases=[], keywords=[],
                         body=methods, decorator_list=[])], type_ignores=[])
    namespace = {"copy": copy, "statistics": statistics, "logger": logging.getLogger("layout-probe")}
    exec(compile(ast.fix_missing_locations(tree), str(source_path), "exec"), namespace)
    return namespace["LayoutProbe"]


class Unit:
    def __init__(self, text, width, can_break=False):
        self.text, self.width, self.can_break_line = text, width, can_break
        self.char = None
        self.height = self.font_size = 1
        self.is_space = text == " "
        self.is_cjk_char = text == "中"
        self.is_hung_punctuation = self.is_cannot_appear_in_line_end_punctuation = False

    def relocate(self, x, y, scale):
        unit = copy.copy(self)
        unit.box = SimpleNamespace(x=x, y=y, x2=x + self.width * scale, y2=y + self.height * scale)
        return unit

    def try_get_unicode(self):
        return self.text


def test_jin_stays_on_one_line_after_patch(layout_type):
    probe = layout_type()
    probe.font_mapper = SimpleNamespace(base_font=SimpleNamespace(char_lengths=lambda text, size: [1]))
    units = [Unit("J", .5), Unit("i", .2), Unit("n", .6)]
    box = SimpleNamespace(x=0, y=0, x2=1.81, y2=4)
    paragraph = SimpleNamespace(first_line_indent=False)
    before, fits = probe._layout_typesetting_units(units, box, 1, 1.5, paragraph)
    assert fits and [unit.box.y for unit in before] == [3, 3, 1.5]
    _install_word_width_patch(layout_type, "0.6.4")
    after, fits = probe._layout_typesetting_units(units, box, 1, 1.5, paragraph)
    assert fits and [unit.box.y for unit in after] == [3, 3, 3]
    assert [unit.try_get_unicode() for unit in after] == list("Jin")


@pytest.mark.parametrize("units, expected", [
    ([], 0),
    ([Unit(" ", .2, True), Unit("J", .5)], 0),
    ([Unit("中", 1, True), Unit("J", .5)], 0),
    ([Unit(None, 2, True), Unit("J", .5)], 0),
    ([Unit("J", .5), Unit("i", .2), Unit("n", .6)], .8),
    ([Unit("J", .5), Unit(None, 2, True), Unit("n", .6)], 0),
    ([Unit("J", .5)], 0),
])
def test_boundaries_and_scaling_are_preserved(layout_type, units, expected):
    _install_word_width_patch(layout_type, "0.6.4")
    assert layout_type()._get_width_before_next_break_point(units, 2) == pytest.approx(expected * 2)


def test_patch_is_idempotent(layout_type):
    assert not _install_word_width_patch(layout_type, "0.6.4")["already_installed"]
    installed = layout_type._get_width_before_next_break_point
    assert _install_word_width_patch(layout_type, "0.6.4")["already_installed"]
    assert layout_type._get_width_before_next_break_point is installed


def test_unknown_version_is_rejected_without_modification(layout_type):
    original = layout_type._get_width_before_next_break_point
    with pytest.raises(RuntimeError, match="版本变化"):
        _install_word_width_patch(layout_type, "0.6.5")
    assert layout_type._get_width_before_next_break_point is original


def test_different_source_is_rejected(layout_type):
    def changed(self, units, scale):
        return 0
    layout_type._get_width_before_next_break_point = changed
    with pytest.raises(RuntimeError, match="源码与已验证版本不符"):
        _install_word_width_patch(layout_type, "0.6.4")
    assert layout_type._get_width_before_next_break_point is changed
