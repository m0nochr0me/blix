"""Threshold tiles for ordered dithering and error diffusion for gradients."""

from collections.abc import Callable
from functools import cache

import numpy as np

TILE = 8
BLUE_NOISE_SIZE = 64
BLUE_NOISE_SIGMA = 1.5
BLUE_NOISE_SEED = 0

Taps = tuple[tuple[int, int, float], ...]

FLOYD_STEINBERG: Taps = ((1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16))
ATKINSON_OFFSETS = ((1, 0), (2, 0), (-1, 1), (0, 1), (1, 1), (0, 2))
ATKINSON: Taps = tuple((dx, dy, 1 / 8) for dx, dy in ATKINSON_OFFSETS)


def _thresholds(rank: np.ndarray) -> np.ndarray:
    return ((rank + 0.5) / rank.size).astype(np.float32)


@cache
def bayer_matrix(n: int) -> np.ndarray:
    if n == 2:
        return np.array([[0, 2], [3, 1]], dtype=np.int64)
    half = bayer_matrix(n // 2)
    return np.block([[4 * half, 4 * half + 2], [4 * half + 3, 4 * half + 1]])


@cache
def bayer(n: int) -> np.ndarray:
    return _thresholds(bayer_matrix(n))


def ranked(values: np.ndarray) -> np.ndarray:
    """Thresholds by ascending value; ties fall in Bayer order so flat runs still ramp evenly."""
    height, width = values.shape
    tie = bayer_matrix(TILE)[np.arange(height)[:, None] % TILE, np.arange(width)[None, :] % TILE]
    order = np.lexsort((tie.ravel(), values.ravel()))
    rank = np.empty(values.size, dtype=np.int64)
    rank[order] = np.arange(values.size)
    return _thresholds(rank.reshape(height, width))


def _bayer_line() -> np.ndarray:
    row = bayer_matrix(TILE)[0]
    return _thresholds(np.argsort(np.argsort(row)))


def lines_h() -> np.ndarray:
    return _bayer_line()[:, None]


def lines_v() -> np.ndarray:
    return _bayer_line()[None, :]


def lines_diagonal() -> np.ndarray:
    yy, xx = np.mgrid[0:TILE, 0:TILE]
    return _bayer_line()[(xx + yy) % TILE]


def lines_antidiagonal() -> np.ndarray:
    yy, xx = np.mgrid[0:TILE, 0:TILE]
    return _bayer_line()[(xx - yy) % TILE]


def _torus_distance(centers: tuple[tuple[int, int], ...]) -> np.ndarray:
    yy, xx = np.mgrid[0:TILE, 0:TILE]
    distance = np.full((TILE, TILE), np.inf)
    for cx, cy in centers:
        dx = np.minimum(np.abs(xx - cx), TILE - np.abs(xx - cx))
        dy = np.minimum(np.abs(yy - cy), TILE - np.abs(yy - cy))
        distance = np.minimum(distance, np.hypot(dx, dy))
    return distance


def dots() -> np.ndarray:
    return ranked(_torus_distance(((4, 4),)))


def dots_offset() -> np.ndarray:
    return ranked(_torus_distance(((0, 0), (4, 4))))


def _wrapped_gaussian(n: int, sigma: float) -> np.ndarray:
    d = np.minimum(np.arange(n), n - np.arange(n)).astype(np.float64)
    return np.exp(-(d[:, None] ** 2 + d[None, :] ** 2) / (2 * sigma * sigma))


def _energy(pattern: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    n = pattern.shape[0]
    return np.fft.irfft2(np.fft.rfft2(pattern.astype(np.float64)) * np.fft.rfft2(kernel), s=(n, n))


def _kernel_at(kernel: np.ndarray, index: int) -> np.ndarray:
    n = kernel.shape[0]
    return np.roll(kernel, (index // n, index % n), axis=(0, 1))


def _tightest_cluster(pattern: np.ndarray, energy: np.ndarray) -> int:
    return int(np.argmax(np.where(pattern, energy, -np.inf)))


def _largest_void(pattern: np.ndarray, energy: np.ndarray) -> int:
    return int(np.argmin(np.where(pattern, np.inf, energy)))


def _assign_ranks(
    pattern: np.ndarray,
    energy: np.ndarray,
    kernel: np.ndarray,
    rank: np.ndarray,
    ranks: range,
    remove: bool,
) -> None:
    for value in ranks:
        index = _tightest_cluster(pattern, energy) if remove else _largest_void(pattern, energy)
        pattern.flat[index] = not remove
        energy += (-1.0 if remove else 1.0) * _kernel_at(kernel, index)
        rank.flat[index] = value


@cache
def blue_noise(n: int = BLUE_NOISE_SIZE, seed: int = BLUE_NOISE_SEED) -> np.ndarray:
    """Void-and-cluster ranks over a wrapping n x n tile; deterministic per seed."""
    rng = np.random.default_rng(seed)
    kernel = _wrapped_gaussian(n, BLUE_NOISE_SIGMA)
    total = n * n
    pattern = np.zeros(total, dtype=bool)
    pattern[rng.choice(total, total // 10, replace=False)] = True
    pattern = pattern.reshape(n, n)
    energy = _energy(pattern, kernel)
    while True:
        cluster = _tightest_cluster(pattern, energy)
        pattern.flat[cluster] = False
        energy -= _kernel_at(kernel, cluster)
        void = _largest_void(pattern, energy)
        pattern.flat[void] = True
        energy += _kernel_at(kernel, void)
        if void == cluster:
            break
    ones = int(pattern.sum())
    rank = np.zeros((n, n), dtype=np.int64)
    _assign_ranks(pattern.copy(), energy.copy(), kernel, rank, range(ones - 1, -1, -1), True)
    _assign_ranks(pattern, energy, kernel, rank, range(ones, total // 2), False)
    pattern = ~pattern
    _assign_ranks(pattern, _energy(pattern, kernel), kernel, rank, range(total // 2, total), True)
    return _thresholds(rank)


TILES: dict[str, Callable[[], np.ndarray]] = {
    "BAYER2": lambda: bayer(2),
    "BAYER4": lambda: bayer(4),
    "BAYER8": lambda: bayer(8),
    "BLUE": blue_noise,
    "LINES_H": lines_h,
    "LINES_V": lines_v,
    "DIAGONAL": lines_diagonal,
    "ANTIDIAGONAL": lines_antidiagonal,
    "DOTS": dots,
    "DOTS_OFFSET": dots_offset,
}


def tile(rect: tuple[int, int, int, int], thresholds: np.ndarray) -> np.ndarray:
    """Thresholds tiled over rect by absolute coordinates, so patterns stay put across edits."""
    x0, y0, x1, y1 = rect
    height, width = thresholds.shape
    return thresholds[np.arange(y0, y1)[:, None] % height, np.arange(x0, x1)[None, :] % width]


def diffuse(values: np.ndarray, taps: Taps) -> np.ndarray:
    """Raster error diffusion of values in [0, 1] to a bool mask, vectorized on x+2y wavefronts."""
    height, width = values.shape
    pad = 2
    error = np.zeros((height + pad, width + 2 * pad), dtype=np.float32)
    out = np.zeros((height, width), dtype=bool)
    for k in range(width + 2 * height - 2):
        y_lo = max(0, (k - width + 2) // 2)
        y_hi = min(height - 1, k // 2)
        if y_lo > y_hi:
            continue
        ys = np.arange(y_lo, y_hi + 1)
        xs = k - 2 * ys
        value = values[ys, xs] + error[ys, xs + pad]
        on = value >= 0.5
        out[ys, xs] = on
        spill = value - on
        for dx, dy, weight in taps:
            error[ys + dy, xs + dx + pad] += weight * spill
    return out
