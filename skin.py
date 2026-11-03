"""RiffCam's look: a 2010 "older brother" skin (carbon fibre, chrome, acid green, glossy buttons),
drawn procedurally with Pillow. Every function returns an RGBA image.

Sizes passed in are real pixels; the skin's own details (radii, screws, scanlines...) are scaled by Z,
the display scale (1.5 on a 150% screen), so everything is drawn at native resolution."""
import math, os, random
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
LOGO_FONT, UI_FONT, LCD_FONT = "MetalMania-Regular.ttf", "RussoOne-Regular.ttf", "VT323-Regular.ttf"

ACID, HOT, PINK = (157, 255, 0), (255, 26, 60), (255, 0, 170)
ACID_HEX, HOT_HEX, PINK_HEX = "#9dff00", "#ff1a3c", "#ff00aa"
CHROME = [(0, (255, 255, 255)), (0.42, (178, 186, 196)), (0.5, (52, 56, 64)), (0.62, (110, 118, 128)), (1, (236, 240, 244))]
KINDS = {  # button body top, bottom, label colour
    "acid": ((205, 255, 90), (52, 150, 0), (8, 22, 0)),
    "red": ((255, 105, 115), (150, 0, 24), (255, 255, 255)),
    "pink": ((255, 110, 215), (150, 0, 100), (255, 255, 255)),
    "metal": ((122, 128, 138), (26, 28, 32), (235, 240, 245)),
}
S = 3  # supersampling for smooth edges
Z = 1.0  # display scale, set by the app


def z(v):
    return max(1, round(v * Z))


def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), max(1, int(size)))


def rgba(c, a=255):
    return c if len(c) == 4 else (*c, a)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def vgrad(w, h, stops):
    """Vertical gradient through (position 0..1, colour) stops."""
    col = Image.new("RGBA", (1, max(1, h)))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                px[0, y] = lerp(rgba(c0), rgba(c1), (t - p0) / max(1e-6, p1 - p0))
                break
    return col.resize((max(1, w), max(1, h)))


def rrect_mask(w, h, r, box=None):
    m = Image.new("L", (w, h))
    ImageDraw.Draw(m).rounded_rectangle(box or (0, 0, w - 1, h - 1), r, fill=255)
    return m


def glow(mask, color, radius, strength=1.0):
    layer = Image.new("RGBA", mask.size, rgba(color))
    layer.putalpha(mask.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, int(v * strength))))
    return layer


def solid(mask, color):
    layer = Image.new("RGBA", mask.size, rgba(color))
    layer.putalpha(ImageChops.multiply(mask, Image.new("L", mask.size, rgba(color)[3])))
    return layer


def grayed(img):
    gray = img.convert("LA").convert("RGBA")
    gray.putalpha(img.getchannel("A").point(lambda v: v * 45 // 100))
    return gray


def text_mask(text, fnt, pad, stroke=0):
    l, t, r, b = fnt.getbbox(text, stroke_width=stroke)
    m = Image.new("L", (r - l + 2 * pad, b - t + 2 * pad))
    ImageDraw.Draw(m).text((pad - l, pad - t), text, font=fnt, fill=255, stroke_width=stroke, stroke_fill=255)
    return m


def flare(img, x, y, r):
    """Lens flare: soft core, long horizontal streak, shorter vertical and diagonal rays."""
    layer = Image.new("RGBA", img.size)
    d = ImageDraw.Draw(layer)
    d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 120))
    d.line((x - r * 7, y, x + r * 7, y), fill=(255, 255, 255, 170), width=max(1, r // 4))
    d.line((x, y - r * 3, x, y + r * 3), fill=(255, 255, 255, 140), width=max(1, r // 5))
    for a in (45, 135):
        dx, dy = math.cos(math.radians(a)) * r * 2, math.sin(math.radians(a)) * r * 2
        d.line((x - dx, y - dy, x + dx, y + dy), fill=(220, 255, 180, 90), width=max(1, r // 6))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(max(1, r / 6))))
    core = Image.new("RGBA", img.size)
    ImageDraw.Draw(core).ellipse((x - r / 3, y - r / 3, x + r / 3, y + r / 3), fill=(255, 255, 255, 255))
    img.alpha_composite(core.filter(ImageFilter.GaussianBlur(max(1, r / 8))))


def chrome_text(text, size, fnt_name=LOGO_FONT, glow_color=ACID, flare_at=0.86):
    """Chrome lettering with a black outline, a neon glow and a lens flare: the logo."""
    fnt = font(fnt_name, size)
    pad = int(size * 0.6)
    fill = text_mask(text, fnt, pad)
    edge = text_mask(text, fnt, pad, stroke=max(2, size // 16))
    w, h = fill.size
    img = Image.new("RGBA", (w, h))
    img.alpha_composite(glow(edge, glow_color, size / 5, 2.4))
    img.alpha_composite(solid(edge, (0, 0, 0)))
    box = fill.getbbox()
    chrome = Image.new("RGBA", (w, h))
    chrome.paste(vgrad(w, box[3] - box[1], CHROME), (0, box[1]))
    chrome.putalpha(fill)
    img.alpha_composite(chrome)
    if flare_at:
        flare(img, int(box[0] + (box[2] - box[0]) * flare_at), box[1] + size // 10, max(4, size // 7))
    return img


def carbon(w, h):
    """Carbon-fibre weave: 4x4 cells shaded diagonally, alternating direction."""
    tile = Image.new("RGBA", (8, 8))
    px = tile.load()
    for y in range(8):
        for x in range(8):
            cell = (x // 4 + y // 4) % 2
            t = ((x % 4) + (y % 4)) / 6 if cell else ((3 - x % 4) + (y % 4)) / 6
            v = int(14 + 26 * (1 - t))
            px[x, y] = (v, v + 1, v + 2, 255)
    tile = tile.resize((z(8), z(8)), Image.BILINEAR)
    img = Image.new("RGBA", (w, h))
    for y in range(0, h, tile.height):
        for x in range(0, w, tile.width):
            img.paste(tile, (x, y))
    return img


def grunge(img, seed=7):
    """Scratches, noise and faint acid splatter."""
    rnd = random.Random(seed)
    w, h = img.size
    layer = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(layer)
    for _ in range(int(w * h / 4000 / Z / Z)):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        a, length = rnd.uniform(0, math.pi), rnd.uniform(8, 60) * Z
        d.line((x, y, x + math.cos(a) * length, y + math.sin(a) * length), fill=(255, 255, 255, rnd.randint(10, 28)), width=z(1))
    for _ in range(int(w * h / 9000 / Z / Z)):
        x, y, r = rnd.uniform(0, w), rnd.uniform(0, h), rnd.uniform(0.6, 2.6) * Z
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*ACID, rnd.randint(14, 40)))
    img.alpha_composite(layer)
    noise = Image.effect_noise((w, h), 60).convert("L")
    img.alpha_composite(solid(noise.point(lambda v: v // 14), (255, 255, 255)))


def claws(img, x, y, size, color=ACID, alpha=60):
    """Three tapering slashes."""
    layer = Image.new("RGBA", img.size)
    d = ImageDraw.Draw(layer)
    for i in range(3):
        ox = x + i * size * 0.32
        d.polygon([(ox, y), (ox + size * 0.1, y), (ox - size * 0.35, y + size), (ox - size * 0.38, y + size * 0.96)],
                  fill=(*color, alpha))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(Z)))


def screw(d):
    s = Image.new("RGBA", (d * S, d * S))
    m = Image.new("L", s.size)
    ImageDraw.Draw(m).ellipse((0, 0, d * S - 1, d * S - 1), fill=255)
    body = vgrad(d * S, d * S, [(0, (230, 232, 236)), (0.5, (120, 124, 130)), (1, (40, 42, 46))])
    body.putalpha(m)
    s.alpha_composite(body)
    ImageDraw.Draw(s).line((d * S * 0.22, d * S * 0.78, d * S * 0.78, d * S * 0.22), fill=(20, 20, 22, 255), width=max(S, d * S // 8))
    return s.resize((d, d), Image.LANCZOS)


def metal_panel(w, h, seed=3):
    """Brushed gunmetal plate, bevelled, with a screw in each corner."""
    rnd = random.Random(seed)
    streaks = Image.effect_noise((max(1, w // 10), h), 50).convert("L").resize((w, h))
    base = vgrad(w, h, [(0, (58, 62, 68)), (1, (30, 32, 36))])
    base.alpha_composite(solid(streaks.point(lambda v: v // 5), (255, 255, 255)))
    r = z(10)
    plate = Image.new("RGBA", (w, h))
    plate.paste(base, mask=rrect_mask(w, h, r))
    d = ImageDraw.Draw(plate)
    d.rounded_rectangle((0, 0, w - 1, h - 1), r, outline=(0, 0, 0, 255), width=z(2))
    d.rounded_rectangle((z(2), z(2), w - 1 - z(2), h - 1 - z(2)), r - 1, outline=(255, 255, 255, 40), width=z(1))
    s, m = z(10), z(8)
    for sx, sy in ((m, m), (w - m - s, m), (m, h - m - s), (w - m - s, h - m - s)):
        plate.alpha_composite(screw(s).rotate(rnd.uniform(0, 180), resample=Image.BICUBIC), (sx, sy))
    return plate


def inset(w, h, tint=(6, 14, 6)):
    """Recessed dark well (LCD, waveform)."""
    r = z(8)
    img = Image.new("RGBA", (w, h))
    img.paste(vgrad(w, h, [(0, tint), (1, lerp(tint, (0, 0, 0), 0.5))]), mask=rrect_mask(w, h, r))
    d = ImageDraw.Draw(img)
    for y in range(z(3), h - z(3), z(3)):  # LCD pixel rows
        d.line((z(6), y, w - z(7), y), fill=(255, 255, 255, 6))
    d.rounded_rectangle((0, 0, w - 1, h - 1), r, outline=(0, 0, 0, 255), width=z(2))
    d.line((r, h - z(2), w - r, h - z(2)), fill=(255, 255, 255, 45), width=z(1))
    return img


def bezel(w, h, sw, sh, sx, sy):
    """CRT bezel of size w x h with a screen hole sw x sh at (sx, sy), a badge and a power LED."""
    R = z(26)
    img = Image.new("RGBA", (w, h))
    img.paste(vgrad(w, h, [(0, (74, 76, 82)), (0.08, (44, 46, 50)), (1, (16, 17, 19))]), mask=rrect_mask(w, h, R))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), R, outline=(0, 0, 0, 255), width=z(2))
    d.rounded_rectangle((z(3), z(3), w - 1 - z(3), h - 1 - z(3)), R - z(2), outline=(255, 255, 255, 30), width=z(1))
    d.rounded_rectangle((sx - z(4), sy - z(4), sx + sw + z(3), sy + sh + z(3)), z(12), fill=(0, 0, 0, 255))
    d.rounded_rectangle((sx - z(5), sy - z(5), sx + sw + z(4), sy + sh + z(4)), z(13), outline=(255, 255, 255, 25), width=z(1))
    strip = h - sy - sh  # the chin under the screen
    badge = chrome_text("RIFFTRON", z(14), UI_FONT, glow_color=(0, 0, 0), flare_at=0)
    img.alpha_composite(badge, (sx - z(8), sy + sh + (strip - badge.height) // 2 + z(1)))
    lx, ly = w - z(30), sy + sh + strip // 2
    led = Image.new("L", img.size)
    ImageDraw.Draw(led).ellipse((lx - z(4), ly - z(4), lx + z(4), ly + z(4)), fill=255)
    img.alpha_composite(glow(led, ACID, z(5), 2.5))
    img.alpha_composite(solid(led, (200, 255, 120)))
    d.text((lx - z(12), ly), "POWER", font=font(UI_FONT, z(9)), fill=(150, 155, 160, 255), anchor="rm")
    return img


def crt_overlay(w, h):
    """Scanlines, dark corners and a glass glare, laid over the camera picture."""
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(0, h, z(3)):
        d.line((0, y, w, y), fill=(0, 0, 0, 70), width=z(1))
    vign = Image.new("L", (w, h), 255)
    ImageDraw.Draw(vign).rounded_rectangle((w * 0.06, h * 0.08, w * 0.94, h * 0.92), z(60), fill=0)
    img.alpha_composite(solid(vign.filter(ImageFilter.GaussianBlur(z(40))).point(lambda v: v * 150 // 255), (0, 0, 0)))
    glare = Image.new("L", (w, h))
    ImageDraw.Draw(glare).polygon([(0, 0), (w * 0.55, 0), (w * 0.25, h * 0.55), (0, h * 0.7)], fill=22)
    img.alpha_composite(solid(glare.filter(ImageFilter.GaussianBlur(z(25))), (255, 255, 255)))
    return img


def gear(cx, cy, r, teeth=8):
    pts = []
    for k in range(teeth * 4):
        a = math.radians(k * 360 / (teeth * 4))
        rr = r if k % 4 in (0, 1) else r * 0.72
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def draw_icon(d, name, x, y, s, color, hole):
    """Icons the fonts don't have, drawn in an s x s box at (x, y)."""
    if name == "play":
        d.polygon([(x + s * 0.15, y), (x + s, y + s / 2), (x + s * 0.15, y + s)], fill=color)
    elif name == "pause":
        d.rectangle((x + s * 0.1, y, x + s * 0.4, y + s), fill=color)
        d.rectangle((x + s * 0.6, y, x + s * 0.9, y + s), fill=color)
    elif name == "gear":
        d.polygon(gear(x + s / 2, y + s / 2, s / 2), fill=color)
        d.ellipse((x + s * 0.32, y + s * 0.32, x + s * 0.68, y + s * 0.68), fill=hole)
    elif name == "check":
        d.line([(x, y + s * 0.55), (x + s * 0.38, y + s * 0.9), (x + s, y + s * 0.1)], fill=color, width=max(2, int(s * 0.2)), joint="curve")
    elif name == "cross":
        w = max(2, int(s * 0.2))
        d.line((x, y, x + s, y + s), fill=color, width=w)
        d.line((x, y + s, x + s, y), fill=color, width=w)
    elif name == "in":
        d.rectangle((x, y, x + s * 0.2, y + s), fill=color)
        d.polygon([(x + s, y + s * 0.15), (x + s * 0.35, y + s / 2), (x + s, y + s * 0.85)], fill=color)
    elif name == "out":
        d.rectangle((x + s * 0.8, y, x + s, y + s), fill=color)
        d.polygon([(x, y + s * 0.15), (x + s * 0.65, y + s / 2), (x, y + s * 0.85)], fill=color)


def button(w, h, label, kind="metal", state="normal", size=None, icon=None):
    """Glossy Web 2.0 button, w x h real pixels. state: normal / hover / pressed / disabled."""
    top, bot, fg = KINDS[kind]
    if state == "pressed":
        top, bot = lerp(top, (0, 0, 0), 0.25), lerp(bot, (0, 0, 0), 0.2)
    pad = z(10)
    W, H, P = (w + 2 * pad) * S, (h + 2 * pad) * S, pad * S
    r = min(h // 2, z(16)) * S
    img = Image.new("RGBA", (W, H))
    shape = rrect_mask(W, H, r, (P, P, P + w * S - 1, P + h * S - 1))
    img.alpha_composite(glow(ImageChops.offset(shape, 0, z(3) * S), (0, 0, 0), z(4) * S, 1.3))
    if state == "hover":
        img.alpha_composite(glow(shape, top, z(7) * S, 1.6))
    body = Image.new("RGBA", (W, H))
    body.paste(vgrad(w * S, h * S, [(0, top), (0.5, lerp(top, bot, 0.55)), (0.52, lerp(top, bot, 0.75)), (1, bot)]), (P, P))
    body.putalpha(shape)
    img.alpha_composite(body)
    gloss = Image.new("L", (W, H))
    ImageDraw.Draw(gloss).rounded_rectangle((P + z(3) * S, P + z(2) * S, P + w * S - z(3) * S, P + h * S * 0.5), r * 0.8, fill=255)
    fade = vgrad(W, H, [(0, (255, 255, 255, 150)), ((P + h * S * 0.5) / H, (255, 255, 255, 25)), (1, (255, 255, 255, 0))])
    fade.putalpha(ImageChops.multiply(gloss, fade.getchannel("A")))
    if state != "pressed":
        img.alpha_composite(fade)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((P, P, P + w * S - 1, P + h * S - 1), r, outline=(0, 0, 0, 230), width=z(1) * S)
    fnt = font(UI_FONT, (size or h * 0.42) * S)
    l, t, rr, b = fnt.getbbox(label) if label else (0, 0, 0, 0)
    isz = h * S * 0.36 if icon else 0
    gap = isz * 0.5 if icon and label else 0
    push = z(1) * S if state == "pressed" else 0
    x0 = P + (w * S - (rr - l + isz + gap)) / 2
    tx, ty = x0 + isz + gap - l, P + (h * S - (b - t)) / 2 - t + push
    shadow = (255, 255, 255, 90) if kind == "acid" else (0, 0, 0, 170)
    if icon:
        iy = P + (h * S - isz) / 2 + push
        draw_icon(d, icon, x0, iy + z(1) * S, isz, shadow, (0, 0, 0, 0))
        draw_icon(d, icon, x0, iy, isz, rgba(fg), lerp(rgba(top), rgba(bot), 0.4))
    if label:
        d.text((tx, ty + z(1) * S), label, font=fnt, fill=shadow)
        d.text((tx, ty), label, font=fnt, fill=rgba(fg))
    img = img.resize((w + 2 * pad, h + 2 * pad), Image.LANCZOS)
    return grayed(img) if state == "disabled" else img


def orb(d, label="REC", kind="red", state="normal", stop=False):
    """Round glossy button in a chrome ring, d real pixels across: the record button."""
    top, bot, fg = KINDS[kind]
    if state == "pressed":
        top, bot = lerp(top, (0, 0, 0), 0.25), lerp(bot, (0, 0, 0), 0.2)
    pad = z(14)
    W, P, D = (d + 2 * pad) * S, pad * S, d * S
    img = Image.new("RGBA", (W, W))
    ring = Image.new("L", (W, W))
    ImageDraw.Draw(ring).ellipse((P, P, P + D, P + D), fill=255)
    img.alpha_composite(glow(ImageChops.offset(ring, 0, z(4) * S), (0, 0, 0), z(5) * S, 1.4))
    if state == "hover" or stop:
        img.alpha_composite(glow(ring, top, z(10) * S, 1.8))
    chrome = Image.new("RGBA", (W, W))
    chrome.paste(vgrad(D, D, CHROME), (P, P))
    chrome.putalpha(ring)
    img.alpha_composite(chrome)
    i = int(D * 0.11)
    core = Image.new("L", (W, W))
    ImageDraw.Draw(core).ellipse((P + i, P + i, P + D - i, P + D - i), fill=255)
    body = Image.new("RGBA", (W, W))
    body.paste(vgrad(D, D, [(0, top), (0.55, lerp(top, bot, 0.7)), (1, bot)]), (P, P))
    body.putalpha(core)
    img.alpha_composite(body)
    rim = Image.new("L", (W, W))
    ImageDraw.Draw(rim).ellipse((P + i, P + i, P + D - i, P + D - i), outline=255, width=int(D * 0.06))
    img.alpha_composite(solid(rim.filter(ImageFilter.GaussianBlur(D * 0.03)), (0, 0, 0, 120)))
    if state != "pressed":
        g = Image.new("L", (W, W))
        ImageDraw.Draw(g).ellipse((P + D * 0.24, P + D * 0.16, P + D * 0.76, P + D * 0.5), fill=150)
        img.alpha_composite(solid(g.filter(ImageFilter.GaussianBlur(D * 0.03)), (255, 255, 255)))
    dr = ImageDraw.Draw(img)
    c = P + D / 2
    if stop:
        q = D * 0.13
        dr.rounded_rectangle((c - q, c - q + D * 0.06, c + q, c + q + D * 0.06), D * 0.03, fill=rgba(fg))
    elif label:
        fnt = font(UI_FONT, D * 0.22)
        l, t, r, b = fnt.getbbox(label)
        x, y = c - (r - l) / 2 - l, c - (b - t) / 2 - t + D * 0.07
        dr.text((x, y + D * 0.02), label, font=fnt, fill=(0, 0, 0, 160))
        dr.text((x, y), label, font=fnt, fill=rgba(fg))
    img = img.resize((d + 2 * pad, d + 2 * pad), Image.LANCZOS)
    return grayed(img) if state == "disabled" else img


def knob(d):
    """Chrome slider thumb, d real pixels across."""
    pad = z(6)
    img = Image.new("RGBA", ((d + 2 * pad) * S,) * 2)
    P, D = pad * S, d * S
    m = Image.new("L", img.size)
    ImageDraw.Draw(m).ellipse((P, P, P + D, P + D), fill=255)
    img.alpha_composite(glow(m, ACID, z(4) * S, 1.3))
    body = Image.new("RGBA", img.size)
    body.paste(vgrad(D, D, CHROME), (P, P))
    body.putalpha(m)
    img.alpha_composite(body)
    ImageDraw.Draw(img).ellipse((P, P, P + D, P + D), outline=(0, 0, 0, 255), width=z(1) * S)
    return img.resize((d + 2 * pad,) * 2, Image.LANCZOS)


def star(cx, cy, r1, r2, n=5, rot=-90):
    return [(cx + (r1 if k % 2 == 0 else r2) * math.cos(math.radians(rot + k * 180 / n)),
             cy + (r1 if k % 2 == 0 else r2) * math.sin(math.radians(rot + k * 180 / n))) for k in range(2 * n)]


def toast(title, text):
    """Xbox 360-style 'achievement unlocked' pop-up."""
    w, h, pad = z(380), z(62), z(12)
    img = Image.new("RGBA", ((w + 2 * pad) * S, (h + 2 * pad) * S))
    P = pad * S
    shape = rrect_mask(img.width, img.height, h * S // 2, (P, P, P + w * S, P + h * S))
    img.alpha_composite(glow(shape, ACID, z(8) * S, 1.2))
    body = Image.new("RGBA", img.size)
    body.paste(vgrad(w * S, h * S, [(0, (70, 74, 80)), (0.5, (34, 36, 40)), (1, (14, 15, 17))]), (P, P))
    body.putalpha(shape)
    img.alpha_composite(body)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((P, P, P + w * S, P + h * S), h * S // 2, outline=(*ACID, 255), width=z(2) * S)
    dd = h - z(12)
    disc = orb(dd, "", "acid")
    disc = disc.resize((disc.width * S, disc.height * S), Image.LANCZOS)
    off = (h * S - disc.height) // 2
    img.alpha_composite(disc, (P + off, P + off))
    c = P + (h * S) / 2
    d.polygon(star(c, c, h * S * 0.2, h * S * 0.085), fill=(10, 30, 0, 255))
    d.text((P + h * S + z(6) * S, P + h * S * 0.18), title, font=font(UI_FONT, z(11) * S), fill=(*ACID, 255))
    d.text((P + h * S + z(6) * S, P + h * S * 0.45), text, font=font(UI_FONT, z(17) * S), fill=(255, 255, 255, 255))
    return img.resize((w + 2 * pad, h + 2 * pad), Image.LANCZOS)


def static(w, h, text="NO SIGNAL"):
    """TV snow with a caption: what the screen shows before the camera is up."""
    snow = Image.effect_noise((max(1, w // z(2)), max(1, h // z(2))), 90).convert("L").resize((w, h), Image.NEAREST)
    img = Image.merge("RGBA", (snow.point(lambda v: v // 3),) * 3 + (Image.new("L", (w, h), 255),))
    m = Image.new("L", (w, h))
    ImageDraw.Draw(m).text((w / 2, h / 2), text, font=font(LCD_FONT, z(44)), fill=255, anchor="mm")
    img.alpha_composite(solid(m.filter(ImageFilter.MaxFilter(z(4) * 2 + 1)), (0, 0, 0, 220)))
    img.alpha_composite(glow(m, ACID, z(6), 1.5))
    img.alpha_composite(solid(m, ACID))
    return img


def starburst(r, text, color=(255, 230, 0), fg=(200, 0, 60)):
    """Web 2.0 sticker, radius r real pixels."""
    pad = max(10, r // 4)
    img = Image.new("RGBA", ((2 * r + 2 * pad) * S,) * 2)
    c = (r + pad) * S
    m = Image.new("L", img.size)
    ImageDraw.Draw(m).polygon(star(c, c, r * S, r * S * 0.82, n=18, rot=0), fill=255)
    img.alpha_composite(glow(ImageChops.offset(m, 0, r * S // 12), (0, 0, 0), r * S / 9, 1.2))
    body = Image.new("RGBA", img.size)
    body.paste(vgrad(img.width, img.height, [(0, lerp(color, (255, 255, 255), 0.4)), (1, lerp(color, (0, 0, 0), 0.15))]))
    body.putalpha(m)
    img.alpha_composite(body)
    ImageDraw.Draw(img).text((c, c), text, font=font(UI_FONT, r * S * 0.42), fill=rgba(fg), anchor="mm")
    return img.resize(((2 * r + 2 * pad),) * 2, Image.LANCZOS)


def background(w, h, header, bezel_box, screen, panel, lcd):
    """The whole window (real pixels): carbon fibre, grunge, header with a neon tube, CRT bezel, metal panel, LCD well."""
    img = carbon(w, h)
    grunge(img)
    claws(img, w - z(120), z(4), z(66))
    vig = Image.new("L", (w, h), 255)
    ImageDraw.Draw(vig).rectangle((z(20), z(20), w - z(20), h - z(20)), fill=0)
    img.alpha_composite(solid(vig.filter(ImageFilter.GaussianBlur(z(30))).point(lambda v: v * 160 // 255), (0, 0, 0)))
    hx0, hy0, hx1, hy1 = header
    img.alpha_composite(vgrad(hx1 - hx0, hy1 - hy0, [(0, (40, 42, 46, 210)), (1, (6, 7, 8, 235))]), (hx0, hy0))
    line = Image.new("L", (w, h))
    ImageDraw.Draw(line).line((0, hy1 - z(2), w, hy1 - z(2)), fill=255, width=z(3))
    neon = vgrad(h, w, [(0, PINK), (0.5, (255, 255, 255)), (1, ACID)]).rotate(90, expand=True)  # pink -> green, left -> right
    for strength, blur in ((2.2, z(6)), (1, 0)):
        layer = neon.copy()
        layer.putalpha(line.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: min(255, int(v * strength))) if blur else line)
        img.alpha_composite(layer)
    bx0, by0, bx1, by1 = bezel_box
    sx, sy, sw, sh = screen
    img.alpha_composite(glow(rrect_mask(w, h, z(26), bezel_box), (0, 0, 0), z(8), 1.2))
    img.alpha_composite(bezel(bx1 - bx0, by1 - by0, sw, sh, sx - bx0, sy - by0), (bx0, by0))
    sticker = starburst(z(27), "HD!").rotate(-14, resample=Image.BICUBIC)
    img.alpha_composite(sticker, (w * 62 // 100 - sticker.width // 2, z(-6)))  # slapped on the header, like on a real CRT
    img.alpha_composite(glow(rrect_mask(w, h, z(10), panel), ACID, z(5), 0.9))
    img.alpha_composite(metal_panel(panel[2] - panel[0], panel[3] - panel[1]), panel[:2])
    img.alpha_composite(inset(lcd[2] - lcd[0], lcd[3] - lcd[1]), lcd[:2])
    return img


def badge88(text1, text2, colors):
    """A tiny 88x31 web button, 2000s style."""
    bg, fg1, fg2 = colors
    img = Image.new("RGBA", (88, 31), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((1, 1, 86, 29), fill=rgba(bg))
    d.rectangle((1, 1, 86, 29), outline=(255, 255, 255, 120))
    d.rectangle((3, 3, 32, 27), fill=(0, 0, 0, 255))
    d.text((17, 15), text1, font=font(LCD_FONT, 18), fill=rgba(fg1), anchor="mm")
    lines = text2.split("\n")
    for i, line in enumerate(lines):
        d.text((60, 15 + (i - (len(lines) - 1) / 2) * 10), line, font=font(LCD_FONT, 13), fill=rgba(fg2), anchor="mm")
    return img
