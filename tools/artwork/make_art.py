"""Generate DeckCompanion Steam library artwork as SVG (text converted to paths)."""
import re
import sys
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

FONTS, ICON, OUT = (Path(a) for a in sys.argv[1:4])
OUT.mkdir(parents=True, exist_ok=True)
BOLD = TTFont(FONTS / "Inter-ExtraBold.ttf")
SEMI = TTFont(FONTS / "Inter-SemiBold.ttf")

WHITE, LIME, CREAM, TEAL = "#f2f5f7", "#b9e07a", "#e9d9a0", "#2f6f68"


def text(font, s, size, tracking=0.0):
    """Return (svg path d, width) for s at font size, baseline at y=0."""
    glyphs, cmap, upem = font.getGlyphSet(), font.getBestCmap(), font["head"].unitsPerEm
    scale = size / upem
    pen, x = SVGPathPen(glyphs), 0.0
    for ch in s:
        name = cmap[ord(ch)]
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, 0)))
        x += glyphs[name].width * scale + tracking * size
    return pen.getCommands(), x - tracking * size


def word(x, y, size, anchor="start"):
    """'Deck' white + 'Companion' lime as paths."""
    d1, w1 = text(BOLD, "Deck", size, -0.02)
    d2, w2 = text(BOLD, "Companion", size, -0.02)
    total = w1 + w2
    left = x - (total / 2 if anchor == "middle" else 0)
    return (f'<path transform="translate({left:.1f} {y:.1f})" d="{d1}" fill="{WHITE}"/>'
            f'<path transform="translate({left + w1:.1f} {y:.1f})" d="{d2}" fill="{LIME}"/>'), total


def line(s, x, y, size, fill, anchor="middle", tracking=0.18):
    d, w = text(SEMI, s, size, tracking)
    left = x - (w / 2 if anchor == "middle" else 0)
    return f'<path transform="translate({left:.1f} {y:.1f})" d="{d}" fill="{fill}"/>'


icon_svg = ICON.read_text(encoding="utf-8")
icon_inner = re.sub(r"^<svg[^>]*>\s*", "", icon_svg).rsplit("</svg>", 1)[0]
icon_inner = re.sub(r"<title[^>]*>.*?</title>\s*", "", icon_inner)
# The handheld artwork without the rounded tile, for use on larger backgrounds.
device_only = re.sub(r'<rect width="512" height="512" rx="112" fill="url\(#bg\)"/>', "", icon_inner)


def icon(x, y, size, tile=True):
    body = icon_inner if tile else device_only
    return f'<g transform="translate({x} {y}) scale({size / 512:.4f})">{body}</g>'


def background(w, h, horizon):
    """Night sky, warm moon glow, and field rows that run to a vanishing point."""
    vx = w * 0.5
    rows = []
    for i in range(-14, 15):
        rows.append(f'<line x1="{vx + i * w * 0.012:.1f}" y1="{horizon}" x2="{vx + i * w * 0.11:.1f}" y2="{h + 4}" />')
    hills = (f'<path d="M0 {horizon + h*0.02} C{w*0.2} {horizon - h*0.10} {w*0.35} {horizon - h*0.05} {w*0.5} {horizon} '
             f'C{w*0.65} {horizon + h*0.04} {w*0.8} {horizon - h*0.12} {w} {horizon - h*0.03} V{h} H0 Z" fill="#203a35"/>')
    stars = "".join(
        f'<circle cx="{(i * 397) % w}" cy="{(i * 211) % int(horizon * 0.8) + 8}" r="{1.2 + (i % 3) * 0.6}" fill="{CREAM}" opacity="{0.25 + (i % 4) * 0.12:.2f}"/>'
        for i in range(1, 46))
    return f"""
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#070d12"/><stop offset="0.7" stop-color="#132330"/><stop offset="1" stop-color="#1b3a3a"/>
    </linearGradient>
    <linearGradient id="field" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2f6f68"/><stop offset="1" stop-color="#0d1a17"/>
    </linearGradient>
    <radialGradient id="moon" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="{CREAM}" stop-opacity="0.45"/><stop offset="1" stop-color="{CREAM}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{w}" height="{h}" fill="url(#sky)"/>
  {stars}
  <circle cx="{w*0.78}" cy="{horizon*0.42}" r="{h*0.32}" fill="url(#moon)"/>
  <circle cx="{w*0.78}" cy="{horizon*0.42}" r="{h*0.075}" fill="{CREAM}"/>
  {hills}
  <rect y="{horizon}" width="{w}" height="{h - horizon}" fill="url(#field)"/>
  <g stroke="{LIME}" stroke-opacity="0.32" stroke-width="{max(2, w/600):.1f}">{''.join(rows)}</g>
  <rect y="{horizon}" width="{w}" height="{h*0.012:.1f}" fill="{LIME}" opacity="0.55"/>"""


def svg(name, w, h, body):
    (OUT / f"{name}.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">{body}</svg>\n', encoding="utf-8")


# Hero 1920x620: art only, Steam draws the logo on top (left side), so keep the left calm.
svg("hero", 1920, 620, background(1920, 620, 420) + f'<g opacity="0.95">{icon(1240, 70, 520, tile=False)}</g>')

# Logo, transparent, wordmark only.
mark, width = word(0, 150, 170)
svg("logo", int(width) + 20, 200, f'<g transform="translate(10 0)">{mark}</g>')

# Wide capsule 920x430: device left, stacked wordmark right, both above the horizon.
d1, _ = text(BOLD, "Deck", 104, -0.02)
d2, _ = text(BOLD, "Companion", 88, -0.02)
svg("wide", 920, 430, background(920, 430, 330) + icon(0, 20, 380, tile=False)
    + f'<path transform="translate(392 178)" d="{d1}" fill="{WHITE}"/>'
    + f'<path transform="translate(392 280)" d="{d2}" fill="{LIME}"/>')

# Portrait capsule 600x900: device, wordmark in the sky, tagline on the field.
m1, w1 = text(BOLD, "Deck", 132, -0.02)
m2, w2 = text(BOLD, "Companion", 106, -0.02)
portrait = (background(600, 900, 730) + icon(40, 20, 520, tile=False)
            + f'<path transform="translate({300 - w1/2:.1f} 560)" d="{m1}" fill="{WHITE}"/>'
            + f'<path transform="translate({300 - w2/2:.1f} 672)" d="{m2}" fill="{LIME}"/>'
            + line("FIXES · MODS · ONE BUTTON", 300, 820, 24, CREAM))
svg("portrait", 600, 900, portrait)

# Icon: the app icon as is.
(OUT / "icon.svg").write_text(icon_svg, encoding="utf-8")
print("wrote", sorted(p.name for p in OUT.glob("*.svg")))
