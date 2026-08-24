from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image


def knock_black(im: Image.Image, threshold: int = 28) -> Image.Image:
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    visited = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def dark(x: int, y: int) -> bool:
        r, g, b, _a = px[x, y]
        return r <= threshold and g <= threshold and b <= threshold

    for x in range(w):
        q.append((x, 0))
        q.append((x, h - 1))
    for y in range(h):
        q.append((0, y))
        q.append((w - 1, y))

    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        i = y * w + x
        if visited[i]:
            continue
        visited[i] = 1
        if not dark(x, y):
            continue
        r, g, b, _a = px[x, y]
        px[x, y] = (r, g, b, 0)
        q.append((x + 1, y))
        q.append((x - 1, y))
        q.append((x, y + 1))
        q.append((x, y - 1))

    # Soften the cut against hair/shadows.
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            if r > 42 or g > 42 or b > 42:
                continue
            near = False
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and px[nx, ny][3] == 0:
                    near = True
                    break
            if near:
                fade = max(r, g, b)
                px[x, y] = (r, g, b, min(a, int(fade * 6)))
    return im


def load_pose(path: Path, max_h: int) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    im.thumbnail((max(32, int(max_h * 1.15)), max(32, int(max_h * 1.15))), Image.Resampling.LANCZOS)
    im = knock_black(im)
    return im
