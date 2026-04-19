from __future__ import annotations

import io
from dataclasses import dataclass
from textwrap import shorten
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont, ImageOps

from inky_arena.config import AppConfig
from inky_arena.models import DisplayCandidate


BACKGROUND = "#f3efe4"
TEXT = "#151515"
MUTED = "#5a5a5a"
ACCENT = "#c86c20"


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


@dataclass(slots=True)
class FontSet:
    regular_path: str
    bold_path: str
    mono_path: str

    def regular(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        return _load_font(self.regular_path, size)

    def bold(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        return _load_font(self.bold_path, size)

    def mono(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        return _load_font(self.mono_path, size)


def render_candidate(config: AppConfig, candidate: DisplayCandidate, image_bytes: bytes) -> Image.Image:
    canvas = Image.new("RGB", (config.display_width, config.display_height), BACKGROUND)
    source_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image_height = config.display_height - config.caption_height
    fitted = ImageOps.fit(source_image, (config.display_width, image_height), method=Image.Resampling.LANCZOS)
    canvas.paste(fitted, (0, 0))

    draw = ImageDraw.Draw(canvas)
    footer_y = image_height
    draw.rectangle((0, footer_y, config.display_width, config.display_height), fill=BACKGROUND)
    draw.line((0, footer_y, config.display_width, footer_y), fill=TEXT, width=2)

    fonts = FontSet(config.primary_font_path, config.bold_font_path, config.mono_font_path)
    title = shorten(candidate.title or "Untitled", width=64, placeholder="...")
    meta = f"{candidate.channel_title}  •  {candidate.block_type}"
    source = _source_label(candidate)

    draw.text((20, footer_y + 10), title, fill=TEXT, font=fonts.bold(30))
    draw.text((20, footer_y + 46), meta, fill=MUTED, font=fonts.regular(20))
    if source:
        draw.text((config.display_width - 20, footer_y + 46), source, fill=ACCENT, font=fonts.mono(18), anchor="ra")

    return canvas


def render_status(config: AppConfig, title: str, detail: str) -> Image.Image:
    canvas = Image.new("RGB", (config.display_width, config.display_height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    fonts = FontSet(config.primary_font_path, config.bold_font_path, config.mono_font_path)

    draw.rounded_rectangle((40, 48, config.display_width - 40, config.display_height - 48), radius=28, outline=TEXT, width=3)
    draw.text((64, 92), title, fill=TEXT, font=fonts.bold(42))
    draw.text((64, 172), detail, fill=MUTED, font=fonts.regular(24), spacing=8)
    draw.text((64, config.display_height - 88), "inky-arena", fill=ACCENT, font=fonts.mono(22))
    return canvas


def _source_label(candidate: DisplayCandidate) -> str:
    if candidate.source_title:
        return shorten(candidate.source_title, width=28, placeholder="...")
    if candidate.source_url:
        parsed = urlparse(candidate.source_url)
        return parsed.netloc or candidate.source_url
    return ""

