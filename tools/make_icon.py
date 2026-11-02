"""Draws riffcam.ico: a red guitar pick with a camera lens. Run once; the .ico is committed."""
import math, os
from PIL import Image, ImageDraw

S = 1024


def hull(points):
    """Convex hull, monotone chain."""
    pts = sorted(set(points))

    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(p)
        return h
    return half(pts)[:-1] + half(pts[::-1])[:-1]


# a pick is the hull of three circles: two big rounded shoulders, one tighter tip
circles = [(300, 320, 230), (724, 320, 230), (512, 860, 110)]
outline = hull([(cx + r * math.cos(a / 90 * math.pi), cy + r * math.sin(a / 90 * math.pi))
                for cx, cy, r in circles for a in range(180)])
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.polygon(outline, fill="#c62828")
cx, cy = 512, 420  # camera lens
for r, color in ((200, "#1b1b1b"), (130, "#f5f5f5"), (55, "#c62828")):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
here = os.path.dirname(os.path.abspath(__file__))
img.save(os.path.join(here, "..", "riffcam.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(os.path.join(here, "..", "docs", "icon.png"))
