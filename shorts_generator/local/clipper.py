"""Local clipping: ffmpeg subclip + OpenCV face-aware vertical crop.

Two stages per highlight:
  1. Cut the source video to [start, end] with ffmpeg (re-encoded, audio kept).
  2. Reframe the cut to the target aspect ratio. For 9:16 we slide a vertical
     window horizontally across the frame to keep faces centred (Haar
     cascade — same approach as the original repo, no external models).
"""
import os
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from ..config import LOCAL_OUTPUT_DIR
from .templates import TEMPLATE_BY_ID, normalize_template_ids, render_template_variant


def _ratio(aspect_ratio: str) -> float:
    """Parse '9:16' → 9/16, '1:1' → 1.0."""
    try:
        w, h = aspect_ratio.split(":")
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return 9.0 / 16.0


def _ffmpeg_cmd() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    try:
        import imageio_ffmpeg  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "ffmpeg is required for --mode local. Install ffmpeg on PATH or install "
            "imageio-ffmpeg with:\n"
            "    pip install imageio-ffmpeg"
        ) from e

    return imageio_ffmpeg.get_ffmpeg_exe()


def _cut_subclip(source_path: str, start: float, end: float, out_path: str) -> str:
    """ffmpeg -ss start -to end → re-encoded mp4 with audio."""
    cmd = [
        _ffmpeg_cmd(), "-y", "-loglevel", "error",
        "-i", source_path,
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    return out_path


def _assemble_parts(source_path: str, parts: List[Dict], out_path: str) -> str:
    """Cut source-time parts and concatenate them in editorial order."""
    if len(parts) == 1:
        return _cut_subclip(
            source_path,
            float(parts[0]["start_time"]),
            float(parts[0]["end_time"]),
            out_path,
        )

    part_paths: List[str] = []
    manifest_path = f"{out_path}.concat.txt"
    try:
        for index, part in enumerate(parts, 1):
            part_path = f"{out_path}.part{index:02d}.mp4"
            _cut_subclip(
                source_path,
                float(part["start_time"]),
                float(part["end_time"]),
                part_path,
            )
            part_paths.append(part_path)

        with open(manifest_path, "w", encoding="utf-8") as manifest:
            for part_path in part_paths:
                escaped = str(os.path.abspath(part_path)).replace("'", r"'\''")
                manifest.write(f"file '{escaped}'\n")

        cmd = [
            _ffmpeg_cmd(), "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", manifest_path,
            "-c", "copy", "-movflags", "+faststart", out_path,
        ]
        subprocess.run(cmd, check=True)
    finally:
        if os.path.exists(manifest_path):
            os.remove(manifest_path)
        for part_path in part_paths:
            if os.path.exists(part_path):
                os.remove(part_path)
    return out_path


def _reframe_vertical(in_path: str, out_path: str, aspect_ratio: str) -> str:
    """Crop the cut clip to the target aspect ratio, tracking faces if possible."""
    try:
        import cv2  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "opencv-python is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    target_ratio = _ratio(aspect_ratio)
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open {in_path}")

    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # Compute the largest crop that fits inside the frame at the target ratio.
    if target_ratio < src_w / src_h:
        crop_h = src_h
        crop_w = int(crop_h * target_ratio)
    else:
        crop_w = src_w
        crop_h = int(crop_w / target_ratio)
    crop_w = max(2, crop_w - (crop_w % 2))
    crop_h = max(2, crop_h - (crop_h % 2))

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    silent_path = out_path + ".silent.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(silent_path, fourcc, fps, (crop_w, crop_h))

    last_center: Optional[Tuple[int, int]] = None
    smoothing = 0.15  # how aggressively to chase a new face position
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        if len(faces) > 0:
            # Pick the largest face — usually the speaker.
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            cx = x + w // 2
            cy = y + h // 2
            if last_center is None:
                last_center = (cx, cy)
            else:
                lx, ly = last_center
                last_center = (
                    int(lx + (cx - lx) * smoothing),
                    int(ly + (cy - ly) * smoothing),
                )
        if last_center is None:
            last_center = (src_w // 2, src_h // 2)

        cx, cy = last_center
        x0 = max(0, min(src_w - crop_w, cx - crop_w // 2))
        y0 = max(0, min(src_h - crop_h, cy - crop_h // 2))
        cropped = frame[y0:y0 + crop_h, x0:x0 + crop_w]
        writer.write(cropped)

    cap.release()
    writer.release()

    # Mux audio from the cut clip back onto the silent reframed video.
    cmd = [
        _ffmpeg_cmd(), "-y", "-loglevel", "error",
        "-i", silent_path,
        "-i", in_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a:0?",
        "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    os.remove(silent_path)
    return out_path


def crop_clip_local(
    source_path: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str,
    out_path: str,
    edit_parts: Optional[List[Dict]] = None,
) -> str:
    """Cut + reframe one highlight, returning the local mp4 path."""
    cut_path = out_path + ".cut.mp4"
    try:
        parts = edit_parts or [
            {"start_time": start_time, "end_time": end_time, "role": "linear"}
        ]
        _assemble_parts(source_path, parts, cut_path)
        _reframe_vertical(cut_path, out_path, aspect_ratio)
    finally:
        if os.path.exists(cut_path):
            os.remove(cut_path)
    return out_path


def crop_highlights_local(
    source_path: str,
    highlights: List[Dict],
    aspect_ratio: str = "9:16",
    out_dir: Optional[str] = None,
    template_ids: Optional[List[str]] = None,
) -> List[Dict]:
    out_dir = out_dir or LOCAL_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    selected_templates = normalize_template_ids(template_ids)
    results: List[Dict] = []
    for i, h in enumerate(highlights, 1):
        base_path = os.path.join(out_dir, f".reel_{i:02d}_base.mp4")
        print(
            f"[clip/local] reel {i}/{len(highlights)}: {h.get('title', '(untitled)')} "
            f"({len(selected_templates)} templates)",
            flush=True,
        )
        try:
            crop_clip_local(
                source_path,
                float(h["start_time"]),
                float(h["end_time"]),
                aspect_ratio,
                base_path,
                edit_parts=h.get("edit_parts"),
            )
        except Exception as e:
            print(f"[clip/local] reel {i} base edit failed: {e}", flush=True)
            if os.path.exists(base_path):
                os.remove(base_path)
            public_highlight = {key: value for key, value in h.items() if not key.startswith("_")}
            for template_id in selected_templates:
                template = TEMPLATE_BY_ID[template_id]
                results.append(
                    {
                        **public_highlight,
                        "reel_index": i,
                        "template_id": template_id,
                        "template_name": template["name"],
                        "clip_url": None,
                        "error": str(e),
                    }
                )
            continue

        public_highlight = {key: value for key, value in h.items() if not key.startswith("_")}
        captions = h.get("_caption_segments") or []
        try:
            for template_index, template_id in enumerate(selected_templates, 1):
                template = TEMPLATE_BY_ID[template_id]
                out_path = os.path.join(out_dir, f"reel_{i:02d}__{template_id}.mp4")
                print(
                    f"[template] {template_index}/{len(selected_templates)} {template['name']}",
                    flush=True,
                )
                try:
                    render_template_variant(
                        _ffmpeg_cmd(),
                        base_path,
                        out_path,
                        template_id,
                        aspect_ratio,
                        str(h.get("hook_sentence") or h.get("title") or ""),
                        captions,
                    )
                    results.append(
                        {
                            **public_highlight,
                            "reel_index": i,
                            "template_id": template_id,
                            "template_name": template["name"],
                            "template_signal": template["signal"],
                            "clip_url": out_path,
                        }
                    )
                except Exception as e:
                    print(f"[template] {template['name']} failed: {e}", flush=True)
                    results.append(
                        {
                            **public_highlight,
                            "reel_index": i,
                            "template_id": template_id,
                            "template_name": template["name"],
                            "clip_url": None,
                            "error": str(e),
                        }
                    )
        finally:
            if os.path.exists(base_path):
                os.remove(base_path)
    return results
