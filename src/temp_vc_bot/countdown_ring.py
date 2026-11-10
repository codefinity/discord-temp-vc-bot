"""Draw the circular countdown shown in the timer embed.

Discord embeds can't draw shapes, so the ring is a PNG made here and attached to
the timer message. This file only draws. What to draw, and when, is decided in
services/timer.py.
"""

from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw, ImageFont

FILENAME = "countdown.png"  # The embed points at the attachment by this name.
# Width and height of the finished image, in pixels. Discord shrinks a bigger
# picture to fit the embed (about 300 pixels tall on desktop) but shows one this
# small at its real size, so this sets how big the ring looks. 150 is half of
# the roughly 300 that the earlier 512 was shown at.
SIZE = 150
# Pillow doesn't smooth the edges of circles, so the dial is drawn this many times
# larger and then shrunk, which does.
SUPERSAMPLE = 3

_RING_WIDTH = 0.11  # How thick the ring is, as a share of the image's width.
_MARGIN = 0.06  # The dark border between the ring and the edge of the dial.

# Discord's own dark greys. The dial brings its own dark background, so its text
# stays readable whether people use Discord's light or dark theme.
_DIAL = (30, 31, 34)
_TRACK = (56, 58, 64)
_HEADLINE = (242, 243, 245)
_CAPTION = (181, 186, 193)

RGB = tuple[int, int, int]


def render_ring(fraction_left: float, colour: RGB, headline: str, caption: str) -> bytes:
    """Draw the dial and return it as PNG bytes.

    `fraction_left` is how much of the ring is coloured, from 0 (empty) to 1 (full).
    As it falls, the coloured part shrinks clockwise from 12 o'clock, like a clock
    hand sweeping round. `headline` is the big text in the middle, and `caption`
    the small line under it.

    This takes tens of milliseconds, so async code should run it in a thread.
    """
    fraction_left = min(max(fraction_left, 0.0), 1.0)
    canvas = SIZE * SUPERSAMPLE
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, canvas - 1, canvas - 1), fill=_DIAL)

    width = round(canvas * _RING_WIDTH)
    inset = round(canvas * _MARGIN)
    box = (inset, inset, canvas - 1 - inset, canvas - 1 - inset)
    draw.ellipse(box, outline=_TRACK, width=width)
    if fraction_left > 0:
        # Pillow measures angles clockwise from 3 o'clock, so 270 is 12 o'clock.
        start = 270 - 360 * fraction_left
        draw.arc(box, start, 270, fill=colour, width=width)
        for angle in (start, 270):
            _round_cap(draw, canvas, inset, width, angle, colour)

    _draw_labels(draw, canvas, inset + width, headline, caption)

    buffer = io.BytesIO()
    image.resize((SIZE, SIZE), Image.Resampling.LANCZOS).save(buffer, format="PNG")
    return buffer.getvalue()


def _round_cap(draw: ImageDraw.ImageDraw, canvas: int, inset: int, width: int, angle: float, colour: RGB) -> None:
    """Round off one end of the arc, which Pillow would otherwise cut flat."""
    centre = canvas / 2
    radius = centre - inset - width / 2  # The middle of the ring's thickness.
    x = centre + radius * math.cos(math.radians(angle))
    y = centre + radius * math.sin(math.radians(angle))
    draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=colour)


def _draw_labels(draw: ImageDraw.ImageDraw, canvas: int, ring_inner_edge: int, headline: str, caption: str) -> None:
    """Write the headline with the caption under it, centred together in the ring."""
    centre = canvas / 2
    room = (centre - ring_inner_edge) * 2 * 0.8  # The widest the text may be.
    headline_font = _fitting_font(headline, round(canvas * 0.30), room)
    caption_font = _fitting_font(caption, round(canvas * 0.075), room)
    gap = round(canvas * 0.02)

    # With the "ls" anchor, box[1] is how far the text rises above its baseline.
    headline_box = draw.textbbox((0, 0), headline, font=headline_font, anchor="ls")
    caption_box = draw.textbbox((0, 0), caption, font=caption_font, anchor="ls")
    headline_height = headline_box[3] - headline_box[1]
    block_height = headline_height + gap + (caption_box[3] - caption_box[1])
    top = centre - block_height / 2

    # A thin outline in the text's own colour makes the built-in font look bold.
    bold = round(canvas * 0.006)
    draw.text(
        (centre, top - headline_box[1]),
        headline,
        font=headline_font,
        fill=_HEADLINE,
        anchor="ms",
        stroke_width=bold,
        stroke_fill=_HEADLINE,
    )
    draw.text((centre, top + headline_height + gap - caption_box[1]), caption, font=caption_font, fill=_CAPTION, anchor="ms")


def _fitting_font(text: str, size: int, max_width: float) -> ImageFont.FreeTypeFont:
    """Pillow's built-in font at `size`, made smaller if `text` would be too wide.

    The built-in font needs no font file on the server. Loading it is quick, so
    each drawing loads its own rather than sharing one between threads.
    """
    font = ImageFont.load_default(size)
    width = font.getlength(text)
    if width > max_width:
        font = ImageFont.load_default(max(1, int(size * max_width / width)))
    return font  # type: ignore[return-value]
