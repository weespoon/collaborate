"""Checking what came back against the contract the prompt sets out.

The prompt owns the rules; this module only verifies them. Two severities:

* **errors** reject the turn — the client's document is left untouched.
* **warnings** are reported and the turn is accepted anyway. They're the
  interesting output during prototyping: a rising warning rate on a prompt
  version is how you find out a wording change stopped landing.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

SVG_NS = "http://www.w3.org/2000/svg"
ALLOWED_IN_AI_LAYER = {"path", "polyline", "line"}
LAYER_GREY = "#8a8a8a"
AI_TURN_ID = re.compile(r"^ai-turn-(\d+)$")
_SUBPATH = re.compile(r"[Mm]")
_HEX = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", re.I)
_RGB = re.compile(r"^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", re.I)


class InvalidSVG(ValueError):
    """The response isn't a document we can work with at all."""


@dataclass
class Review:
    svg: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ai_strokes: int = 0
    new_user_strokes: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def _local(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


def extract_document(text: str) -> str:
    """Pull the SVG out of a response, tolerating wrappers the prompt forbids.

    Being lenient here and loud about it beats failing a good drawing over a
    stray code fence.
    """
    start = text.find("<?xml")
    if start == -1:
        start = text.find("<svg")
    end = text.rfind("</svg>")
    if start == -1 or end == -1:
        raise InvalidSVG("no <svg> element in the response")
    return text[start : end + len("</svg>")]


def parse(svg: str) -> ET.Element:
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise InvalidSVG(f"not well-formed XML: {exc}") from exc
    if _local(root.tag) != "svg":
        raise InvalidSVG(f"root element is <{_local(root.tag)}>, expected <svg>")
    return root


def _style_props(element: ET.Element) -> dict[str, str]:
    """Presentation attributes merged with anything in a `style=` attribute.

    The sample documents carry `style="fill: none; stroke: rgb(0,0,0)"`, so
    reading only the attributes would see no strokes at all.
    """
    props = {k: v for k, v in element.attrib.items() if k in ("fill", "stroke")}
    for declaration in element.get("style", "").split(";"):
        name, _, value = declaration.partition(":")
        name = name.strip().lower()
        if name in ("fill", "stroke"):
            props[name] = value.strip()
    return props


def _is_grey(color: str) -> bool:
    color = color.strip().lower()
    if color in ("grey", "gray"):
        return True
    if match := _HEX.match(color):
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return digits[0:2] == digits[2:4] == digits[4:6]
    if match := _RGB.match(color):
        r, g, b = (int(v) for v in match.groups())
        return r == g == b
    return False


def stroke_count(element: ET.Element) -> int:
    """One continuous pen movement, as the prompt defines it."""
    name = _local(element.tag)
    if name == "path":
        return len(_SUBPATH.findall(element.get("d", ""))) or 1
    if name in ("polyline", "line"):
        return 1
    return 0


def _ai_turn_groups(root: ET.Element) -> list[ET.Element]:
    return [
        child
        for child in root
        if _local(child.tag) == "g" and AI_TURN_ID.match(child.get("id", ""))
    ]


def _shapes(element: ET.Element) -> list[ET.Element]:
    return [e for e in element.iter() if _local(e.tag) in ("path", "polyline", "line")]


def _signature(root: ET.Element) -> list[tuple[str, str]]:
    """Ordered fingerprint of every mark, for the preservation check."""
    return [
        (_local(e.tag), (e.get("d") or e.get("points") or "").strip())
        for e in _shapes(root)
    ]


def count_new_user_strokes(svg: str) -> int:
    """Strokes the user has added since the last AI turn.

    Leans on the invariant that document order is chronological: anything after
    the final `ai-turn-` group is new.
    """
    root = parse(svg)
    children = list(root)
    last_ai = -1
    for index, child in enumerate(children):
        if _local(child.tag) == "g" and AI_TURN_ID.match(child.get("id", "")):
            last_ai = index
    return sum(
        stroke_count(shape)
        for child in children[last_ai + 1 :]
        for shape in _shapes(child)
    )


def review(input_svg: str, response_text: str) -> Review:
    """Compare a response against the document that was sent."""
    result = Review(svg="")

    body = extract_document(response_text)
    if body.strip() != response_text.strip():
        result.warnings.append(
            "Response had text around the SVG; the prompt asks for the document alone."
        )
    result.svg = body

    out_root = parse(body)
    in_root = parse(input_svg)

    before = _ai_turn_groups(in_root)
    after = _ai_turn_groups(out_root)
    result.new_user_strokes = count_new_user_strokes(input_svg)

    if len(after) == len(before):
        if result.new_user_strokes == 0:
            result.warnings.append("No new user strokes; document returned unchanged.")
            return result
        result.errors.append("No new ai-turn group in the response.")
        return result
    if len(after) != len(before) + 1:
        result.errors.append(
            f"Expected {len(before) + 1} ai-turn groups, found {len(after)}."
        )
        return result

    new_layer = after[-1]
    if list(out_root)[-1] is not new_layer:
        result.errors.append("The new ai-turn group is not the last element.")

    expected_id = f"ai-turn-{len(before) + 1}"
    if new_layer.get("id") != expected_id:
        result.warnings.append(
            f"New layer is id={new_layer.get('id')!r}, expected {expected_id!r}."
        )

    # --- what's inside the new layer -------------------------------------
    for element in new_layer.iter():
        name = _local(element.tag)
        if element is new_layer or name in ALLOWED_IN_AI_LAYER:
            continue
        result.errors.append(f"Disallowed <{name}> inside the ai-turn group.")

    for element in [new_layer, *_shapes(new_layer)]:
        props = _style_props(element)
        fill = props.get("fill", "").strip().lower()
        if fill and fill != "none":
            result.errors.append(f"ai-turn layer sets fill={fill!r}; must be none.")
        stroke = props.get("stroke", "").strip()
        if not stroke:
            continue
        if not _is_grey(stroke):
            result.errors.append(f"ai-turn layer stroke {stroke!r} is not grey.")
        elif stroke.lower() != LAYER_GREY:
            result.warnings.append(
                f"ai-turn layer stroke is {stroke!r}, not {LAYER_GREY}."
            )

    # --- did anything already on the page move? --------------------------
    old = _signature(in_root)
    new = _signature(out_root)
    if new[: len(old)] != old:
        result.warnings.append(
            "Existing marks were modified or reordered — the prompt says reproduce "
            "the input exactly."
        )

    # --- budget ----------------------------------------------------------
    result.ai_strokes = sum(stroke_count(s) for s in _shapes(new_layer))
    if result.ai_strokes > result.new_user_strokes:
        result.warnings.append(
            f"Over budget: {result.ai_strokes} AI strokes against "
            f"{result.new_user_strokes} new user strokes."
        )

    return result
