"""Web-facing wrapper around the EDL conversion pipeline.

Calls the core converter functions and returns a ConversionResult
with stats plus the path to the generated XLSX file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .converter import (
    annotate_occurrences,
    collapse_edl,
    generate_def_list,
    load_edl,
    load_source,
    save_excel_output,
    filter_edl_entries,
)
from .exclusion import ExclusionRuleSet, parse_exclusion_rules, ExclusionRuleSyntaxError


@dataclass
class ConversionResult:
    edl_count: int
    source_count: int
    excluded_count: int
    collapsed_count: int   # entries removed by collapse (before - after)
    def_count: int
    matched_count: int     # entries with a source match


class ConversionError(Exception):
    pass


def run_conversion(
    edl_path: Path,
    source_path: Path,
    output_path: Path,
    fps: int = 25,
    collapse: bool = True,
    include_frames: bool = False,
    exclusion_rules_text: str = "",
) -> ConversionResult:
    """Run the full EDL → XLSX pipeline and return stats.

    Raises ConversionError on validation or processing failures.
    """
    # Parse exclusion rules
    rules: ExclusionRuleSet | None = None
    if exclusion_rules_text.strip():
        try:
            rules = parse_exclusion_rules(exclusion_rules_text)
        except ExclusionRuleSyntaxError as e:
            raise ConversionError(f"Exclusion rule syntax error: {e}") from e

    # Load files
    try:
        edl_entries = load_edl(edl_path, fps=fps)
    except Exception as e:
        raise ConversionError(f"Could not read EDL file: {e}") from e

    if not edl_entries:
        raise ConversionError("EDL file contains no valid entries.")

    try:
        source_entries = load_source(source_path)
    except Exception as e:
        raise ConversionError(f"Could not read Source file: {e}") from e

    edl_count = len(edl_entries)
    source_count = len(source_entries)

    # Apply exclusion rules
    excluded_count = 0
    if rules and len(rules) > 0:
        edl_entries, excluded = filter_edl_entries(edl_entries, rules)
        excluded_count = len(excluded)

    # Collapse consecutive same-name entries
    before_collapse = len(edl_entries)
    if collapse:
        edl_entries = collapse_edl(edl_entries, fps=fps)
    collapsed_count = before_collapse - len(edl_entries)

    # Match EDL → sources
    def_list = generate_def_list(edl_entries, source_entries)
    annotate_occurrences(def_list)

    matched_count = sum(1 for d in def_list if d.description or d.link)

    # Save output XLSX
    try:
        save_excel_output(
            edl_path=edl_path,
            source_path=source_path,
            def_list=def_list,
            output_path=output_path,
            include_frames=include_frames,
        )
    except Exception as e:
        raise ConversionError(f"Could not write output file: {e}") from e

    return ConversionResult(
        edl_count=edl_count,
        source_count=source_count,
        excluded_count=excluded_count,
        collapsed_count=collapsed_count,
        def_count=len(def_list),
        matched_count=matched_count,
    )
