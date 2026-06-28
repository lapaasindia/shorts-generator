"""Reusable reel template registry and ffmpeg rendering helpers."""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path
from typing import Dict, Iterable, List, Optional


REEL_TEMPLATES: List[Dict[str, str]] = [
    {
        "id": "yellow-pop",
        "name": "Yellow Pop",
        "signal": "Broad viral",
        "accent": "#ffd33d",
        "surface": "#171714",
        "font": "#ffffff",
        "filter": "eq=contrast=1.12:saturation=1.22",
    },
    {
        "id": "red-alert",
        "name": "Red Alert",
        "signal": "Urgency",
        "accent": "#ef3d32",
        "surface": "#180c0b",
        "font": "#ffffff",
        "filter": "eq=contrast=1.2:saturation=1.12,drawbox=x=0:y=0:w=iw:h=18:color=0xef3d32:t=fill",
    },
    {
        "id": "clean-authority",
        "name": "Clean Authority",
        "signal": "Expert",
        "accent": "#111111",
        "surface": "#f5f2e9",
        "font": "#111111",
        "filter": "eq=contrast=1.04:saturation=0.94,drawbox=x=0:y=0:w=iw:h=14:color=white:t=fill",
    },
    {
        "id": "money-green",
        "name": "Money Green",
        "signal": "Business",
        "accent": "#30c875",
        "surface": "#07180f",
        "font": "#ffffff",
        "filter": "colorbalance=gs=.05:bs=-.025,eq=contrast=1.1:saturation=1.08",
    },
    {
        "id": "creator-blue",
        "name": "Creator Blue",
        "signal": "Creator economy",
        "accent": "#2f7df4",
        "surface": "#071427",
        "font": "#ffffff",
        "filter": "colorbalance=bs=.06:rs=-.025,eq=contrast=1.08:saturation=1.12",
    },
    {
        "id": "mono-punch",
        "name": "Mono Punch",
        "signal": "Serious quote",
        "accent": "#ffffff",
        "surface": "#080808",
        "font": "#ffffff",
        "filter": "hue=s=0,eq=contrast=1.34:brightness=-.025",
    },
    {
        "id": "orange-flash",
        "name": "Orange Flash",
        "signal": "High energy",
        "accent": "#ff7a24",
        "surface": "#1d0d05",
        "font": "#ffffff",
        "filter": "colorbalance=rs=.08:gs=.025:bs=-.06,eq=contrast=1.12:saturation=1.18",
    },
    {
        "id": "tech-lime",
        "name": "Tech Lime",
        "signal": "AI and tools",
        "accent": "#b8f23a",
        "surface": "#101607",
        "font": "#101607",
        "filter": "eq=contrast=1.14:saturation=1.18,drawgrid=width=iw:height=10:thickness=1:color=0x0b1208@0.10",
    },
    {
        "id": "white-hot",
        "name": "White Hot",
        "signal": "Bold clean",
        "accent": "#ffffff",
        "surface": "#cf2f27",
        "font": "#111111",
        "filter": "eq=contrast=1.22:saturation=1.05,drawbox=x=0:y=ih-14:w=iw:h=14:color=white:t=fill",
    },
    {
        "id": "fanpage-gold",
        "name": "Fanpage Gold",
        "signal": "Fan-page default",
        "accent": "#e5b83b",
        "surface": "#1b1608",
        "font": "#ffffff",
        "filter": "colorbalance=rs=.06:gs=.035:bs=-.045,eq=contrast=1.1:saturation=1.08",
    },
    {
        "id": "cinema-black",
        "name": "Cinema Black",
        "signal": "Story peak",
        "accent": "#e1c169",
        "surface": "#050505",
        "font": "#ffffff",
        "filter": "eq=contrast=1.13:saturation=.9,drawbox=x=0:y=0:w=iw:h=86:color=black:t=fill,drawbox=x=0:y=ih-86:w=iw:h=86:color=black:t=fill",
    },
    {
        "id": "teal-editorial",
        "name": "Teal Editorial",
        "signal": "Insight",
        "accent": "#1fc8bc",
        "surface": "#071d1c",
        "font": "#ffffff",
        "filter": "colorbalance=gs=.035:bs=.04:rs=-.035,eq=contrast=1.08:saturation=1.02,drawbox=x=0:y=0:w=14:h=ih:color=0x1fc8bc:t=fill",
    },
    {
        "id": "coral-story",
        "name": "Coral Story",
        "signal": "Human story",
        "accent": "#ff6f61",
        "surface": "#27100e",
        "font": "#ffffff",
        "filter": "colorbalance=rs=.055:bs=-.025,eq=contrast=1.04:saturation=1.04,drawbox=x=iw-14:y=0:w=14:h=ih:color=0xff6f61:t=fill",
    },
    {
        "id": "cold-chrome",
        "name": "Cold Chrome",
        "signal": "Modern proof",
        "accent": "#a9ddff",
        "surface": "#0c151b",
        "font": "#ffffff",
        "filter": "colorbalance=rs=-.055:gs=.015:bs=.075,eq=contrast=1.14:saturation=.86",
    },
    {
        "id": "warm-film",
        "name": "Warm Film",
        "signal": "Personal",
        "accent": "#f1a85b",
        "surface": "#24160c",
        "font": "#ffffff",
        "filter": "colorbalance=rs=.075:gs=.025:bs=-.065,eq=contrast=1.03:saturation=.96,noise=alls=4:allf=t+u",
    },
    {
        "id": "violet-night",
        "name": "Violet Night",
        "signal": "Culture",
        "accent": "#c28cff",
        "surface": "#160c22",
        "font": "#ffffff",
        "filter": "colorbalance=rs=.035:bs=.07:gs=-.025,eq=contrast=1.1:saturation=1.08",
    },
    {
        "id": "cyan-signal",
        "name": "Cyan Signal",
        "signal": "News flash",
        "accent": "#00e4e8",
        "surface": "#04191b",
        "font": "#061618",
        "filter": "eq=contrast=1.16:saturation=1.1,drawbox=x=0:y=0:w=iw:h=10:color=0x00e4e8:t=fill,drawbox=x=0:y=ih-10:w=iw:h=10:color=0x00e4e8:t=fill",
    },
    {
        "id": "paper-cut",
        "name": "Paper Cut",
        "signal": "Educational",
        "accent": "#efe8d3",
        "surface": "#24231f",
        "font": "#171714",
        "filter": "eq=contrast=1.02:saturation=.82,drawbox=x=0:y=0:w=iw:h=22:color=0xefe8d3:t=fill,drawbox=x=0:y=ih-22:w=iw:h=22:color=0xefe8d3:t=fill",
    },
    {
        "id": "soft-glow",
        "name": "Soft Glow",
        "signal": "Reflective",
        "accent": "#ff9db8",
        "surface": "#25131a",
        "font": "#ffffff",
        "filter": "gblur=sigma=.32,unsharp=5:5:.6:3:3:0,eq=brightness=.025:saturation=.94",
    },
    {
        "id": "spotlight",
        "name": "Spotlight",
        "signal": "Confession",
        "accent": "#f6d66b",
        "surface": "#0b0b0a",
        "font": "#ffffff",
        "filter": "eq=contrast=1.12:saturation=.96,vignette=PI/5",
    },
    # ── New beautiful templates ──────────────────────────────────────────────
    {
        "id": "neon-cyber",
        "name": "Neon Cyber",
        "signal": "Futuristic",
        "accent": "#ff00ff",
        "surface": "#04000f",
        "font": "#ffffff",
        "filter": "colorbalance=rs=.08:bs=.12:gs=-.03,eq=contrast=1.22:saturation=1.3,drawbox=x=0:y=0:w=iw:h=3:color=0xff00ff:t=fill,drawbox=x=0:y=ih-3:w=iw:h=3:color=0x00ffff:t=fill",
    },
    {
        "id": "golden-hour",
        "name": "Golden Hour",
        "signal": "Lifestyle",
        "accent": "#ffb347",
        "surface": "#1a0a00",
        "font": "#fff8e7",
        "filter": "colorbalance=rs=.12:gs=.04:bs=-.09,eq=contrast=1.06:saturation=1.14:brightness=.02,vignette=PI/4",
    },
    {
        "id": "midnight-blue",
        "name": "Midnight Blue",
        "signal": "Premium",
        "accent": "#4fc3f7",
        "surface": "#000d1a",
        "font": "#e8f4ff",
        "filter": "colorbalance=bs=.1:rs=-.06:gs=.02,eq=contrast=1.18:saturation=.92,drawbox=x=0:y=0:w=iw:h=4:color=0x4fc3f7:t=fill",
    },
    {
        "id": "rose-gold",
        "name": "Rose Gold",
        "signal": "Beauty",
        "accent": "#e8a598",
        "surface": "#1a0a08",
        "font": "#fff0ee",
        "filter": "colorbalance=rs=.09:gs=-.02:bs=-.04,eq=contrast=1.04:saturation=1.06:brightness=.015,gblur=sigma=.18",
    },
    {
        "id": "matrix-green",
        "name": "Matrix Green",
        "signal": "Tech thriller",
        "accent": "#00ff41",
        "surface": "#000500",
        "font": "#00ff41",
        "filter": "colorbalance=gs=.15:rs=-.1:bs=-.12,eq=contrast=1.28:saturation=1.1,hue=h=5",
    },
    {
        "id": "blood-moon",
        "name": "Blood Moon",
        "signal": "Dramatic",
        "accent": "#ff2400",
        "surface": "#0d0000",
        "font": "#ffdddd",
        "filter": "colorbalance=rs=.14:gs=-.08:bs=-.1,eq=contrast=1.32:saturation=1.2,vignette=PI/3",
    },
    {
        "id": "arctic-white",
        "name": "Arctic White",
        "signal": "Minimal luxury",
        "accent": "#ffffff",
        "surface": "#f0f4f8",
        "font": "#0a0a0a",
        "filter": "eq=contrast=1.02:saturation=.78:brightness=.04,drawbox=x=0:y=0:w=iw:h=3:color=white:t=fill",
    },
    {
        "id": "forest-dark",
        "name": "Forest Dark",
        "signal": "Nature / Calm",
        "accent": "#4caf50",
        "surface": "#030d05",
        "font": "#d8f0da",
        "filter": "colorbalance=gs=.08:rs=-.05:bs=-.05,eq=contrast=1.12:saturation=1.04,vignette=PI/4",
    },
    {
        "id": "electric-purple",
        "name": "Electric Purple",
        "signal": "Music / Drop",
        "accent": "#bf5fff",
        "surface": "#0c0014",
        "font": "#f5e8ff",
        "filter": "colorbalance=rs=.06:bs=.1:gs=-.04,eq=contrast=1.2:saturation=1.25,drawbox=x=0:y=ih-6:w=iw:h=6:color=0xbf5fff:t=fill",
    },
    {
        "id": "amber-noir",
        "name": "Amber Noir",
        "signal": "Mystery",
        "accent": "#c87941",
        "surface": "#0a0600",
        "font": "#f5dcc0",
        "filter": "colorbalance=rs=.07:gs=.02:bs=-.07,eq=contrast=1.18:saturation=.88,noise=alls=6:allf=t+u",
    },
    {
        "id": "ice-storm",
        "name": "Ice Storm",
        "signal": "Shock reveal",
        "accent": "#a8e6f0",
        "surface": "#010a12",
        "font": "#e8f8ff",
        "filter": "colorbalance=bs=.1:rs=-.08:gs=.02,eq=contrast=1.24:saturation=.8:brightness=.03",
    },
    {
        "id": "sunset-pop",
        "name": "Sunset Pop",
        "signal": "Feel-good",
        "accent": "#ff6b35",
        "surface": "#1a0800",
        "font": "#fffaf0",
        "filter": "colorbalance=rs=.1:gs=.025:bs=-.07,eq=contrast=1.08:saturation=1.22,drawbox=x=0:y=0:w=iw:h=5:color=0xff6b35:t=fill,drawbox=x=0:y=ih-5:w=iw:h=5:color=0xffd700:t=fill",
    },
]


# ── 50-template pack (AI_Shorts_50_Template_Pack.json) ────────────────────────
# The pack describes layout/composition templates (category, master_layout,
# layers, animations) but carries no colors or ffmpeg looks. We map each entry
# onto the render schema below — a distinct palette + grade per category, with
# a per-template accent so they stay visually varied — while preserving the
# original layout metadata for the UI and future compositing work.

_PACK_PATH = Path(__file__).resolve().parent / "template_pack.json"

# surface / font / ffmpeg grade + a rotating accent list per category.
_CATEGORY_STYLES: Dict[str, Dict] = {
    "talking_head": {
        "label": "Talking head",
        "surface": "#14181f",
        "font": "#ffffff",
        "filter": "eq=contrast=1.1:saturation=1.18",
        "accents": ["#ffd33d", "#4fa3ff", "#ff7a59", "#43d6a0", "#b98bff", "#ff5e8a", "#5ad1e0"],
    },
    "business": {
        "label": "Business",
        "surface": "#0a1410",
        "font": "#ffffff",
        "filter": "colorbalance=gs=.04:bs=-.02,eq=contrast=1.08:saturation=1.05",
        "accents": ["#30c875", "#2f7df4", "#d4af37", "#1abc9c", "#3a7bd5"],
    },
    "ai_tech": {
        "label": "AI / Tech",
        "surface": "#04000f",
        "font": "#ffffff",
        "filter": "colorbalance=bs=.08:rs=.02,eq=contrast=1.2:saturation=1.28",
        "accents": ["#00ffd5", "#ff00ff", "#7b5bff", "#00b3ff", "#39ff14"],
    },
    "podcast": {
        "label": "Podcast",
        "surface": "#160a12",
        "font": "#ffffff",
        "filter": "colorbalance=rs=.06:gs=-.01:bs=-.02,eq=contrast=1.12:saturation=1.1",
        "accents": ["#ff6b5e", "#e2bd52", "#ff9f1c", "#c77dff", "#ff5e8a"],
    },
    "cinematic": {
        "label": "Cinematic",
        "surface": "#060809",
        "font": "#ffffff",
        "filter": "colorbalance=rs=.06:bs=.04:gs=-.03,eq=contrast=1.28:saturation=1.08,vignette=PI/4",
        "accents": ["#55d5c9", "#ff7a3d", "#d9c39a", "#6ec6ff", "#e08e6d"],
    },
}

_DEFAULT_STYLE = {
    "label": "Reel",
    "surface": "#101216",
    "font": "#ffffff",
    "filter": "eq=contrast=1.1:saturation=1.12",
    "accents": ["#ffd33d", "#4fa3ff", "#43d6a0"],
}


def _pretty_layout(layout: str) -> str:
    return " ".join(part.capitalize() for part in str(layout).split("_"))


def _load_pack_templates() -> List[Dict]:
    if not _PACK_PATH.exists():
        return []
    try:
        import json
        data = json.loads(_PACK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

    per_category_index: Dict[str, int] = {}
    out: List[Dict] = []
    for entry in data.get("templates", []):
        category = entry.get("category", "")
        style = _CATEGORY_STYLES.get(category, _DEFAULT_STYLE)
        idx = per_category_index.get(category, 0)
        per_category_index[category] = idx + 1
        accents = style["accents"]
        accent = accents[idx % len(accents)]
        out.append({
            "id": str(entry.get("template_id", f"pack{idx}")).lower(),
            "name": entry.get("name", "Reel template"),
            "signal": f"{style['label']} · {_pretty_layout(entry.get('master_layout', ''))}",
            "accent": accent,
            "surface": style["surface"],
            "font": style["font"],
            "filter": style["filter"],
            # Preserved layout metadata (not used by the ffmpeg grade yet).
            "category": category,
            "master_layout": entry.get("master_layout", ""),
            "aspect_ratios": entry.get("aspect_ratios", {}),
            "layers": entry.get("layers", []),
            "animations": entry.get("animations", []),
            "editable": entry.get("editable", []),
        })
    return out


# Append the pack to the built-in templates (skip id collisions).
_existing_ids = {t["id"] for t in REEL_TEMPLATES}
for _packed in _load_pack_templates():
    if _packed["id"] not in _existing_ids:
        REEL_TEMPLATES.append(_packed)
        _existing_ids.add(_packed["id"])


TEMPLATE_BY_ID = {template["id"]: template for template in REEL_TEMPLATES}


def list_templates() -> List[Dict[str, str]]:
    return [dict(template) for template in REEL_TEMPLATES]


def normalize_template_ids(template_ids: Optional[Iterable[str]]) -> List[str]:
    selected: List[str] = []
    for template_id in template_ids or []:
        clean_id = str(template_id).strip().lower()
        if clean_id in TEMPLATE_BY_ID and clean_id not in selected:
            selected.append(clean_id)
    return selected or ["fanpage-gold"]


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remainder = seconds % 60
    return f"{hours}:{minutes:02d}:{remainder:05.2f}"


def _ass_color(hex_color: str, alpha: str = "00") -> str:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        value = "ffffff"
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha}{blue}{green}{red}"


def _ass_escape(value: str) -> str:
    return value.replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _wrap_hook(value: str) -> str:
    words = " ".join(value.split())
    lines = textwrap.wrap(words, width=24, break_long_words=False)[:3]
    return r"\N".join(line.upper() for line in lines)


def write_ass_overlay(
    path: str,
    template: Dict[str, str],
    hook: str,
    captions: List[Dict],
    width: int,
    height: int,
) -> None:
    accent = _ass_color(template["accent"])
    surface = _ass_color(template["surface"], alpha="18")
    font = _ass_color(template["font"])
    caption_size = max(38, round(height * 0.034))
    hook_size = max(48, round(height * 0.042))
    outline = max(2, round(height / 640))
    margin_side = max(56, round(width * 0.065))
    caption_margin = max(150, round(height * 0.12))
    hook_margin = max(120, round(height * 0.085))

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Hook,Arial,{hook_size},{font},{font},{surface},{surface},-1,0,0,0,100,100,0,0,3,{outline},0,8,{margin_side},{margin_side},{hook_margin},1",
        f"Style: Caption,Arial,{caption_size},&H00FFFFFF,&H00FFFFFF,&H00111111,&H8A000000,-1,0,0,0,100,100,0,0,1,{outline + 2},1,2,{margin_side},{margin_side},{caption_margin},1",
        f"Style: Accent,Arial,{caption_size},{accent},{accent},&H00111111,&H8A000000,-1,0,0,0,100,100,0,0,1,{outline + 2},1,2,{margin_side},{margin_side},{caption_margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    if hook.strip():
        lines.append(f"Dialogue: 2,0:00:00.00,0:00:04.80,Hook,,0,0,0,,{_ass_escape(_wrap_hook(hook))}")

    for index, caption in enumerate(captions):
        start = float(caption.get("start", 0.0))
        end = max(start + 0.35, float(caption.get("end", start + 1.0)))
        text = " ".join(str(caption.get("text") or "").split())
        if not text:
            continue
        style = "Accent" if index == 0 else "Caption"
        lines.append(
            f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{_ass_escape(text)}"
        )

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _target_size(aspect_ratio: str) -> tuple[int, int]:
    return {
        "9:16": (1080, 1920),
        "1:1": (1080, 1080),
        "4:5": (1080, 1350),
        "16:9": (1920, 1080),
    }.get(aspect_ratio, (1080, 1920))


def render_target_size(aspect_ratio: str, upscale: bool = False) -> tuple[int, int]:
    width, height = _target_size(aspect_ratio)
    if upscale:
        return width * 2, height * 2
    return width, height


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _upscale_video(ffmpeg: str, source_path: str, out_path: str, aspect_ratio: str) -> str:
    width, height = render_target_size(aspect_ratio, upscale=True)
    filters = [
        f"scale={width}:{height}:flags=lanczos",
        "unsharp=5:5:0.8:3:3:0.4",
        "fps=30",
        "setsar=1",
        "format=yuv420p",
    ]
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        source_path,
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    return out_path


def render_template_variant(
    ffmpeg: str,
    source_path: str,
    out_path: str,
    template_id: str,
    aspect_ratio: str,
    hook: str,
    captions: List[Dict],
    upscale: bool = False,
) -> str:
    template = TEMPLATE_BY_ID[template_id]
    width, height = _target_size(aspect_ratio)
    ass_path = f"{out_path}.ass"
    write_ass_overlay(ass_path, template, hook, captions, width, height)
    render_path = f"{out_path}.render.mp4" if upscale else out_path

    accent = template["accent"].replace("#", "0x")
    filters = [
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
        template["filter"],
        f"drawbox=x=0:y=ih-12:w=iw:h=12:color={accent}:t=fill",
        f"ass=filename='{_escape_filter_path(ass_path)}'",
        "fps=30",
        "setsar=1",
        "format=yuv420p",
    ]
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        source_path,
        "-vf",
        ",".join(filters),
        "-af",
        "loudnorm=I=-14:LRA=9:TP=-1.5",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        render_path,
    ]
    try:
        subprocess.run(cmd, check=True)
        if upscale:
            _upscale_video(ffmpeg, render_path, out_path, aspect_ratio)
    finally:
        if os.path.exists(ass_path):
            os.remove(ass_path)
        if upscale and os.path.exists(render_path):
            os.remove(render_path)
    return out_path
