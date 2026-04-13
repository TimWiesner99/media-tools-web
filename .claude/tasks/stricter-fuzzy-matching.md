# Stricter Fuzzy Filename Matching in edl-to-archive

## Context

The `find_source_match()` function in `converter.py` uses raw substring containment (`in` operator) to fuzzy-match EDL filenames against source list entries. This is too loose: a source entry named `"x"` (a researcher's marker) matches `"SpreadsheetWars_Excel.mp4"` because `"x"` appears inside `"spreadsheetwars_excel"` after normalization.

## Changes Made

### 1. Created `edl_to_archive/settings.py` (new file)

Follows the same pattern as `yt_bulk_dl/settings.py` and `green_to_red/settings.py`:

```python
@dataclass
class MatchSettings:
    min_match_length: int = 4  # minimum chars for prefix matching
```

With `get_settings()` and `update_settings(min_match_length)` functions.

### 2. Rewrote `find_source_match()` in `converter.py` (lines 681-722)

- Imports `get_settings` from `edl_to_archive.settings` to read `min_match_length` at call time
- **Skips single-character source names** entirely (even for exact matches — these are markers)
- **Two-pass structure**: exact matches first (full list), then prefix matches (full list)
- **Replaced `in` with `startswith`** for the fuzzy pass — truncation happens at the end, not mid-string
- **Minimum length guard**: shorter name must be >= `min_match_length` for prefix match

### 3. Added admin panel integration

- **`admin.py`**: Added edl-to-archive to `_get_all_settings()`, added `POST /admin/settings/edl-to-archive` route with bounds [2, 20]
- **`admin/index.html`**: Replaced "No Server Settings" placeholder with a form for "Min match length" field with tooltip

## Key behaviors

| EDL name | Source name | Result | Why |
|---|---|---|---|
| `SpreadsheetWars_Excel.mp4` | `x` | No match | Single-char source skipped |
| `Interview_John.mxf` | `Interview_John` | Exact match | Extensions stripped |
| `Interview_John.mxf` | `Interview_Jo` | Prefix match | Truncated, len 12 >= 4 |
| `Inter` | `Interview_John` | Prefix match | Truncated EDL, len 5 >= 4 |
| `abc.mov` | `abcdef` | No match | Shorter len 3 < 4 |
| `clip_001.mov` | `clip_001.mxf` | Exact match | Both normalize to `clip_001` |

## Files modified

1. **New**: `services/edl-to-archive/edl_to_archive/settings.py`
2. **Edit**: `services/edl-to-archive/edl_to_archive/core/converter.py` — rewrote `find_source_match()`
3. **Edit**: `services/gateway/gateway/admin.py` — added edl-to-archive settings + POST route
4. **Edit**: `services/gateway/gateway/templates/admin/index.html` — replaced placeholder with form
