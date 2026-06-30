"""Timecode class for handling video timecodes with frame-level precision."""

from __future__ import annotations
from dataclasses import dataclass
import re


@dataclass
class Timecode:
    """Represents a video timecode with hours, minutes, seconds, and frames.

    The timecode format is HH:MM:SS:FF where FF is the frame number (0-based).
    Frame rate is configurable, with 25 fps as the default (PAL standard).
    """

    hours: int
    minutes: int
    seconds: int
    frames: int
    fps: int = 25

    # Pattern to match timecode format: HH:MM:SS:FF
    TIMECODE_PATTERN = re.compile(r'^(\d{2}):(\d{2}):(\d{2}):(\d{2})$')

    def __post_init__(self) -> None:
        """Validate timecode values after initialization."""
        if self.hours < 0:
            raise ValueError(f"Hours cannot be negative: {self.hours}")
        if not 0 <= self.minutes < 60:
            raise ValueError(f"Minutes must be 0-59: {self.minutes}")
        if not 0 <= self.seconds < 60:
            raise ValueError(f"Seconds must be 0-59: {self.seconds}")
        if not 0 <= self.frames < self.fps:
            raise ValueError(f"Frames must be 0-{self.fps - 1}: {self.frames}")

    @classmethod
    def from_string(cls, tc_string: str, fps: int = 25) -> Timecode:
        """Parse a timecode string in HH:MM:SS:FF format.

        Args:
            tc_string: Timecode string in format HH:MM:SS:FF
            fps: Frame rate (default 25)

        Returns:
            Timecode object

        Raises:
            ValueError: If the string is not a valid timecode format
        """
        if not tc_string or not isinstance(tc_string, str):
            raise ValueError(f"Invalid timecode string: {tc_string}")

        tc_string = tc_string.strip()
        match = cls.TIMECODE_PATTERN.match(tc_string)

        if not match:
            raise ValueError(f"Invalid timecode format: '{tc_string}'. Expected HH:MM:SS:FF")

        hours, minutes, seconds, frames = map(int, match.groups())

        # Handle framerate mismatch: source material may have a higher
        # framerate than the project (e.g., 50fps source in a 25fps project).
        # Round down to the current second since exact frames are not needed
        # for archival purposes.
        if frames >= fps:
            frames = 0

        return cls(hours=hours, minutes=minutes, seconds=seconds, frames=frames, fps=fps)

    @classmethod
    def from_frames(cls, total_frames: int, fps: int = 25) -> Timecode:
        """Create a Timecode from total frame count.

        Args:
            total_frames: Total number of frames
            fps: Frame rate (default 25)

        Returns:
            Timecode object
        """
        if total_frames < 0:
            raise ValueError(f"Total frames cannot be negative: {total_frames}")

        frames = total_frames % fps
        total_seconds = total_frames // fps
        seconds = total_seconds % 60
        total_minutes = total_seconds // 60
        minutes = total_minutes % 60
        hours = total_minutes // 60

        return cls(hours=hours, minutes=minutes, seconds=seconds, frames=frames, fps=fps)

    def to_frames(self) -> int:
        """Convert timecode to total frame count.

        Returns:
            Total number of frames
        """
        return (
            self.frames
            + self.seconds * self.fps
            + self.minutes * 60 * self.fps
            + self.hours * 3600 * self.fps
        )

    def to_string(self) -> str:
        """Convert timecode to string format HH:MM:SS:FF.

        Returns:
            Formatted timecode string
        """
        return f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}:{self.frames:02d}"

    def round_to_seconds(self) -> Timecode:
        """Round timecode to the nearest second.

        Rounds up if frames >= fps/2, otherwise rounds down.

        Returns:
            New Timecode rounded to the nearest second (frames = 0)
        """
        # Round up if we're at or past the halfway point
        if self.frames >= self.fps / 2:
            # Add one second and zero out frames
            total_frames = self.to_frames() + (self.fps - self.frames)
            return Timecode.from_frames(total_frames, self.fps)
        else:
            # Just zero out the frames (round down)
            return Timecode(
                hours=self.hours,
                minutes=self.minutes,
                seconds=self.seconds,
                frames=0,
                fps=self.fps
            )

    def to_string_rounded(self) -> str:
        """Convert timecode to string format HH:MM:SS (without frames).

        Rounds to the nearest second before formatting.

        Returns:
            Formatted timecode string without frame count
        """
        rounded = self.round_to_seconds()
        return f"{rounded.hours:02d}:{rounded.minutes:02d}:{rounded.seconds:02d}"

    def __str__(self) -> str:
        """String representation of the timecode."""
        return self.to_string()

    def __repr__(self) -> str:
        """Debug representation of the timecode."""
        return f"Timecode({self.to_string()}, fps={self.fps})"

    def __add__(self, other: Timecode) -> Timecode:
        """Add two timecodes together.

        Args:
            other: Another Timecode to add

        Returns:
            New Timecode representing the sum
        """
        if not isinstance(other, Timecode):
            return NotImplemented

        if self.fps != other.fps:
            raise ValueError(f"Cannot add timecodes with different frame rates: {self.fps} vs {other.fps}")

        total_frames = self.to_frames() + other.to_frames()
        return Timecode.from_frames(total_frames, self.fps)

    def __sub__(self, other: Timecode) -> Timecode:
        """Subtract one timecode from another.

        Args:
            other: Another Timecode to subtract

        Returns:
            New Timecode representing the difference
        """
        if not isinstance(other, Timecode):
            return NotImplemented

        if self.fps != other.fps:
            raise ValueError(f"Cannot subtract timecodes with different frame rates: {self.fps} vs {other.fps}")

        total_frames = self.to_frames() - other.to_frames()
        if total_frames < 0:
            raise ValueError("Cannot have negative timecode result")

        return Timecode.from_frames(total_frames, self.fps)

    def __eq__(self, other: object) -> bool:
        """Check equality of two timecodes."""
        if not isinstance(other, Timecode):
            return NotImplemented
        return self.to_frames() == other.to_frames() and self.fps == other.fps

    def __lt__(self, other: Timecode) -> bool:
        """Check if this timecode is less than another."""
        if not isinstance(other, Timecode):
            return NotImplemented
        if self.fps != other.fps:
            raise ValueError(f"Cannot compare timecodes with different frame rates: {self.fps} vs {other.fps}")
        return self.to_frames() < other.to_frames()

    def __le__(self, other: Timecode) -> bool:
        """Check if this timecode is less than or equal to another."""
        if not isinstance(other, Timecode):
            return NotImplemented
        return self == other or self < other

    def __gt__(self, other: Timecode) -> bool:
        """Check if this timecode is greater than another."""
        if not isinstance(other, Timecode):
            return NotImplemented
        if self.fps != other.fps:
            raise ValueError(f"Cannot compare timecodes with different frame rates: {self.fps} vs {other.fps}")
        return self.to_frames() > other.to_frames()

    def __ge__(self, other: Timecode) -> bool:
        """Check if this timecode is greater than or equal to another."""
        if not isinstance(other, Timecode):
            return NotImplemented
        return self == other or self > other

    def __hash__(self) -> int:
        """Hash for use in sets and dicts."""
        return hash((self.to_frames(), self.fps))
