"""STTN chunk planning and overlap blending (torch-free, unit-testable).

Splitting a clip into inference windows and blending their overlap zones is
pure array bookkeeping; keeping it here lets the API test suite cover it
without a GPU. The worker imports this module for the real path.
"""
from __future__ import annotations


def plan_windows(
    total: int, chunk: int, overlap: int, min_chunk: int = 4
) -> list[tuple[int, int, int]]:
    """Return (start, end, blend_count) per inference window.

    Windows advance by (chunk - overlap) so consecutive windows share an
    overlap zone. ``blend_count`` frames at the start of each window after
    the first were already produced by the previous window.
    Advances keep every frame covered exactly: coverage is verified with an
    internal assertion so a bad parameter combination fails loudly instead of
    silently skipping frames.
    """
    if total <= 0:
        return []
    chunk = max(min_chunk, int(chunk))
    overlap = max(0, min(int(overlap), chunk // 2))
    windows: list[tuple[int, int, int]] = []
    start = 0
    covered_until = 0
    while start < total:
        end = min(total, start + chunk)
        blend = min(overlap, end - start) if start > 0 else 0
        windows.append((start, end, blend))
        if start > covered_until:
            # A gap between windows would silently drop frames — refuse.
            raise RuntimeError(f"window gap: covered_until={covered_until}, next start={start}")
        covered_until = end
        if end >= total:
            break
        start = end - overlap if overlap > 0 else end
    if covered_until < total:
        raise RuntimeError(f"windows do not cover clip: {covered_until}/{total}")
    return windows


def blend_zone(prev_out, new_out, mask, alpha: float):
    """Blend one overlap frame inside the mask with weight ``alpha``.

    ``alpha`` is the new window's weight (0 = keep previous, 1 = take new).
    Outside the mask the previous output is returned untouched — pixels there
    are original bytes and must never change.
    """
    import numpy as np

    m = mask > 0
    blended = prev_out.copy()
    if m.any():
        blended[m] = (
            prev_out[m].astype(np.float32) * (1.0 - alpha)
            + new_out[m].astype(np.float32) * alpha
        ).astype(prev_out.dtype)
    return blended
