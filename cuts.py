#!/usr/bin/env python3

import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"


def c(text, colour):
    return f"{colour}{text}{RESET}"


def format_elapsed(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(seconds, 60)
    return f"{int(minutes)}m {seconds:.0f}s"


def format_size(path):
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} TB"


def die(message):
    print(f"\n{c('ERROR', RED)} {message}\n")
    sys.exit(1)


def check_dependency(name):
    if shutil.which(name) is None:
        die(
            f"{name} was not found in PATH.\n"
            f"Install FFmpeg to use this script."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Time handling
# ─────────────────────────────────────────────────────────────────────────────

def parse_time(value):
    """
    Accept:
        SS
        MM:SS
        HH:MM:SS
    """

    value = value.strip()

    try:
        parts = [int(x) for x in value.split(":")]
    except ValueError:
        raise ValueError(f"Invalid timestamp: {value}")

    if any(part < 0 for part in parts):
        raise ValueError(f"Invalid timestamp: {value}")

    if len(parts) == 1:
        return parts[0]

    if len(parts) == 2:
        minutes, seconds = parts
        if seconds >= 60:
            raise ValueError(f"Invalid timestamp: {value}")
        return minutes * 60 + seconds

    if len(parts) == 3:
        hours, minutes, seconds = parts
        if minutes >= 60 or seconds >= 60:
            raise ValueError(f"Invalid timestamp: {value}")
        return hours * 3600 + minutes * 60 + seconds

    raise ValueError(f"Invalid timestamp: {value}")


def format_time(seconds):
    seconds = max(0, int(round(seconds)))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{minutes:02d}:{secs:02d}"


# ─────────────────────────────────────────────────────────────────────────────
# FFprobe
# ─────────────────────────────────────────────────────────────────────────────

def get_duration(video):
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        die(f"Could not read duration of:\n{video}")

    try:
        return float(result.stdout.strip())
    except ValueError:
        die(f"Could not determine duration of:\n{video}")


def get_stream_types(media_path):
    """Return the set of stream types reported by ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(media_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        die(f"Could not inspect media streams:\n{media_path}")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def measure_peak_db(media_path):
    """Measure decoded sample peak in dBFS using FFmpeg volumedetect."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(media_path),
         "-map", "0:a:0", "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = result.stderr[-2000:].strip()
        die(f"Could not analyze audio in {media_path.name}.\n{detail}")
    peak_match = re.search(r"max_volume:\s*(-?inf|-?[0-9.]+) dB", result.stderr)
    if not peak_match:
        die(f"FFmpeg could not determine the audio peak in {media_path.name}.")
    peak_text = peak_match.group(1)
    if peak_text == "-inf":
        die(f"The audio in {media_path.name} is silent; normalization is impossible.")
    return float(peak_text)


# ─────────────────────────────────────────────────────────────────────────────
# cuts.txt
# ─────────────────────────────────────────────────────────────────────────────

def read_cuts(path, duration):
    cuts = []

    with open(path, "r", encoding="utf-8") as f:
        for line_number, raw_line in enumerate(f, 1):

            line = raw_line.strip()

            # Blank lines and comments are allowed.
            if not line or line.startswith("#"):
                continue

            if "-" not in line:
                raise ValueError(
                    f"Line {line_number}: expected START-END\n"
                    f"Got: {line}"
                )

            start_text, end_text = line.split("-", 1)

            try:
                start = parse_time(start_text)
                end = parse_time(end_text)
            except ValueError as e:
                raise ValueError(f"Line {line_number}: {e}")

            if start < 0:
                raise ValueError(
                    f"Line {line_number}: start cannot be negative."
                )

            if end <= start:
                raise ValueError(
                    f"Line {line_number}: end must be after start."
                )

            if start >= duration:
                raise ValueError(
                    f"Line {line_number}: start {format_time(start)} "
                    f"is beyond the video duration ({format_time(duration)})."
                )

            # Clamp a final cut that extends microscopically past EOF.
            end = min(end, duration)

            cuts.append((start, end))

    cuts.sort()

    # Merge overlapping / touching cuts.
    merged = []

    for start, end in cuts:
        if not merged:
            merged.append([start, end])
            continue

        previous_start, previous_end = merged[-1]

        if start <= previous_end:
            merged[-1][1] = max(previous_end, end)
        else:
            merged.append([start, end])

    return [(start, end) for start, end in merged]


# ─────────────────────────────────────────────────────────────────────────────
# DELETE → KEEP
# ─────────────────────────────────────────────────────────────────────────────

def calculate_keep_ranges(cuts, duration):
    keep = []
    cursor = 0

    for cut_start, cut_end in cuts:

        if cut_start > cursor:
            keep.append((cursor, cut_start))

        cursor = max(cursor, cut_end)

    if cursor < duration:
        keep.append((cursor, duration))

    return keep


# ─────────────────────────────────────────────────────────────────────────────
# Human-readable summary
# ─────────────────────────────────────────────────────────────────────────────

def print_ranges(title, ranges, colour):
    print(f"\n{c(title, colour)}")

    if not ranges:
        print(f"  {DIM}(none){RESET}")
        return

    for i, (start, end) in enumerate(ranges, 1):
        duration = end - start

        print(
            f"  {c(f'{i:02d}', DIM)}  "
            f"{format_time(start)} → {format_time(end)}  "
            f"{DIM}({format_time(duration)}){RESET}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# FFmpeg runner
# ─────────────────────────────────────────────────────────────────────────────

def run_ffmpeg(command, description):
    print(f"\n{c('▶', CYAN)} {description}")
    started_at = time.perf_counter()
    process = subprocess.run(command)
    if process.returncode != 0:
        die(
            f"FFmpeg failed while {description.lower()}.\n"
            f"Nothing was done to the original source."
        )
    print(f"  {DIM}Completed in {format_elapsed(time.perf_counter() - started_at)}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    started_at = time.perf_counter()
    check_dependency("ffmpeg")
    check_dependency("ffprobe")

    # Run directly from the file manager: use the video and cuts.txt beside
    # this script. Exactly one source video must be present in the folder.
    script_dir = Path(__file__).resolve().parent
    video_extensions = {
        ".mov", ".mp4", ".mkv", ".avi", ".m4v", ".webm",
        ".mts", ".m2ts", ".mpg", ".mpeg", ".wmv", ".flv", ".ts",
    }
    candidates = [
        path for path in script_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in video_extensions
        and path.name != "Pass A.mov"
    ]

    if len(candidates) != 1:
        names = "\n".join(f"  {path.name}" for path in candidates) or "  (none)"
        die(
            "Expected exactly one source video beside cuts.py. Found "
            f"{len(candidates)}:\n{names}"
        )

    video = candidates[0].resolve()
    cuts_file = (script_dir / "cuts.txt").resolve()

    if not video.is_file():
        die(f"Source video does not exist:\n{video}")

    if not cuts_file.is_file():
        die(f"cuts.txt does not exist:\n{cuts_file}")

    # ─────────────────────────────────────────────────────────────────────────
    # Source information
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print(c("=" * 64, BLUE))
    print(c(" LOSSLESS VIDEO CUTTER ", BOLD + BLUE))
    print(c("=" * 64, BLUE))

    print(f"\n{c('SOURCE', CYAN)}")
    print(f"  {video}")

    duration = get_duration(video)
    source_streams = get_stream_types(video)
    if "video" not in source_streams:
        die("The selected source does not contain a video stream.")
    if "audio" not in source_streams:
        die("The selected source has no audio stream; an audio FLAC cannot be created.")
    print(f"  Duration: {c(format_time(duration), BOLD)}")
    print(f"  Streams : {', '.join(sorted(source_streams))}")

    # ─────────────────────────────────────────────────────────────────────────
    # Read DELETE ranges
    # ─────────────────────────────────────────────────────────────────────────

    try:
        cuts = read_cuts(cuts_file, duration)
    except ValueError as e:
        die(str(e))

    if not cuts:
        die("cuts.txt contains no valid cut ranges.")

    print_ranges("DELETE", cuts, RED)

    # ─────────────────────────────────────────────────────────────────────────
    # Calculate KEEP ranges
    # ─────────────────────────────────────────────────────────────────────────

    keep = calculate_keep_ranges(cuts, duration)

    kept_duration = sum(end - start for start, end in keep)
    removed_duration = duration - kept_duration

    print_ranges("KEEP", keep, GREEN)

    print()
    print(c("EDIT SUMMARY", MAGENTA))
    print(f"  Original : {format_time(duration)}")
    print(f"  Removed  : {format_time(removed_duration)}")
    print(f"  Final    : {c(format_time(kept_duration), BOLD + GREEN)}")
    print()

    if not keep:
        die("The cut ranges remove the entire video; there is nothing left to export.")
    # ─────────────────────────────────────────────────────────────────────────
    # Output path
    # ─────────────────────────────────────────────────────────────────────────

    output = video.with_name("Pass A.mov")
    audio_output = video.with_name("To Clean.flac")

    for existing_output in (output, audio_output):
        if existing_output.exists():
            print(
                f"{c('WARNING', YELLOW)} Output already exists:\n"
                f"  {existing_output}"
            )

            answer = input("Overwrite it? [y/N] ").strip().lower()

            if answer != "y":
                print("Cancelled.")
                sys.exit(0)

            # Existing files remain in place until validated replacements are ready.

    # ─────────────────────────────────────────────────────────────────────────
    # Temporary workspace
    #
    # IMPORTANT:
    # Do NOT use /tmp.
    #
    # Temporary clips can be large, so keep them on the same filesystem
    # as the source unless the user explicitly provides another location.
    # ─────────────────────────────────────────────────────────────────────────

    work_parent = video.parent

    work_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{video.stem}_cut_",
            dir=work_parent
        )
    )

    print(f"{c('WORKSPACE', DIM)}")
    print(f"  {work_dir}")

    # All intermediate files and unpublished outputs stay inside this workspace.
    clips = []
    pass_a_temp = work_dir / "pass_a_video.mov"
    audio_temp = work_dir / "normalized_audio.flac"

    try:

        # ─────────────────────────────────────────────────────────────────────
        # Extract KEEP ranges directly from ORIGINAL source
        # ─────────────────────────────────────────────────────────────────────

        print()
        print(c("EXTRACTING KEEP RANGES", BOLD + GREEN))
        print(
            f"{DIM}"
            "Each clip is extracted from the ORIGINAL source."
            f"{RESET}"
        )

        for index, (start, end) in enumerate(keep, 1):

            start_label = format_time(start).replace(":", "").zfill(6)
            end_label = format_time(end).replace(":", "").zfill(6)
            clip = work_dir / (
                f"segment_{index:03d}_{start_label}-{end_label}{video.suffix}"
            )

            clip_duration = end - start

            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "warning",

                # Seek directly in the ORIGINAL file.
                "-ss", str(start),

                "-i", str(video),

                # Copy exactly the compressed streams.
                "-t", str(clip_duration),

                "-map", "0",
                "-c", "copy",

                # Give each temporary clip a clean timestamp origin.
                "-avoid_negative_ts", "make_zero",

                "-y",
                str(clip),
            ]

            run_ffmpeg(
                command,
                f"Extracting clip {index}/{len(keep)} "
                f"({format_time(start)} → {format_time(end)})"
            )

            clips.append(clip)

        # ─────────────────────────────────────────────────────────────────────
        # Build concat manifest
        # ─────────────────────────────────────────────────────────────────────

        concat_file = work_dir / "concat_manifest.txt"

        with open(concat_file, "w", encoding="utf-8") as f:
            for clip in clips:
                # concat demuxer uses single-quoted filenames.
                # Escape apostrophes safely.
                filename = str(clip).replace("'", "'\\''")
                f.write(f"file '{filename}'\n")

        # ─────────────────────────────────────────────────────────────────────
        # Concatenate WITHOUT RE-ENCODING
        # ─────────────────────────────────────────────────────────────────────

        concat_command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",

            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),

            "-map", "0",
            "-c", "copy",

            "-y",
            str(pass_a_temp),
        ]

        run_ffmpeg(
            concat_command,
            "Concatenating kept clips"
        )

        # ─────────────────────────────────────────────────────────────────────
        # Validate temporary video output before publishing it.
        output_duration = get_duration(pass_a_temp)
        output_streams = get_stream_types(pass_a_temp)
        if "video" not in output_streams:
            die("The temporary Pass A output contains no video stream.")
        if "audio" not in output_streams:
            die("The temporary Pass A output contains no audio stream.")

        print(f"\n{c('VIDEO VALIDATION', BOLD + CYAN)}")
        print(f"  Duration : {format_time(output_duration)}")
        print(f"  Size     : {format_size(pass_a_temp)}")
        difference = abs(output_duration - kept_duration)
        if difference <= 2:
            print(f"  Status   : {c('Duration looks correct', GREEN)}")
        else:
            print(f"  Status   : {c('Duration differs from expectation', YELLOW)}")
            print(f"  Expected : {format_time(kept_duration)}")

        # Peak normalization only: no DC-offset or high-pass filter.
        print(f"\n{c('AUDIO NORMALIZATION', BOLD + CYAN)}")
        print("  Measuring sample peak...")
        peak_db = measure_peak_db(pass_a_temp)
        target_db = -1.0
        gain_db = target_db - peak_db
        print(f"  Measured peak : {peak_db:.2f} dBFS")
        print(f"  Target peak   : {target_db:.2f} dBFS")
        print(f"  Applied gain  : {gain_db:+.2f} dB")

        run_ffmpeg(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-i", str(pass_a_temp), "-map", "0:a:0", "-vn",
                "-af", f"volume={gain_db:.8f}dB",
                "-c:a", "flac", "-compression_level", "8",
                "-y", str(audio_temp),
            ],
            "Normalizing audio and creating lossless FLAC"
        )

        # Validate the FLAC before replacing any existing deliverables.
        audio_streams = get_stream_types(audio_temp)
        if "audio" not in audio_streams:
            die("The generated FLAC contains no audio stream.")
        audio_duration = get_duration(audio_temp)
        if audio_duration <= 0:
            die("The generated FLAC has an invalid duration.")
        verified_peak_db = measure_peak_db(audio_temp)
        if abs(verified_peak_db - target_db) > 0.10:
            die(
                "The normalized FLAC failed peak validation. "
                f"Expected about {target_db:.2f} dBFS; got {verified_peak_db:.2f} dBFS. "
                "Existing output files have been preserved."
            )

        print(f"  Verified peak : {verified_peak_db:.2f} dBFS")
        print(f"  Audio duration: {format_time(audio_duration)}")
        print(f"  FLAC size     : {format_size(audio_temp)}")

        # Publish only after both outputs have been generated and validated.
        print(f"\n{c('PUBLISHING OUTPUTS', BOLD + GREEN)}")
        pass_a_temp.replace(output)
        audio_temp.replace(audio_output)
        print(f"  Video : {output} ({format_size(output)})")
        print(f"  Audio : {audio_output} ({format_size(audio_output)})")
        print(f"  Total elapsed: {format_elapsed(time.perf_counter() - started_at)}")
        print(c("Done.", BOLD + GREEN))

    finally:

        # ─────────────────────────────────────────────────────────────────────
        # Cleanup
        # ─────────────────────────────────────────────────────────────────────

        shutil.rmtree(work_dir, ignore_errors=True)

        print()
        print(
            f"{c('CLEANUP', DIM)} "
            f"Temporary workspace removed."
        )


if __name__ == "__main__":
    main()
