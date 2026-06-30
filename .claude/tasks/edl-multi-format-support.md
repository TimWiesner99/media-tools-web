## Plan: Add Multi-Format EDL Input Support

Normalize the first upload into one canonical tabular EDL representation before the existing conversion flow runs. Treat the raw CMX `.edl` file as the semantic ground truth, accept both example `.xlsx` variants in the EDL upload slot, and keep the current source-file, exclusion, collapse, matching, and DEF-output logic as unchanged as possible.

### Steps

1. Add an EDL-side format classifier at the conversion boundary, called immediately after the uploaded files are persisted and before `load_edl()` is used.
2. Introduce a normalization helper that returns the canonical EDL columns the current code expects.
3. Parse raw CMX `.edl` files into canonical rows and normalize the two workbook variants into the same shape.
4. Refactor `load_edl()` and `validate_edl_file()` to consume normalized canonical EDL data.
5. Update workbook export so it writes the normalized EDL table instead of re-reading the original upload as a spreadsheet.
6. Update the form copy and accepted EDL extensions to include raw `.edl`.
7. Add focused regression coverage or, if no test harness exists, run targeted conversion smoke checks against all three example inputs.

### Notes

- Both example `.xlsx` files are treated as valid EDL-side inputs.
- The separate source archive upload remains unchanged.
- Keep the existing fps selection, matching, exclusion, collapse, and DEF-sheet logic unless normalization exposes a concrete incompatibility.