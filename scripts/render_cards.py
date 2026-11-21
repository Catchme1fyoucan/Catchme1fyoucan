#!/usr/bin/env python3
"""Render the profile README as a stack of animated SVG cards.

    python scripts/render_cards.py            # animated cards -> cards/ (+ README.md)
    STATIC=1 python scripts/render_cards.py   # final frame, no animation -> cards-static/
                                              # (+ cards-static/preview.html to screenshot)

Inputs   config.json                  name, tagline, tiles, featured repos
         data/contributions.json      refreshed daily by .github/workflows/cards.yml
         fonts/*.ttf                  subset per card with pyftsubset, embedded as WOFF
Env      STATIC=1  CONFIG=path  DATA=path  OUT=dir

Cards are shown through <img>, so each SVG is self-contained: an inline <style>
block (CSS keyframes), SMIL <animate> for the decorative loops, and base64 WOFF
fonts. Every element's base style is its FINAL state; keyframes only describe the
`from` state. That is what makes STATIC=1 and prefers-reduced-motion trivial:
remove the animation and the final frame is what remains.
"""
import base64
import datetime as dt
import html
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
STATIC = os.environ.get("STATIC") == "1"
CONFIG = Path(os.environ.get("CONFIG", ROOT / "config.json"))
DATA = Path(os.environ.get("DATA", ROOT / "data" / "contributions.json"))
OUT = Path(os.environ.get("OUT", ROOT / ("cards-static" if STATIC else "cards")))

# --------------------------------------------------------------------------- theme
# No website was available to sample, so this palette is chosen, not extracted.
# Change BRAND (and the fonts below) and every card follows.
BRAND = "#FF6B3D"
C = dict(
    panel="#171A24",
    line="#2A2E3D",
    grid="#242838",
    ink="#F5F0E8",
    muted="#A3A1AE",
    brand=BRAND,
)


def mix(a, b, t):
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(pa, pb))


# contribution graph ramp: empty cell, then one hue light -> saturated
RAMP = [C["grid"]] + [mix(C["grid"], BRAND, t) for t in (0.34, 0.58, 0.8, 1.0)]
BAR_SOFT = mix(C["panel"], BRAND, 0.62)

FONT_DIR = ROOT / "fonts"
FONT_FILES = {
    ("sans", 400): "BricolageGrotesque-Regular.ttf",
    ("sans", 700): "BricolageGrotesque-Bold.ttf",
    ("mono", 400): "JetBrainsMono-Regular.ttf",
    ("mono", 700): "JetBrainsMono-Bold.ttf",
}
FAMILY = {"sans": "Bricolage Grotesque", "mono": "JetBrains Mono"}
FALLBACK = {
    "sans": "system-ui,-apple-system,'Segoe UI',sans-serif",
    "mono": "ui-monospace,SFMono-Regular,Menlo,monospace",
}

W = 830
PAD = 44

# ------------------------------------------------------------------ font helpers
_fonts, _missing, _subsets = {}, set(), {}


def _font(font, weight):
    key = (font, weight)
    if key not in _fonts:
        _fonts[key] = TTFont(FONT_DIR / FONT_FILES[key])
    return _fonts[key]


def width(text, size, font="sans", weight=400):
    """Advance width in px (no kerning; callers keep a little slack)."""
    f = _font(font, weight)
    cmap, hmtx, upem = f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm
    total = 0
    for ch in text:
        g = cmap.get(ord(ch))
        if g is None:
            _missing.add(ch)
            g = ".notdef"
        total += hmtx[g][0]
    return total * size / upem


def fit(text, maxw, size, minsize, font="sans", weight=400):
    while size > minsize and width(text, size, font, weight) > maxw:
        size -= 1
    return size


def wrap(text, maxw, size, font="sans", weight=400, maxlines=2):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if not cur or width(trial, size, font, weight) <= maxw:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        last = lines[-1]
        while last and width(last + "...", size, font, weight) > maxw:
            last = last[:-1].rstrip()
        lines[-1] = last + "..."
    return lines


def wrap_fit(text, maxw, size, minsize, font="sans", weight=400, maxlines=2):
    """Largest size (down to minsize) at which the text fits maxlines untruncated."""
    while size > minsize and len(wrap(text, maxw, size, font, weight, 99)) > maxlines:
        size -= 1
    return size, wrap(text, maxw, size, font, weight, maxlines)


def subset_b64(font, weight, chars):
    """Subset one face to `chars` with pyftsubset and return it as base64 WOFF."""
    chars = "".join(sorted(set(chars) | {" "}))
    key = (font, weight, chars)
    if key in _subsets:
        return _subsets[key]
    cli = shutil.which("pyftsubset")
    cmd = [cli] if cli else [sys.executable, "-m", "fontTools.subset"]
    with tempfile.TemporaryDirectory() as tmp:
        txt, out = Path(tmp, "chars.txt"), Path(tmp, "face.woff")
        txt.write_text(chars, encoding="utf-8")
        subprocess.run(
            cmd + [
                str(FONT_DIR / FONT_FILES[(font, weight)]),
                f"--text-file={txt}",
                f"--output-file={out}",
                "--flavor=woff",
                "--layout-features=kern,liga",
                "--no-hinting",
            ],
            check=True,
            capture_output=True,
        )
        data = base64.b64encode(out.read_bytes()).decode()
    _subsets[key] = data
    return data


# ------------------------------------------------------------------- animation
KEYFRAMES = {
    "rise": (
        "@keyframes rise{from{opacity:0;transform:translateY(12px)}}",
        ".a-rise{animation:rise .7s cubic-bezier(.2,.7,.2,1) both}",
    ),
    "grow": (
        "@keyframes grow{from{transform:scaleY(0)}}",
        ".a-grow{transform-box:fill-box;transform-origin:50% 100%;"
        "animation:grow .8s cubic-bezier(.2,.8,.2,1) both}",
    ),
    "colin": (
        "@keyframes colin{from{opacity:0;transform:translateY(6px)}}",
        ".a-colin{animation:colin .55s ease-out both}",
    ),
    "pop": (
        "@keyframes pop{from{opacity:0;transform:scale(.6)}}",
        ".a-pop{transform-box:fill-box;transform-origin:center;"
        "animation:pop .7s cubic-bezier(.3,1.4,.5,1) both}",
    ),
    "trace": (  # brand outline draws around the card, then fades to nothing
        "@keyframes trace{0%{stroke-dashoffset:1;opacity:1}"
        "65%{stroke-dashoffset:0;opacity:1}100%{stroke-dashoffset:0;opacity:0}}",
        ".a-trace{animation:trace 1.7s ease-in-out both}",
    ),
    "breathe": (  # ambient loop, no delay
        "@keyframes breathe{from{opacity:.55}}",
        ".a-breathe{animation:breathe 6s ease-in-out infinite alternate}",
    ),
}


def esc(s):
    return html.escape(str(s), quote=True)


class Card:
    def __init__(self, name, w, h, title, desc, t0=0.0):
        self.name, self.w, self.h, self.t0 = name, w, h, t0
        self.title, self.desc = title, desc
        self.parts, self.defs = [], []
        self.glyphs, self.delays, self.kinds = {}, set(), set()
        self.extra_css = ""

    # -- animation classes (empty in STATIC mode, so nothing animates) ----------
    def anim(self, kind, delay=None):
        if STATIC:
            return ""
        self.kinds.add(kind)
        if delay is None:
            return f"a-{kind}"
        ms = round((self.t0 + delay) * 1000)
        self.delays.add(ms)
        return f"a-{kind} d{ms}"

    def cls(self, kind=None, delay=None, extra=""):
        c = (extra + " " + (self.anim(kind, delay) if kind else "")).strip()
        return f' class="{c}"' if c else ""

    def add(self, markup):
        self.parts.append(markup)

    def smil(self, markup):
        """Decorative SMIL loops; hidden entirely under prefers-reduced-motion."""
        if not STATIC:
            self.parts.append(f'<g class="smil" aria-hidden="true">{markup}</g>')

    # -- primitives -------------------------------------------------------------
    def text(self, x, y, t, size, font="sans", weight=400, fill=None,
             anchor="start", anim=None, extra=""):
        self.glyphs.setdefault((font, weight), set()).update(t)
        cls = f"{font} " + (self.anim(*anim) if anim else "")
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        self.add(
            f'<text x="{x:g}" y="{y:g}" class="{cls.strip()}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill or C["ink"]}"{a}{extra}>{esc(t)}</text>'
        )

    def panel(self, rx=20):
        self.add(
            f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="{rx}" '
            f'fill="{C["panel"]}" stroke="{C["line"]}"/>'
        )

    def trace(self, delay=0.0, rx=20):
        if STATIC:
            return
        self.add(
            f'<rect x=".75" y=".75" width="{self.w - 1.5}" height="{self.h - 1.5}" '
            f'rx="{rx - 0.75}" fill="none" stroke="{C["brand"]}" stroke-width="1.5" '
            f'pathLength="1" stroke-dasharray="1" stroke-dashoffset="0" opacity="0"'
            f'{self.cls("trace", delay)}/>'
        )

    # -- output -------------------------------------------------------------------
    def css(self):
        out = []
        for (font, weight), chars in sorted(self.glyphs.items()):
            b64 = subset_b64(font, weight, chars)
            out.append(
                f"@font-face{{font-family:'{FAMILY[font]}';font-weight:{weight};"
                f"src:url(data:font/woff;base64,{b64}) format('woff')}}"
            )
        for font in ("sans", "mono"):
            out.append(f".{font}{{font-family:'{FAMILY[font]}',{FALLBACK[font]}}}")
        out.append(self.extra_css)
        if not STATIC:
            for kind in sorted(self.kinds):
                out.extend(KEYFRAMES[kind])
            # delay classes come after the .a-* rules so they win the cascade
            out.extend(f".d{ms}{{animation-delay:{ms}ms}}" for ms in sorted(self.delays))
            out.append(
                "@media (prefers-reduced-motion:reduce)"
                "{*{animation:none!important}.smil{display:none}}"
            )
        return "".join(out)

    def svg(self):
        body = "".join(self.parts)  # build body first: fonts depend on glyphs used
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="t d">'
            f'<title id="t">{esc(self.title)}</title><desc id="d">{esc(self.desc)}</desc>'
            f'<defs><style>{self.css()}</style>{"".join(self.defs)}</defs>{body}</svg>\n'
        )


# ------------------------------------------------------------------------ data
def load_data():
    raw = json.loads(DATA.read_text()) if DATA.exists() else {}
    days = sorted((dt.date.fromisoformat(d), int(n)) for d, n in raw.get("days", []))
    return days, raw.get("repos", {})


def month_add(y, m, k):
    i = y * 12 + (m - 1) + k
    return i // 12, i % 12 + 1


def metrics(days):
    m = {"empty": not days}
    if not days:
        return m
    m["first"], m["last"] = days[0][0], days[-1][0]
    by_month = {}
    for d, n in days:
        by_month[(d.year, d.month)] = by_month.get((d.year, d.month), 0) + n
    ly, lm = m["last"].year, m["last"].month
    m["months"] = [
        ((y, mo), by_month.get((y, mo), 0))
        for y, mo in (month_add(ly, lm, k) for k in range(-11, 1))
    ]
    m["total"] = sum(n for _, n in days)
    m["active"] = sum(1 for _, n in days if n > 0)
    m["busiest"] = max(days, key=lambda t: (t[1], t[0]))  # latest wins ties
    longest = run = 0
    for _, n in days:
        run = run + 1 if n > 0 else 0
        longest = max(longest, run)
    m["longest"] = longest
    seq = [n for _, n in days]
    if seq and seq[-1] == 0:  # today not over yet: the streak is still alive
        seq = seq[:-1]
    cur = 0
    for n in reversed(seq):
        if n <= 0:
            break
        cur += 1
    m["current"] = cur
    return m


def nice_axis(maxv):
    if maxv <= 0:
        return 1, [0, 1]
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000):
        if maxv / step <= 4:
            top = math.ceil(maxv / step) * step
            return top, list(range(0, top + 1, step))
    top = math.ceil(maxv / 10000) * 10000
    return top, list(range(0, top + 1, 10000))


def fmt_date(d, year=True):
    return f"{d.strftime('%b')} {d.day}" + (f", {d.year}" if year else "")


# ------------------------------------------------------------------------ cards
def load_portrait(cfg):
    p = cfg.get("portrait")
    if not p or not (ROOT / p).is_file():
        return None
    path = ROOT / p
    try:  # square-crop and shrink so the embedded data URI stays small
        from PIL import Image
        im = Image.open(path).convert("RGB")
        s = min(im.size)
        l, t = (im.width - s) // 2, (im.height - s) // 3  # bias toward the face
        im = im.crop((l, t, l + s, t + s)).resize((360, 360), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=84, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def hero(cfg, t0):
    H = 292
    name, tagline = cfg["name"], cfg["tagline"]
    portrait = load_portrait(cfg)
    c = Card("hero", W, H, f"{name} on GitHub", tagline, t0)

    c.defs.append(
        f'<radialGradient id="glow" cx=".88" cy=".05" r=".75">'
        f'<stop offset="0" stop-color="{C["brand"]}" stop-opacity=".30"/>'
        f'<stop offset="1" stop-color="{C["brand"]}" stop-opacity="0"/></radialGradient>'
        f'<pattern id="dots" width="18" height="18" patternUnits="userSpaceOnUse">'
        f'<circle cx="2" cy="2" r="1.3" fill="{C["brand"]}" fill-opacity=".38"/></pattern>'
        f'<linearGradient id="fg" x1="0" x2="1"><stop offset="0" stop-color="#000"/>'
        f'<stop offset="1" stop-color="#fff"/></linearGradient>'
        f'<mask id="fm" maskUnits="userSpaceOnUse" x="0" y="0" width="{W}" height="{H}">'
        f'<rect x="{W * .42:g}" y="0" width="{W * .58:g}" height="{H}" fill="url(#fg)"/></mask>'
        f'<clipPath id="clip"><rect width="{W}" height="{H}" rx="20"/></clipPath>'
    )
    c.panel()
    c.add(
        f'<g clip-path="url(#clip)">'
        f'<rect width="{W}" height="{H}" fill="url(#glow)"{c.cls("breathe")}/>'
        f'<rect x="{W * .42:g}" width="{W * .58:g}" height="{H}" fill="url(#dots)" mask="url(#fm)"/>'
        f"</g>"
    )
    c.trace(0.0)

    # eyebrow: status dot (SMIL pulse ring) + handle
    c.add(f'<circle cx="{PAD + 5}" cy="62" r="5" fill="{C["brand"]}"{c.cls("pop", 0.1)}/>')
    c.smil(
        f'<circle cx="{PAD + 5}" cy="62" r="5" fill="none" stroke="{C["brand"]}" stroke-width="1.5">'
        f'<animate attributeName="r" values="5;15" dur="2.4s" begin="1s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values=".8;0" dur="2.4s" begin="1s" repeatCount="indefinite"/>'
        f"</circle>"
    )
    c.text(PAD + 22, 67, cfg["eyebrow"], 15, "mono", 400, C["muted"], anim=("rise", 0.1))

    text_w = (W - 2 * PAD - (184 + 40)) if portrait else (W - 2 * PAD - 60)
    size = fit(name + ".", text_w, 84, 44, "sans", 700)
    c.glyphs.setdefault(("sans", 700), set()).update(name + ".")
    c.add(
        f'<text x="{PAD - 3}" y="158" class="sans{" " + c.anim("rise", 0.2) if not STATIC else ""}" '
        f'font-size="{size}" font-weight="700" letter-spacing="-2" fill="{C["ink"]}">'
        f'{esc(name)}<tspan fill="{C["brand"]}">.</tspan></text>'
    )

    # underline + SMIL highlight sweeping along it
    c.add(
        f'<rect x="{PAD}" y="180" width="72" height="4" rx="2" fill="{C["brand"]}"'
        f'{c.cls("rise", 0.4)}/>'
    )
    c.smil(
        f'<rect x="{PAD}" y="180" width="22" height="4" rx="2" fill="{C["ink"]}" opacity=".85">'
        f'<animate attributeName="x" values="{PAD};{PAD + 50};{PAD}" dur="2.6s" begin="1.2s" '
        f'repeatCount="indefinite" calcMode="spline" keySplines=".4 0 .2 1;.4 0 .2 1"/></rect>'
    )

    tsize, lines = wrap_fit(tagline, text_w, 21, 16)
    for i, line in enumerate(lines):
        c.text(PAD, 228 + round(tsize * 1.43) * i, line, tsize, "sans", 400, C["ink"],
               anim=("rise", 0.55 + 0.12 * i))

    if portrait:
        cx, cy = W - PAD - 92, H / 2
        c.defs.append(f'<clipPath id="pc"><circle cx="{cx:g}" cy="{cy:g}" r="84"/></clipPath>')
        c.add(
            f'<g{c.cls("pop", 0.35)}><circle cx="{cx:g}" cy="{cy:g}" r="90" fill="none" '
            f'stroke="{C["brand"]}" stroke-width="3"/>'
            f'<image href="{portrait}" x="{cx - 84:g}" y="{cy - 84:g}" width="168" height="168" '
            f'preserveAspectRatio="xMidYMid slice" clip-path="url(#pc)"/></g>'
        )
    return c


def tile(i, t, t0):
    w, h = 270, 120
    c = Card(f"tile-{i + 1}", w, h, t["title"], t["sub"], t0)
    c.panel(16)
    c.trace(0.0, 16)
    c.text(22, 33, f"{i + 1:02d}", 12, "mono", 400, C["muted"], anim=("rise", 0.15))
    size = fit(t["title"], w - 22 - 56, 25, 17, "sans", 700)
    c.text(22, 72, t["title"], size, "sans", 700, C["ink"], anim=("rise", 0.25))
    sub = wrap(t["sub"], w - 44, 12, "mono", 400, 1)[0]
    c.text(22, 98, sub, 12, "mono", 400, C["muted"], anim=("rise", 0.35))
    ax, ay = w - 42, 24  # arrow, up and to the right
    c.add(
        f'<path d="M{ax} {ay + 14}L{ax + 14} {ay}M{ax + 3} {ay}H{ax + 14}V{ay + 11}" fill="none" '
        f'stroke="{C["brand"]}" stroke-width="2.4" stroke-linecap="round" '
        f'stroke-linejoin="round"{c.cls("rise", 0.4)}/>'
    )
    return c


def chart(m, t0):
    H = 336
    c = Card("contributions", W, H, "Contributions per month",
             "Bar chart of contributions per month for the last 12 months, with streaks.", t0)
    c.panel()
    c.trace(0.0)
    c.text(PAD, 58, "Contributions per month", 24, "sans", 700, C["ink"], anim=("rise", 0.1))
    if m["empty"]:
        sub = "Waiting for the first data refresh"
    else:
        (y0, m0), (y1, m1) = m["months"][0][0], m["months"][-1][0]
        sub = f"{dt.date(y0, m0, 1).strftime('%b %Y')} - {dt.date(y1, m1, 1).strftime('%b %Y')}"
    c.text(PAD, 82, sub, 13, "mono", 400, C["muted"], anim=("rise", 0.2))

    x0, x1, top, base = 92, 500, 128, 272
    values = [v for _, v in m["months"]] if not m["empty"] else [0] * 12
    ymax, ticks = nice_axis(max(values))
    for tk in ticks:
        y = base - (base - top) * tk / ymax
        c.add(f'<line x1="{x0}" y1="{y:g}" x2="{x1}" y2="{y:g}" stroke="{C["grid"]}" stroke-width="1"/>')
        c.text(x0 - 12, y + 4, f"{tk:,}", 11, "mono", 400, C["muted"], anchor="end", anim=("rise", 0.2))

    slot = (x1 - x0) / 12
    bw = 22
    peak = max(range(12), key=lambda i: (values[i], i)) if max(values) > 0 else None
    for i, v in enumerate(values):
        cx = x0 + slot * (i + 0.5)
        if not m["empty"]:
            label = dt.date(*m["months"][i][0], 1).strftime("%b")
            c.text(cx, 296, label, 11, "mono", 400, C["muted"], anchor="middle", anim=("rise", 0.3))
        if v <= 0:
            continue
        hgt = max(3, (base - top) * v / ymax)
        yt, r = base - hgt, min(4, hgt / 2)
        xl, xr = cx - bw / 2, cx + bw / 2
        fill = C["brand"] if i == peak else BAR_SOFT
        c.add(
            f'<path d="M{xl:g} {base}V{yt + r:g}Q{xl:g} {yt:g} {xl + r:g} {yt:g}H{xr - r:g}'
            f'Q{xr:g} {yt:g} {xr:g} {yt + r:g}V{base}Z" fill="{fill}"'
            f'{c.cls("grow", 0.35 + 0.05 * i)}/>'
        )
        if i == peak or i == 11:  # label only the extreme and the latest month
            c.text(cx, yt - 8, f"{v:,}", 12, "mono", 700, C["ink"], anchor="middle",
                   anim=("rise", 0.35 + 0.05 * i + 0.5))

    c.add(f'<line x1="528" y1="{top - 8}" x2="528" y2="{base + 24}" stroke="{C["grid"]}"/>')
    d = "0" if m["empty"] else None
    busiest = m.get("busiest")
    stats = [
        ("Current streak", d or f"{m['current']:,}", "days"),
        ("Longest streak", d or f"{m['longest']:,}", "days"),
        ("Active days", d or f"{m['active']:,}", "" if m["empty"] else f"of {(m['last'] - m['first']).days + 1}"),
        ("Busiest day", d or f"{busiest[1]:,}", "" if m["empty"] else fmt_date(busiest[0])),
    ]
    for i, (label, value, cap) in enumerate(stats):
        sx = 556 + (i % 2) * 124
        sy = 112 + (i // 2) * 100
        dl = 0.6 + 0.12 * i
        c.text(sx, sy + 14, label, 12, "mono", 400, C["muted"], anim=("rise", dl))
        c.text(sx, sy + 58, value, 40, "sans", 700, C["ink"], anim=("rise", dl + 0.05))
        c.text(sx, sy + 80, cap, 12, "mono", 400, C["muted"], anim=("rise", dl + 0.1))
    return c


def star_path(cx, cy, ro=6.2, ri=2.7):
    pts = []
    for k in range(10):
        r = ro if k % 2 == 0 else ri
        a = -math.pi / 2 + k * math.pi / 5
        pts.append(f"{cx + r * math.cos(a):.2f},{cy + r * math.sin(a):.2f}")
    return "M" + "L".join(pts) + "Z"


def repo_card(i, r, live, t0):
    H = 152
    full = r["repo"]
    owner, name = full.split("/", 1)
    c = Card(f"repo-{i + 1}", W, H, full, r.get("description", ""), t0)
    c.panel()
    c.trace(0.0)

    c.text(PAD, 52, owner + "/", 15, "mono", 400, C["muted"], anim=("rise", 0.15))
    ox = PAD + width(owner + "/", 15, "mono", 400) + 1
    c.text(ox, 52, name, 20, "mono", 700, C["ink"], anim=("rise", 0.15))
    if not live:  # honest marker: this card is not reading a real repo yet
        pw = width("placeholder", 11, "mono") + 22
        px = W - PAD - pw
        c.add(
            f'<rect x="{px:g}" y="34" width="{pw:g}" height="24" rx="12" fill="none" '
            f'stroke="{C["muted"]}" stroke-dasharray="3 3"{c.cls("rise", 0.2)}/>'
        )
        c.text(px + pw / 2, 50, "placeholder", 11, "mono", 400, C["muted"], anchor="middle",
               anim=("rise", 0.2))

    lines = wrap(r.get("description") or "No description yet.", W - 2 * PAD, 15, "sans", 400, 2)
    for k, line in enumerate(lines):
        c.text(PAD, 80 + 21 * k, line, 15, "sans", 400, C["muted"], anim=("rise", 0.3 + 0.08 * k))

    y, x, dl = 130, PAD, 0.5
    if r.get("language"):
        c.add(f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{r.get("color") or "#8B8B8B"}"{c.cls("pop", dl)}/>')
        c.text(x + 16, y, r["language"], 13, "mono", 400, C["muted"], anim=("rise", dl))
        x += 16 + width(r["language"], 13, "mono") + 26
    stars = f"{int(r.get('stars', 0)):,}"
    c.add(f'<path d="{star_path(x + 6, y - 4)}" fill="{C["muted"]}"{c.cls("rise", dl + 0.08)}/>')
    c.text(x + 18, y, stars, 13, "mono", 400, C["muted"], anim=("rise", dl + 0.08))
    x += 18 + width(stars, 13, "mono") + 26
    forks = f"{int(r.get('forks', 0)):,}"
    fx, fy = x + 6, y - 4
    c.add(
        f'<g fill="none" stroke="{C["muted"]}" stroke-width="1.4" stroke-linecap="round"'
        f'{c.cls("rise", dl + 0.16)}><circle cx="{fx - 4}" cy="{fy - 5}" r="1.9"/>'
        f'<circle cx="{fx + 4}" cy="{fy - 5}" r="1.9"/><circle cx="{fx}" cy="{fy + 5}" r="1.9"/>'
        f'<path d="M{fx - 4} {fy - 3}v1.5a2.5 2.5 0 0 0 2.5 2.5h3a2.5 2.5 0 0 0 2.5-2.5V{fy - 3}M{fx} {fy}v3"/></g>'
    )
    c.text(x + 18, y, forks, 13, "mono", 400, C["muted"], anim=("rise", dl + 0.16))
    return c


def graph(days, t0):
    H = 264
    total = sum(n for _, n in days)
    c = Card("graph", W, H, "Contribution graph",
             f"Calendar heatmap of {total:,} contributions over the last year.", t0)
    c.panel()
    c.trace(0.0)
    c.text(PAD, 58, "Contribution graph", 24, "sans", 700, C["ink"], anim=("rise", 0.1))
    sub = "Waiting for the first data refresh" if not days else f"{total:,} contributions in the last year"
    c.text(PAD, 82, sub, 13, "mono", 400, C["muted"], anim=("rise", 0.2))

    pitch, cell = 13, 10
    gx0, gy0 = PAD + 32, 124
    c.extra_css = "".join(f".l{k}{{fill:{col}}}" for k, col in enumerate(RAMP))

    for row, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.text(PAD, gy0 + row * pitch + 9, lab, 11, "mono", 400, C["muted"], anim=("rise", 0.3))

    nz = sorted(n for _, n in days if n > 0)
    thr = [nz[min(len(nz) - 1, int(len(nz) * p))] for p in (0.25, 0.5, 0.75)] if nz else []

    def level(n):
        return 0 if n <= 0 else 1 + sum(n > t for t in thr)

    if days:
        off = (days[0][0].weekday() + 1) % 7  # weeks start on Sunday
        cols = {}
        last_label_col = -9
        for i, (d, n) in enumerate(days):
            col, row = (off + i) // 7, (off + i) % 7
            cols.setdefault(col, []).append(
                f'<rect x="{gx0 + col * pitch}" y="{gy0 + row * pitch}" width="{cell}" '
                f'height="{cell}" rx="2.5" class="l{level(n)}"/>'
            )
            if d.day == 1 and col - last_label_col >= 3 and gx0 + col * pitch < W - PAD - 24:
                c.text(gx0 + col * pitch, gy0 - 12, d.strftime("%b"), 11, "mono", 400, C["muted"],
                       anim=("rise", 0.3))
                last_label_col = col
        for col in sorted(cols):
            c.add(f'<g{c.cls("colin", 0.35 + 0.014 * col)}>{"".join(cols[col])}</g>')
        lc, lr = (off + len(days) - 1) // 7, (off + len(days) - 1) % 7
        c.smil(
            f'<rect x="{gx0 + lc * pitch - 2}" y="{gy0 + lr * pitch - 2}" width="{cell + 4}" '
            f'height="{cell + 4}" rx="4" fill="none" stroke="{C["ink"]}" stroke-width="1.5">'
            f'<animate attributeName="stroke-opacity" values="1;.1;1" dur="2.4s" begin="2.5s" '
            f'repeatCount="indefinite"/></rect>'
        )

    # legend, right-aligned on the grid's right edge
    ly = 242
    more_w = width("More", 11, "mono")
    xr = W - PAD
    c.text(xr, ly, "More", 11, "mono", 400, C["muted"], anchor="end", anim=("rise", 1.0))
    sx = xr - more_w - 8 - 5 * pitch + 3
    c.add("".join(
        f'<rect x="{sx + k * pitch}" y="{ly - 9}" width="{cell}" height="{cell}" rx="2.5" class="l{k}"/>'
        for k in range(5)
    ))
    c.text(sx - 8, ly, "Less", 11, "mono", 400, C["muted"], anchor="end", anim=("rise", 1.0))
    return c


# --------------------------------------------------------- README / preview markup
def layout(rows, prefix):
    out = []
    for row in rows:
        imgs = []
        for im in row:
            tag = f'<img src="{prefix}{im["file"]}" alt="{esc(im["alt"])}" width="{im["width"]}">'
            imgs.append(f'<a href="{esc(im["href"])}">{tag}</a>' if im.get("href") else tag)
        out.append('<p align="center">\n  ' + "\n  ".join(imgs) + "\n</p>")
    return "\n\n".join(out) + "\n"


# --------------------------------------------------------------------------- main
def main():
    cfg = json.loads(CONFIG.read_text())
    days, live_repos = load_data()
    m = metrics(days)
    profile = f"https://github.com/{cfg['login']}"

    cards, rows = [], []

    def put(card, alt, href=None, width_="100%"):
        cards.append(card)
        return {"file": f"{card.name}.svg", "alt": alt, "href": href, "width": width_}

    rows.append([put(hero(cfg, 0.0), f"{cfg['name']}: {cfg['tagline']}")])

    rows.append([
        put(tile(i, t, 0.7 + 0.12 * i), f"{t['title']}: {t['sub']}", t["href"], "32.5%")
        for i, t in enumerate(cfg["tiles"][:3])
    ])

    rows.append([put(chart(m, 1.4), "Contributions per month, with streaks and busiest day")])

    for i, r in enumerate(cfg.get("repos", [])):
        live = r["repo"] in live_repos
        merged = {**r, **live_repos.get(r["repo"], {})}
        href = f"https://github.com/{r['repo']}" if live else f"{profile}?tab=repositories"
        rows.append([put(repo_card(i, merged, live, 2.0 + 0.35 * i), f"{r['repo']}: {merged.get('description', '')}", href)])

    rows.append([put(graph(days, 2.1 + 0.35 * len(cfg.get("repos", []))), "Contribution graph for the last year")])

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()  # drop cards of repos that were removed from config.json
    for card in cards:
        svg = card.svg()
        (OUT / f"{card.name}.svg").write_text(svg, encoding="utf-8")
        print(f"{OUT.name}/{card.name}.svg  {len(svg) / 1024:5.1f} KB")

    if STATIC:
        page = (
            '<!doctype html><meta charset="utf-8"><title>Static preview</title>'
            '<body style="margin:0;background:#fff"><div style="width:830px;margin:0 auto;padding:24px 0">'
            + layout(rows, "") + "</div></body>\n"
        )
        (OUT / "preview.html").write_text(page, encoding="utf-8")
        print(f"{OUT.name}/preview.html  (screenshot this)")
    else:
        header = "<!-- generated by scripts/render_cards.py; edit config.json, not this file -->\n"
        (ROOT / "README.md").write_text(header + layout(rows, "cards/"), encoding="utf-8")

    if _missing:
        print(f"warning: glyphs missing from fonts: {''.join(sorted(_missing))!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
