from __future__ import annotations

import io
import random
from dataclasses import dataclass
from datetime import datetime
from textwrap import shorten

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from inky_arena.build_info import get_version_label
from inky_arena.config import AppConfig
from inky_arena.models import DisplayCandidate
from inky_arena.vocabulary import VocabularyEntry


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


def render_candidate(
    config: AppConfig,
    candidate: DisplayCandidate,
    image_bytes: bytes,
    degraded: bool = False,
    vocabulary: VocabularyEntry | None = None,
) -> Image.Image:
    source_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if _looks_blank(source_image):
        raise ValueError("source image is visually blank")

    if not config.uses_footer:
        return _render_art_candidate(config, candidate, source_image, degraded=degraded, vocabulary=vocabulary)
    return _render_footer_candidate(config, candidate, source_image, degraded=degraded, vocabulary=vocabulary)


def _render_footer_candidate(
    config: AppConfig,
    candidate: DisplayCandidate,
    source_image: Image.Image,
    degraded: bool = False,
    vocabulary: VocabularyEntry | None = None,
) -> Image.Image:
    canvas = Image.new("RGB", (config.display_width, config.display_height), BACKGROUND)
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
    version_font = fonts.mono(11)
    time_text = datetime.now().astimezone().strftime("%-I:%M %p")
    version_text = get_version_label()
    time_bbox = draw.textbbox((0, 0), time_text, font=time_font)
    version_bbox = draw.textbbox((0, 0), version_text, font=version_font)
    top_padding = 9
    line_gap = 8
    title_y = footer_y + top_padding
    title_height = 0
    if title:
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_height = title_bbox[3] - title_bbox[1]
    meta_y = title_y + title_height + (line_gap if title else 0)

    time_x = config.display_width - 16
    version_height = version_bbox[3] - version_bbox[1]
    time_height = time_bbox[3] - time_bbox[1]
    version_top = config.display_height - 8 - version_height
    time_top = version_top - 8 - time_height
    draw.text((time_x, time_top - time_bbox[1]), time_text, fill=MUTED, font=time_font, anchor="ra")
    draw.text((time_x, version_top - version_bbox[1]), version_text, fill=MUTED, font=version_font, anchor="ra")
    time_left = time_x - (time_bbox[2] - time_bbox[0])

    if title:
        draw.text((16, title_y), title, fill=TEXT, font=title_font)
    meta_max_width = config.display_width - 32
    meta_text = _fit_text_to_width(draw, meta, meta_font, max_width=meta_max_width)
    if meta_text:
        draw.text((16, meta_y), meta_text, fill=MUTED, font=meta_font)

    if time_left < 250:
        draw.text((time_x, title_y), time_text, fill=TEXT, font=time_font, anchor="ra")

    if vocabulary is not None:
        _draw_vocabulary_overlay(canvas, fonts, vocabulary, max_bottom=image_height - 12)

    if degraded:
        _draw_degraded_dot(draw, canvas.size, corner="bottom-right")

    return canvas


def _render_art_candidate(
    config: AppConfig,
    candidate: DisplayCandidate,
    source_image: Image.Image,
    degraded: bool = False,
    vocabulary: VocabularyEntry | None = None,
) -> Image.Image:
    del candidate
    size = (config.display_width, config.display_height)
    canvas = _compose_art_image(source_image, size)
    draw = ImageDraw.Draw(canvas)
    fonts = FontSet(config.primary_font_path, config.bold_font_path, config.mono_font_path)
    if vocabulary is not None:
        _draw_vocabulary_overlay(canvas, fonts, vocabulary)
        draw = ImageDraw.Draw(canvas)
    _draw_time_overlay(draw, canvas, fonts)
    if degraded:
        _draw_degraded_dot(draw, canvas.size, corner="top-right")
    return canvas


def _compose_art_image(source_image: Image.Image, size: tuple[int, int]) -> Image.Image:
    source_ratio = source_image.width / max(1, source_image.height)
    target_ratio = size[0] / max(1, size[1])
    if source_ratio >= target_ratio * 0.8:
        return ImageOps.fit(source_image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    background = ImageOps.fit(source_image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    background = background.filter(ImageFilter.GaussianBlur(radius=18))
    background = ImageOps.autocontrast(background)

    foreground = ImageOps.contain(source_image, size, method=Image.Resampling.LANCZOS)
    paste_x = (size[0] - foreground.width) // 2
    paste_y = (size[1] - foreground.height) // 2
    background.paste(foreground, (paste_x, paste_y))
    return background


def _draw_time_overlay(draw: ImageDraw.ImageDraw, canvas: Image.Image, fonts: FontSet) -> None:
    width, height = canvas.size
    time_font = fonts.bold(14)
    version_font = fonts.mono(11)
    time_text = datetime.now().astimezone().strftime("%-I:%M %p")
    version_text = get_version_label()
    time_bbox = draw.textbbox((0, 0), time_text, font=time_font)
    version_bbox = draw.textbbox((0, 0), version_text, font=version_font)
    time_width = time_bbox[2] - time_bbox[0]
    time_height = time_bbox[3] - time_bbox[1]
    version_width = version_bbox[2] - version_bbox[0]
    version_height = version_bbox[3] - version_bbox[1]
    left = 12
    right = width - 12
    bottom = height - 10
    time_top = bottom - time_height
    version_top = bottom - version_height
    time_box = (right - time_width - 4, time_top - 4, width, height)
    version_box = (0, version_top - 4, left + version_width + 4, height)
    time_color = _subdued_overlay_color(canvas, time_box)
    version_color = _subdued_overlay_color(canvas, version_box)

    draw.text(
        (right - time_width - time_bbox[0], time_top - time_bbox[1]),
        time_text,
        fill=time_color,
        font=time_font,
    )
    draw.text(
        (left - version_bbox[0], version_top - version_bbox[1]),
        version_text,
        fill=version_color,
        font=version_font,
    )


def _subdued_overlay_color(canvas: Image.Image, box: tuple[int, int, int, int]) -> str:
    sample = canvas.crop(box).convert("L")
    luminance = float(ImageStat.Stat(sample).mean[0])
    return "#ded8cc" if luminance < 125 else "#4a4a4a"


def _draw_vocabulary_overlay(
    canvas: Image.Image,
    fonts: FontSet,
    entry: VocabularyEntry,
    max_bottom: int | None = None,
) -> None:
    margin = 18
    inner_x = margin + 18
    max_card_width = min(590, canvas.width - margin * 2)
    label_font = fonts.bold(12)
    word_font = fonts.bold(30)
    definition_font = fonts.regular(17)
    measurement_draw = ImageDraw.Draw(canvas)
    definition_lines = _wrap_text(
        measurement_draw,
        entry.definition,
        definition_font,
        max_width=max_card_width - 36,
    )[:2]
    label = f"WORD · {entry.part_of_speech.upper()}"
    text_widths = [
        measurement_draw.textbbox((0, 0), label, font=label_font)[2],
        measurement_draw.textbbox((0, 0), entry.word, font=word_font)[2],
        *(measurement_draw.textbbox((0, 0), line, font=definition_font)[2] for line in definition_lines),
    ]
    card_width = min(max_card_width, max(280, max(text_widths) + 36))
    card_height = 102 + max(0, len(definition_lines) - 1) * 22
    bottom_limit = max_bottom if max_bottom is not None else canvas.height - margin
    top = min(margin, max(0, bottom_limit - card_height))
    card = (margin, top, margin + card_width, top + card_height)

    sample = canvas.crop(card).convert("L")
    luminance = float(ImageStat.Stat(sample).mean[0])
    if luminance < 130:
        card_fill = (243, 239, 228, 214)
        primary = "#171717"
        secondary = "#4a4a4a"
    else:
        card_fill = (20, 20, 20, 190)
        primary = "#f3efe4"
        secondary = "#d5cfc4"

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(card, radius=14, fill=card_fill)
    composed = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    canvas.paste(composed)

    draw = ImageDraw.Draw(canvas)
    draw.text((inner_x, top + 12), label, fill=secondary, font=label_font)
    draw.text((inner_x, top + 30), entry.word, fill=primary, font=word_font)
    definition_y = top + 66
    for line in definition_lines:
        draw.text((inner_x, definition_y), line, fill=primary, font=definition_font)
        definition_y += 22


def _draw_degraded_dot(draw: ImageDraw.ImageDraw, size: tuple[int, int], corner: str) -> None:
    cx = size[0] - 10
    cy = 10 if corner == "top-right" else size[1] - 10
    draw.ellipse([(cx - 5, cy - 5), (cx + 5, cy + 5)], fill=ACCENT)


def render_status(config: AppConfig, title: str, detail: str) -> Image.Image:
    canvas = Image.new("RGB", (config.display_width, config.display_height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    fonts = FontSet(config.primary_font_path, config.bold_font_path, config.mono_font_path)

    width = config.display_width
    height = config.display_height
    outer_margin = 18
    card_x = 34
    card_top = 42
    card_bottom = height - 34
    card = (card_x, card_top, width - card_x, card_bottom)
    header = (card[0] + 18, card[1] + 18, card[2] - 18, card[1] + 74)
    footer_y = card[3] - 56
    detail_top = min(card[1] + 184, max(card[1] + 160, height - 266))
    detail_bottom = max(detail_top + 126, footer_y - 18)
    detail_box = (card[0] + 24, detail_top, card[2] - 24, detail_bottom)

    draw.rounded_rectangle((outer_margin, outer_margin, width - outer_margin, height - outer_margin), radius=28, outline=TEXT, width=2)
    draw.rounded_rectangle(card, radius=28, fill=BACKGROUND, outline=TEXT, width=3)
    draw.rounded_rectangle(header, radius=16, fill=TEXT)
    draw.rounded_rectangle(detail_box, radius=22, outline=TEXT, width=2)
    draw.rounded_rectangle((detail_box[0] + 14, detail_box[1] + 14, detail_box[0] + 116, detail_box[1] + 52), radius=12, fill=ACCENT)
    draw.line((card[0] + 20, footer_y, card[2] - 20, footer_y), fill=TEXT, width=1)

    chip_font = fonts.bold(15)
    title_font = fonts.bold(30)
    subtitle_font = fonts.regular(17)
    detail_font = fonts.regular(18)
    label_font = fonts.bold(15)
    footer_font = fonts.bold(15)
    footer_mono = fonts.mono(14)

    timestamp = datetime.now().astimezone().strftime("%-I:%M %p")
    title_text = shorten(title.strip() or "Display status", width=26, placeholder="...")
    subtitle = "The display is still running. This message only appears when a refresh needs attention."

    draw.text((card[0] + 42, card[1] + 34), "STATUS", fill=BACKGROUND, font=chip_font)
    draw.text((card[2] - 34, card[1] + 34), timestamp, fill=BACKGROUND, font=chip_font, anchor="ra")
    draw.text((card[0] + 34, card[1] + 100), title_text, fill=TEXT, font=title_font)

    subtitle_lines = _wrap_text(draw, subtitle, subtitle_font, max_width=card[2] - card[0] - 68)
    subtitle_y = card[1] + 148
    for line in subtitle_lines[:2]:
        draw.text((card[0] + 34, subtitle_y), line, fill=MUTED, font=subtitle_font)
        subtitle_y += 24

    draw.text((detail_box[0] + 30, detail_box[1] + 24), "DETAILS", fill=BACKGROUND, font=label_font)

    detail_lines = _wrap_text(draw, detail.strip(), detail_font, max_width=detail_box[2] - detail_box[0] - 40)
    detail_y = detail_box[1] + 66
    line_height = 24
    max_lines = max(1, (detail_box[3] - detail_y - 14) // line_height)
    for line in detail_lines[:max_lines]:
        draw.text((detail_box[0] + 22, detail_y), line, fill=TEXT if line else MUTED, font=detail_font)
        detail_y += line_height

    draw.text((card[0] + 34, footer_y + 16), "inky-arena", fill=ACCENT, font=footer_font)
    draw.text((card[0] + 34, footer_y + 38), "will retry automatically on the next refresh", fill=MUTED, font=subtitle_font)
    draw.text((card[2] - 34, footer_y + 27), f"e-ink status · {get_version_label()}", fill=MUTED, font=footer_mono, anchor="ra")
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


def _fit_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> str:
    value = (text or "").strip()
    if not value or max_width <= 0:
        return ""
    if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
        return value

    placeholder = "..."
    if draw.textbbox((0, 0), placeholder, font=font)[2] > max_width:
        return ""

    low = 0
    high = len(value)
    best = placeholder

    while low <= high:
        mid = (low + high) // 2
        candidate = f"{value[:mid].rstrip()}{placeholder}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1

    return best


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
