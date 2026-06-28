"""Local LLM backend — OpenAI, Gemini, or heuristic, selected by LLM_PROVIDER."""
import json
import re

from ..config import (
    GEMINI_MODEL,
    LLM_PROVIDER,
    OPENAI_MODEL,
    require_gemini_key,
    require_openai_key,
)


def call_openai_llm(prompt: str) -> str:
    """OpenAI Chat Completions backend used by --mode local."""
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "openai is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    client = OpenAI(api_key=require_openai_key())
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.7,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def call_gemini_llm(prompt: str) -> str:
    """Gemini backend used by --mode local when LLM_PROVIDER=gemini."""
    try:
        from google import genai  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "google-genai is required for LLM_PROVIDER=gemini. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    client = genai.Client(api_key=require_gemini_key())
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "temperature": 0.2,
            "response_mime_type": "application/json",
            "max_output_tokens": 8192,
        },
    )
    return response.text or ""


def _extract_transcript_segments(prompt: str) -> list[dict]:
    transcript = prompt.split("Transcript:", 1)[-1]
    segments = []
    for match in re.finditer(r"^\[(\d+(?:\.\d+)?)s\]\s+(.+)$", transcript, re.MULTILINE):
        start = float(match.group(1))
        text = match.group(2).strip()
        segments.append({"start": start, "text": text})

    for current, next_segment in zip(segments, segments[1:]):
        current["end"] = max(current["start"] + 1.0, next_segment["start"])
    if segments:
        segments[-1]["end"] = segments[-1]["start"] + 8.0
    return segments


def _title_from_text(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9']+", text)
    title = " ".join(words[:8]).strip()
    return title or "Heuristic Highlight"


def call_heuristic_llm(prompt: str) -> str:
    """Offline fallback for demos and smoke tests when no LLM key is available."""
    lower = prompt.lower()
    if "classify the content type" in lower:
        return json.dumps({"content_type": "other", "density": "medium"})

    segments = _extract_transcript_segments(prompt)
    if not segments:
        return json.dumps({"highlights": []})

    focus_match = re.search(r"^Viewer focus:\s*(.+)$", prompt, re.MULTILINE)
    focus_terms = set()
    if focus_match and "No extra topic constraint" not in focus_match.group(1):
        stop_words = {"about", "find", "moments", "related", "request", "strongest", "this", "when", "with"}
        focus_terms = {
            word.lower()
            for word in re.findall(r"[A-Za-z0-9']+", focus_match.group(1))
            if len(word) >= 3 and word.lower() not in stop_words
        }

    hook_terms = (
        "secret", "nobody", "mistake", "wrong", "cost", "changed", "learned",
        "surprising", "never", "truth", "important", "simple", "warning",
        "money", "earn", "job", "risk", "stop", "best", "worst", "why",
    )
    candidates = []
    duration = max(float(segment["end"]) for segment in segments)
    for index, segment in enumerate(segments):
        text = segment["text"]
        text_lower = text.lower()
        score = 65 + min(25, sum(5 for term in hook_terms if term in text_lower))
        if re.search(r"\b\d+(?:[.,]\d+)?\b", text):
            score += 8
        if any(mark in text for mark in ("?", "!")):
            score += 3
        score += min(18, sum(6 for term in focus_terms if term in text_lower))

        hook_start = float(segment["start"])
        hook_end = min(float(segment["end"]), hook_start + 8.0)
        context_index = max(0, index - 3)
        start = float(segments[context_index]["start"])
        end = min(duration, max(hook_end + 20.0, start + 55.0))
        while end - start < 25.0 and end < duration:
            end = min(duration, end + 10.0)
        if end <= start:
            continue

        candidates.append(
            {
                "title": _title_from_text(text),
                "start_time": start,
                "end_time": end,
                "hook_start_time": hook_start,
                "hook_end_time": hook_end,
                "score": min(100, score),
                "hook_sentence": text,
                "virality_reason": (
                    "Hook-first selection aligned to the requested topic and built around a strong claim, number, or quotable line."
                    if focus_terms
                    else "Hook-first selection built around a strong claim, number, or quotable line with its surrounding context."
                ),
            }
        )

    candidates.sort(key=lambda item: int(item["score"]), reverse=True)
    return json.dumps({"highlights": candidates})


def call_local_llm(prompt: str) -> str:
    """Dispatch to the configured local LLM provider."""
    provider = (LLM_PROVIDER or "openai").strip().lower()
    if provider == "openai":
        return call_openai_llm(prompt)
    if provider == "gemini":
        return call_gemini_llm(prompt)
    if provider == "heuristic":
        return call_heuristic_llm(prompt)
    raise RuntimeError(
        f"Unknown LLM_PROVIDER={provider!r}. Use 'openai', 'gemini', or 'heuristic'."
    )
