#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成默认素材 monster_transparent_burst_shake.mov。

来源是 monster_transparent_fast_front_accel.mov（保留它 0.067 秒的爆发窜出与
怪兽自身的实体形变），从第 5 帧起叠加“画外抓点”的物理甩动：绕画面底部支点的
大角度旋转＋大幅平移＋轻微缩放脉冲，弹簧模型带过冲与回弹，方向每 3–5 帧变一次
（约每秒 6–8 次）。与 2026-08-27 被否掉的“单帧抠图整体晃动”不同：这里被甩的是
持续形变中的实体视频，不是一张静止图片。

跑法：python build_burst_shake_asset.py
产物：assets/monster_transparent_burst_shake.mov ＋ 同名 _poster.png、
_metadata.json、_audio.wav；脚本结尾自动回读产物做验收量化（窜出帧、位移分布）。
"""

from __future__ import annotations

import json
import math
import random
import shutil
from fractions import Fraction
from pathlib import Path

import av
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent   # 脚本在 asset-src/，素材在上一级 assets/
SRC = ROOT / "assets" / "monster_transparent_fast_front_accel.mov"
DST = ROOT / "assets" / "monster_transparent_burst_shake.mov"
DST_WEBM = ROOT / "assets" / "monster_transparent_burst_shake.webm"
W, H, FPS = 1344, 768, 30
PIVOT = (672.0, 850.0)  # 底部中央偏下的“手握点”，减少底边裁切穿帮
BURST_END = 5           # 前 5 帧（0.167 秒内）原样保留爆发窜出
RAMP_FRAMES = 4         # 甩动幅度在 4 帧内爬满


def decode_frames(path):
    with av.open(str(path)) as container:
        stream = next(s for s in container.streams if s.type == "video")
        for frame in container.decode(stream):
            conv = frame.reformat(width=W, height=H, format="rgba")
            plane = conv.planes[0]
            raw = bytes(plane)
            row = W * 4
            if plane.line_size != row:
                raw = b"".join(
                    raw[o : o + row]
                    for o in range(0, plane.line_size * H, plane.line_size)
                )
            else:
                raw = raw[: row * H]
            yield Image.frombuffer("RGBA", (W, H), raw, "raw", "RGBA", 0, 1)


def flail_transforms(total):
    """返回每帧 (tx, ty, rot_deg, scale)；前 BURST_END 帧为恒等变换。"""
    rng = random.Random(20260829)
    out = [(0.0, 0.0, 0.0, 1.0)] * min(BURST_END, total)
    tx = ty = rot = vx = vy = vr = 0.0
    scale = 1.0
    ttx = tty = trot = 0.0
    tscale = 1.0
    next_switch = BURST_END
    sign = 1.0
    for i in range(BURST_END, total):
        if i >= next_switch:
            sign = -sign
            ttx = sign * rng.uniform(70, 130)
            tty = rng.uniform(-15, 45)
            trot = -sign * rng.uniform(9, 16)
            tscale = rng.uniform(0.97, 1.06)
            next_switch = i + rng.randint(3, 5)
        ramp = min(1.0, (i - BURST_END + 1) / RAMP_FRAMES)
        k, damp = 0.5, 0.55
        vx = vx * damp + (ttx * ramp - tx) * k
        vy = vy * damp + (tty * ramp - ty) * k
        vr = vr * damp + (trot * ramp - rot) * k
        tx += vx
        ty += vy
        rot += vr
        scale += ((tscale - 1.0) * ramp + 1.0 - scale) * 0.4
        jx = rng.uniform(-4, 4)
        jy = rng.uniform(-4, 4)
        out.append((tx + jx, ty + jy, rot, scale))
    return out


def apply_transform(img, tx, ty, rot_deg, scale):
    if abs(tx) < 0.01 and abs(ty) < 0.01 and abs(rot_deg) < 0.01 and abs(scale - 1) < 0.001:
        return img
    theta = math.radians(rot_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    inv = 1.0 / scale
    a, b = cos_t * inv, sin_t * inv
    d, e = -sin_t * inv, cos_t * inv
    px, py = PIVOT
    cx, cy = px + tx, py + ty
    c = px - (a * cx + b * cy)
    f = py - (d * cx + e * cy)
    # 预乘后再重采样，避免透明区颜色渗入边缘
    pm = img.convert("RGBa")
    moved = pm.transform((W, H), Image.Transform.AFFINE, (a, b, c, d, e, f), resample=Image.Resampling.BILINEAR)
    return moved.convert("RGBA")


def encode(frames):
    out = av.open(str(DST), "w")
    stream = out.add_stream(
        "prores_ks", rate=FPS, options={"profile": "4", "alpha_bits": "8"}
    )
    stream.width, stream.height = W, H
    stream.pix_fmt = "yuva444p10le"
    # ⚠ 所有输出流必须在第一次 mux（写容器头）之前建齐；
    # 事后 add_stream_from_template 会让 libav 原生崩溃（0xC0000094）。
    audio_src = None
    audio_in = None
    audio_out = None
    try:
        audio_src = av.open(str(SRC))
        audio_in = next((s for s in audio_src.streams if s.type == "audio"), None)
        if audio_in is not None:
            audio_out = out.add_stream_from_template(audio_in)
    except Exception as exc:  # noqa: BLE001
        print("音轨准备失败（不影响使用，声音走 _audio.wav）：%s" % exc)
        audio_out = None
    count = 0
    for i, img in enumerate(frames):
        frame = av.VideoFrame(W, H, "rgba")
        assert frame.planes[0].line_size == W * 4
        frame.planes[0].update(img.tobytes())
        frame = frame.reformat(format="yuva444p10le")
        frame.pts = None
        for pkt in stream.encode(frame):
            out.mux(pkt)
        count += 1
    for pkt in stream.encode():
        out.mux(pkt)
    if audio_out is not None:
        try:
            for packet in audio_src.demux(audio_in):
                if packet.dts is None:
                    continue
                packet.stream = audio_out
                out.mux(packet)
        except Exception as exc:  # noqa: BLE001
            print("音轨并入失败（不影响使用，声音走 _audio.wav）：%s" % exc)
    if audio_src is not None:
        audio_src.close()
    out.close()
    return count


def encode_webm(frames):
    """发行版：VP9＋alpha 的 webm，体积约为 ProRes 的十分之一。"""
    out = av.open(str(DST_WEBM), "w")
    stream = out.add_stream(
        "libvpx-vp9",
        rate=FPS,
        options={"crf": "24", "b:v": "0", "row-mt": "1", "cpu-used": "2"},
    )
    stream.width, stream.height = W, H
    stream.pix_fmt = "yuva420p"
    for img in frames:
        frame = av.VideoFrame(W, H, "rgba")
        assert frame.planes[0].line_size == W * 4
        frame.planes[0].update(img.tobytes())
        frame = frame.reformat(format="yuva420p")
        frame.pts = None
        for pkt in stream.encode(frame):
            out.mux(pkt)
    for pkt in stream.encode():
        out.mux(pkt)
    out.close()


def decode_output_frames(path):
    """回读产物；带 alpha 的 webm 必须强制 libvpx-vp9（原生 vp9 解码器丢 alpha）。"""
    with av.open(str(path)) as container:
        stream = next(s for s in container.streams if s.type == "video")
        if stream.codec_context.name == "vp9" and stream.metadata.get("alpha_mode") == "1":
            codec = av.CodecContext.create("libvpx-vp9", "r")
            for packet in container.demux(stream):
                for frame in codec.decode(packet):
                    yield frame
        else:
            for frame in container.decode(stream):
                yield frame


def verify(path):
    boxes = []
    fmt = None
    for frame in decode_output_frames(path):
        if fmt is None:
            fmt = frame.format.name
        conv = frame.reformat(width=W, height=H, format="rgba")
        plane = conv.planes[0]
        raw = bytes(plane)[: W * H * 4]
        img = Image.frombuffer("RGBA", (W, H), raw, "raw", "RGBA", 0, 1)
        boxes.append(img.getchannel("A").getbbox())
    heights = [(b[3] - b[1]) if b else 0 for b in boxes]
    max_h = max(heights)
    full_i = next(i for i, h in enumerate(heights) if h >= 0.95 * max_h)
    centers = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if b else None for b in boxes]
    disps = []
    for i in range(BURST_END + RAMP_FRAMES, len(boxes)):
        if centers[i] and centers[i - 1]:
            dx = centers[i][0] - centers[i - 1][0]
            dy = centers[i][1] - centers[i - 1][1]
            disps.append(math.hypot(dx, dy))
    disps_sorted = sorted(disps)
    median = disps_sorted[len(disps_sorted) // 2]
    p90 = disps_sorted[int(len(disps_sorted) * 0.9)]
    print("产物验收（%s）：%d 帧，像素格式 %s" % (path.name, len(boxes), fmt))
    print("窜出：第 %d 帧（%.3f 秒）达到 95%% 高度" % (full_i, full_i / FPS))
    print(
        "甩动位移（帧间轮廓中心，px）：中位 %.1f，p90 %.1f，最大 %.1f"
        % (median, p90, max(disps))
    )
    ok = full_i <= 6 and median >= 25 and p90 >= 55
    print("判定：%s" % ("达标" if ok else "不达标"))
    return ok


def main():
    if not SRC.is_file():
        raise SystemExit("找不到源素材：%s" % SRC)
    frames = list(decode_frames(SRC))
    print("源帧数：%d" % len(frames))
    transforms = flail_transforms(len(frames))
    shaken = [apply_transform(img, *transforms[i]) for i, img in enumerate(frames)]
    count = encode(iter(shaken))
    print("编码完成：%d 帧 -> %s（%.1f MB）" % (count, DST.name, DST.stat().st_size / 1e6))
    encode_webm(iter(shaken))
    print("编码完成：%s（%.1f MB，发行用）" % (DST_WEBM.name, DST_WEBM.stat().st_size / 1e6))
    shutil.copyfile(
        SRC.with_name(SRC.stem + "_poster.png"),
        DST.with_name(DST.stem + "_poster.png"),
    )
    shutil.copyfile(
        SRC.with_name(SRC.stem + "_audio.wav"),
        DST.with_name(DST.stem + "_audio.wav"),
    )
    # mov 与 webm 同名主干，海报／元数据／音频三件套两个容器共用
    metadata = {
        "width": W,
        "height": H,
        "fps": float(FPS),
        "duration": round(count / FPS, 3),
        "has_alpha": True,
    }
    DST.with_name(DST.stem + "_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print("伴随文件已写：poster / metadata / audio")
    ok = verify(DST)
    ok_webm = verify(DST_WEBM)
    if not (ok and ok_webm):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
