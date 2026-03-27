"""Data models for EDL entries, source entries, and the definitive archive list."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from .timecode import Timecode


@dataclass
class EDLEntry:
    """Represents a single entry (row) from an Edit Decision List (EDL).

    This contains the clip information extracted from AVID or Premiere Pro.
    """

    id: int
    name: str
    timecode_in: Timecode
    timecode_out: Timecode
    duration: Timecode
    source_start: Timecode
    source_end: Timecode
    reel: str = ""
    file_name: str = ""
    track: str = ""
    audio_channels: str = ""
    comment: str = ""
    source_total_usage: Optional[Timecode] = None  # Pre-computed sum of source durations (set by collapse)

    @classmethod
    def from_dict(cls, data: dict, fps: int = 25) -> EDLEntry:
        """Create an EDLEntry from a dictionary (typically from a pandas row).

        Args:
            data: Dictionary containing EDL row data
            fps: Frame rate for timecode parsing

        Returns:
            EDLEntry instance
        """
        return cls(
            id=int(data.get("id", 0)),
            name=str(data.get("name", "")),
            timecode_in=Timecode.from_string(str(data.get("timecode_in", "00:00:00:00")), fps),
            timecode_out=Timecode.from_string(str(data.get("timecode_out", "00:00:00:00")), fps),
            duration=Timecode.from_string(str(data.get("duration", "00:00:00:00")), fps),
            source_start=Timecode.from_string(str(data.get("source_start", "00:00:00:00")), fps),
            source_end=Timecode.from_string(str(data.get("source_end", "00:00:00:00")), fps),
            reel=str(data.get("reel", "")),
            file_name=str(data.get("file_name", "")),
            track=str(data.get("track", "")),
            audio_channels=str(data.get("audio_channels", "")),
            comment=str(data.get("comment", "")),
        )


@dataclass
class SourceEntry:
    """Represents a source entry from the image researcher's archive list.

    This contains metadata about media sources including links and rights info.
    """

    name: str
    description: str = ""
    link: str = ""
    source: str = ""
    cost: str = ""
    price_nl: str = ""
    price_sales: str = ""
    rights_contact: str = ""
    todo_notes: str = ""
    source_in_frame: str = ""
    credits: str = ""
    row_number: int = 0  # Track row number for error messages

    @classmethod
    def from_dict(cls, data: dict, row_number: int = 0) -> SourceEntry:
        """Create a SourceEntry from a dictionary (typically from a pandas row).

        Args:
            data: Dictionary containing source row data
            row_number: Row number in the source file (for error messages)

        Returns:
            SourceEntry instance

        Raises:
            ValueError: If both price_nl and price_sales are filled with different values
        """
        # Extract price fields
        cost = str(data.get("cost", "")).strip()
        price_nl = str(data.get("price_nl", "")).strip()
        price_sales = str(data.get("price_sales", "")).strip()

        # Determine final cost value with validation
        final_cost = ""
        name = str(data.get("name", ""))

        # Check if both price fields are filled
        if price_nl and price_sales:
            if price_nl == price_sales:
                # Both filled but same value - use it
                final_cost = price_nl
            else:
                # Both filled with different values - error
                raise ValueError(
                    f"Both 'Prijs NL' and 'Prijs sales' are filled with different values "
                    f"for '{name}' at row {row_number}: "
                    f"Prijs NL='{price_nl}', Prijs sales='{price_sales}'"
                )
        elif price_nl:
            final_cost = price_nl
        elif price_sales:
            final_cost = price_sales
        elif cost:
            # Fallback to 'kosten' column if neither price field is filled
            final_cost = cost

        return cls(
            name=name,
            description=str(data.get("description", "")),
            link=str(data.get("link", "")),
            source=str(data.get("source", "")),
            cost=final_cost,
            price_nl=price_nl,
            price_sales=price_sales,
            rights_contact=str(data.get("rights_contact", "")),
            todo_notes=str(data.get("todo_notes", "")),
            source_in_frame=str(data.get("source_in_frame", "")),
            credits=str(data.get("credits", "")),
            row_number=row_number,
        )


@dataclass
class DefEntry:
    """Represents an entry in the definitive archive list.

    This combines EDL timing information with source metadata.
    """

    name: str
    timecode_in: Timecode
    duration: Timecode
    description: str = ""
    link: str = ""
    source: str = ""
    cost: str = ""
    rights_contact: str = ""
    todo_notes: str = ""
    source_in_frame: str = ""
    credits: str = ""
    # Additional fields from combining
    source_start: Optional[Timecode] = None
    source_end: Optional[Timecode] = None
    source_total_usage: Optional[Timecode] = None  # Pre-computed sum of source durations (from collapse)
    # Occurrence tracking (set by annotate_occurrences)
    occurrence_number: int = 0   # Which occurrence this is (1-based)
    total_occurrences: int = 0   # Total times this source appears

    @classmethod
    def from_edl_and_source(
        cls,
        edl: EDLEntry,
        source: Optional[SourceEntry] = None
    ) -> DefEntry:
        """Create a DefEntry by combining EDL timing with source metadata.

        Args:
            edl: EDL entry with timing information
            source: Optional source entry with metadata (if not found, fields are empty)

        Returns:
            DefEntry instance
        """
        entry = cls(
            name=edl.name,
            timecode_in=edl.timecode_in,
            duration=edl.duration,
            source_start=edl.source_start,
            source_end=edl.source_end,
            source_total_usage=edl.source_total_usage,
        )

        if source:
            entry.description = source.description
            entry.link = source.link
            entry.source = source.source
            entry.cost = source.cost
            entry.rights_contact = source.rights_contact
            entry.todo_notes = source.todo_notes
            entry.source_in_frame = source.source_in_frame
            entry.credits = source.credits

        return entry

    def to_dict(self, include_frames: bool = False) -> dict:
        """Convert DefEntry to dictionary for output.

        Args:
            include_frames: If True, include frame-level precision in timecodes (HH:MM:SS:FF).
                          If False (default), round to seconds (HH:MM:SS).

        Returns:
            Dictionary representation of the entry in the column order:
            TC in, Duur, Bestandsnaam, Omschrijving, Link, Bron, Kosten,
            rechten/contact, to do, Bron in beeld, Aftiteling,
            Nummer/aantal, Bron TC in, Bron TC out
        """
        # Choose formatting method based on include_frames flag
        tc_format = lambda tc: tc.to_string() if include_frames else tc.to_string_rounded()

        # For cost, source_in_frame, and credits: show actual value on first
        # occurrence, "-" on subsequent occurrences of the same source.
        if self.occurrence_number > 1:
            display_cost = "-"
            display_source_in_frame = "-"
            display_credits = "-"
        else:
            display_cost = self.cost
            display_source_in_frame = self.source_in_frame
            display_credits = self.credits

        # Nummer/aantal: "occurrence/total" (e.g. "2/4"), empty if not annotated
        if self.total_occurrences > 0:
            nummer_aantal = f"{self.occurrence_number}/{self.total_occurrences}"
        else:
            nummer_aantal = ""

        return {
            "TC in": tc_format(self.timecode_in),
            "Duur": tc_format(self.duration),
            "Bestandsnaam": self.name,
            "Omschrijving": self.description,
            "Link": self.link,
            "Bron": self.source,
            "Kosten": display_cost,
            "rechten/contact": self.rights_contact,
            "to do": self.todo_notes,
            "Bron in beeld": display_source_in_frame,
            "Aftiteling": display_credits,
            "Nummer/aantal": nummer_aantal,
            "Bron TC in": tc_format(self.source_start) if self.source_start else "",
            "Bron TC out": tc_format(self.source_end) if self.source_end else "",
        }
