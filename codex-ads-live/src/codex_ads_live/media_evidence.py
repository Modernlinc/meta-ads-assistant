"""Bounded, local processing of supplied advertising media."""
from hashlib import file_digest
from html import escape
import json
import math
from pathlib import Path
import re
from shutil import which
import subprocess

from .business_profile import SECRET_PATTERN
from .local_paths import checked_path
from .validation import InputError, fail, object_only, strict_json


MAX_BYTES = 100 * 1024 * 1024
MAX_DURATION = 180
FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,png_pipe,jpeg_pipe,webp_pipe"
EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".png", ".jpg", ".jpeg", ".webp"}
DIMENSIONS = ("selling_point", "hook", "conversion_point", "human_feel")
SRT_TIMES = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def local_path(path):
    return checked_path(path, link_error="MEDIA_LINK_BLOCKED", volume_error="MEDIA_VOLUME_NOT_MOUNTED")


def source_file(path, max_bytes=MAX_BYTES):
    path = local_path(path)
    if not path.is_file():
        fail("MEDIA_INPUT_NOT_REGULAR_FILE")
    if not 0 < path.stat().st_size <= max_bytes:
        fail("MEDIA_INPUT_SIZE")
    return path


def fingerprint(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def run_tool(program, arguments, timeout):
    binary = which(program)
    if binary is None:
        fail("MEDIA_TOOL_UNAVAILABLE")
    try:
        return subprocess.run(
            [binary, *map(str, arguments)], stdin=subprocess.DEVNULL,
            capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        fail("MEDIA_TOOL_TIMEOUT")


def probe(source):
    reply = run_tool(
        "ffprobe",
        ["-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", FORMATS,
         "-show_entries", "stream=codec_type,width,height:format=duration",
         "-of", "json", source],
        12,
    )
    if reply.returncode:
        fail("MEDIA_PROBE_FAILED")
    meta = strict_json(reply.stdout.decode("utf-8"), max_chars=24_000)
    streams = meta.get("streams", [])
    picture = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not picture or not all(0 < picture.get(k, 0) <= 8192 for k in ("width", "height")):
        fail("MEDIA_VISUAL_STREAM_UNSUPPORTED")
    try:
        duration = float(meta.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        fail("MEDIA_DURATION_UNAVAILABLE")
    if not math.isfinite(duration) or not 0 <= duration <= MAX_DURATION:
        fail("MEDIA_DURATION_LIMIT")
    if not duration and source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        fail("MEDIA_DURATION_UNAVAILABLE")
    return round(duration * 1000), any(row.get("codec_type") == "audio" for row in streams)


def milliseconds(parts):
    hours, minutes, seconds, fragment = map(int, parts)
    if minutes >= 60 or seconds >= 60:
        fail("MEDIA_SUBTITLE_TIMECODE")
    return (hours * 3600 + minutes * 60 + seconds) * 1000 + fragment


def read_srt(path, duration_ms, source):
    text = source_file(path, 200_000).read_text(encoding="utf-8-sig").strip()
    if not text or SECRET_PATTERN.search(text):
        fail("MEDIA_SUBTITLE_CONTENT_INVALID")
    result = []
    for chunk in re.split(r"\n[ \t]*\n", text):
        lines = chunk.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            fail("MEDIA_SUBTITLE_FORMAT")
        match = SRT_TIMES.fullmatch(lines[1].strip())
        if not match:
            fail("MEDIA_SUBTITLE_TIMECODE")
        start, end = milliseconds(match.groups()[:4]), milliseconds(match.groups()[4:])
        if not 0 <= start < end <= duration_ms + 250 or (result and start < result[-1]["start_ms"]):
            fail("MEDIA_SUBTITLE_OUT_OF_RANGE")
        message = "\n".join(lines[2:]).strip()
        if not 0 < len(message) <= 1000 or any(ord(c) < 32 and c not in "\n\t" for c in message):
            fail("MEDIA_SUBTITLE_CONTENT_INVALID")
        result.append({"start_ms": start, "end_ms": end, "text": message, "source": source})
        if len(result) > 500:
            fail("MEDIA_SUBTITLE_COUNT")
    return result


def extract(source, output, arguments):
    reply = run_tool(
        "ffmpeg",
        ["-nostdin", "-v", "error", "-n", "-protocol_whitelist", "file,pipe",
         "-format_whitelist", FORMATS, "-i", source, *arguments, output],
        25,
    )
    return reply.returncode == 0 and output.is_file() and output.stat().st_size > 0


def load_observations(path, sha256):
    if path is None:
        return {}
    doc = strict_json(source_file(path, 32_000).read_text(encoding="utf-8-sig"))
    object_only(doc, ("media_sha256", "observations"), ("media_sha256", "observations"))
    if doc["media_sha256"] != sha256:
        fail("MEDIA_OBSERVATION_HASH_MISMATCH")
    observations = doc["observations"]
    object_only(observations, DIMENSIONS)
    return observations


def evaluate_observations(observations, frames, subtitles):
    review = {axis: {"state": "UNOBSERVED"} for axis in DIMENSIONS}
    for axis, row in observations.items():
        object_only(row, ("kind", "index", "observation"), ("kind", "index", "observation"))
        kind, index, note = row["kind"], row["index"], row["observation"]
        evidence = frames if kind == "FRAME" else subtitles if kind == "SUBTITLE" else ()
        if type(index) is not int or not 0 <= index < len(evidence):
            fail("MEDIA_OBSERVATION_LOCATOR_INVALID")
        if axis == "human_feel" and kind != "FRAME":
            fail("MEDIA_HUMAN_FEEL_REQUIRES_VISUAL_EVIDENCE")
        if type(note) is not str or not 0 < len(note.strip()) <= 400 or SECRET_PATTERN.search(note):
            fail("MEDIA_OBSERVATION_TEXT_INVALID")
        review[axis] = {"state": "USER_OBSERVED", "kind": kind, "index": index, "observation": note.strip()}
    return review


def write_html(output, result):
    frames = "".join(
        f'<figure><img src="{row["path"]}" alt="Sample at {row["timestamp_ms"]} ms">'
        f'<figcaption>{row["timestamp_ms"]} ms</figcaption></figure>' for row in result["frames"]
    )
    captions = "".join(
        f'<li>{row["start_ms"]}–{row["end_ms"]} ms {escape(row["text"])}</li>'
        for row in result["subtitles"]
    )
    axes = "".join(
        f'<li>{axis}: {escape(value.get("observation", value["state"]))}</li>'
        for axis, value in result["review"].items()
    )
    with (output / "review.html").open("x", encoding="utf-8") as stream:
        stream.write(
            '<!doctype html><html lang="zh"><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\'">'
            f'<title>广告素材审阅</title><h1>广告素材审阅</h1><p>SHA-256 {result["media_sha256"]}</p>'
            f'<h2>画面</h2>{frames}<h2>字幕</h2><ol>{captions}</ol>'
            f'<h2>素材观察</h2><ul>{axes}</ul></html>'
        )


def workspace_review(result, concept_id):
    asset = result["media_sha256"]
    observed = {}
    comments = dict.fromkeys(DIMENSIONS)
    for axis, row in result["review"].items():
        if row["state"] != "USER_OBSERVED":
            continue
        frame = row["kind"] == "FRAME"
        index = row["index"]
        locator = result["frames" if frame else "subtitles"][index]
        reference = f"frame-{index:02}" if frame else f"subtitle-{index:03}"
        observed[reference] = dict(id=reference, asset_sha256=asset, kind="VISUAL" if frame else "SUBTITLE",
                                   evidence_ref=reference, time_ms=locator["timestamp_ms" if frame else "start_ms"])
        comments[axis] = dict(observation_id=reference, comment=row["observation"])
    return dict(brand=None, sources=[], concepts=[], learning=[],
                reviews=[dict(asset_sha256=asset, concept_id=concept_id, observations=list(observed.values()),
                              comments=comments, weakest=None, suggestion=None)])


def analyze(source, output, *, subtitles=None, model=None, observations=None, concept_id=None,
            model_sha256=None, language="auto", fallback_subtitles=None):
    if concept_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", concept_id):
        fail("MEDIA_CONCEPT_ID_INVALID")
    source = source_file(source)
    output = local_path(output)
    if source.suffix.lower() not in EXTENSIONS:
        fail("MEDIA_EXTENSION_UNSUPPORTED")
    if (subtitles and (model or fallback_subtitles)) or (not model and (fallback_subtitles or model_sha256)):
        fail("MEDIA_TRANSCRIPT_SOURCE_CONFLICT")
    if not re.fullmatch(r"(?:auto|[a-z]{2,3})", language):
        fail("MEDIA_LANGUAGE_INVALID")
    if model_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", model_sha256):
        fail("MEDIA_MODEL_DIGEST_INVALID")
    model_basis = None
    valid_model = None
    if model:
        candidate = local_path(model)
        model_basis = {"state": "MODEL_NOT_AVAILABLE", "sha256": None, "language": language}
        if candidate.exists():
            candidate = source_file(candidate, 2_000_000_000)
            actual = fingerprint(candidate)
            english_only = bool(re.search(r"\.en(?:[.-]|$)", candidate.name, re.I))
            model_basis["sha256"] = actual
            model_basis["state"] = ("MODEL_HASH_MISMATCH" if model_sha256 and model_sha256 != actual else
                                    "MODEL_LANGUAGE_UNSUPPORTED" if english_only and language not in ("auto", "en") else
                                    "MODEL_CHECKED" if model_sha256 else "MODEL_HASH_RECORDED")
            if model_basis["state"] in ("MODEL_CHECKED", "MODEL_HASH_RECORDED"):
                valid_model = candidate
                if english_only and language == "auto":
                    language = "en"
                    model_basis["language"] = "en"
    duration, has_audio = probe(source)
    sha256 = fingerprint(source)
    observations = load_observations(observations, sha256)
    cues = read_srt(subtitles, duration, "USER_SUPPLIED_SRT") if subtitles else []
    if output.exists() or not output.parent.is_dir():
        fail("MEDIA_OUTPUT_NEW_DIRECTORY_REQUIRED")
    output.mkdir(exist_ok=False)
    frames = []
    sample_times = sorted({0, duration // 3, duration * 2 // 3})
    for index, timestamp in enumerate(sample_times):
        target = output / f"frame-{index:02}.png"
        if extract(source, target, ["-ss", f"{timestamp / 1000:.3f}", "-map", "0:v:0",
                                    "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-threads", "1"]):
            frames.append({"timestamp_ms": timestamp, "path": target.name, "sha256": fingerprint(target)})
    audio_target = output / "audio.wav"
    audio_ok = has_audio and extract(
        source, audio_target, ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
    )
    audio = {"state": "EXTRACTED", "path": audio_target.name, "sha256": fingerprint(audio_target)} if audio_ok else {
        "state": "DECODE_FAILED" if has_audio else "NO_AUDIO_STREAM"
    }
    transcript = "USER_SUPPLIED_SRT" if subtitles else "NO_TRANSCRIPT_SUPPLIED"
    if model:
        transcript = "LOCAL_MODEL_UNAVAILABLE"
        if valid_model and audio_ok and which("whisper-cli"):
            target = output / "speech"
            transcript = "LOCAL_TRANSCRIPTION_UNAVAILABLE"
            try:
                call = run_tool("whisper-cli", ["-m", valid_model, "-f", audio_target, "-l", language,
                                               "-osrt", "-of", target], 120)
                if not call.returncode and target.with_suffix(".srt").is_file():
                    cues = read_srt(target.with_suffix(".srt"), duration, "LOCAL_MODEL")
            except (InputError, OSError, UnicodeError):
                cues = []
            if cues:
                transcript = "LOCAL_MODEL"
        if not cues and fallback_subtitles is not None:
            cues = read_srt(fallback_subtitles, duration, "USER_SUPPLIED_SRT_FALLBACK")
            transcript = "USER_SUPPLIED_SRT_FALLBACK"
    if fingerprint(source) != sha256:
        fail("MEDIA_SOURCE_CHANGED")
    review = evaluate_observations(observations, frames, cues)
    result = {
        "status": "LOCAL_MEDIA_EVIDENCE" if len(frames) == len(sample_times) and (not has_audio or audio_ok)
        else "LOCAL_MEDIA_PARTIAL", "media_sha256": sha256, "duration_ms": duration,
        "frames": frames, "audio": audio, "subtitles": cues, "transcript_state": transcript,
        "transcription_model": model_basis,
        "review": review, "actions_authorized": False,
    }
    if concept_id is not None:
        result["workspace_review_file"] = "workspace-review.json"
        with (output / result["workspace_review_file"]).open("x", encoding="utf-8") as stream:
            json.dump(workspace_review(result, concept_id), stream, ensure_ascii=False, indent=2)
    write_html(output, result)
    with (output / "media-evidence.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result
