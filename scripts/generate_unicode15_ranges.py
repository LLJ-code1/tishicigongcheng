"""Generate frozen Unicode 15 assignment and lowercase tables for both runtimes."""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_OUTPUT = ROOT / "prototype" / "unicode15_data.py"
JAVASCRIPT_OUTPUT = ROOT / "prototype" / "unicode15-data.js"
UNICODE_VERSION = "15.0.0"


def assigned_ranges() -> list[tuple[int, int]]:
    if unicodedata.unidata_version != UNICODE_VERSION:
        raise RuntimeError(
            "Unicode range generation requires Python unicodedata "
            f"{UNICODE_VERSION}, found {unicodedata.unidata_version}"
        )
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    previous: int | None = None
    for code_point in range(0x110000):
        assigned = unicodedata.category(chr(code_point)) not in {"Cn", "Cs"}
        if assigned and start is None:
            start = previous = code_point
        elif assigned and previous is not None and code_point == previous + 1:
            previous = code_point
        elif assigned:
            ranges.append((start, previous))  # type: ignore[arg-type]
            start = previous = code_point
        elif start is not None:
            ranges.append((start, previous))  # type: ignore[arg-type]
            start = previous = None
    if start is not None:
        ranges.append((start, previous))  # type: ignore[arg-type]
    return ranges


def lowercase_mappings() -> list[tuple[int, str]]:
    """Return Unicode 15 default lowercase mappings for assigned scalars."""

    if unicodedata.unidata_version != UNICODE_VERSION:
        raise RuntimeError(
            "Unicode lowercase generation requires Python unicodedata "
            f"{UNICODE_VERSION}, found {unicodedata.unidata_version}"
        )
    mappings: list[tuple[int, str]] = []
    for code_point in range(0x110000):
        character = chr(code_point)
        if unicodedata.category(character) in {"Cn", "Cs"}:
            continue
        lowered = character.lower()
        if lowered != character:
            mappings.append((code_point, lowered))
    return mappings


def format_pairs(ranges: list[tuple[int, int]], *, javascript: bool) -> str:
    pairs = [
        f"[0x{start:06X}, 0x{end:06X}]"
        if javascript
        else f"(0x{start:06X}, 0x{end:06X})"
        for start, end in ranges
    ]
    lines = ["    " + ", ".join(pairs[index : index + 4]) + ","
             for index in range(0, len(pairs), 4)]
    return "\n".join(lines)


def format_lowercase_mappings(
    mappings: list[tuple[int, str]], *, javascript: bool
) -> str:
    if javascript:
        pairs = [
            f"0x{code_point:06X}: {json.dumps(value, ensure_ascii=True)}"
            for code_point, value in mappings
        ]
    else:
        pairs = [
            f"0x{code_point:06X}: {ascii(value)}"
            for code_point, value in mappings
        ]
    lines = [
        "    " + ", ".join(pairs[index : index + 3]) + ","
        for index in range(0, len(pairs), 3)
    ]
    return "\n".join(lines)


def render_python(
    ranges: list[tuple[int, int]], mappings: list[tuple[int, str]]
) -> str:
    return f'''"""Generated Unicode {UNICODE_VERSION} assignment table; do not edit."""

from bisect import bisect_right


UNICODE15_VERSION = "{UNICODE_VERSION}"
UNICODE15_ASSIGNED_RANGES = (
{format_pairs(ranges, javascript=False)}
)
UNICODE15_LOWERCASE_MAPPINGS = {{
{format_lowercase_mappings(mappings, javascript=False)}
}}
_RANGE_STARTS = tuple(start for start, _ in UNICODE15_ASSIGNED_RANGES)


def is_unicode15_assigned(code_point: int) -> bool:
    if not isinstance(code_point, int) or not 0 <= code_point <= 0x10FFFF:
        return False
    index = bisect_right(_RANGE_STARTS, code_point) - 1
    return index >= 0 and code_point <= UNICODE15_ASSIGNED_RANGES[index][1]


def lowercase_unicode15(value: str) -> str:
    return "".join(
        UNICODE15_LOWERCASE_MAPPINGS.get(ord(character), character)
        for character in value
    )
'''


def render_javascript(
    ranges: list[tuple[int, int]], mappings: list[tuple[int, str]]
) -> str:
    return f'''// Generated Unicode {UNICODE_VERSION} assignment table; do not edit.
(function (root, factory) {{
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.PROMPT_STUDIO_UNICODE15 = api;
}})(typeof globalThis !== "undefined" ? globalThis : this, function () {{
  "use strict";
  const assignedRanges = Object.freeze([
{format_pairs(ranges, javascript=True)}
  ]);
  const lowercaseMappings = Object.freeze({{
{format_lowercase_mappings(mappings, javascript=True)}
  }});

  function isAssignedCodePoint(codePoint) {{
    if (!Number.isInteger(codePoint) || codePoint < 0 || codePoint > 0x10ffff) {{
      return false;
    }}
    let low = 0;
    let high = assignedRanges.length - 1;
    while (low <= high) {{
      const middle = (low + high) >> 1;
      const [start, end] = assignedRanges[middle];
      if (codePoint < start) high = middle - 1;
      else if (codePoint > end) low = middle + 1;
      else return true;
    }}
    return false;
  }}

  function lowercase(value) {{
    let output = "";
    for (const character of String(value ?? "")) {{
      output += lowercaseMappings[character.codePointAt(0)] || character;
    }}
    return output;
  }}

  return Object.freeze({{
    unicodeVersion: "{UNICODE_VERSION}",
    assignedRanges,
    isAssignedCodePoint,
    lowercase,
  }});
}});
'''


def generated_outputs() -> dict[Path, str]:
    ranges = assigned_ranges()
    mappings = lowercase_mappings()
    return {
        PYTHON_OUTPUT: render_python(ranges, mappings),
        JAVASCRIPT_OUTPUT: render_javascript(ranges, mappings),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when committed generated files are not current",
    )
    args = parser.parse_args()
    stale: list[Path] = []
    for path, content in generated_outputs().items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(path)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
            print(path)
    if stale:
        names = ", ".join(str(path.relative_to(ROOT)) for path in stale)
        raise SystemExit(f"generated Unicode tables are stale: {names}")


if __name__ == "__main__":
    main()
