"""Local MJPEG frame producer for the isolated USB/IP UVC camera."""
from __future__ import annotations

import re
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parent
_SERVER = _ROOT / 'usbip-experiment' / 'build' / 'Release' / 'wechat_uvc_pair.exe'
_USBIP = Path(r'C:\Program Files\USBip\usbip.exe')
_CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# Each device uses its own bus, PID and ports so Windows exposes it separately.
CAMERA_PROFILES = (
    (53241, 53242, '1-2', 'Hi Camara', 0x5684),
    (53243, 53244, '1-3', 'Pure Camara', 0x5685),
)


class UsbipCamera:
    """Create a loopback-only UVC device, then send BGR frames as MJPEG."""
    def __init__(self, width, height, fps, *, profile=CAMERA_PROFILES[0], **_):
        self.width, self.height, self.fps = width, height, fps
        self._usbip_port, self._frame_port, self._bus, self.device, self._product_id = profile
        self._proc = None
        self._socket = None
        self._port = None
        self._owns_attach_attempt = False
        self._last_frame = None
        self._next_frame = time.monotonic()
        if not _SERVER.is_file() or not _USBIP.is_file():
            raise RuntimeError('USB/IP UVC 组件未找到，请检查测试组件和 USBip 安装。')
        try:
            if self._find_port() is not None:
                raise RuntimeError("USB/IP 视频设备已连接，请先关闭先前的视频会话。")
            # Refuse to interfere with an existing live test or unrelated service.
            for port in (self._usbip_port, self._frame_port):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    if probe.connect_ex(('127.0.0.1', port)) == 0:
                        raise RuntimeError(f'USB/IP 视频服务端口 {port} 已被占用，请先关闭先前的视频会话。')
            with tempfile.TemporaryDirectory(prefix='wechat_uvc_') as temp:
                fallback = Path(temp) / 'blank.jpg'
                ok, encoded = cv2.imencode('.jpg', np.zeros((height, width, 3), np.uint8))
                if not ok:
                    raise RuntimeError('无法创建初始视频帧。')
                fallback.write_bytes(encoded.tobytes())
                self._proc = subprocess.Popen([str(_SERVER), '--port', str(self._usbip_port), '--busid', self._bus,
                    '--frame-port', str(self._frame_port), '--fallback', str(fallback),
                    '--width', str(width), '--height', str(height), '--fps', str(fps),
                    '--product-id', str(self._product_id), '--device-name', self.device],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_CREATE_NO_WINDOW)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if self._proc.poll() is not None:
                        raise RuntimeError(f'USB/IP 视频服务退出（{self._proc.returncode}）。')
                    try:
                        self._socket = socket.create_connection(('127.0.0.1', self._frame_port), timeout=.2)
                        self._socket.settimeout(2.0)
                        break
                    except OSError:
                        time.sleep(.05)
                else:
                    raise RuntimeError('USB/IP 视频服务未就绪或本机端口被占用。')
            self._owns_attach_attempt = True
            result = subprocess.run([str(_USBIP), '-t', str(self._usbip_port), 'attach', '-r', '127.0.0.1',
                                     '-b', self._bus, '--once'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=_CREATE_NO_WINDOW, timeout=15)
            out = (result.stdout + result.stderr).decode('utf-8', errors='replace')
            match = re.search(r'port\s+(\d+)', out, re.IGNORECASE)
            if result.returncode != 0 or not match:
                raise RuntimeError(f'无法连接 USB/IP 摄像头：{out.strip()}')
            self._port = int(match.group(1))
        except Exception as exc:
            try:
                self.close()
            except Exception as cleanup_exc:
                raise RuntimeError(f'{exc}；清理失败：{cleanup_exc}') from exc
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def send(self, frame):
        if frame is self._last_frame:
            return
        payload = encode_frame(frame)
        self.send_encoded(payload)
        self._last_frame = frame

    def send_encoded(self, payload):
        try:
            self._socket.sendall(payload)
        except OSError as exc:
            raise RuntimeError(f'{self.device} 画面发送中断：{exc}') from exc

    def sleep_until_next_frame(self):
        self._next_frame += 1 / self.fps
        now = time.monotonic()
        if self._next_frame < now - 1 / self.fps:
            self._next_frame = now
        else:
            time.sleep(max(0, self._next_frame - now))

    def _find_port(self):
        result = subprocess.run([str(_USBIP), 'port'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=_CREATE_NO_WINDOW, timeout=10)
        if result.returncode:
            raise RuntimeError('无法查询 USB/IP 设备状态。')
        listing = result.stdout.decode('utf-8', errors='replace')
        uri = f'usbip://127.0.0.1:{self._usbip_port}/{self._bus}'
        pattern = r'(?s)Port\s+(\d+):(?:(?!Port\s+\d+:).)*' + re.escape(uri) + r'(?=\s|$)'
        match = re.search(pattern, listing)
        return int(match.group(1)) if match else None

    def close(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        try:
            if self._owns_attach_attempt:
                self._owns_attach_attempt = False
                port = self._find_port()
                if port is not None:
                    result = subprocess.run([str(_USBIP), 'detach', '-p', str(port)], stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, creationflags=_CREATE_NO_WINDOW, timeout=10)
                    if result.returncode:
                        raise RuntimeError('USB/IP 摄像头无法正常分离：' + (result.stdout + result.stderr).decode('utf-8', errors='replace').strip())
                self._port = None
        finally:
            self._terminate_server()

    def _terminate_server(self):
        if self._proc is not None:
            proc, self._proc = self._proc, None
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def encode_frame(frame):
    ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok or len(jpeg) > 4 * 1024 * 1024:
        raise RuntimeError('无法编码视频帧。')
    return struct.pack('<I', len(jpeg)) + jpeg.tobytes()


class UsbipCameraPair:
    """Two independently enumerable UVC devices fed by the same encoded frame."""
    def __init__(self, width, height, fps, *, profiles=CAMERA_PROFILES, camera_factory=UsbipCamera, **kwargs):
        self._cameras = []
        self._last_frame = None
        self.device = 'Hi Camara / Pure Camara'
        try:
            for profile in profiles:
                self._cameras.append(camera_factory(width=width, height=height, fps=fps, profile=profile, **kwargs))
        except Exception as exc:
            try:
                self.close()
            except Exception as cleanup_exc:
                raise RuntimeError(f'{exc}；清理失败：{cleanup_exc}') from exc
            raise

    def send(self, frame):
        if frame is self._last_frame:
            return
        payload = encode_frame(frame)
        for camera in self._cameras:
            camera.send_encoded(payload)
        self._last_frame = frame

    def sleep_until_next_frame(self):
        self._cameras[0].sleep_until_next_frame()

    def close(self):
        errors = []
        while self._cameras:
            try:
                self._cameras.pop().close()
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError('；'.join(errors))
