"""Draws the committed art: riffcam.ico, docs/icon.png, docs/banner.png, docs/badges.png. Run after changing the skin."""
import math, os, sys
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import skin  # noqa: E402

DOCS = os.path.join(ROOT, "docs")


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


def pick(scale=1.0, cx=512, cy=512):
    """Guitar pick outline: the hull of two big rounded shoulders and a tighter tip."""
    circles = [(-212, -192, 230), (212, -192, 230), (0, 348, 110)]
    return hull([(cx + (x + r * math.cos(a / 90 * math.pi)) * scale, cy + (y + r * math.sin(a / 90 * math.pi)) * scale)
                 for x, y, r in circles for a in range(180)])


def icon():
    """Glossy red pick in a chrome rim with a camera lens."""
    S = 1024
    img = Image.new("RGBA", (S, S))
    m = Image.new("L", (S, S))
    ImageDraw.Draw(m).polygon(pick(0.96), fill=255)
    img.alpha_composite(skin.glow(m, skin.ACID, 28, 1.4))
    chrome = skin.vgrad(S, S, skin.CHROME)
    chrome.putalpha(m)
    img.alpha_composite(chrome)
    inner = Image.new("L", (S, S))
    ImageDraw.Draw(inner).polygon(pick(0.84, cy=500), fill=255)
    body = skin.vgrad(S, S, [(0, (255, 110, 120)), (0.45, (220, 20, 50)), (1, (110, 0, 20))])
    body.putalpha(inner)
    img.alpha_composite(body)
    gloss = Image.new("L", (S, S))
    ImageDraw.Draw(gloss).ellipse((170, 120, 854, 470), fill=120)
    img.alpha_composite(skin.solid(Image.composite(gloss, Image.new("L", (S, S)), inner).filter(ImageFilter.GaussianBlur(14)), (255, 255, 255)))
    cx, cy = 512, 430
    for r, color in ((190, (10, 10, 12)), (150, skin.ACID), (128, (12, 16, 12)), (70, (40, 44, 50)), (34, (255, 255, 255))):
        ImageDraw.Draw(img).ellipse((cx - r, cy - r, cx + r, cy + r), fill=skin.rgba(color))
    img.save(os.path.join(ROOT, "riffcam.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.resize((256, 256), Image.LANCZOS).save(os.path.join(DOCS, "icon.png"))
    return img


def banner(ico):
    w, h = 1280, 360
    img = skin.carbon(w, h)
    skin.grunge(img, seed=11)
    skin.claws(img, 1010, 30, 150, alpha=70)
    skin.claws(img, 60, 210, 110, color=skin.PINK, alpha=45)
    line = Image.new("L", (w, h))
    ImageDraw.Draw(line).line((0, h - 6, w, h - 6), fill=255, width=6)
    neon = skin.vgrad(h, w, [(0, skin.PINK), (0.5, (255, 255, 255)), (1, skin.ACID)]).rotate(90, expand=True)
    for strength, blur in ((2.2, 12), (1, 0)):
        layer = neon.copy()
        layer.putalpha(line.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: min(255, int(v * strength))) if blur else line)
        img.alpha_composite(layer)
    img.alpha_composite(ico.resize((250, 250), Image.LANCZOS), (40, 40))
    logo = skin.chrome_text("RiffCam", 150)
    img.alpha_composite(logo, (270, 10))
    d = ImageDraw.Draw(img)
    d.text((330, 236), "FILM YOURSELF SHREDDING.", font=skin.font(skin.UI_FONT, 38), fill=skin.rgba(skin.ACID),
           stroke_width=4, stroke_fill=(0, 0, 0, 255))
    d.text((332, 288), "One window. One button. Zero editing.", font=skin.font(skin.LCD_FONT, 36), fill=(235, 240, 245, 255),
           stroke_width=2, stroke_fill=(0, 0, 0, 255))
    sticker = skin.starburst(70, "FREE!").rotate(14, resample=Image.BICUBIC)
    img.alpha_composite(sticker, (1100, 160))
    rec = skin.orb(110, "REC")
    img.alpha_composite(rec, (960, 175))
    img.convert("RGB").save(os.path.join(DOCS, "banner.png"))


def badges():
    row = [("REC", "REAPER\npowered", ((30, 30, 30), skin.HOT, skin.ACID)),
           ("OBS", "inside", ((0, 40, 0), skin.ACID, (255, 255, 255))),
           ("CRT", "best viewed\non a", ((60, 0, 60), skin.PINK, (255, 255, 255))),
           ("$0", "no ads\nno bloat", ((40, 40, 0), (255, 230, 0), (255, 255, 255))),
           ("UA", "made in\nUkraine", ((0, 60, 140), (255, 215, 0), (255, 255, 255)))]
    img = Image.new("RGBA", (len(row) * 96 - 8, 31))
    for i, (a, b, c) in enumerate(row):
        img.alpha_composite(skin.badge88(a, b, c), (i * 96, 0))
    img.resize((img.width * 2, 62), Image.NEAREST).save(os.path.join(DOCS, "badges.png"))


if __name__ == "__main__":
    os.makedirs(DOCS, exist_ok=True)
    banner(icon())
    badges()
