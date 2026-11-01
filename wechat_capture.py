"""Receive the most recent Windows Graphics Capture frame in Python."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
import os
import subprocess
import threading
import time

import numpy as np

_HEADER = struct.Struct("<IIqI")
_MAX_FRAME_BYTES = 256 * 1024 * 1024
_DEFAULT_HELPER = Path(__file__).resolve().parent / "native" / "target" / "release" / "wechat-capture-helper.exe"


class CaptureError(RuntimeError):
    """Capture failed or the Windows selection dialog was canceled."""


class CaptureEnded(EOFError):
    """The selected window closed or capture was stopped."""


@dataclass(frozen=True, slots=True)
class VideoFrame:
    image: np.ndarray  # height x width x 4, uint8 BGRA; owned by this object
    width: int
    height: int
    timestamp_100ns: int  # Windows SystemRelativeTime, 100 ns units, not wall time
    sequence: int  # increases for every frame received, including frames later overwritten


def _read_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        part = stream.read(size - len(chunks))
        if not part:
            if not chunks:
                return b""
            raise CaptureError("采集进程输出了不完整的帧")
        chunks.extend(part)
    return bytes(chunks)


class WindowCapture:
    """Start a system picker; read() returns only frames newer than the last read.

    Call close() or use a context manager. Capture is local and video-only.
    helper_command is primarily for integration testing and custom installations.
    """

    def __init__(self, helper_command=None):
        command = list(helper_command) if helper_command is not None else [str(_DEFAULT_HELPER)]
        if not command:
            raise ValueError("helper_command 不能为空")
        try:
            self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, bufsize=0,
                                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as exc:
            raise CaptureError(f"无法启动采集程序：{exc}。请先按 README 构建。") from exc
        self._condition = threading.Condition()
        self._latest = None
        self._delivered = 0
        self._sequence = 0
        self._done = False
        self._closed = False
        self._error = None
        self._stderr = bytearray()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._reader_thread = threading.Thread(target=self._read_frames, daemon=True)
        self._stderr_thread.start()
        self._reader_thread.start()

    def _drain_stderr(self):
        while True:
            chunk = self._process.stderr.read(4096)
            if not chunk:
                return
            self._stderr.extend(chunk)
            if len(self._stderr) > 16384:
                del self._stderr[:-16384]

    def _read_frames(self):
        problem = None
        try:
            while True:
                header = _read_exact(self._process.stdout, _HEADER.size)
                if not header:
                    break
                width, height, timestamp, size = _HEADER.unpack(header)
                if not width or not height or size != width * height * 4 or size > _MAX_FRAME_BYTES:
                    raise CaptureError("采集进程返回了无效的帧尺寸")
                pixels = _read_exact(self._process.stdout, size)
                if len(pixels) != size:
                    raise CaptureError("采集进程输出了不完整的帧")
                image = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4)
                with self._condition:
                    self._sequence += 1
                    self._latest = VideoFrame(image, width, height, timestamp, self._sequence)
                    self._condition.notify_all()
        except (OSError, CaptureError, ValueError) as exc:
            problem = exc
        finally:
            if problem and self._process.poll() is None:
                self._process.terminate()
            code = self._process.wait()
            self._stderr_thread.join(timeout=2)
            details = self._stderr.decode("utf-8", errors="replace").strip()
            with self._condition:
                if not self._closed:
                    if problem:
                        self._error = CaptureError(str(problem))
                    elif code == 2:
                        self._error = CaptureError("已取消窗口选择")
                    elif code != 0:
                        self._error = CaptureError(f"采集进程退出（{code}）：{details or '未知错误'}")
                self._done = True
                self._condition.notify_all()

    def read(self, timeout: float | None = None) -> VideoFrame | None:
        """Get newest unread frame, None on timeout; raise CaptureEnded on normal end."""
        if timeout is not None and timeout < 0:
            raise ValueError("timeout 必须大于等于 0")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                if self._latest is not None and self._latest.sequence > self._delivered:
                    self._delivered = self._latest.sequence
                    return self._latest
                if self._done or self._closed:
                    if self._error:
                        raise self._error
                    raise CaptureEnded("采集已结束")
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        if self._process.poll() is None:
            self._process.terminate()
        self._reader_thread.join(timeout=5)
        if self._process.poll() is None:
            self._process.kill()
            self._process.wait()
        self._reader_thread.join(timeout=2)
        self._process.stdout.close()
        self._process.stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
