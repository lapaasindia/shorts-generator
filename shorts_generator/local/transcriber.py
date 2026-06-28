"""Local transcription via faster-whisper or Sarvam AI.

Reads a local media file and returns the same shape the highlight generator
expects: {duration, segments[start, end, text]}.
"""
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Dict, List, Optional

from ..config import (
    LOCAL_OUTPUT_DIR,
    LOCAL_WHISPER_DEVICE,
    LOCAL_WHISPER_MODEL,
    SARVAM_BASE_URL,
    SARVAM_CHUNK_SECONDS,
    SARVAM_LANGUAGE_CODE,
    SARVAM_STT_MODE,
    SARVAM_STT_MODEL,
    TRANSCRIBER_PROVIDER,
    require_sarvam_key,
)


def _transcript_cache_path(media_path: str, out_dir: Optional[str] = None) -> Path:
    """Return the .srt cache path for a media file."""
    cache_dir = Path(out_dir or LOCAL_OUTPUT_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / (Path(media_path).stem + ".srt")


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_srt_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value.strip())
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value!r}")
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + (millis / 1000.0)


def _write_srt_cache(media_path: str, transcript: Dict, out_dir: Optional[str] = None) -> Path:
    cache_path = _transcript_cache_path(media_path, out_dir=out_dir)
    lines = []
    for idx, segment in enumerate(transcript.get("segments", []), start=1):
        start = _format_srt_timestamp(float(segment["start"]))
        end = _format_srt_timestamp(float(segment["end"]))
        text = str(segment.get("text", "")).strip().replace("\r", "").replace("\n", " ")
        lines.append(str(idx))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")

    cache_path.write_text("\n".join(lines), encoding="utf-8")
    return cache_path


def _load_srt_cache(cache_path: Path) -> Dict:
    content = cache_path.read_text(encoding="utf-8-sig").strip()
    if not content:
        return {"duration": 0.0, "segments": []}

    segments = []
    for block in re.split(r"\n\s*\n", content):
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if "-->" not in lines[0] and len(lines) > 1 and "-->" in lines[1]:
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start_raw, end_raw = [part.strip() for part in lines[0].split("-->", 1)]
        text = "\n".join(lines[1:]).strip()
        segments.append(
            {
                "start": _parse_srt_timestamp(start_raw),
                "end": _parse_srt_timestamp(end_raw),
                "text": text,
            }
        )

    duration = segments[-1]["end"] if segments else 0.0
    return {"duration": duration, "segments": segments}


def _ffmpeg_cmd() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    try:
        import imageio_ffmpeg  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "Sarvam transcription needs ffmpeg to split audio. Install ffmpeg on PATH "
            "or install imageio-ffmpeg with:\n"
            "    pip install imageio-ffmpeg"
        ) from e

    return imageio_ffmpeg.get_ffmpeg_exe()


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
        return frames / float(rate or 1)


def _split_audio_for_sarvam(media_path: str, work_dir: Path) -> List[Path]:
    segment_seconds = max(5.0, min(float(SARVAM_CHUNK_SECONDS), 29.0))
    pattern = work_dir / "chunk_%04d.wav"
    cmd = [
        _ffmpeg_cmd(), "-y", "-loglevel", "error",
        "-i", media_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        "-f", "segment",
        "-segment_time", f"{segment_seconds:.3f}",
        "-reset_timestamps", "1",
        str(pattern),
    ]
    subprocess.run(cmd, check=True)
    chunks = sorted(work_dir.glob("chunk_*.wav"))
    if not chunks:
        raise RuntimeError("ffmpeg produced no audio chunks for Sarvam transcription.")
    return chunks


def _sarvam_language(language: Optional[str]) -> str:
    if not language:
        return SARVAM_LANGUAGE_CODE
    if "-" in language:
        return language
    language_map = {
        "bn": "bn-IN",
        "en": "en-IN",
        "gu": "gu-IN",
        "hi": "hi-IN",
        "kn": "kn-IN",
        "ml": "ml-IN",
        "mr": "mr-IN",
        "od": "od-IN",
        "or": "od-IN",
        "pa": "pa-IN",
        "ta": "ta-IN",
        "te": "te-IN",
    }
    return language_map.get(language.lower(), language)


def _post_sarvam_chunk(chunk_path: Path, language: Optional[str]) -> Dict:
    import requests

    url = f"{SARVAM_BASE_URL}/speech-to-text"
    data = {
        "model": SARVAM_STT_MODEL,
        "mode": SARVAM_STT_MODE,
        "language_code": _sarvam_language(language),
        "with_timestamps": "true",
    }
    headers = {"api-subscription-key": require_sarvam_key()}

    with chunk_path.open("rb") as fh:
        response = requests.post(
            url,
            headers=headers,
            data=data,
            files={"file": (chunk_path.name, fh, "audio/wav")},
            timeout=120,
        )
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"Sarvam transcription failed: {response.text}") from e
    return response.json()


def _payload_text(payload: Dict) -> str:
    for key in ("transcript", "text", "output", "transcription"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _timestamp_candidates(payload: Dict) -> List[Dict]:
    candidates = []
    top = payload.get("timestamps")
    if isinstance(top, dict):
        candidates.append(top)
        nested = top.get("timestamps")
        if isinstance(nested, dict):
            candidates.append(nested)
    nested_top = payload.get("timestamp")
    if isinstance(nested_top, dict):
        candidates.append(nested_top)
    return candidates


def _items_from_timestamp_block(block: Dict) -> tuple[str, List[Dict]]:
    starts = block.get("start_time_seconds") or block.get("start_times") or block.get("starts")
    ends = block.get("end_time_seconds") or block.get("end_times") or block.get("ends")

    label = "words"
    texts = block.get("chunks")
    if texts is not None:
        label = "chunks"
    else:
        texts = block.get("words") or block.get("texts")

    if not (isinstance(texts, list) and isinstance(starts, list) and isinstance(ends, list)):
        return label, []

    items = []
    for text, start, end in zip(texts, starts, ends):
        try:
            item = {
                "start": float(start),
                "end": float(end),
                "text": str(text).strip(),
            }
        except (TypeError, ValueError):
            continue
        if item["text"] and item["end"] > item["start"]:
            items.append(item)
    return label, items


def _merge_word_items(items: List[Dict], offset: float) -> List[Dict]:
    merged = []
    current_text: List[str] = []
    current_start: Optional[float] = None
    current_end = 0.0

    def flush() -> None:
        nonlocal current_text, current_start, current_end
        if current_start is not None and current_text:
            merged.append(
                {
                    "start": offset + current_start,
                    "end": offset + current_end,
                    "text": " ".join(current_text).strip(),
                }
            )
        current_text = []
        current_start = None
        current_end = 0.0

    for item in items:
        if current_start is None:
            current_start = float(item["start"])
        current_end = float(item["end"])
        token = str(item["text"]).strip()
        current_text.append(token)
        elapsed = current_end - current_start
        if elapsed >= 8.0 or token.endswith((".", "?", "!", "।")):
            flush()

    flush()
    return merged


def _segments_from_sarvam_payload(payload: Dict, offset: float, chunk_duration: float) -> List[Dict]:
    for block in _timestamp_candidates(payload):
        label, items = _items_from_timestamp_block(block)
        if not items:
            continue
        if label == "words":
            return _merge_word_items(items, offset)
        return [
            {
                "start": offset + float(item["start"]),
                "end": offset + float(item["end"]),
                "text": str(item["text"]).strip(),
            }
            for item in items
        ]

    text = _payload_text(payload)
    if not text:
        return []
    return [{"start": offset, "end": offset + chunk_duration, "text": text}]


def _transcribe_sarvam(media_path: str, language: Optional[str] = None) -> Dict:
    print(
        f"[transcribe/sarvam] model={SARVAM_STT_MODEL} language={_sarvam_language(language)}",
        flush=True,
    )
    segments: List[Dict] = []
    offset = 0.0
    with tempfile.TemporaryDirectory(prefix="sarvam-stt-") as tmp:
        chunks = _split_audio_for_sarvam(media_path, Path(tmp))
        print(f"[transcribe/sarvam] split audio into {len(chunks)} chunk(s)", flush=True)
        for index, chunk_path in enumerate(chunks, 1):
            chunk_duration = _wav_duration(chunk_path)
            print(f"[transcribe/sarvam] chunk {index}/{len(chunks)}", flush=True)
            payload = _post_sarvam_chunk(chunk_path, language)
            segments.extend(_segments_from_sarvam_payload(payload, offset, chunk_duration))
            offset += chunk_duration

    duration = segments[-1]["end"] if segments else offset
    print(f"[transcribe/sarvam] {len(segments)} segments, {duration:.0f}s of audio", flush=True)
    return {"duration": duration, "segments": segments}


def _resolve_device() -> str:
    if LOCAL_WHISPER_DEVICE != "auto":
        return LOCAL_WHISPER_DEVICE
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            # Test that CUDA actually works (catches missing cuBLAS/cuDNN libs)
            torch.zeros(1, device="cuda")
            return "cuda"
    except (ImportError, OSError, RuntimeError):
        pass
    return "cpu"


def transcribe_local(media_path: str, language: Optional[str] = None, out_dir: Optional[str] = None) -> Dict:
    """Run the selected local transcriber on a local file path, caching as .srt."""
    cache_path = _transcript_cache_path(media_path, out_dir=out_dir)
    if cache_path.exists():
        source_mtime = os.path.getmtime(media_path)
        cache_mtime = cache_path.stat().st_mtime
        if cache_mtime >= source_mtime:
            print(f"[transcribe/local] reusing cached transcript: {cache_path}", flush=True)
            cached = _load_srt_cache(cache_path)
            # Treat empty cache as invalid (likely from a failed/partial run) — delete and re-transcribe
            if not cached["segments"] or cached["duration"] <= 0.0:
                print(f"[transcribe/local] cache is empty/invalid, deleting: {cache_path}", flush=True)
                cache_path.unlink(missing_ok=True)
            else:
                print(
                    f"[transcribe/local] {len(cached['segments'])} cached segments, "
                    f"{cached['duration']:.0f}s of audio",
                    flush=True,
                )
                return cached

    provider = (TRANSCRIBER_PROVIDER or "whisper").strip().lower()
    if provider == "sarvam":
        transcript = _transcribe_sarvam(media_path, language=language)
        cache_path = _write_srt_cache(media_path, transcript, out_dir=out_dir)
        print(f"[transcribe/sarvam] wrote cache: {cache_path}", flush=True)
        return transcript
    if provider != "whisper":
        raise RuntimeError(
            f"Unknown TRANSCRIBER_PROVIDER={provider!r}. Use 'whisper' or 'sarvam'."
        )

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "faster-whisper is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    device = _resolve_device()
    compute_type = "float16" if device == "cuda" else "int8"
    print(f"[transcribe/local] faster-whisper model={LOCAL_WHISPER_MODEL} device={device}", flush=True)

    from ..config import LOCAL_WHISPER_VAD_FILTER, LOCAL_WHISPER_VAD_PARAMETERS

    model = WhisperModel(LOCAL_WHISPER_MODEL, device=device, compute_type=compute_type)

    transcribe_kwargs = {
        "audio": media_path,
        "language": language,
        "beam_size": 5,
        "condition_on_previous_text": False,
    }
    if LOCAL_WHISPER_VAD_FILTER:
        transcribe_kwargs["vad_filter"] = True
        transcribe_kwargs["vad_parameters"] = LOCAL_WHISPER_VAD_PARAMETERS
    else:
        transcribe_kwargs["vad_filter"] = False

    segments_iter, info = model.transcribe(**transcribe_kwargs)

    segments = []
    for s in segments_iter:
        segments.append({
            "start": float(s.start),
            "end": float(s.end),
            "text": (s.text or "").strip(),
        })

    duration = float(getattr(info, "duration", 0.0)) or (segments[-1]["end"] if segments else 0.0)
    print(f"[transcribe/local] {len(segments)} segments, {duration:.0f}s of audio", flush=True)
    transcript = {"duration": duration, "segments": segments}
    cache_path = _write_srt_cache(media_path, transcript, out_dir=out_dir)
    print(f"[transcribe/local] wrote cache: {cache_path}", flush=True)
    return transcript
