#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monster Prank 桌面恶作剧工具。

这是一个可见、可取消的桌面叠加工具：控制面板负责设置一次性定时，
到点后在主屏幕底部播放带 Alpha 通道的怪兽视频，播放结束自动关闭。

依赖：Windows 或 macOS、Python 3.10+、Pillow、PyAV。没有 PyAV 时，源码环境仍可回退到 FFmpeg 工具。
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import sys
import wave

from PIL import Image, ImageDraw, ImageTk

import customtkinter as ctk


APP_TITLE = "Monster Prank｜桌面恶作剧"
DEFAULT_VIDEO_NAME = "monster_transparent_burst_shake.webm"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def enable_windows_dpi_awareness():
    """让进程感知 DPI，避免高分屏上画面被系统整体放大导致发虚；返回界面缩放倍率。"""
    if os.name != "nt":
        return 1.0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (OSError, AttributeError):
            return 1.0
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except (OSError, AttributeError):
        return 1.0


def resource_roots():
    """返回源码运行和 PyInstaller 运行时可能存放资源的目录。"""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
    executable = getattr(sys, "executable", None)
    if executable:
        executable_dir = Path(executable).resolve().parent
        candidates.extend(
            [
                executable_dir,
                executable_dir / "_internal",
                executable_dir.parent / "Resources",
                executable_dir.parent / "Resources" / "_internal",
            ]
        )
    candidates.append(Path(__file__).resolve().parent)
    roots = []
    for candidate in candidates:
        if candidate not in roots:
            roots.append(candidate)
    return roots


def resource_path(*parts):
    """找到随程序分发的资源；开发环境下也支持源码目录。"""
    relative = Path(*parts)
    roots = resource_roots()
    for root in roots:
        candidate = root / relative
        if candidate.exists():
            return candidate
    return roots[0] / relative


DEFAULT_VIDEO = resource_path("assets", DEFAULT_VIDEO_NAME)


if os.name == "nt":
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


    class SIZE(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", wintypes.BYTE),
            ("BlendFlags", wintypes.BYTE),
            ("SourceConstantAlpha", wintypes.BYTE),
            ("AlphaFormat", wintypes.BYTE),
        ]


    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]


    class RGBQUAD(ctypes.Structure):
        _fields_ = [
            ("rgbBlue", wintypes.BYTE),
            ("rgbGreen", wintypes.BYTE),
            ("rgbRed", wintypes.BYTE),
            ("rgbReserved", wintypes.BYTE),
        ]


    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def system_idle_seconds():
    """系统距上次键鼠输入的秒数；非 Windows 或读取失败返回 None。"""
    if os.name != "nt":
        return None
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        ticks = ctypes.windll.kernel32.GetTickCount()
    except (OSError, AttributeError):
        return None
    return max(0.0, (ticks - info.dwTime) / 1000.0)


def _win32_api():
    if os.name != "nt":
        raise RuntimeError("Windows 透明叠加窗口只能在 Windows 上创建")
    return ctypes.windll.user32, ctypes.windll.gdi32


def find_tool(name):
    names = [name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        names.insert(0, name + ".exe")
    for root in resource_roots():
        for candidate_name in names:
            bundled = root / "runtime" / candidate_name
            if bundled.is_file():
                return str(bundled)
    path = next((shutil.which(candidate_name) for candidate_name in names if shutil.which(candidate_name)), None)
    if not path:
        raise RuntimeError("找不到 %s，请把 FFmpeg 放入软件包，或加入系统 PATH" % name)
    return path


def load_pyav():
    """按需加载 PyAV；没有它时允许回退到外部 FFmpeg。"""
    try:
        import av
    except (ImportError, OSError):
        return None
    return av


ALPHA_FORMAT_NAMES = ("rgba", "bgra", "argb", "abgr", "ya8", "ya16le", "ya16be")


def metadata_marks_alpha(path):
    """读伴随元数据里的 has_alpha 标记；没有标记时返回 None。"""
    metadata_path = path.with_name(path.stem + "_metadata.json")
    if not metadata_path.is_file():
        return None
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    value = data.get("has_alpha")
    if value is None:
        return None
    return bool(value)


def video_has_alpha(path):
    """检查视频第一路视频流是否带透明通道；读不出来时返回 None。

    带 alpha 的 VP9 webm 探测不到 yuva 格式（alpha 在旁路数据里），
    要靠元数据标记或流上的 alpha_mode 标签识别。
    """
    marker = metadata_marks_alpha(path)
    if marker is not None:
        return marker
    av = load_pyav()
    if av is None:
        return None
    try:
        with av.open(str(path)) as container:
            stream = next(
                (item for item in container.streams if item.type == "video"),
                None,
            )
            if stream is None:
                return None
            if stream.metadata.get("alpha_mode") == "1":
                return True
            if stream.codec_context.format is None:
                return None
            name = stream.codec_context.format.name
    except Exception:
        return None
    return "yuva" in name or name.startswith("gbrap") or name in ALPHA_FORMAT_NAMES


def detect_corner_color(path):
    """采样第一帧四角各一小块，取中位颜色作为抠像键色；读不出来返回 None。"""
    av = load_pyav()
    if av is None:
        return None
    try:
        with av.open(str(path)) as container:
            stream = next(
                (item for item in container.streams if item.type == "video"),
                None,
            )
            if stream is None:
                return None
            frame = next(container.decode(stream), None)
            if frame is None:
                return None
            rgb = frame.reformat(format="rgb24")
            plane = rgb.planes[0]
            raw = bytes(plane)
            width, height, stride = rgb.width, rgb.height, plane.line_size
            patch = 8
            samples = []
            for base_x in (2, max(2, width - patch - 2)):
                for base_y in (2, max(2, height - patch - 2)):
                    for y in range(base_y, min(height, base_y + patch)):
                        row = raw[y * stride : y * stride + width * 3]
                        for x in range(base_x, min(width, base_x + patch)):
                            samples.append(row[x * 3 : x * 3 + 3])
            if not samples:
                return None
            mid = len(samples) // 2
            reds = sorted(s[0] for s in samples)
            greens = sorted(s[1] for s in samples)
            blues = sorted(s[2] for s in samples)
            return "%02X%02X%02X" % (reds[mid], greens[mid], blues[mid])
    except Exception:
        return None


def extract_audio_wav(video_path, wav_path, max_seconds=120):
    """把素材里的音轨抽成 16 位立体声 WAV；没有音轨或失败返回 False。"""
    av = load_pyav()
    if av is None:
        return False
    try:
        with av.open(str(video_path)) as container:
            stream = next(
                (item for item in container.streams if item.type == "audio"),
                None,
            )
            if stream is None:
                return False
            rate = int(stream.codec_context.sample_rate or 48000)
            resampler = av.AudioResampler(format="s16", layout="stereo", rate=rate)
            written = 0
            limit = rate * max_seconds
            with wave.open(str(wav_path), "wb") as out:
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(rate)
                for frame in container.decode(stream):
                    for converted in resampler.resample(frame):
                        out.writeframes(
                            bytes(converted.planes[0])[: converted.samples * 4]
                        )
                        written += converted.samples
                        if written >= limit:
                            return True
            return written > 0
    except Exception:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except OSError:
            pass
        return False


def load_video_metadata(path):
    """读取随素材分发的元数据，避免打包版每次启动都依赖 ffprobe。"""
    metadata_path = path.with_name(path.stem + "_metadata.json")
    if not metadata_path.is_file():
        return None
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        width = int(data["width"])
        height = int(data["height"])
        fps = float(data["fps"])
        duration = float(data["duration"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if width <= 0 or height <= 0 or fps <= 0 or duration <= 0:
        return None
    return width, height, fps, duration


def probe_video(path):
    """读取视频尺寸、帧率和时长。"""
    metadata = load_video_metadata(path)
    if metadata is not None:
        return metadata

    av = load_pyav()
    if av is not None:
        try:
            with av.open(str(path)) as container:
                stream = next(
                    (item for item in container.streams if item.type == "video"),
                    None,
                )
                if stream is None:
                    raise RuntimeError("素材中没有视频流")
                width = int(stream.width or 0)
                height = int(stream.height or 0)
                rate = stream.average_rate or stream.base_rate or stream.guessed_rate
                fps = float(rate) if rate else 24.0
                duration = 0.0
                if container.duration is not None and container.duration > 0:
                    duration = float(container.duration) / float(av.time_base)
                elif stream.duration is not None and stream.time_base is not None:
                    duration = float(stream.duration * stream.time_base)
                if width > 0 and height > 0 and fps > 0 and duration > 0:
                    return width, height, fps, duration
        except Exception:
            pass

    ffprobe = find_tool("ffprobe")
    cmd = [
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration",
        "-of", "json",
        str(path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        creationflags=CREATE_NO_WINDOW,
    )
    data = json.loads(result.stdout)
    stream = (data.get("streams") or [{}])[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    rate = str(stream.get("r_frame_rate") or "24/1")
    if "/" in rate:
        numerator, denominator = rate.split("/", 1)
        fps = float(numerator) / float(denominator or 1)
    else:
        fps = float(rate)
    duration = float((data.get("format") or {}).get("duration") or 0)
    if not width or not height or not fps or not duration:
        raise RuntimeError("无法读取视频尺寸、帧率或时长：%s" % path)
    return width, height, fps, duration


def fit_to_screen(video_width, video_height, screen_width, screen_height):
    """让透明叠加层尽量铺满主屏幕，同时保持视频比例。"""
    scale = min(screen_width / video_width, screen_height / video_height)
    width = max(1, int(round(video_width * scale)))
    height = max(1, int(round(video_height * scale)))
    x = int((screen_width - width) / 2)
    y = screen_height - height
    return width, height, x, y


def load_poster(video_path):
    """读取与视频同名的透明首帧海报；不存在或损坏时返回 None。"""
    poster_path = video_path.with_name(video_path.stem + "_poster.png")
    if not poster_path.is_file():
        return None
    try:
        with Image.open(poster_path) as poster:
            return poster.convert("RGBA").copy()
    except (OSError, ValueError):
        return None


def load_thumbnail(video_path):
    """面板素材卡片用的缩略图。

    优先读同名 `_thumb.png`（画面里能看见怪兽的一帧），没有才退回首帧海报。
    ⛔ 别直接拿海报当缩略图：海报必须等于视频第 0 帧（否则起播会跳），
    而这一版素材的第 0 帧怪兽还没窜进画面，不透明像素只有 1.3%，卡片上是一片空白。
    """
    thumb_path = video_path.with_name(video_path.stem + "_thumb.png")
    if thumb_path.is_file():
        try:
            with Image.open(thumb_path) as thumb:
                return thumb.convert("RGBA").copy()
        except (OSError, ValueError):
            pass
    return load_poster(video_path)


class LayeredOverlay:
    """使用 UpdateLayeredWindow 把 Pillow 的 RGBA 帧写进无边框窗口。"""

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    HWND_TOPMOST = -1
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    DIB_RGB_COLORS = 0
    BI_RGB = 0
    ULW_ALPHA = 0x00000002
    AC_SRC_OVER = 0x00
    AC_SRC_ALPHA = 0x01

    def __init__(self, parent, width, height, x, y, on_escape):
        self.user32, self.gdi32 = _win32_api()
        self.window = tk.Toplevel(parent)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#000000")
        self.window.geometry("%dx%d+%d+%d" % (width, height, x, y))
        self.window.withdraw()
        self.window.bind("<Escape>", lambda _event: on_escape())
        self.window.protocol("WM_DELETE_WINDOW", on_escape)
        self.window.update_idletasks()
        self.hwnd = self.window.winfo_id()
        self.width = width
        self.height = height
        self.x = x
        self.y = y
        self.closed = False
        self.visible = False
        self._enable_layered_style()

    def _enable_layered_style(self):
        old_style = self.user32.GetWindowLongPtrW(self.hwnd, self.GWL_EXSTYLE)
        new_style = old_style | self.WS_EX_LAYERED | self.WS_EX_TOOLWINDOW
        self.user32.SetWindowLongPtrW(self.hwnd, self.GWL_EXSTYLE, new_style)
        self.user32.SetWindowPos(
            self.hwnd,
            self.HWND_TOPMOST,
            self.x,
            self.y,
            self.width,
            self.height,
            self.SWP_NOACTIVATE,
        )

    def show(self):
        if self.closed or self.visible:
            return
        self.user32.SetWindowPos(
            self.hwnd,
            self.HWND_TOPMOST,
            self.x,
            self.y,
            self.width,
            self.height,
            self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW,
        )
        self.window.deiconify()
        self.visible = True
        try:
            self.window.focus_force()
        except tk.TclError:
            pass

    def update(self, image):
        if self.closed:
            return
        # UpdateLayeredWindow 的 AC_SRC_ALPHA 要求预乘 alpha，直通 alpha 会在
        # 半透明边缘产生白色光晕；预乘后再缩放也能避免透明区颜色渗进边缘。
        image = image.convert("RGBA").convert("RGBa")
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height), Image.Resampling.LANCZOS)
        raw = image.tobytes("raw", "BGRa")

        bitmap_info = BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = self.width
        bitmap_info.bmiHeader.biHeight = -self.height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = self.BI_RGB
        bits = ctypes.c_void_p()
        bitmap = self.gdi32.CreateDIBSection(
            0,
            ctypes.byref(bitmap_info),
            self.DIB_RGB_COLORS,
            ctypes.byref(bits),
            0,
            0,
        )
        if not bitmap or not bits.value:
            raise RuntimeError("CreateDIBSection 失败")
        ctypes.memmove(bits, raw, len(raw))
        dc = self.gdi32.CreateCompatibleDC(0)
        previous = self.gdi32.SelectObject(dc, bitmap)
        dst = POINT(self.x, self.y)
        size = SIZE(self.width, self.height)
        src = POINT(0, 0)
        blend = BLENDFUNCTION(
            self.AC_SRC_OVER,
            0,
            255,
            self.AC_SRC_ALPHA,
        )
        ok = self.user32.UpdateLayeredWindow(
            self.hwnd,
            0,
            ctypes.byref(dst),
            ctypes.byref(size),
            dc,
            ctypes.byref(src),
            0,
            ctypes.byref(blend),
            self.ULW_ALPHA,
        )
        self.gdi32.SelectObject(dc, previous)
        self.gdi32.DeleteObject(bitmap)
        self.gdi32.DeleteDC(dc)
        if not ok:
            raise RuntimeError("UpdateLayeredWindow 失败")

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.window.destroy()
            except tk.TclError:
                pass


class MacOverlay:
    """使用 macOS Tk 的透明无边框窗口显示带 Alpha 的帧。"""

    def __init__(self, parent, width, height, x, y, on_escape):
        if sys.platform != "darwin":
            raise RuntimeError("macOS 透明叠加窗口只能在 macOS 上创建")
        self.window = tk.Toplevel(parent)
        self.window.overrideredirect(True)
        self.window.geometry("%dx%d+%d+%d" % (width, height, x, y))
        self.window.withdraw()
        try:
            self.window.attributes("-topmost", True)
            self.window.attributes("-transparent", True)
            self.window.configure(bg="systemTransparent")
        except tk.TclError as exc:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            raise RuntimeError(
                "当前 Python 的 Tk 不支持 macOS 透明窗口，请使用软件包内置版本"
            ) from exc
        self.canvas = tk.Canvas(
            self.window,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            relief="flat",
            bg="systemTransparent",
        )
        self.canvas.pack(fill="both", expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.photo = None
        self.width = width
        self.height = height
        self.x = x
        self.y = y
        self.closed = False
        self.visible = False
        self.window.bind("<Escape>", lambda _event: on_escape())
        self.window.protocol("WM_DELETE_WINDOW", on_escape)
        self.window.update_idletasks()

    def show(self):
        if self.closed or self.visible:
            return
        self.window.deiconify()
        self.window.lift()
        self.window.attributes("-topmost", True)
        self.visible = True
        try:
            self.window.focus_force()
        except tk.TclError:
            pass

    def update(self, image):
        if self.closed:
            return
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image=image.convert("RGBA"))
        self.canvas.itemconfigure(self.image_item, image=self.photo)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.window.destroy()
            except tk.TclError:
                pass


def create_overlay(parent, width, height, x, y, on_escape):
    """按当前系统创建透明播放窗口。"""
    if os.name == "nt":
        return LayeredOverlay(parent, width, height, x, y, on_escape)
    if sys.platform == "darwin":
        return MacOverlay(parent, width, height, x, y, on_escape)
    raise RuntimeError("目前只支持 Windows 和 macOS")


class FrameDecoder:
    """在后台线程解码 BGRA 帧，优先使用 PyAV，必要时回退到 FFmpeg。"""

    def __init__(self, video_path, width, height, chroma=None):
        self.video_path = video_path
        self.width = width
        self.height = height
        self.chroma = chroma  # 绿幕键色（RRGGBB），None 表示不做抠像
        self.frames = queue.Queue(maxsize=4)
        self.stop_event = threading.Event()
        self.process = None
        self.error = None
        self.backend = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        av = load_pyav()
        if av is not None:
            self.backend = "PyAV"
            try:
                self._run_pyav(av)
                return
            except Exception as exc:
                if self.stop_event.is_set():
                    return
                pyav_error = exc
                try:
                    self.backend = "FFmpeg"
                    self._run_ffmpeg()
                    return
                except Exception as fallback_exc:
                    self.error = RuntimeError(
                        "PyAV 解码失败：%s；FFmpeg 回退也失败：%s"
                        % (pyav_error, fallback_exc)
                    )
                    return

        self.backend = "FFmpeg"
        try:
            self._run_ffmpeg()
        except Exception as exc:
            if not self.stop_event.is_set():
                self.error = exc

    def _pyav_decoded_frames(self, av, container, stream):
        """产出原始解码帧。

        带 alpha 的 VP9（webm 流上标着 alpha_mode=1）必须强制 libvpx-vp9 解码：
        FFmpeg 原生 vp9 解码器会丢掉 alpha 旁路数据（实测透明像素直接归零）。
        """
        if (
            stream.codec_context.name == "vp9"
            and stream.metadata.get("alpha_mode") == "1"
        ):
            codec = av.CodecContext.create("libvpx-vp9", "r")
            try:
                codec.thread_count = min(4, os.cpu_count() or 1)
            except Exception:
                pass
            for packet in container.demux(stream):
                for frame in codec.decode(packet):
                    yield frame
        else:
            for frame in container.decode(stream):
                yield frame

    def _pyav_frames(self, av, container, stream):
        """在解码帧之上按需叠加 chromakey 抠像。"""
        decoded = self._pyav_decoded_frames(av, container, stream)
        if not self.chroma:
            for frame in decoded:
                yield frame
            return
        graph = None
        sink = None
        counter = 0
        for frame in decoded:
            if graph is None:
                graph = av.filter.Graph()
                source = graph.add_buffer(template=frame)
                keyer = graph.add("chromakey", "0x%s:0.30:0.15" % self.chroma)
                sink = graph.add("buffersink")
                source.link_to(keyer)
                keyer.link_to(sink)
                graph.configure()
            if frame.pts is None:
                frame.pts = counter
            counter += 1
            graph.push(frame)
            while True:
                try:
                    yield graph.pull()
                except (av.error.BlockingIOError, av.error.EOFError):
                    break

    def _run_pyav(self, av):
        with av.open(str(self.video_path)) as container:
            stream = next(
                (item for item in container.streams if item.type == "video"),
                None,
            )
            if stream is None:
                raise RuntimeError("素材中没有视频流")
            for frame in self._pyav_frames(av, container, stream):
                if self.stop_event.is_set():
                    break
                converted = frame.reformat(
                    width=self.width,
                    height=self.height,
                    format="bgra",
                )
                plane = converted.planes[0]
                row_bytes = self.width * 4
                raw = bytes(plane)
                if plane.line_size == row_bytes:
                    data = raw[: row_bytes * self.height]
                else:
                    required = plane.line_size * self.height
                    if len(raw) < required:
                        raise RuntimeError("PyAV 输出的视频帧数据不完整")
                    data = b"".join(
                        raw[offset : offset + row_bytes]
                        for offset in range(0, required, plane.line_size)
                    )
                self._enqueue(data)

    def _run_ffmpeg(self):
        ffmpeg = find_tool("ffmpeg")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
        # 带 alpha 的 webm 同样要强制 libvpx-vp9（原生 vp9 解码丢 alpha）；
        # 没有 PyAV 的环境靠 metadata 的 has_alpha 标记也能判出来。
        if (
            self.video_path.suffix.lower() == ".webm"
            and video_has_alpha(self.video_path)
        ):
            cmd += ["-c:v", "libvpx-vp9"]
        cmd += ["-i", str(self.video_path)]
        filters = []
        if self.chroma:
            filters.append("chromakey=0x%s:0.30:0.15" % self.chroma)
        filters.append("scale=%d:%d" % (self.width, self.height))
        cmd += [
            "-vf", ",".join(filters),
            "-f", "rawvideo",
            "-pix_fmt", "bgra",
            "-",
        ]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            frame_size = self.width * self.height * 4
            while not self.stop_event.is_set():
                data = self._read_exact(frame_size)
                if data is None:
                    break
                self._enqueue(data)
        finally:
            self._close_process()

    def _enqueue(self, data):
        while not self.stop_event.is_set():
            try:
                self.frames.put(data, timeout=0.1)
                break
            except queue.Full:
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass

    def _read_exact(self, size):
        if not self.process or not self.process.stdout:
            return None
        chunks = []
        remaining = size
        while remaining and not self.stop_event.is_set():
            chunk = self.process.stdout.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining:
            return None
        return b"".join(chunks)

    def get_nowait(self):
        return self.frames.get_nowait()

    def stop(self):
        self.stop_event.set()
        self._close_process()

    def _close_process(self):
        process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass


class OverlayPlayer:
    def __init__(
        self, app, video_path, duration, on_done, video_info=None, poster=None, chroma=None
    ):
        self.app = app
        self.root = app.root
        self.video_path = video_path
        self.duration = duration
        self.on_done = on_done
        self.chroma = chroma
        if video_info is None:
            video_info = probe_video(video_path)
        self.width, self.height, self.fps, self.source_duration = video_info
        if duration > self.source_duration + 0.1:
            raise ValueError("当前透明素材只有 %.2f 秒，播放时长不能超过素材长度" % self.source_duration)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.out_width, self.out_height, self.x, self.y = fit_to_screen(
            self.width,
            self.height,
            screen_width,
            screen_height,
        )
        self.overlay = None
        self.decoder = None
        self.audio_process = None
        self.audio_backend = None
        self.audio_path = self.video_path.with_name(self.video_path.stem + "_audio.wav")
        self.audio_started = False
        self.poster = poster
        self.frame_job = None
        self.stop_job = None
        self.stopped = False
        self.prepared = False    # prepare() 已做完重活（窗口、解码器、海报）
        self.live = False        # go() 已开演（窗口可见、音频、计时）
        self.poster_ready = False
        self.play_start = None   # 第一帧真正显示的墙钟时刻
        self.frames_shown = 0    # 已消费的解码帧数（跳帧追赶时含被丢弃的）

    def start(self):
        self.prepare()
        self.go()

    def prepare(self):
        """预热：把全部重活提前做完，全程不可见——创建层窗口（不 show）、
        启动解码线程把队列填满、海报预先缩放/预乘/画进窗口位图。
        触发前几秒调它，到点 go() 只剩 ShowWindow，首帧不再卡。"""
        if self.prepared:
            return
        self.overlay = create_overlay(
            self.root,
            self.out_width,
            self.out_height,
            self.x,
            self.y,
            self.stop,
        )
        # 解码线程直接出叠加层尺寸的帧（C 层缩放），主循环不再每帧做 PIL 重采样。
        self.decoder = FrameDecoder(
            self.video_path, self.out_width, self.out_height, chroma=self.chroma
        )
        self.decoder.start()
        poster = self.poster if self.poster is not None else load_poster(self.video_path)
        if poster is not None:
            # update 只画位图不显示窗口，重的缩放＋预乘＋memcpy 都发生在这里
            self.overlay.update(poster)
            self.poster_ready = True
        self.prepared = True

    def go(self):
        """开演：显示窗口＋启动音频＋按时间戳调度帧。prepare() 之后这里只剩轻活。"""
        if self.stopped or self.live:
            return
        self.live = True
        if self.poster_ready:
            self.overlay.show()
            self._start_audio_once()
        self.frame_job = self.root.after(0, self._tick)
        self.stop_job = self.root.after(int(self.duration * 1000), self.stop)

    def discard(self):
        """丢弃一个预热好但还没开演的播放器：清资源，但不触发完成回调。"""
        self.on_done = lambda: None
        self.stop()

    def _start_audio_once(self):
        if self.audio_started:
            return
        self.audio_started = True
        self._start_audio()

    def _start_audio(self):
        if self.audio_path.is_file() and os.name == "nt":
            try:
                import winsound

                winsound.PlaySound(
                    str(self.audio_path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC,
                )
                self.audio_backend = "winsound"
                return
            except (OSError, RuntimeError):
                self.audio_backend = None

        if self.audio_path.is_file() and sys.platform == "darwin":
            afplay = shutil.which("afplay")
            if afplay:
                cmd = [
                    afplay,
                    "-t", "%.3f" % self.duration,
                    str(self.audio_path),
                ]
                try:
                    self.audio_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=CREATE_NO_WINDOW,
                    )
                    self.audio_backend = "afplay"
                    return
                except OSError:
                    self.audio_process = None

        try:
            ffplay = find_tool("ffplay")
        except RuntimeError:
            return
        cmd = [
            ffplay,
            "-hide_banner",
            "-loglevel", "quiet",
            "-nodisp",
            "-autoexit",
            "-vn",
            "-t", "%.3f" % self.duration,
            str(self.video_path),
        ]
        try:
            self.audio_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError:
            self.audio_process = None

    def _show_frame(self, data):
        image = Image.frombuffer(
            "RGBA",
            (self.out_width, self.out_height),
            data,
            "raw",
            "BGRA",
            0,
            1,
        )
        self.overlay.update(image)
        if not self.overlay.visible:
            self.overlay.show()
            self._start_audio_once()

    def _tick(self):
        """按墙钟时间戳调度帧：以首帧显示时刻为零点，算出此刻该到第几帧，
        落后就消费多帧只显示最后一帧（跳帧追赶），不再按固定间隔漂移。"""
        if self.stopped:
            return
        if self.decoder.error is not None and self.decoder.frames.empty():
            self.app.set_status("播放窗口失败：%s" % self.decoder.error)
            self.stop()
            return
        try:
            if self.play_start is None:
                data = self.decoder.get_nowait()
                self._show_frame(data)
                self.play_start = time.perf_counter()
                self.frames_shown = 1
            else:
                target = int((time.perf_counter() - self.play_start) * self.fps) + 1
                while self.frames_shown < target:
                    data = self.decoder.get_nowait()
                    self.frames_shown += 1
                    if self.frames_shown >= target:
                        self._show_frame(data)
        except queue.Empty:
            pass
        except (RuntimeError, tk.TclError) as exc:
            self.app.set_status("播放窗口失败：%s" % exc)
            self.stop()
            return
        if self.play_start is None:
            delay = 10
        else:
            next_at = self.play_start + self.frames_shown / self.fps
            delay = max(1, int((next_at - time.perf_counter()) * 1000))
        self.frame_job = self.root.after(delay, self._tick)

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        if self.frame_job:
            try:
                self.root.after_cancel(self.frame_job)
            except tk.TclError:
                pass
        if self.stop_job:
            try:
                self.root.after_cancel(self.stop_job)
            except tk.TclError:
                pass
        if self.decoder:
            self.decoder.stop()
        if self.audio_backend == "winsound":
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except (OSError, RuntimeError):
                pass
            self.audio_backend = None
        if self.audio_process and self.audio_process.poll() is None:
            try:
                self.audio_process.terminate()
            except OSError:
                pass
        if self.overlay:
            self.overlay.close()
        self.on_done()


class ControlApp:
    def _apply_window_icon(self):
        """给面板窗口挂上怪兽图标。

        打包后素材被拍平到 `assets/`，源码运行时它在 `assets/logo/`，两边都试一遍。
        ⌒ 挂不上就算了，一个图标不值得把程序弄崩。
        """
        for parts in (("assets", "logo.ico"), ("assets", "logo", "logo.ico")):
            icon = resource_path(*parts)
            if os.name == "nt" and icon.is_file():
                try:
                    self.root.iconbitmap(default=str(icon))
                    return
                except tk.TclError:
                    break
        for parts in (("assets", "logo-128.png"), ("assets", "logo", "logo-128.png")):
            png = resource_path(*parts)
            if png.is_file():
                try:
                    self._window_icon = tk.PhotoImage(file=str(png))
                    self.root.iconphoto(True, self._window_icon)
                except (tk.TclError, OSError):
                    pass
                return

    def __init__(self, video_path, demo=False, chroma=None):
        self.ui_scale = enable_windows_dpi_awareness()
        # 面板配色（暖米底＋白卡片＋怪兽紫点缀）
        self.BG = "#eeeae2"
        self.CARD = "#ffffff"
        self.BORDER = "#e0dacd"
        self.FIELD = "#faf8f3"
        self.INK = "#27222f"
        self.SUB = "#8b8496"
        self.ACCENT = "#5b3fc4"
        self.ACCENT_HOVER = "#6c50d8"
        self.BANNER = "#231b3a"
        ctk.set_appearance_mode("light")
        self.root = ctk.CTk(fg_color=self.BG)
        self.root.title(APP_TITLE)
        self._apply_window_icon()
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind_all("<Escape>", self._on_escape)
        self.video_path = Path(video_path).resolve()
        self.demo = demo
        self.chroma_color = chroma
        self.scheduled_run = False
        self.schedule_job = None
        self.countdown_job = None
        self.prewarm_job = None
        self.player = None
        self.video_info = None
        self.poster_image = None
        self.thumb_image = None
        self.activity_state = None      # None / waiting_idle / armed / returning
        self.activity_job = None
        self.activity_fire_time = None
        self.stealth = False            # 待命隐身：面板完全藏起，Ctrl+Alt+M 召回
        self.stealth_job = None
        self.hotkey_job = None
        self.idle_var = tk.StringVar(value="60")
        self.return_var = tk.StringVar(value="8")
        self.time_var = tk.StringVar(
            value=(datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        )
        self.duration_var = tk.StringVar(value="5.0")
        self.status_var = tk.StringVar(value="就绪。可以立即预览，或设置一次性定时。")
        self.countdown_var = tk.StringVar(value="未设置定时")
        self.video_var = tk.StringVar(value=self.video_path.name)
        self.minutes_var = tk.StringVar(value="3")
        self.material_info_var = tk.StringVar(value="正在读取素材信息……")
        self._thumb_photo = None
        self._build_ui()
        if os.name == "nt":
            self.hotkey_job = self.root.after(300, self._hotkey_poll)
        self._preload_thread = threading.Thread(target=self._preload_assets, daemon=True)
        self._preload_thread.start()

    def _preload_assets(self):
        try:
            self.video_info = probe_video(self.video_path)
            self.poster_image = load_poster(self.video_path)
            self.thumb_image = load_thumbnail(self.video_path)
        except Exception:  # noqa: BLE001
            self.video_info = None
            self.poster_image = None
            self.thumb_image = None
        try:
            self.root.after(0, self._refresh_material_card)
        except (tk.TclError, RuntimeError):
            pass

    def _refresh_material_card(self):
        """把当前素材的缩略图与规格刷到素材卡片上。"""
        source = self.thumb_image if self.thumb_image is not None else self.poster_image
        if source is not None:
            thumb = source.copy().convert("RGBA")
            thumb.thumbnail((108, 60), Image.Resampling.LANCZOS)
            mask = Image.new("L", thumb.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, thumb.size[0] - 1, thumb.size[1] - 1], radius=8, fill=255
            )
            # 圆角遮罩与素材自带的透明度相乘，别把透明背景糊成实心
            from PIL import ImageChops

            thumb.putalpha(ImageChops.multiply(thumb.getchannel("A"), mask))
            self._thumb_photo = ctk.CTkImage(
                light_image=thumb, dark_image=thumb, size=thumb.size
            )
            self.thumb_label.configure(image=self._thumb_photo, text="")
        else:
            self._thumb_photo = None
            try:
                self.thumb_label.configure(image=None, text="👾")
            except (ValueError, tk.TclError):
                pass
        if self.video_info is not None:
            width, height, _fps, duration = self.video_info
            self.material_info_var.set("%.1f 秒 · %d×%d" % (duration, width, height))
        else:
            self.material_info_var.set("素材信息读取中……")

    def _font(self, size, bold=False):
        return ctk.CTkFont(family="Segoe UI", size=size, weight="bold" if bold else "normal")

    def _make_button(self, parent, text, command, primary=False, small=False):
        if primary:
            base, fg, hover, border = self.ACCENT, "#ffffff", self.ACCENT_HOVER, 0
        else:
            base, fg, hover, border = "#ffffff", self.INK, "#f2eee5", 1
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            corner_radius=9,
            height=26 if small else 34,
            width=0,
            fg_color=base,
            text_color=fg,
            hover_color=hover,
            border_width=border,
            border_color=self.BORDER,
            font=self._font(12 if small else 13, bold=primary),
        )

    def _make_entry(self, parent, var, width):
        return ctk.CTkEntry(
            parent,
            textvariable=var,
            width=width,
            height=28,
            corner_radius=8,
            fg_color=self.FIELD,
            border_color=self.BORDER,
            border_width=1,
            text_color=self.INK,
            font=self._font(13),
        )

    def _make_stepper(self, parent, var, lo, hi, step, width=52):
        """[−] 数值 [＋] 三件套，替代原生 Spinbox。"""
        box = ctk.CTkFrame(parent, fg_color="transparent")

        def bump(sign):
            try:
                value = float(var.get())
            except (ValueError, tk.TclError):
                value = lo
            value = min(hi, max(lo, value + sign * step))
            var.set("%g" % round(value, 1))

        def side_btn(text, sign):
            return ctk.CTkButton(
                box,
                text=text,
                command=lambda: bump(sign),
                width=26,
                height=26,
                corner_radius=8,
                fg_color="#efece4",
                text_color=self.INK,
                hover_color="#e2dccd",
                font=self._font(13, bold=True),
            )

        side_btn("−", -1).pack(side="left")
        ctk.CTkEntry(
            box,
            textvariable=var,
            width=width,
            height=26,
            corner_radius=8,
            fg_color=self.FIELD,
            border_color=self.BORDER,
            border_width=1,
            text_color=self.INK,
            justify="center",
            font=self._font(13),
        ).pack(side="left", padx=3)
        side_btn("＋", 1).pack(side="left")
        return box

    def _build_ui(self):
        banner = ctk.CTkFrame(self.root, fg_color=self.BANNER, corner_radius=0)
        banner.pack(fill="x")
        ctk.CTkLabel(
            banner, text="👾  Monster Prank", text_color="#f6f3ff",
            fg_color="transparent", font=self._font(24, bold=True),
        ).pack(anchor="w", padx=24, pady=(15, 0))
        ctk.CTkLabel(
            banner, text="透明怪兽桌面恶作剧｜可见、可取消、按 Esc 随时关",
            text_color="#b3a4e6", fg_color="transparent", font=self._font(12),
        ).pack(anchor="w", padx=25, pady=(0, 13))

        outer = ctk.CTkFrame(self.root, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=16, pady=13)

        def card(title):
            box = ctk.CTkFrame(
                outer, fg_color=self.CARD, corner_radius=14,
                border_width=1, border_color=self.BORDER,
            )
            box.pack(fill="x", pady=(0, 9))
            ctk.CTkLabel(
                box, text=title, text_color=self.SUB, fg_color="transparent",
                font=self._font(12, bold=True), anchor="w",
            ).pack(fill="x", padx=15, pady=(10, 0))
            body = ctk.CTkFrame(box, fg_color="transparent")
            body.pack(fill="x", padx=15, pady=(3, 12))
            return body

        def cap(parent, text):
            return ctk.CTkLabel(
                parent, text=text, text_color=self.INK,
                fg_color="transparent", font=self._font(13),
            )

        def hint(parent, text):
            return ctk.CTkLabel(
                parent, text=text, text_color=self.SUB,
                fg_color="transparent", font=self._font(11),
            )

        row_frame = lambda parent, top=0: (
            frame := ctk.CTkFrame(parent, fg_color="transparent"),
            frame.pack(fill="x", pady=(top, 0)),
        )[0]

        timer = card("⏰  定时惊吓 · 点一个就开始倒计时")
        rowq = row_frame(timer)
        for label, secs in (
            ("30 秒后", 30),
            ("1 分钟后", 60),
            ("3 分钟后", 180),
            ("5 分钟后", 300),
            ("10 分钟后", 600),
        ):
            self._make_button(
                rowq, label, lambda sec=secs: self.schedule_in(sec), small=True
            ).pack(side="left", padx=(0, 6))
        rowc = row_frame(timer, top=9)
        cap(rowc, "或自定义").pack(side="left")
        self._make_stepper(rowc, self.minutes_var, 1, 720, 1).pack(
            side="left", padx=(8, 0)
        )
        cap(rowc, "分钟后").pack(side="left", padx=(6, 0))
        self._make_button(rowc, "开始倒计时", self.schedule_in, small=True).pack(
            side="left", padx=(10, 0)
        )
        rowt = row_frame(timer, top=9)
        cap(rowt, "或精确时刻").pack(side="left")
        self._make_entry(rowt, self.time_var, 176).pack(side="left", padx=(8, 0))
        self._make_button(rowt, "按时刻设定", self.schedule, small=True).pack(
            side="left", padx=(10, 0)
        )
        hint(rowt, "YYYY-MM-DD HH:MM[:SS]").pack(side="left", padx=(8, 0))

        if os.name == "nt":
            activity = card("🚶  回场惊吓 · 无人时布防，人回来再触发")
            row = row_frame(activity)
            cap(row, "无人用满").pack(side="left")
            self._make_stepper(row, self.idle_var, 10, 86400, 10).pack(
                side="left", padx=6
            )
            cap(row, "秒后布防，人回来").pack(side="left")
            self._make_stepper(row, self.return_var, 1, 300, 1, width=40).pack(
                side="left", padx=6
            )
            cap(row, "秒后触发").pack(side="left")
            self.activity_button = self._make_button(
                row, "布防", self.toggle_activity_trigger, small=True
            )
            self.activity_button.pack(side="left", padx=(12, 0))
            hint(activity, "布防后面板彻底隐身（任务栏也看不到），按 Ctrl+Alt+M 召回").pack(
                anchor="w", pady=(5, 0)
            )

        material = card("🎬  怪兽素材与播放")
        rowm = row_frame(material)
        holder = ctk.CTkFrame(
            rowm, fg_color="#efece4", corner_radius=10, width=116, height=66,
            border_width=1, border_color=self.BORDER,
        )
        holder.pack_propagate(False)
        holder.pack(side="left")
        self.thumb_label = ctk.CTkLabel(
            holder, text="👾", fg_color="transparent", font=self._font(20)
        )
        self.thumb_label.pack(expand=True)
        meta = ctk.CTkFrame(rowm, fg_color="transparent")
        meta.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(
            meta, textvariable=self.video_var, text_color=self.INK,
            fg_color="transparent", font=self._font(13, bold=True),
            anchor="w", width=210,
        ).pack(anchor="w")
        ctk.CTkLabel(
            meta, textvariable=self.material_info_var, text_color=self.SUB,
            fg_color="transparent", font=self._font(11), anchor="w",
        ).pack(anchor="w", pady=(2, 0))
        self._make_button(rowm, "选择视频…", self.choose_video, small=True).pack(side="right")
        rowd = row_frame(material, top=9)
        cap(rowd, "播放").pack(side="left")
        self._make_stepper(rowd, self.duration_var, 0.5, 60, 0.5, width=46).pack(
            side="left", padx=(8, 0)
        )
        cap(rowd, "秒").pack(side="left", padx=(6, 0))
        hint(rowd, "上限＝素材长度；绿幕视频可自动抠像，自带音轨自动提取").pack(
            side="left", padx=(10, 0)
        )

        buttons = ctk.CTkFrame(outer, fg_color="transparent")
        buttons.pack(fill="x", pady=(4, 0))
        self._make_button(buttons, "先吓自己试试", self.preview, primary=True).pack(side="left")
        self._make_button(buttons, "取消定时", self.cancel_schedule).pack(side="left", padx=(8, 0))
        self._make_button(buttons, "退出", self.close).pack(side="right")

        wrap = 560
        ctk.CTkLabel(
            outer, textvariable=self.countdown_var, text_color=self.ACCENT,
            fg_color="transparent", font=self._font(13, bold=True), anchor="w",
        ).pack(anchor="w", pady=(12, 2))
        ctk.CTkLabel(
            outer, textvariable=self.status_var, text_color="#565064",
            fg_color="transparent", font=self._font(12),
            wraplength=wrap, justify="left", anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            outer,
            text="安全提示：不注册开机启动，不创建隐藏计划任务；关闭面板即取消全部定时与布防。",
            text_color="#9a7b3d", fg_color="transparent",
            font=self._font(11), wraplength=wrap, justify="left", anchor="w",
        ).pack(anchor="w", pady=(10, 0))

    def set_status(self, text):
        self.status_var.set(text)

    def _on_escape(self, _event=None):
        if self.player:
            self.player.stop()
        return "break"

    def parse_duration(self):
        try:
            duration = float(self.duration_var.get().strip())
        except ValueError:
            raise ValueError("播放时长必须是数字")
        if not 0.2 <= duration <= 60:
            raise ValueError("播放时长请设置在 0.2 到 60 秒之间")
        if self.video_info is not None:
            source_duration = self.video_info[3]
            if duration > source_duration + 0.1:
                raise ValueError("当前素材只有 %.2f 秒，请把播放时长设为不超过素材长度" % source_duration)
        return duration

    def _build_player(self):
        return OverlayPlayer(
            self,
            self.video_path,
            self.parse_duration(),
            self._playback_done,
            video_info=self.video_info,
            poster=self.poster_image,
            chroma=self.chroma_color,
        )

    def _prewarm_for_trigger(self):
        """触发前静默预热：窗口、解码器、海报全部备好但不可见。
        失败不致命——触发时走现场路径，只是首帧会有小顿。"""
        self.prewarm_job = None
        if self.player is not None:
            return
        try:
            player = self._build_player()
            player.prepare()
            self.player = player
        except Exception:  # noqa: BLE001
            self.player = None

    def _discard_prewarmed(self):
        """丢掉预热好但还没开演的播放器（取消定时/撤防时用）。"""
        if self.prewarm_job:
            try:
                self.root.after_cancel(self.prewarm_job)
            except tk.TclError:
                pass
            self.prewarm_job = None
        if self.player is not None and not self.player.live:
            player = self.player
            self.player = None
            player.discard()

    def preview(self):
        player = self.player
        self.player = None
        try:
            if player is None or not player.prepared or player.stopped or player.live:
                if player is not None:
                    player.discard()
                player = self._build_player()
                player.prepare()
            self.set_status("预览播放中。按 Esc 可立即关闭。")
            # 先隐藏控制面板再开播，怪兽画面里任何时刻都不能出现“恶作剧软件”本身。
            self.root.withdraw()
            self.player = player
            player.go()
        except Exception as exc:  # noqa: BLE001
            if player is not None:
                player.discard()
            self.player = None
            self._restore_panel()
            messagebox.showerror("无法预览", str(exc), parent=self.root)
            self.set_status("预览失败：%s" % exc)

    def _restore_panel(self):
        try:
            if self.root.state() == "withdrawn":
                self.root.deiconify()
        except tk.TclError:
            pass

    def choose_video(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择透明视频素材",
            filetypes=[
                ("视频文件", "*.mov *.mp4 *.webm *.mkv *.avi"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        chosen = Path(path).resolve()
        self.video_path = chosen
        self.video_info = None
        self.poster_image = None
        self.thumb_image = None
        self.chroma_color = None
        self.video_var.set(chosen.name)
        self.material_info_var.set("正在读取素材信息……")
        self._thumb_photo = None
        try:
            self.thumb_label.configure(image=None, text="👾")
        except (ValueError, tk.TclError):
            pass
        self.set_status("正在读取素材信息：%s" % chosen.name)
        threading.Thread(
            target=self._inspect_custom_video, args=(chosen,), daemon=True
        ).start()

    def _inspect_custom_video(self, path):
        try:
            info = probe_video(path)
        except Exception as exc:  # noqa: BLE001
            message = "读取素材失败：%s" % exc
            self.root.after(0, lambda: self.set_status(message))
            return
        poster = load_poster(path)
        thumb = load_thumbnail(path)
        has_alpha = video_has_alpha(path)
        corner = detect_corner_color(path) if has_alpha is False else None
        warnings = []
        notes = []
        audio_companion = path.with_name(path.stem + "_audio.wav")
        if not audio_companion.is_file():
            # 素材自带音轨时替用户抽出来，抽不出来才警告没声音
            if extract_audio_wav(path, audio_companion):
                notes.append("已从素材里提取声音（%s）" % audio_companion.name)
            else:
                warnings.append(
                    "没找到同名 %s_audio.wav，素材里也没抽出音轨，播放将没有声音"
                    % path.stem
                )
        if path != self.video_path:
            return
        self.video_info = info
        self.poster_image = poster
        self.thumb_image = thumb
        text = "素材就绪：%s（%.2f 秒）" % (path.name, info[3])
        if notes:
            text += "。" + "；".join(notes) + "。"
        if warnings:
            text += "注意：" + "；".join(warnings) + "。"

        def apply():
            if path != self.video_path:
                return
            try:
                if float(self.duration_var.get().strip()) > info[3]:
                    self.duration_var.set("%.1f" % info[3])
            except (ValueError, tk.TclError):
                pass
            self._refresh_material_card()
            self.set_status(text)
            if has_alpha is False:
                color_text = ("#" + corner) if corner else "绿色（#00FF00）"
                if messagebox.askyesno(
                    "按绿幕素材抠像？",
                    "这个视频没有透明通道。\n\n"
                    "要按绿幕素材处理吗？画面边角检测到的背景色 %s 会被抠成透明。\n\n"
                    "选“否”则原样播放（一整块不透明画面，恶作剧效果会穿帮）。"
                    % color_text,
                    parent=self.root,
                ):
                    self.chroma_color = corner or "00FF00"
                    self.set_status(
                        "%s已开启绿幕抠像，键色 #%s。" % (text, self.chroma_color)
                    )
                else:
                    self.set_status(
                        "%s注意：素材没有透明通道，将原样整块播放。" % text
                    )

        self.root.after(0, apply)

    def schedule(self):
        """按精确时刻设定（“按时刻”按钮）。"""
        try:
            target = self._parse_datetime(self.time_var.get().strip())
            if target <= datetime.now():
                raise ValueError("启动时间必须晚于当前时间")
        except ValueError as exc:
            messagebox.showerror("定时设置有误", str(exc), parent=self.root)
            return
        self._schedule_target(target)

    def schedule_in(self, seconds=None):
        """按“多久之后”设定——快捷按钮直接给秒数；不给则读自定义分钟数。"""
        if seconds is None:
            try:
                minutes = float(self.minutes_var.get().strip())
                if not 0.1 <= minutes <= 720:
                    raise ValueError("自定义分钟数请设在 0.1 到 720 之间")
            except ValueError as exc:
                text = str(exc) if "720" in str(exc) else "自定义分钟数必须是数字"
                messagebox.showerror("定时设置有误", text, parent=self.root)
                return
            seconds = minutes * 60
        self._schedule_target(datetime.now() + timedelta(seconds=seconds))

    def _schedule_target(self, target):
        try:
            self.parse_duration()
        except ValueError as exc:
            messagebox.showerror("定时设置有误", str(exc), parent=self.root)
            return
        if self.schedule_job:
            self.cancel_schedule()
        self._disarm_activity(quiet=True)  # 定时与回场触发互斥，后设的生效
        self._discard_prewarmed()
        delay_ms = max(1, int((target - datetime.now()).total_seconds() * 1000))
        self.schedule_job = self.root.after(delay_ms, self._scheduled_start)
        # 到点前 5 秒静默预热，首帧不卡（定时不足 5 秒就立刻热）
        self.prewarm_job = self.root.after(
            max(0, delay_ms - 5000), self._prewarm_for_trigger
        )
        remaining = max(1, int(round((target - datetime.now()).total_seconds())))
        if remaining >= 60:
            when = "%d 分 %d 秒后" % divmod(remaining, 60)
        else:
            when = "%d 秒后" % remaining
        if os.name == "nt":
            self.set_status(
                "定时已设置，%s启动。面板 3 秒后自动隐身（任务栏也看不到），"
                "按 Ctrl+Alt+M 随时召回。" % when
            )
            self.stealth_job = self.root.after(3000, self._enter_stealth)
        else:
            self.set_status(
                "定时已设置，%s启动。可以把面板最小化，定时照常触发；关闭面板即取消。" % when
            )
        self._update_countdown(target)

    def _scheduled_start(self):
        self.schedule_job = None
        self.prewarm_job = None
        self.stealth = False  # 进入播放态，热键不再弹面板
        if self.countdown_job:
            try:
                self.root.after_cancel(self.countdown_job)
            except tk.TclError:
                pass
            self.countdown_job = None
        self.countdown_var.set("已到时，正在启动")
        # 定时触发的播放结束后整个程序退出：面板弹回来会当场拆穿恶作剧。
        self.scheduled_run = True
        self.preview()

    def _update_countdown(self, target):
        if not self.schedule_job:
            return
        remaining = max(0, int((target - datetime.now()).total_seconds()))
        minutes, seconds = divmod(remaining, 60)
        hours, minutes = divmod(minutes, 60)
        self.countdown_var.set("距离启动：%02d:%02d:%02d" % (hours, minutes, seconds))
        self.countdown_job = self.root.after(1000, lambda: self._update_countdown(target))

    def cancel_schedule(self):
        self._cancel_stealth(recall=True)
        if self.schedule_job:
            try:
                self.root.after_cancel(self.schedule_job)
            except tk.TclError:
                pass
            self.schedule_job = None
        if self.countdown_job:
            try:
                self.root.after_cancel(self.countdown_job)
            except tk.TclError:
                pass
            self.countdown_job = None
        self._discard_prewarmed()
        self.countdown_var.set("未设置定时")
        self.set_status("定时已取消。")

    # ---- 待命隐身：面板彻底藏起（任务栏也看不到），Ctrl+Alt+M 召回 ----

    def _enter_stealth(self):
        """待命隐身。⛔ 用 withdraw 不用 iconify——最小化在任务栏留着
        “Monster Prank”，被恶作剧的人一眼看到就关掉了。"""
        self.stealth_job = None
        if os.name != "nt":
            return  # 没有热键召回的平台不自动藏，免得叫不回来
        if self.schedule_job is None and self.activity_state is None:
            return  # 没有待命中的触发，不藏
        self.stealth = True
        try:
            self.root.withdraw()
        except tk.TclError:
            pass

    def _recall_panel(self):
        self.stealth = False
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass
        self.set_status("面板已召回。定时／布防保持不变，要取消请点对应按钮。")

    def _cancel_stealth(self, recall=False):
        if self.stealth_job:
            try:
                self.root.after_cancel(self.stealth_job)
            except tk.TclError:
                pass
            self.stealth_job = None
        was_hidden = self.stealth
        self.stealth = False
        if recall and was_hidden:
            try:
                self.root.deiconify()
            except tk.TclError:
                pass

    def _hotkey_poll(self):
        """轮询 Ctrl+Alt+M：只在待命隐身时响应，别的时候不抢面板。"""
        if self.stealth and os.name == "nt":
            user32 = ctypes.windll.user32
            pressed = lambda vk: bool(user32.GetAsyncKeyState(vk) & 0x8000)
            if pressed(0x11) and pressed(0x12) and pressed(0x4D):  # Ctrl+Alt+M
                self._recall_panel()
        self.hotkey_job = self.root.after(300, self._hotkey_poll)

    # ---- 回场触发：无人使用满 N 秒后布防，检测到有人回来 M 秒后弹怪兽 ----

    def toggle_activity_trigger(self):
        if self.activity_state is not None:
            self._disarm_activity()
            self._cancel_stealth(recall=True)
            self._discard_prewarmed()
            self.set_status("回场触发已撤防。")
            return
        try:
            try:
                idle_need = float(self.idle_var.get().strip())
                delay = float(self.return_var.get().strip())
            except ValueError:
                raise ValueError("布防参数必须是数字")
            if not 10 <= idle_need <= 86400:
                raise ValueError("布防等待请设在 10 到 86400 秒之间")
            if not 1 <= delay <= 300:
                raise ValueError("回来后的触发延迟请设在 1 到 300 秒之间")
            self.parse_duration()
        except ValueError as exc:
            messagebox.showerror("回场触发设置有误", str(exc), parent=self.root)
            return
        if system_idle_seconds() is None:
            messagebox.showerror(
                "回场触发不可用", "读取系统输入状态失败（仅支持 Windows）。",
                parent=self.root,
            )
            return
        self.cancel_schedule()
        self.activity_state = "waiting_idle"
        self.activity_button.configure(text="撤防")
        self.set_status(
            "回场触发已布防：无人使用满 %d 秒后进入监听（面板届时彻底隐身，"
            "按 Ctrl+Alt+M 召回），此后有人回来 %d 秒后触发。" % (idle_need, delay)
        )
        self._activity_poll()

    def _activity_poll(self):
        self.activity_job = None
        if self.activity_state is None:
            return
        idle = system_idle_seconds()
        if idle is None:
            self._disarm_activity()
            self._discard_prewarmed()
            self.set_status("读取系统输入状态失败，回场触发已撤防。")
            return
        try:
            idle_need = float(self.idle_var.get().strip())
            delay = float(self.return_var.get().strip())
        except ValueError:
            self._disarm_activity()
            self._discard_prewarmed()
            self.set_status("布防参数被改成了非数字，回场触发已撤防。")
            return
        if self.activity_state == "waiting_idle":
            self.countdown_var.set(
                "回场触发：等无人使用满 %d 秒（当前已空闲 %d 秒）"
                % (idle_need, idle)
            )
            if idle >= idle_need:
                self.activity_state = "armed"
                self.countdown_var.set("回场触发：已布防，等人回来")
                # 彻底隐身而不是最小化——任务栏上留个名字等于自首
                self._enter_stealth()
                # 人不在的此刻预热最无痕，等人回来时一切早已就绪
                self._prewarm_for_trigger()
        elif self.activity_state == "armed":
            if idle < 1.0:
                self.activity_state = "returning"
                self.activity_fire_time = time.monotonic() + delay
        elif self.activity_state == "returning":
            remaining = self.activity_fire_time - time.monotonic()
            if remaining <= 0:
                self._disarm_activity(quiet=True)
                self.stealth = False  # 进入播放态，热键不再弹面板
                self.countdown_var.set("回场触发：启动！")
                # 与定时路径同款：播完整个程序退出，面板不弹回来穿帮
                self.scheduled_run = True
                self.preview()
                return
            self.countdown_var.set(
                "回场触发：%d 秒后启动" % max(1, round(remaining))
            )
        self.activity_job = self.root.after(500, self._activity_poll)

    def _disarm_activity(self, quiet=False):
        self.activity_state = None
        self.activity_fire_time = None
        if self.activity_job:
            try:
                self.root.after_cancel(self.activity_job)
            except tk.TclError:
                pass
            self.activity_job = None
        if hasattr(self, "activity_button"):
            try:
                self.activity_button.configure(text="布防")
            except tk.TclError:
                pass
        if not quiet:
            self.stealth = False
            self.countdown_var.set("未设置定时")
            try:
                if self.root.state() in ("iconic", "withdrawn"):
                    self.root.deiconify()
            except tk.TclError:
                pass

    def _playback_done(self):
        self.player = None
        if self.demo or self.scheduled_run:
            self.root.after(200, self.close)
            return
        self._restore_panel()
        self.set_status("播放完成。")

    @staticmethod
    def _parse_datetime(value):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
        raise ValueError("时间格式应为 YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS")

    def close(self):
        if self.hotkey_job:
            try:
                self.root.after_cancel(self.hotkey_job)
            except tk.TclError:
                pass
            self.hotkey_job = None
        self._cancel_stealth()
        self._disarm_activity(quiet=True)
        self.cancel_schedule()
        if self.player:
            self.player.discard()
            self.player = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="透明怪兽桌面恶作剧工具")
    parser.add_argument("--video", default=str(DEFAULT_VIDEO), help="透明视频路径")
    parser.add_argument("--preview", action="store_true", help="启动后立即预览")
    parser.add_argument("--demo", action="store_true", help="开发验证：预览结束后自动退出")
    parser.add_argument("--check", action="store_true", help="只检查素材和播放组件，不打开界面")
    parser.add_argument(
        "--chroma",
        nargs="?",
        const="auto",
        default=None,
        help="把素材按绿幕抠像播放；不带值时自动检测画面边角颜色，也可指定 RRGGBB",
    )
    args = parser.parse_args()
    video_path = Path(args.video).expanduser().resolve()
    if not video_path.is_file():
        parser.error("找不到透明视频：%s" % video_path)
    chroma_color = None
    if args.chroma:
        if args.chroma == "auto":
            chroma_color = detect_corner_color(video_path) or "00FF00"
        else:
            chroma_color = args.chroma.lstrip("#").upper()
            if len(chroma_color) != 6 or any(
                c not in "0123456789ABCDEF" for c in chroma_color
            ):
                parser.error("--chroma 需要 RRGGBB 十六进制颜色，例如 00FF00")
    if args.check:
        width, height, fps, duration = probe_video(video_path)
        print("platform=%s" % sys.platform)
        print("video=%s" % video_path)
        print("video_info=%dx%d %.3ffps %.3fs" % (width, height, fps, duration))
        has_alpha = video_has_alpha(video_path)
        print("alpha=%s" % {True: "yes", False: "no", None: "unknown"}[has_alpha])
        if chroma_color:
            print("chroma=#%s" % chroma_color)
        elif has_alpha is False:
            print("warning=素材没有透明通道，播放时会是一整块不透明画面（可加 --chroma 按绿幕抠像）")
        audio_companion = video_path.with_name(video_path.stem + "_audio.wav")
        print("audio=%s" % audio_companion)
        if not audio_companion.is_file():
            print("warning=没找到同名 _audio.wav，打包版播放将没有声音")
        av = load_pyav()
        if av is not None:
            print("decoder=PyAV %s" % getattr(av, "__version__", ""))
        else:
            print("decoder=FFmpeg %s" % find_tool("ffmpeg"))
        if av is None and load_video_metadata(video_path) is None:
            print("ffprobe=%s" % find_tool("ffprobe"))
        if not video_path.with_name(video_path.stem + "_audio.wav").is_file():
            try:
                print("ffplay=%s" % find_tool("ffplay"))
            except RuntimeError:
                print("ffplay=not-found")
        return 0
    app = ControlApp(video_path, demo=args.demo, chroma=chroma_color)
    if args.preview or args.demo:
        app.root.after(500, app.preview)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
