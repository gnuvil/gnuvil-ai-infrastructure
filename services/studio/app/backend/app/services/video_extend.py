"""Longer videos by chaining: the last frame of a finished clip starts the next clip and the clips are joined.

Each extra segment costs a full generation (~9 min on an RTX 3060), and small identity/colour drift can
accumulate from segment to segment, so the number of segments is capped by the caller.
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

from app.core.config import OUTPUTS_DIR

FFMPEG = shutil.which("ffmpeg")


class FfmpegMissingError(RuntimeError):
    pass


def require_ffmpeg() -> str:
    if not FFMPEG:
        raise FfmpegMissingError("ffmpeg를 찾을 수 없습니다. 이어 붙이기(여러 구간)에는 ffmpeg가 필요합니다 (PATH에 추가하세요).")
    return FFMPEG


async def _run(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {err.decode(errors='ignore')[-400:]}")


async def extract_last_frame(video: Path) -> bytes:
    ffmpeg = require_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "last.png"
        await _run(ffmpeg, "-v", "error", "-y", "-sseof", "-0.2", "-i", str(video), "-update", "1", "-q:v", "1", str(out))
        return out.read_bytes()


async def join_segments(segments: list[Path], name: str) -> Path:
    """Concatenate clips, dropping the first frame of every later clip (it repeats the previous clip's last frame)."""
    ffmpeg = require_ffmpeg()
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUTS_DIR / f"{name}_ext.mp4"
    inputs: list[str] = []
    parts = []
    for i, seg in enumerate(segments):
        inputs += ["-i", str(seg)]
        parts.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}]" if i == 0 else f"[{i}:v]trim=start_frame=1,setpts=PTS-STARTPTS[v{i}]")
    chain = "".join(f"[v{i}]" for i in range(len(segments)))
    graph = ";".join(parts) + f";{chain}concat=n={len(segments)}:v=1:a=0[out]"
    await _run(ffmpeg, "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
               "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p", str(out))
    return out


async def probe_video(path: Path) -> dict:
    """Width, height, fps and duration of a clip."""
    import shutil

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise FfmpegMissingError("ffprobe를 찾을 수 없습니다. ffmpeg를 설치하고 PATH에 추가하세요.")
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-of", "json", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"영상을 읽을 수 없습니다: {path.name}")
    import json

    st = json.loads(out)["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return {"width": int(st["width"]), "height": int(st["height"]), "fps": float(num) / float(den), "duration": float(st.get("duration") or 0)}


async def join_scenes(clips: list[Path], transition: str, name: str, fade_seconds: float = 0.5) -> Path:
    """Joins different scenes into one video. transition: "cut" (default) or "fade" (cross-fade).

    Every clip is scaled/padded to the first clip's size and converted to the highest frame rate so mixed
    16/24/32 fps clips line up.
    """
    ffmpeg = require_ffmpeg()
    if not clips:
        raise ValueError("합칠 영상이 없습니다.")
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    infos = [await probe_video(c) for c in clips]
    width, height = infos[0]["width"], infos[0]["height"]
    fps = max(i["fps"] for i in infos)

    inputs: list[str] = []
    prep = []
    for i, clip in enumerate(clips):
        inputs += ["-i", str(clip)]
        prep.append(
            f"[{i}:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v{i}]"
        )

    if len(clips) == 1:
        graph = ";".join(prep) + ";[v0]null[out]"
    elif transition == "fade":
        fade = min(fade_seconds, min(i["duration"] for i in infos) / 2)
        parts = list(prep)
        prev, elapsed = "v0", infos[0]["duration"]
        for i in range(1, len(clips)):
            out_label = f"x{i}" if i < len(clips) - 1 else "out"
            parts.append(f"[{prev}][v{i}]xfade=transition=fade:duration={fade:.3f}:offset={elapsed - fade:.3f}[{out_label}]")
            prev, elapsed = out_label, elapsed + infos[i]["duration"] - fade
        graph = ";".join(parts)
    else:
        chain = "".join(f"[v{i}]" for i in range(len(clips)))
        graph = ";".join(prep) + f";{chain}concat=n={len(clips)}:v=1:a=0[out]"

    out = OUTPUTS_DIR / f"{name}.mp4"
    await _run(ffmpeg, "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
               "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p", "-r", f"{fps}", str(out))
    return out
