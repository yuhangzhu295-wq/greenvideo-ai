"""Track lifecycle management for multi-watermark pixel masks.

Manages track segments across frames: fixed watermarks get one continuous
segment; jumping watermarks end the old segment and start a new one at the new
corner - never a linear interpolation through the screen centre.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrackSegment:
    """One continuous appearance of a watermark (no teleporting inside it)."""

    track_id: int
    start_frame: int
    end_frame: int
    masks: dict[int, object] = field(default_factory=dict)  # frame_idx -> mask array
    anchor_frame: int = -1
    anchor_box: tuple[int, int, int, int] | None = None


class TrackManager:
    """Assigns pixel masks to frames, splitting on discontinuity jumps.

    A jump is any anchor move whose per-frame displacement implies teleporting
    (corner switch). Segments are never bridged with interpolated masks.
    """

    def __init__(self, jump_distance_px: float = 180.0) -> None:
        self.jump_distance_px = jump_distance_px
        self.segments: list[TrackSegment] = []
        self._next_id = 0

    def add_mask(self, frame_idx: int, mask, box: tuple[int, int, int, int] | None = None) -> int:
        """Add a segmented mask for a frame; returns the segment's track_id."""
        import numpy as np

        if self.segments:
            last = self.segments[-1]
            if frame_idx == last.end_frame + 1 and box is not None and last.anchor_box is not None:
                dx = abs((box[0] + box[2] / 2) - (last.anchor_box[0] + last.anchor_box[2] / 2))
                dy = abs((box[1] + box[3] / 2) - (last.anchor_box[1] + last.anchor_box[3] / 2))
                if dx <= self.jump_distance_px and dy <= self.jump_distance_px:
                    last.end_frame = frame_idx
                    last.masks[frame_idx] = np.asarray(mask)
                    last.anchor_box = box
                    return last.track_id

        seg = TrackSegment(
            track_id=self._next_id,
            start_frame=frame_idx,
            end_frame=frame_idx,
            anchor_frame=frame_idx,
            anchor_box=box,
        )
        seg.masks[frame_idx] = np.asarray(mask)
        self.segments.append(seg)
        self._next_id += 1
        return seg.track_id

    def mask_for(self, frame_idx: int):
        for seg in self.segments:
            if seg.start_frame <= frame_idx <= seg.end_frame and frame_idx in seg.masks:
                return seg.masks[frame_idx]
        return None

    def gaps(self) -> list[tuple[int, int]]:
        """Frame ranges with no track coverage (openly reported, never faked)."""
        gaps = []
        for prev, nxt in zip(self.segments, self.segments[1:], strict=False):
            if nxt.start_frame > prev.end_frame + 1:
                gaps.append((prev.end_frame + 1, nxt.start_frame - 1))
        return gaps
