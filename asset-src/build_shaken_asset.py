#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把透明怪兽图层合成为快速弹出、强烈甩动的 ProRes 4444 素材。

这个脚本刻意把动作放在本地逐帧控制：生成模型负责提供怪兽外观和声音，
首帧弹出速度、甩动幅度、旋转和惯性由脚本固定下来，便于反复调整。
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import math
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image


DEFAULT_WIDTH = 1344
DEFAULT_HEIGHT = 768
DEFAULT_FPS = 30
DEFAULT_DURATION = 5.0
DEFAULT_POP_SECONDS = 0.17
BASE_SCALE = 0.92


def find_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"找不到 {name}，请确认 FFmpeg 已加入 PATH")
    return path


def lerp(a: float, b: float, amount: float) -> float:
    return a + (b - a) * amount


def make_noise_points(
    rng: random.Random,
    duration: float,
    step: float,
    amplitude: float,
) -> list[tuple[float, float]]:
    """生成带突然变向感的分段随机运动曲线。"""
    count = max(2, math.ceil(duration / step) + 1)
    points = [(0.0, 0.0)]
    for index in range(1, count):
        time = min(duration, index * step)
        points.append((time, rng.uniform(-amplitude, amplitude)))
    if points[-1][0] < duration:
        points.append((duration, rng.uniform(-amplitude, amplitude)))
    return points


def sample_points(points: list[tuple[float, float]], time: float) -> float:
    if time <= points[0][0]:
        return points[0][1]
    if time >= points[-1][0]:
        return points[-1][1]
    for left, right in zip(points, points[1:]):
        left_time, left_value = left
        right_time, right_value = right
        if left_time <= time <= right_time:
            span = max(1e-6, right_time - left_time)
            return lerp(left_value, right_value, (time - left_time) / span)
    return points[-1][1]


def build_motion(duration: float, pop_seconds: float):
    shake_duration = max(0.1, duration - pop_seconds)
    rng = random.Random(20260827)
    return {
        "x": make_noise_points(rng, shake_duration, 0.10, 58.0),
        "y": make_noise_points(rng, shake_duration, 0.11, 34.0),
        "angle": make_noise_points(rng, shake_duration, 0.13, 5.0),
        "scale": make_noise_points(rng, shake_duration, 0.15, 0.018),
    }


def motion_at(
    time: float,
    duration: float,
    pop_seconds: float,
    motion: dict[str, list[tuple[float, float]]],
) -> tuple[float, float, float, float, float]:
    """返回 x、y、旋转角度、横向缩放、纵向缩放。"""
    if time < pop_seconds:
        progress = max(0.0, min(1.0, time / pop_seconds))
        # 先快后稳，确保头部不是在底部停留，而是直接冲上来。
        eased = progress ** 0.62
        return 0.0, eased, 0.0, 1.0, 1.0

    shake_time = time - pop_seconds
    x_noise = sample_points(motion["x"], shake_time)
    y_noise = sample_points(motion["y"], shake_time)
    angle_noise = sample_points(motion["angle"], shake_time)
    scale_noise = sample_points(motion["scale"], shake_time)

    # 低频大摆动叠加高频抖动，再叠加分段随机曲线，形成抓住玩具甩动时的
    # 惯性、过冲和突然变向，而不是规则的平滑正弦波。
    x = (
        105.0 * math.sin(2.0 * math.pi * 3.15 * shake_time + 0.35)
        + 42.0 * math.sin(2.0 * math.pi * 6.6 * shake_time + 1.15)
        + x_noise
    )
    y = (
        38.0 * math.sin(2.0 * math.pi * 4.25 * shake_time + 1.2)
        + 20.0 * math.sin(2.0 * math.pi * 8.4 * shake_time + 0.3)
        + y_noise
    )
    angle = (
        8.5 * math.sin(2.0 * math.pi * 3.0 * shake_time + 0.75)
        + 3.0 * math.sin(2.0 * math.pi * 7.2 * shake_time + 1.7)
        + angle_noise
    )
    squash = 1.0 + 0.022 * math.sin(2.0 * math.pi * 4.1 * shake_time)
    stretch = 1.0 - 0.018 * math.sin(2.0 * math.pi * 4.1 * shake_time)
    return x, y, angle, 1.0 + scale_noise + squash - 1.0, stretch


def load_sprite(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    if alpha.getextrema()[1] == 0:
        raise RuntimeError(f"输入图片没有可见 Alpha：{path}")
    bbox = alpha.point(lambda value: 255 if value >= 8 else 0).getbbox()
    if not bbox:
        raise RuntimeError(f"输入图片没有可见主体：{path}")
    return image.crop(bbox)


def render_frame(
    sprite: Image.Image,
    canvas_size: tuple[int, int],
    time: float,
    duration: float,
    pop_seconds: float,
    motion: dict[str, list[tuple[float, float]]],
) -> Image.Image:
    canvas_width, canvas_height = canvas_size
    base_width = max(1, round(sprite.width * BASE_SCALE))
    base_height = max(1, round(sprite.height * BASE_SCALE))
    base_top = canvas_height - base_height
    base_center_y = base_top + base_height / 2.0
    start_center_y = canvas_height + base_height / 2.0 + 12.0

    x, y, angle, scale_x, scale_y = motion_at(
        time, duration, pop_seconds, motion
    )
    if time < pop_seconds:
        center_y = lerp(start_center_y, base_center_y, y)
        center_x = canvas_width / 2.0
    else:
        center_y = base_center_y + y
        center_x = canvas_width / 2.0 + x

    width = max(1, round(sprite.width * BASE_SCALE * scale_x))
    height = max(1, round(sprite.height * BASE_SCALE * scale_y))
    transformed = sprite.resize((width, height), Image.Resampling.LANCZOS)
    if abs(angle) > 0.01:
        transformed = transformed.rotate(
            angle,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )

    frame = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    left = round(center_x - transformed.width / 2.0)
    top = round(center_y - transformed.height / 2.0)
    frame.alpha_composite(transformed, (left, top))
    return frame


def encode_video(
    frames: Iterable[Image.Image],
    output: Path,
    width: int,
    height: int,
    fps: int,
) -> None:
    ffmpeg = find_tool("ffmpeg")
    command = [
        ffmpeg,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4444",
        "-pix_fmt",
        "yuva444p10le",
        str(output),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    for frame in frames:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace")
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"FFmpeg 编码失败：\n{stderr[-4000:]}")


def mux_audio(video_without_audio: Path, audio_source: Path, output: Path) -> None:
    ffmpeg = find_tool("ffmpeg")
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_without_audio),
        "-i",
        str(audio_source),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0?",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"FFmpeg 合并音频失败：\n{result.stderr[-4000:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-image", type=Path, required=True, help="带 Alpha 的怪兽图片")
    parser.add_argument("--audio-source", type=Path, help="提供怪叫声的源视频")
    parser.add_argument("--output", type=Path, required=True, help="输出 ProRes 4444 视频")
    parser.add_argument("--poster", type=Path, required=True, help="输出透明首帧海报")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION)
    parser.add_argument("--pop-seconds", type=float, default=DEFAULT_POP_SECONDS)
    args = parser.parse_args()

    if args.duration <= args.pop_seconds or args.pop_seconds <= 0:
        raise RuntimeError("--duration 必须大于 --pop-seconds，且两者都必须为正数")
    if not args.source_image.exists():
        raise RuntimeError(f"找不到输入图片：{args.source_image}")
    if args.audio_source and not args.audio_source.exists():
        raise RuntimeError(f"找不到音频源视频：{args.audio_source}")

    sprite = load_sprite(args.source_image)
    frame_count = round(args.duration * args.fps)
    motion = build_motion(args.duration, args.pop_seconds)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.poster.parent.mkdir(parents=True, exist_ok=True)
    first_frame = render_frame(
        sprite,
        (args.width, args.height),
        0.0,
        args.duration,
        args.pop_seconds,
        motion,
    )
    first_frame.save(args.poster, format="PNG")

    def frame_stream():
        for index in range(frame_count):
            yield render_frame(
                sprite,
                (args.width, args.height),
                index / args.fps,
                args.duration,
                args.pop_seconds,
                motion,
            )

    with tempfile.TemporaryDirectory(prefix="monster_shake_") as temp_dir:
        silent_video = Path(temp_dir) / "video_without_audio.mov"
        encode_video(frame_stream(), silent_video, args.width, args.height, args.fps)
        if args.audio_source:
            mux_audio(silent_video, args.audio_source, args.output)
        else:
            shutil.copyfile(silent_video, args.output)

    print(f"已生成：{args.output}")
    print(f"首帧海报：{args.poster}")
    print(f"参数：{args.duration:.2f} 秒，{args.fps} 帧每秒，{args.pop_seconds:.2f} 秒内完成弹出")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
