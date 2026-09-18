"""Contract checks, run against the real sample documents."""

from __future__ import annotations

from pathlib import Path

import pytest

from collaborate import prompts, svgdoc

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def sample(name: str) -> str:
    return (SAMPLES / f"{name}.svg").read_text(encoding="utf-8")


def reply(base: str, layer: str) -> str:
    return base.replace("</svg>", f"{layer}\n</svg>")


GOOD_LAYER = """  <g id="ai-turn-1" data-author="ai" fill="none" stroke="#8a8a8a"
     stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M 300 300 C 320 310 340 300 360 290"/>
  </g>"""


@pytest.mark.parametrize("name,strokes", [("t01", 2), ("t02", 5), ("t03", 19)])
def test_samples_parse_and_count(name: str, strokes: int) -> None:
    assert svgdoc.count_new_user_strokes(sample(name)) == strokes


def test_clean_reply_passes() -> None:
    base = sample("t02")
    result = svgdoc.review(base, reply(base, GOOD_LAYER))
    assert result.ok, result.errors
    assert result.warnings == []
    assert result.ai_strokes == 1
    assert result.new_user_strokes == 5


def test_style_attribute_strokes_are_seen() -> None:
    """The samples put stroke in `style=`, not an attribute — easy to miss."""
    base = sample("t01")
    layer = GOOD_LAYER.replace('stroke="#8a8a8a"', 'style="stroke: #ff0000"')
    result = svgdoc.review(base, reply(base, layer))
    assert not result.ok
    assert any("not grey" in e for e in result.errors)


def test_missing_layer_is_an_error() -> None:
    base = sample("t01")
    result = svgdoc.review(base, base)
    assert not result.ok
    assert "No new ai-turn group" in result.errors[0]


def test_disallowed_element_is_an_error() -> None:
    base = sample("t01")
    layer = GOOD_LAYER.replace(
        '<path d="M 300 300 C 320 310 340 300 360 290"/>',
        '<rect x="10" y="10" width="20" height="20"/>',
    )
    result = svgdoc.review(base, reply(base, layer))
    assert not result.ok
    assert any("<rect>" in e for e in result.errors)


def test_layer_must_be_last() -> None:
    base = sample("t01")
    body = reply(base, GOOD_LAYER).replace(
        "</g>", '</g>\n  <path style="stroke: rgb(0,0,0)" d="M 5 5 L 9 9"/>', 1
    )
    result = svgdoc.review(base, body)
    assert not result.ok
    assert any("not the last element" in e for e in result.errors)


def test_modified_existing_marks_warn_but_pass() -> None:
    base = sample("t02")
    tampered = reply(base, GOOD_LAYER).replace("M 79.263 28.593", "M 79.26 28.59")
    result = svgdoc.review(base, tampered)
    assert result.ok
    assert any("Existing marks were modified" in w for w in result.warnings)


def test_over_budget_warns_but_passes() -> None:
    base = sample("t01")  # 2 new user strokes
    paths = "\n".join(
        f'    <path d="M {n} {n} C {n + 5} {n} {n + 8} {n + 3} {n + 12} {n}"/>'
        for n in (300, 320, 340)
    )
    layer = GOOD_LAYER.replace(
        '    <path d="M 300 300 C 320 310 340 300 360 290"/>', paths
    )
    result = svgdoc.review(base, reply(base, layer))
    assert result.ok
    assert any("Over budget: 3 AI strokes" in w for w in result.warnings)


def test_code_fence_is_tolerated_with_a_warning() -> None:
    base = sample("t01")
    wrapped = f"```svg\n{reply(base, GOOD_LAYER)}\n```"
    result = svgdoc.review(base, wrapped)
    assert result.ok
    assert any("text around the SVG" in w for w in result.warnings)


def test_garbage_response_raises() -> None:
    with pytest.raises(svgdoc.InvalidSVG):
        svgdoc.review(sample("t01"), "I'd rather not draw today.")


def test_prompt_loads_with_its_defaults() -> None:
    prompt = prompts.load("doodle", 1)
    assert prompt.title == "Collaborative Doodling"
    assert prompt.model == "claude-opus-5"
    assert prompt.thinking["display"] == "summarized"
    assert "Collaborative Doodling: Instructions" in prompt.body
    assert "Colll" not in prompt.body


def test_prompt_id_cannot_escape_the_prompts_directory() -> None:
    with pytest.raises(ValueError):
        prompts.load("../../etc", 1)


def test_precision_is_clamped_on_the_way_back() -> None:
    base = sample("t01")
    layer = """  <g id="ai-turn-1" fill="none" stroke="#8a8a8a">
    <path d="M 10.123456789 20.5 C 30.98765 40.000001 50 60.1239"/>
  </g>"""
    result = svgdoc.review(base, reply(base, layer))
    assert result.ok
    assert 'd="M 10.123 20.5 C 30.988 40 50 60.124"' in result.svg


def test_clamping_leaves_everything_that_is_not_geometry_alone() -> None:
    marked_up = '<path d="M 1.00005 2" stroke="#8a8a8a" id="turn-1.5" opacity="0.55555"/>'
    clamped = svgdoc.clamp_precision(marked_up)
    assert 'd="M 1 2"' in clamped
    assert 'stroke="#8a8a8a"' in clamped
    assert 'id="turn-1.5"' in clamped
    assert 'opacity="0.55555"' in clamped


def test_clamping_does_not_trip_the_preservation_check() -> None:
    """The input is already at this precision, so rounding is a no-op on it."""
    base = sample("t01")
    result = svgdoc.review(base, reply(base, GOOD_LAYER))
    assert result.ok
    assert not any("modified or reordered" in w for w in result.warnings)
