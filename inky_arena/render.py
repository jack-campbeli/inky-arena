from __future__ import annotations

import io
import random
from dataclasses import dataclass
from datetime import datetime
from textwrap import shorten, wrap

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

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
    if _looks_blank(source_image):
        raise ValueError("source image is visually blank")
    image_height = config.display_height - config.caption_height
    fitted = ImageOps.contain(source_image, (config.display_width, image_height), method=Image.Resampling.LANCZOS)
    paste_x = (config.display_width - fitted.width) // 2
    paste_y = (image_height - fitted.height) // 2
    _draw_pixel_stars(canvas, fitted.size, (paste_x, paste_y), candidate.id, image_height)
    canvas.paste(fitted, (paste_x, paste_y))

    draw = ImageDraw.Draw(canvas)
    footer_y = config.display_height - config.caption_height
    draw.rectangle((0, footer_y, config.display_width, config.display_height), fill=BACKGROUND)
    draw.line((0, footer_y, config.display_width, footer_y), fill=TEXT, width=1)

    fonts = FontSet(config.primary_font_path, config.bold_font_path, config.mono_font_path)
    raw_title = (candidate.title or "").strip()
    title = shorten(raw_title, width=42, placeholder="...") if raw_title else ""
    meta = shorten(candidate.channel_title, width=34, placeholder="...")

    title_font = fonts.bold(18)
    meta_font = fonts.bold(16)
    time_font = fonts.bold(14)
    time_text = datetime.now().astimezone().strftime("%-I:%M %p")
    time_bbox = draw.textbbox((0, 0), time_text, font=time_font)
    top_padding = 9
    line_gap = 8
    title_y = footer_y + top_padding
    title_height = 0
    if title:
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_height = title_bbox[3] - title_bbox[1]
    meta_y = title_y + title_height + (line_gap if title else 0)

    time_x = config.display_width - 16
    time_y = footer_y + max(1, (config.caption_height - (time_bbox[3] - time_bbox[1])) // 2 - 1)
    draw.text((time_x, time_y), time_text, fill=TEXT, font=time_font, anchor="ra")
    time_left = time_x - (time_bbox[2] - time_bbox[0])

    if title:
        draw.text((16, title_y), title, fill=TEXT, font=title_font)
    draw.text((16, meta_y), meta, fill=MUTED, font=meta_font)

    if time_left < 250:
        draw.text((time_x, title_y), time_text, fill=TEXT, font=time_font, anchor="ra")

    return canvas


def render_status(config: AppConfig, title: str, detail: str) -> Image.Image:
    canvas = Image.new("RGB", (config.display_width, config.display_height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    fonts = FontSet(config.primary_font_path, config.bold_font_path, config.mono_font_path)

    left = 28
    top = 36
    right = config.display_width - 28
    bottom = config.display_height - 36
    draw.rounded_rectangle((left, top, right, bottom), radius=22, outline=TEXT, width=3)

    title_text = shorten(title, width=24, placeholder="...")
    draw.text((left + 20, top + 26), title_text, fill=TEXT, font=fonts.bold(26))

    detail_font = fonts.regular(16)
    detail_lines = _wrap_text(draw, detail, detail_font, max_width=(right - left - 40))
    detail_y = top + 88
    line_height = 22
    max_lines = max(6, (bottom - detail_y - 54) // line_height)
    for line in detail_lines[:max_lines]:
        draw.text((left + 20, detail_y), line, fill=MUTED, font=detail_font)
        detail_y += line_height

    draw.text((left + 20, bottom - 34), "inky-arena", fill=ACCENT, font=fonts.regular(16))
    return canvas


def _looks_blank(image: Image.Image) -> bool:
    sample = image.convert("L")
    sample.thumbnail((64, 64))
    stat = ImageStat.Stat(sample)
    mean = float(stat.mean[0])
    stddev = float(stat.stddev[0])
    histogram = sample.histogram()
    total = float(sum(histogram) or 1)
    near_white = sum(histogram[245:256]) / total
    near_black = sum(histogram[:10]) / total

    if stddev < 8 and (near_white > 0.9 or near_black > 0.9):
        return True
    if mean > 245 and near_white > 0.85:
        return True
    if mean < 10 and near_black > 0.85:
        return True
    return False


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    paragraphs = text.splitlines() or [text]
    lines: list[str] = []

    for paragraph in paragraphs:
        if not paragraph.strip():
            lines.append("")
            continue

        words = paragraph.split()
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)

    return lines


def _draw_pixel_stars(
    canvas: Image.Image,
    image_size: tuple[int, int],
    image_origin: tuple[int, int],
    seed_text: str,
    image_height: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    image_x, image_y = image_origin
    image_w, image_h = image_size
    image_right = image_x + image_w
    image_bottom = image_y + image_h
    rng = random.Random(f"stars:{seed_text}:{image_size[0]}x{image_size[1]}@{image_x},{image_y}")

    star_count = max(18, (canvas.width * image_height) // 24000)
    attempts = 0
    placed = 0

    while placed < star_count and attempts < star_count * 12:
        attempts += 1
        x = rng.randint(8, canvas.width - 9)
        y = rng.randint(8, image_height - 9)
        if image_x <= x <= image_right and image_y <= y <= image_bottom:
            continue
        _draw_star(draw, x, y, TEXT, rng.randint(1, 2))
        placed += 1


def _draw_star(draw: ImageDraw.ImageDraw, x: int, y: int, color: str, scale: int) -> None:
    points = {(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)}
    if scale > 1:
        points.update({(-2, 0), (2, 0), (0, -2), (0, 2)})
        points.update({(-1, -1), (1, -1), (-1, 1), (1, 1)})

    for dx, dy in points:
        draw.point((x + dx, y + dy), fill=color)
