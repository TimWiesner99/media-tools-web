"""Main converter logic for transforming EDL and source lists into a definitive archive list."""

from __future__ import annotations

import pandas as pd
from pathlib import Path
from typing import Optional

from .timecode import Timecode
from .models import EDLEntry, SourceEntry, DefEntry
from .exclusion import ExclusionRuleSet, filter_edl_entries


# Encodings to try when reading files
ENCODINGS = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']


def read_file_with_encoding(filepath: Path) -> tuple[str, str]:
    """Read a file trying multiple encodings.

    Args:
        filepath: Path to the file

    Returns:
        Tuple of (content, encoding_used)

    Raises:
        UnicodeDecodeError: If no encoding works
    """
    for encoding in ENCODINGS:
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                content = f.read()
            return content, encoding
        except UnicodeDecodeError:
            continue

    raise UnicodeDecodeError(
        'all', b'', 0, 1,
        f"Could not decode file with any of: {ENCODINGS}"
    )


# Supported text/CSV extensions (read via pd.read_csv)
_TEXT_EXTENSIONS = {'.csv', '.tsv', '.txt'}


def _detect_csv_delimiter(content: str) -> str:
    """Pick the best CSV delimiter by inspecting the first 20 lines.

    Checks for tabs vs commas across multiple lines (not just the first)
    so that preamble rows with no delimiters don't mislead the detection.
    """
    lines = content.split('\n')[:20]
    tab_max = max((line.count('\t') for line in lines), default=0)
    comma_max = max((line.count(',') for line in lines), default=0)
    return '\t' if tab_max >= comma_max and tab_max > 0 else ','


def _find_header_line_in_text(
    lines: list[str],
    delimiter: str,
    known_columns: set[str],
    min_matches: int = 2,
) -> int:
    """Find the header line index in raw text lines using known column names.

    Scans the first 20 lines, splits each by the delimiter, and picks the
    line where the most cells match known column names.

    Returns:
        0-based line index of the best header match, or 0 if nothing found.
    """
    max_scan = min(20, len(lines))
    best_idx = 0
    best_count = 0

    for i in range(max_scan):
        cells = [c.strip().strip('"') for c in lines[i].split(delimiter)]
        match_count = sum(
            1 for c in cells
            if c and normalize_column_name(c) in known_columns
        )
        if match_count > best_count:
            best_count = match_count
            best_idx = i

    return best_idx if best_count >= min_matches else 0


def _read_file_as_dataframe(
    filepath: Path,
    header: int | None = 0,
    skiprows: int = 0,
) -> pd.DataFrame:
    """Read a file into a DataFrame, dispatching by file extension.

    Args:
        filepath: Path to the input file
        header: Row number to use as headers (0 = first row, None = no header).
        skiprows: Number of leading rows to skip before parsing (CSV only,
            used to skip preamble when the header row is known).

    Returns:
        DataFrame with string data types and NaN filled as empty strings

    Raises:
        ValueError: If the file format is unsupported
    """
    suffix = filepath.suffix.lower()

    if suffix == '.xlsx':
        df = pd.read_excel(filepath, dtype=str, engine='openpyxl', header=header)
    elif suffix == '.ods':
        df = pd.read_excel(filepath, dtype=str, engine='odf', header=header)
    elif suffix in _TEXT_EXTENSIONS:
        content, encoding = read_file_with_encoding(filepath)
        delimiter = _detect_csv_delimiter(content)
        df = pd.read_csv(
            filepath, delimiter=delimiter, dtype=str,
            encoding=encoding, header=header, skiprows=skiprows,
        )
    else:
        raise ValueError(
            f"Unsupported file format: '{suffix}'. "
            f"Supported formats: .xlsx, .ods, .csv, .tsv"
        )

    df = df.fillna("")
    return df


def _collect_known_column_names(column_maps: list[dict[str, str]]) -> set[str]:
    """Collect all known column names from column maps, normalized.

    Args:
        column_maps: List of column name mappings (key = expected column name)

    Returns:
        Set of normalized known column names
    """
    known: set[str] = set()
    for cmap in column_maps:
        for col_name in cmap:
            known.add(normalize_column_name(col_name))
    return known


def find_header_row(
    df: pd.DataFrame,
    known_columns: set[str],
    min_matches: int = 2,
) -> int | None:
    """Find the row that contains column headers by matching known column names.

    Scans the first rows of a headerless DataFrame to find the row where the
    most cells match known column names. Returns that row's index if it has
    at least *min_matches* hits, otherwise ``None``.

    Args:
        df: DataFrame read with ``header=None`` (columns are 0, 1, 2, …)
        known_columns: Set of normalized known column names
        min_matches: Minimum number of matching cells required

    Returns:
        0-based row index of the header row, or None if not found
    """
    max_scan_rows = min(20, len(df))
    best_row: int | None = None
    best_count = 0

    for row_idx in range(max_scan_rows):
        row_values = df.iloc[row_idx]
        match_count = sum(
            1 for cell in row_values
            if str(cell).strip()
            and normalize_column_name(str(cell)) in known_columns
        )
        if match_count > best_count:
            best_count = match_count
            best_row = row_idx

    if best_count >= min_matches:
        return best_row
    return None


def read_input_file(
    filepath: Path,
    known_columns: set[str] | None = None,
) -> pd.DataFrame:
    """Read an input file (CSV, TSV, XLSX, or ODS) into a DataFrame.

    When *known_columns* is provided the function auto-detects which row
    contains the actual column headers (skipping any preamble rows such as
    project notes or metadata).  The detected offset is stored in
    ``df.attrs['header_row_offset']`` so callers can adjust row numbers in
    error messages.

    Args:
        filepath: Path to the input file
        known_columns: Optional set of normalized column names used to
            detect the header row.  When ``None``, the first row is used.

    Returns:
        DataFrame with string data types and NaN filled as empty strings

    Raises:
        ValueError: If the file format is unsupported
        UnicodeDecodeError: If CSV encoding cannot be determined
    """
    filepath = Path(filepath)
    suffix = filepath.suffix.lower()

    if known_columns is None:
        # Original fast path — first row is the header
        df = _read_file_as_dataframe(filepath, header=0)
        df.attrs['header_row_offset'] = 0
        return df

    # --- Header auto-detection path ---

    if suffix in _TEXT_EXTENSIONS:
        # For CSV/TSV: detect header row from raw text, then read with skiprows
        # (avoids pandas column-count mismatch when preamble rows have fewer fields)
        content, encoding = read_file_with_encoding(filepath)
        delimiter = _detect_csv_delimiter(content)
        lines = [l for l in content.split('\n') if l.strip()]
        offset = _find_header_line_in_text(lines, delimiter, known_columns)

        df = pd.read_csv(
            filepath, delimiter=delimiter, dtype=str,
            encoding=encoding, header=0, skiprows=range(offset),
        )
        df = df.fillna("")
        df.attrs['header_row_offset'] = offset
        return df

    # For spreadsheets (xlsx, ods): read without header, scan DataFrame
    df_raw = _read_file_as_dataframe(filepath, header=None)

    if df_raw.empty:
        df_raw.attrs['header_row_offset'] = 0
        return df_raw

    header_row = find_header_row(df_raw, known_columns)
    offset = header_row if header_row is not None else 0

    # Use the detected (or fallback) row as column headers
    new_headers = [str(v).strip() for v in df_raw.iloc[offset]]
    df = df_raw.iloc[offset + 1:].reset_index(drop=True)
    df.columns = new_headers
    df.attrs['header_row_offset'] = offset
    return df


# Column name mappings: English -> internal name
EDL_COLUMN_MAP = {
    # English names (from AVID/Premiere)
    "id": "id",
    "reel": "reel",
    "name": "name",
    "file name": "file_name",
    "track": "track",
    "timecode in": "timecode_in",
    "timecode out": "timecode_out",
    "duration": "duration",
    "source start": "source_start",
    "source end": "source_end",
    "audio channels": "audio_channels",
    "comment": "comment",
}

# Dutch alternative column names for EDL
EDL_DUTCH_MAP = {
    "bestandsnaam": "name",
    "duur": "duration",
    "tc in": "timecode_in",
    "tc uit": "timecode_out",
    "bron start": "source_start",
    "bron einde": "source_end",
}

# Source list column mappings
SOURCE_COLUMN_MAP = {
    # Dutch names (common in Dutch production)
    "bestandsnaam": "name",
    "omschrijving": "description",
    "link": "link",
    "bron": "source",
    "kosten": "cost",
    "rechten / contact": "rights_contact",
    "rechten/contact": "rights_contact",  # Alternative format
    "to do/opmerkinen": "todo_notes",
    "to do/opmerkingen": "todo_notes",  # Alternative spelling
    "to do": "todo_notes",
    "bron in beeld": "source_in_frame",
    "aftiteling": "credits",
    # Also for DEF format (slightly different column names)
    "tc in": "timecode_in",
    "duur": "duration",
    "prijs nl": "price_nl",
    "prijs sales": "price_sales",
}

# English source column names
SOURCE_ENGLISH_MAP = {
    "name": "name",
    "description": "description",
    "link": "link",
    "source": "source",
    "cost": "cost",
    "rights_contact": "rights_contact",
    "todo_notes": "todo_notes",
    "source_in_frame": "source_in_frame",
    "credits": "credits",
}


def normalize_column_name(col: str) -> str:
    """Normalize a column name for matching.

    Args:
        col: Original column name

    Returns:
        Normalized lowercase column name
    """
    return col.strip().lower()


def map_columns(df: pd.DataFrame, column_maps: list[dict[str, str]]) -> pd.DataFrame:
    """Map DataFrame columns to internal names using provided mappings.

    Args:
        df: DataFrame with original column names
        column_maps: List of column name mappings to try (in order)

    Returns:
        DataFrame with renamed columns
    """
    # Create a combined mapping from all provided maps
    combined_map = {}
    for cmap in column_maps:
        combined_map.update(cmap)

    # Normalize column names and find matches
    rename_map = {}
    for col in df.columns:
        normalized = normalize_column_name(col)
        if normalized in combined_map:
            rename_map[col] = combined_map[normalized]

    return df.rename(columns=rename_map)


def load_edl(filepath: Path | str, fps: int = 25) -> list[EDLEntry]:
    """Load an EDL from a CSV or Excel file.

    Args:
        filepath: Path to the EDL file (.csv, .tsv, .xlsx, or .ods)
        fps: Frame rate for timecode parsing

    Returns:
        List of EDLEntry objects
    """
    filepath = Path(filepath)

    known = _collect_known_column_names([EDL_COLUMN_MAP, EDL_DUTCH_MAP])
    df = read_input_file(filepath, known_columns=known)

    # Map columns
    df = map_columns(df, [EDL_COLUMN_MAP, EDL_DUTCH_MAP])

    entries = []
    for _, row in df.iterrows():
        row_dict = row.to_dict()

        # Skip rows without a name
        name = row_dict.get("name", "")
        if not name or name.strip() == "":
            continue

        try:
            entry = EDLEntry.from_dict(row_dict, fps=fps)
            entries.append(entry)
        except ValueError as e:
            print(f"Warning: Skipping row due to error: {e}")
            continue

    return entries


def load_source(filepath: Path | str) -> list[SourceEntry]:
    """Load a source list from a CSV or Excel file.

    Args:
        filepath: Path to the source file (.csv, .tsv, .xlsx, or .ods)

    Returns:
        List of SourceEntry objects

    Raises:
        ValueError: If validation fails (e.g., conflicting price fields)
    """
    filepath = Path(filepath)

    known = _collect_known_column_names([SOURCE_COLUMN_MAP, SOURCE_ENGLISH_MAP])
    df = read_input_file(filepath, known_columns=known)

    # Map columns
    header_offset = df.attrs.get('header_row_offset', 0)
    df = map_columns(df, [SOURCE_COLUMN_MAP, SOURCE_ENGLISH_MAP])

    entries = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()

        # Skip rows without a name
        name = row_dict.get("name", "")
        if not name or name.strip() == "":
            continue

        # Row number for error messages (add 2: 1 for header, 1 for 0-indexing,
        # plus any preamble rows that were skipped)
        row_number = idx + 2 + header_offset

        try:
            entry = SourceEntry.from_dict(row_dict, row_number=row_number)
            entries.append(entry)
        except ValueError as e:
            # Re-raise with file context
            raise ValueError(f"Error in source file '{filepath.name}': {e}") from e

    return entries


def validate_edl_file(filepath: Path | str, fps: int = 25) -> list[str]:
    """Validate an EDL file has correct format and parseable content.

    Checks:
    - File is not empty (beyond headers)
    - Required columns are present after mapping
    - Timecodes are parseable

    Args:
        filepath: Path to the EDL file (.csv, .tsv, .xlsx, or .ods)
        fps: Frame rate for timecode validation

    Returns:
        List of error messages (empty if valid)
    """
    filepath = Path(filepath)
    errors = []

    try:
        known = _collect_known_column_names([EDL_COLUMN_MAP, EDL_DUTCH_MAP])
        df = read_input_file(filepath, known_columns=known)
    except (UnicodeDecodeError, ValueError) as e:
        return [f"Could not read file: {e}"]
    except Exception as e:
        return [f"Could not parse file: {e}"]

    if df.empty:
        return ["File is empty or contains only headers."]

    header_offset = df.attrs.get('header_row_offset', 0)
    df = map_columns(df, [EDL_COLUMN_MAP, EDL_DUTCH_MAP])

    # Check required columns
    required = ["name", "timecode_in", "timecode_out", "duration", "source_start", "source_end"]
    mapped_cols = set(df.columns)
    missing = [col for col in required if col not in mapped_cols]
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}")
        errors.append(f"  Found columns: {', '.join(df.columns.tolist())}")
        return errors

    # Check for data rows
    data_rows = df[df["name"].str.strip() != ""]
    if len(data_rows) == 0:
        errors.append("No data rows found (all 'Name' fields are empty).")
        return errors

    # Validate timecodes on first few rows
    tc_cols = ["timecode_in", "timecode_out", "duration", "source_start", "source_end"]
    for idx, row in data_rows.head(5).iterrows():
        for col in tc_cols:
            val = str(row.get(col, "")).strip()
            if val and val != "00:00:00:00":
                if not Timecode.TIMECODE_PATTERN.match(val):
                    errors.append(f"Row {idx + 2 + header_offset}, column '{col}': invalid timecode format '{val}' (expected HH:MM:SS:FF)")

    return errors


def validate_source_file(filepath: Path | str) -> list[str]:
    """Validate a source file has correct format and content.

    Checks:
    - File is not empty (beyond headers)
    - Required 'name'/'Bestandsnaam' column is present

    Args:
        filepath: Path to the source file (.csv, .tsv, .xlsx, or .ods)

    Returns:
        List of error messages (empty if valid)
    """
    filepath = Path(filepath)
    errors = []

    try:
        known = _collect_known_column_names([SOURCE_COLUMN_MAP, SOURCE_ENGLISH_MAP])
        df = read_input_file(filepath, known_columns=known)
    except (UnicodeDecodeError, ValueError) as e:
        return [f"Could not read file: {e}"]
    except Exception as e:
        return [f"Could not parse file: {e}"]

    if df.empty:
        return ["File is empty or contains only headers."]
    df = map_columns(df, [SOURCE_COLUMN_MAP, SOURCE_ENGLISH_MAP])

    # Check required column
    if "name" not in df.columns:
        errors.append("Missing required column: 'Bestandsnaam' (or 'name')")
        errors.append(f"  Found columns: {', '.join(df.columns.tolist())}")
        return errors

    # Check for data rows
    data_rows = df[df["name"].str.strip() != ""]
    if len(data_rows) == 0:
        errors.append("No data rows found (all 'Bestandsnaam' fields are empty).")
        return errors

    return errors


def normalize_name(name: str) -> str:
    """Normalize a file name for matching.

    Removes file extensions and normalizes case and whitespace.

    Args:
        name: Original file name

    Returns:
        Normalized name for matching
    """
    name = name.strip()
    # Remove common extensions
    for ext in ['.mxf', '.mp4', '.mov', '.avi', '.sync.01', '.sync.02', '.sync']:
        if name.lower().endswith(ext):
            name = name[:-len(ext)]
    return name.lower().strip()


def safe_source_usage(source_start: Timecode, source_end: Timecode, fps: int = 25) -> Timecode:
    """Calculate source usage duration with a minimum of 1 second.

    When source timecodes have been clamped due to framerate mismatch (e.g. 50fps
    source in a 25fps project), source_end can end up less than source_start for
    very short clips within the same second. In that case, return 1 second as the
    minimum duration rather than crashing.

    Args:
        source_start: Source start timecode
        source_end: Source end timecode
        fps: Frame rate

    Returns:
        Duration timecode (minimum 1 second)
    """
    diff_frames = source_end.to_frames() - source_start.to_frames()
    if diff_frames < fps:
        # Less than 1 second (or negative) — use 1 second minimum
        return Timecode.from_frames(fps, fps)
    return Timecode.from_frames(diff_frames, fps)


def collapse_edl(entries: list[EDLEntry], fps: int = 25, verbose: bool = False) -> list[EDLEntry]:
    """Collapse consecutive EDL entries with the same name and continuous source timecodes.

    When two or more consecutive entries have the same name AND their source
    timecodes are continuous (within 1 second), they are combined into a single
    entry with:
    - timecode_in from the first entry
    - timecode_out from the last entry
    - Combined duration
    - source_start as the minimum of all source_starts
    - source_end as the maximum of all source_ends

    Entries with the same name but non-continuous source positions (e.g. different
    parts of the same source clip) are kept separate.

    Args:
        entries: List of EDL entries
        fps: Frame rate for timecode operations
        verbose: If True, print details for each collapse operation

    Returns:
        List of collapsed EDL entries
    """
    if not entries:
        return []

    one_second = Timecode.from_frames(fps, fps)

    collapsed = []
    i = 0

    while i < len(entries):
        current = entries[i]

        # Look ahead for consecutive entries with the same name AND continuous source timecodes
        j = i + 1
        while j < len(entries) and entries[j].name == current.name:
            prev = entries[j - 1]
            curr = entries[j]
            # Check if source timecodes are continuous (within 1 second margin)
            gap_frames = curr.source_start.to_frames() - prev.source_end.to_frames()
            if gap_frames < 0:
                gap_frames = -gap_frames
            if gap_frames > one_second.to_frames():
                break
            j += 1

        # If we found consecutive duplicates with continuous source
        if j > i + 1:
            # Combine all entries from i to j-1
            group = entries[i:j]

            if verbose:
                print(f"  Collapsing {len(group)} consecutive entries: \"{current.name}\"")

            # Calculate combined values
            combined_duration = Timecode.from_frames(0, fps)
            source_usage_sum = Timecode.from_frames(0, fps)
            min_source_start = group[0].source_start
            max_source_end = group[0].source_end

            for entry in group:
                combined_duration = combined_duration + entry.duration
                # Accumulate each individual source usage (preserving actual durations)
                if entry.source_total_usage is not None:
                    source_usage_sum = source_usage_sum + entry.source_total_usage
                else:
                    source_usage_sum = source_usage_sum + safe_source_usage(entry.source_start, entry.source_end, fps)
                if entry.source_start < min_source_start:
                    min_source_start = entry.source_start
                if entry.source_end > max_source_end:
                    max_source_end = entry.source_end

            # Create combined entry
            combined = EDLEntry(
                id=current.id,
                name=current.name,
                timecode_in=group[0].timecode_in,
                timecode_out=group[-1].timecode_out,
                duration=combined_duration,
                source_start=min_source_start,
                source_end=max_source_end,
                reel=current.reel,
                file_name=current.file_name,
                track=current.track,
                audio_channels=current.audio_channels,
                comment=current.comment,
                source_total_usage=source_usage_sum,
            )
            collapsed.append(combined)
        else:
            # No consecutive duplicate, keep as is
            collapsed.append(current)

        i = j

    return collapsed


def find_source_match(
    edl_name: str,
    sources: list[SourceEntry]
) -> Optional[SourceEntry]:
    """Find a matching source entry for an EDL entry name.

    Uses normalized name matching to handle file extensions and case differences.

    Args:
        edl_name: Name from the EDL entry
        sources: List of source entries to search

    Returns:
        Matching SourceEntry or None if not found
    """
    normalized_edl = normalize_name(edl_name)

    for source in sources:
        normalized_source = normalize_name(source.name)

        # Exact match after normalization
        if normalized_edl == normalized_source:
            return source

        # Check if one contains the other (for partial matches)
        if normalized_edl in normalized_source or normalized_source in normalized_edl:
            return source

    return None


def generate_def_list(
    edl_entries: list[EDLEntry],
    source_entries: list[SourceEntry],
    verbose: bool = False
) -> list[DefEntry]:
    """Generate the definitive archive list from EDL and source entries.

    For each EDL entry, finds the matching source entry and combines them.
    If no source is found, the DEF entry has empty metadata fields.

    Args:
        edl_entries: List of (collapsed) EDL entries
        source_entries: List of source entries
        verbose: If True, print details for each match

    Returns:
        List of DefEntry objects
    """
    def_list = []

    for i, edl in enumerate(edl_entries, start=1):
        source = find_source_match(edl.name, source_entries)
        def_entry = DefEntry.from_edl_and_source(edl, source)
        def_list.append(def_entry)

        if verbose:
            if source:
                print(f"  Entry {i}: \"{edl.name}\" -> matched source \"{source.name}\"")
            else:
                print(f"  Entry {i}: \"{edl.name}\" -> NO SOURCE MATCH")

    return def_list


def annotate_occurrences(def_list: list[DefEntry]) -> None:
    """Annotate each DefEntry with its occurrence number and total occurrences.

    After annotation, each entry knows how many times its source appears in total
    and which occurrence it is (1-based). This is used to:
    - Show "Nummer/aantal" (e.g. "2/4") in the output
    - Show cost only on first occurrence, "zie boven" on subsequent

    Modifies entries in place.

    Args:
        def_list: List of DefEntry objects to annotate
    """
    from collections import Counter

    # Count total occurrences of each name
    name_counts = Counter(entry.name for entry in def_list)

    # Track current occurrence number per name
    current_occurrence: dict[str, int] = {}

    for entry in def_list:
        name = entry.name
        current_occurrence[name] = current_occurrence.get(name, 0) + 1
        entry.occurrence_number = current_occurrence[name]
        entry.total_occurrences = name_counts[name]


def print_exclusion_summary(
    excluded: list[EDLEntry],
    rules: ExclusionRuleSet,
    verbose: bool = False
) -> None:
    """Print summary statistics about exclusions.

    Shows total excluded and breakdown by rule.

    Args:
        excluded: List of excluded entries
        rules: The exclusion rule set
        verbose: If True, show detailed breakdown by rule
    """
    if not excluded:
        return

    print(f"\nExclusion Summary:")
    print(f"  Total excluded: {len(excluded)}")

    if verbose:
        stats = rules.get_exclusion_stats(excluded)
        print(f"\n  Breakdown by rule:")
        for rule in rules.rules:
            count = stats.get(rule.line_number, 0)
            if count > 0:
                print(f"    Rule {rule.line_number}: {count} entries")
                print(f"      \"{rule.text}\"")


def save_def_list(
    def_list: list[DefEntry],
    output_path: Path | str,
    delimiter: str = ',',
    include_frames: bool = False
) -> None:
    """Save the definitive list to a CSV file.

    Args:
        def_list: List of DefEntry objects to save
        output_path: Path for the output file
        delimiter: CSV delimiter (default is comma)
        include_frames: If True, include frame-level precision in timecodes
    """
    output_path = Path(output_path)

    # Convert to list of dicts
    rows = [entry.to_dict(include_frames=include_frames) for entry in def_list]

    # Create DataFrame and save
    df = pd.DataFrame(rows)
    df.to_csv(output_path, sep=delimiter, index=False)


def read_raw_input(
    filepath: Path | str,
    known_columns: set[str] | None = None,
) -> pd.DataFrame:
    """Read an input file as-is, preserving original column names and data.

    Supports CSV, TSV, XLSX, and ODS formats.  When *known_columns* is
    provided, preamble rows before the header are skipped.

    Args:
        filepath: Path to the input file
        known_columns: Optional set of normalized column names for header detection

    Returns:
        DataFrame with original column names and string data
    """
    return read_input_file(Path(filepath), known_columns=known_columns)


def save_excel_output(
    edl_path: Path | str,
    source_path: Path | str,
    def_list: list[DefEntry],
    output_path: Path | str,
    include_frames: bool = False
) -> None:
    """Save all data to a single Excel file with three sheets.

    Sheets:
    - SOURCE: Original source archive list
    - EDL: Original edit decision list
    - DEF: Definitive archive list (with occurrence tracking and cost deduplication)

    Args:
        edl_path: Path to the original EDL CSV file
        source_path: Path to the original source CSV file
        def_list: List of DefEntry objects (already annotated with occurrences)
        output_path: Path for the output Excel file (.xlsx)
        include_frames: If True, include frame-level precision in timecodes
    """
    output_path = Path(output_path)

    # Read raw input files (with header detection to skip preamble)
    edl_known = _collect_known_column_names([EDL_COLUMN_MAP, EDL_DUTCH_MAP])
    source_known = _collect_known_column_names([SOURCE_COLUMN_MAP, SOURCE_ENGLISH_MAP])
    edl_raw = read_raw_input(edl_path, known_columns=edl_known)
    source_raw = read_raw_input(source_path, known_columns=source_known)

    # Build DEF DataFrame
    def_rows = [entry.to_dict(include_frames=include_frames) for entry in def_list]
    def_df = pd.DataFrame(def_rows)

    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        source_raw.to_excel(writer, sheet_name="SOURCE", index=False)
        edl_raw.to_excel(writer, sheet_name="EDL", index=False)
        def_df.to_excel(writer, sheet_name="DEF", index=False)

        workbook = writer.book

        # Define formats
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#B8CCE4',  # Light blue
        })
        currency_format = workbook.add_format({
            'num_format': '€ #,##0',  # Euro, no decimals
        })
        currency_header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#B8CCE4',
            'num_format': '€ #,##0',
        })
        text_format = workbook.add_format({
            'num_format': '@',  # Explicit text format to prevent fraction interpretation
        })

        # Apply header formatting to all sheets
        for sheet_name, df in [
            ("SOURCE", source_raw),
            ("EDL", edl_raw),
            ("DEF", def_df),
        ]:
            worksheet = writer.sheets[sheet_name]
            for col_num, col_name in enumerate(df.columns):
                worksheet.write(0, col_num, col_name, header_format)

        # DEF sheet specific formatting
        def_sheet = writer.sheets["DEF"]

        # Format Kosten column as numbers (skip "-" entries for repeated sources)
        if "Kosten" in def_df.columns:
            kosten_col = def_df.columns.get_loc("Kosten")
            for row_num, value in enumerate(def_df["Kosten"], start=1):
                if value and str(value).strip():
                    str_value = str(value).strip()
                    if str_value == "-":
                        # Keep as text string (repeated source, already accounted for)
                        def_sheet.write_string(row_num, kosten_col, str_value)
                    else:
                        # Parse string to number, removing currency symbols and whitespace
                        clean_value = str_value.replace('€', '').replace(',', '.').strip()
                        try:
                            num_value = float(clean_value)
                            def_sheet.write_number(row_num, kosten_col, num_value, currency_format)
                        except ValueError:
                            # Keep as string if not parseable
                            def_sheet.write(row_num, kosten_col, value)

        # Format Nummer/aantal column as text to prevent "2/4" being interpreted as a date/fraction
        if "Nummer/aantal" in def_df.columns:
            nummer_col = def_df.columns.get_loc("Nummer/aantal")
            for row_num, value in enumerate(def_df["Nummer/aantal"], start=1):
                if value and str(value).strip():
                    def_sheet.write_string(row_num, nummer_col, str(value), text_format)

        # Add Kosten sum to DEF sheet
        if "Kosten" in def_df.columns:
            kosten_col = def_df.columns.get_loc("Kosten")
            data_rows = len(def_df)
            # Sum row: 3 rows below last data (row 0 is header, so last data is at row data_rows)
            sum_row = data_rows + 1 + 3  # +1 for header, +3 for empty rows
            # Excel formula uses 1-based row numbers; data starts at row 2
            sum_formula = f"=SUM({chr(65 + kosten_col)}2:{chr(65 + kosten_col)}{data_rows + 1})"
            # Add "Kosten totaal" label to the left of the sum cell
            def_sheet.write(sum_row, kosten_col - 1, "Kosten totaal", header_format)
            def_sheet.write_formula(sum_row, kosten_col, sum_formula, currency_header_format)


def convert(
    edl_path: Path | str,
    source_path: Path | str,
    output_path: Path | str,
    fps: int = 25,
    collapse: bool = True,
    exclusion_rules: ExclusionRuleSet | None = None,
    verbose: bool = False,
    verbose_level: int = 1,
    include_frames: bool = False
) -> list[DefEntry]:
    """Main conversion function: EDL + Source -> Definitive List.

    Args:
        edl_path: Path to the EDL file (.csv, .tsv, .xlsx, or .ods)
        source_path: Path to the source list file (.csv, .tsv, .xlsx, or .ods)
        output_path: Path for the output Excel file (.xlsx)
        fps: Frame rate for timecode handling
        collapse: Whether to collapse consecutive same-name entries
        exclusion_rules: Optional exclusion rules to filter entries before processing
        verbose: If True, print detailed progress for each entry
        verbose_level: 1 = basic output, 2 = detailed evaluation traces
        include_frames: If True, include frame-level precision in output timecodes

    Returns:
        List of DefEntry objects that were saved
    """
    # Step 1: Load EDL
    print("Loading EDL file...")
    edl_entries = load_edl(edl_path, fps=fps)
    print(f"  Loaded {len(edl_entries)} EDL entries")

    # Step 2: Load source
    print("Loading source file...")
    source_entries = load_source(source_path)
    print(f"  Loaded {len(source_entries)} source entries")

    # Step 3: Apply exclusion rules (before collapse)
    if exclusion_rules:
        print(f"Applying {len(exclusion_rules)} exclusion rules...")
        edl_entries, excluded = filter_edl_entries(
            edl_entries, exclusion_rules, verbose=verbose, verbose_level=verbose_level
        )
        print(f"  Excluded {len(excluded)} entries, {len(edl_entries)} remaining")

        # Print summary if verbose
        if verbose:
            print_exclusion_summary(excluded, exclusion_rules, verbose=True)

    # Step 4: Collapse EDL if requested
    if collapse:
        print("Collapsing consecutive entries...")
        before_count = len(edl_entries)
        edl_entries = collapse_edl(edl_entries, fps=fps, verbose=verbose)
        print(f"  Collapsed {before_count} entries to {len(edl_entries)}")

    # Step 5: Generate DEF list
    print("Matching EDL entries with sources...")
    def_list = generate_def_list(edl_entries, source_entries, verbose=verbose)
    matched = sum(1 for d in def_list if d.link)
    print(f"  Matched {matched}/{len(def_list)} entries with sources")

    # Step 6: Annotate occurrences (for Nummer/aantal and cost deduplication)
    print("Annotating source occurrences...")
    annotate_occurrences(def_list)

    # Step 7: Save Excel output
    output_path = Path(output_path)
    if output_path.suffix.lower() != ".xlsx":
        output_path = output_path.with_suffix(".xlsx")
    print("Saving Excel output...")
    save_excel_output(
        edl_path=edl_path,
        source_path=source_path,
        def_list=def_list,
        output_path=output_path,
        include_frames=include_frames,
    )
    print(f"  Saved Excel file to {output_path}")

    return def_list
