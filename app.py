"""A small Windows UI for window capture and virtual-camera output."""
from __future__ import annotations

import queue
import math
import threading
import time
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
import pyvirtualcam
from PIL import Image, ImageTk

from virtual_camera import fit_frame
from wechat_capture import CaptureEnded, CaptureError, WindowCapture
from usbip_camera import UsbipCamera, UsbipCameraPair


def make_preview(output):
    """Fit the outgoing image into a fixed preview without stretching it."""
    height, width = output.shape[:2]
    scale = min(640 / width, 360 / height)
    new_width, new_height = max(1, round(width * scale)), max(1, round(height * scale))
    resized = cv2.resize(output, (new_width, new_height), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    canvas = np.zeros((360, 640, 3), dtype=np.uint8)
    left, top = (640 - new_width) // 2, (360 - new_height) // 2
    canvas[top:top + new_height, left:left + new_width] = resized
    return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)


def preview_image_bounds(width, height):
    """Bounds of source content inside the 640 x 360 preview bitmap."""
    scale = min(640 / width, 360 / height)
    out_width, out_height = max(1, round(width * scale)), max(1, round(height * scale))
    left, top = (640 - out_width) // 2, (360 - out_height) // 2
    return left, top, left + out_width, top + out_height


def crop_frame(image, crop):
    """Apply normalized coordinates to a BGRA image, including after a resize."""
    if crop is None:
        return image
    height, width = image.shape[:2]
    x1, y1, x2, y2 = crop
    left = max(0, min(width - 1, math.floor(x1 * width)))
    top = max(0, min(height - 1, math.floor(y1 * height)))
    right = max(left + 1, min(width, math.ceil(x2 * width)))
    bottom = max(top + 1, min(height, math.ceil(y2 * height)))
    return image[top:bottom, left:right]


def selection_from_drag(start, end, bounds):
    """Convert preview coordinates into a normalized source rectangle."""
    left, top, right, bottom = bounds
    if not (left <= start[0] < right and top <= start[1] < bottom):
        return None
    x1, x2 = sorted((max(left, min(right, start[0])), max(left, min(right, end[0]))))
    y1, y2 = sorted((max(top, min(bottom, start[1])), max(top, min(bottom, end[1]))))
    if x2 - x1 < 5 or y2 - y1 < 5:
        return None
    return ((x1-left)/(right-left), (y1-top)/(bottom-top), (x2-left)/(right-left), (y2-top)/(bottom-top))


class Session:
    """Runs capture on a worker thread; all UI communication goes through a queue."""

    def __init__(self, mode, width, height, fps, backend=None, *, camera_factory=None, capture_factory=None,
                 source_kind='window', phone_ice=None, phone_public_base=None):
        self.mode, self.width, self.height, self.fps, self.backend = mode, width, height, fps, backend
        self.source_kind, self.phone_ice = source_kind, phone_ice
        self.phone_public_base = phone_public_base
        self.camera_factory = camera_factory or ({"usbip": UsbipCamera, "usbip_pair": UsbipCameraPair}.get(backend, pyvirtualcam.Camera))
        self.capture_factory = capture_factory
        self.events = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest_preview = None
        self._crop = None
        self._mirror = False
        self._fill = False
        self._zoom = 1.0
        self._selecting = False
        self._revision = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()

    def take_preview(self):
        with self._lock:
            image, self._latest_preview = self._latest_preview, None
            return image

    def set_selection_mode(self, selecting):
        with self._lock:
            self._selecting = selecting
            self._revision += 1
            self._latest_preview = None

    def set_crop(self, crop):
        with self._lock:
            self._crop = crop
            self._selecting = False
            self._revision += 1
            self._latest_preview = None

    def set_mirror(self, enabled):
        with self._lock:
            self._mirror = bool(enabled)
            self._revision += 1
            self._latest_preview = None

    def set_framing(self, fill, zoom):
        with self._lock:
            self._fill = bool(fill)
            self._zoom = float(zoom)
            self._revision += 1
            self._latest_preview = None

    def _run(self):
        camera = None
        capture = None
        result = "已停止"
        try:
            if self.mode == "camera":
                self.events.put(("status", "正在连接虚拟摄像头…"))
                camera = self.camera_factory(width=self.width, height=self.height, fps=self.fps,
                                             fmt=pyvirtualcam.PixelFormat.BGR, backend=self.backend)
                self.events.put(("device", camera.device))
            if self._stop.is_set():
                return
            if self.capture_factory:
                capture = self.capture_factory()
            elif self.source_kind == 'phone':
                from phone_capture import PhoneCapture
                self.events.put(('status', '正在启动手机连接服务…'))
                capture = PhoneCapture(on_event=lambda kind, value: self.events.put((kind, value)),
                                       ice_servers=self.phone_ice, public_base=self.phone_public_base)
            else:
                self.events.put(("status", "请在系统窗口选择器中选择微信通话窗口"))
                capture = WindowCapture()
            output = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            source = None
            rendered_revision = -1
            source_size = None
            last_frame_time = time.monotonic()
            waiting_notice = False
            next_preview = 0.0
            while not self._stop.is_set():
                frame = capture.read(timeout=0 if camera else 0.05)
                with self._lock:
                    crop, selecting, mirror, fill, zoom, revision = self._crop, self._selecting, self._mirror, self._fill, self._zoom, self._revision
                if frame is not None:
                    last_frame_time = time.monotonic()
                    if waiting_notice:
                        waiting_notice = False
                        self.events.put(("status", "正在输出到虚拟摄像头" if camera else "正在预览采集画面"))
                    source = frame.image
                    current_size = (source.shape[1], source.shape[0])
                    if current_size != source_size:
                        source_size = current_size
                        self.events.put(("source_size", source_size))
                    if rendered_revision == -1 and not (self.source_kind == 'phone' and current_size == (2, 2)):
                        self.events.put(("status", "正在输出到虚拟摄像头" if camera else "正在预览采集画面"))
                if not waiting_notice and time.monotonic() - last_frame_time >= 8:
                    waiting_notice = True
                    if self.source_kind == 'phone' and source is not None:
                        # Do not keep broadcasting a frozen image after a lost connection.
                        source = None
                        output = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                        with self._lock:
                            self._latest_preview = (make_preview(output), False, None)
                    if source is None:
                        message = "等待手机连接和摄像头画面" if self.source_kind == 'phone' else "尚未收到画面：如已选择窗口，请确认它没有最小化"
                        self.events.put(("status", message))
                    else:
                        message = "手机画面暂未更新，请检查手机连接" if self.source_kind == 'phone' else "画面暂未更新：窗口可能静止或已最小化"
                        self.events.put(("status", message))
                if source is not None and (frame is not None or revision != rendered_revision):
                    output = fit_frame(crop_frame(source, crop), self.width, self.height, fill=fill, zoom=zoom)
                    if mirror:
                        output = cv2.flip(output, 1)
                    rendered_revision = revision
                if camera:
                    camera.send(output)
                    camera.sleep_until_next_frame()
                if source is not None and time.monotonic() >= next_preview:
                    if selecting:
                        preview_source = cv2.cvtColor(source, cv2.COLOR_BGRA2BGR)
                        rgb = make_preview(preview_source)
                        preview = (rgb, True, (source.shape[1], source.shape[0]))
                    else:
                        preview = (make_preview(output), False, None)
                    with self._lock:
                        # Discard a preview generated for a selection state that has changed.
                        if revision == self._revision:
                            self._latest_preview = preview
                    next_preview = time.monotonic() + 0.1
        except CaptureEnded:
            result = "采集窗口已关闭"
        except CaptureError as exc:
            if not self._stop.is_set():
                result = f"{'手机' if self.source_kind == 'phone' else '窗口'}采集失败：{exc}"
        except Exception as exc:
            if not self._stop.is_set():
                details = str(exc)
                if camera is None and self.mode == "camera":
                    if self.backend in ("usbip", "usbip_pair"):
                        result = f"USB/IP 摄像头无法启动\n{details}"
                    elif "not found" in details.lower() or "no camera registered" in details.lower():
                        result = "未检测到虚拟摄像头\n请先注册 OBS Virtual Camera 或 Unity Capture，再重新启动。"
                    else:
                        result = f"虚拟摄像头无法启动\n请确认设备未被其他程序占用。\n{details}"
                else:
                    result = f"输出失败：{details}"
        finally:
            if capture is not None:
                try:
                    capture.close()
                except Exception as exc:
                    result = f"关闭采集时出错：{exc}"
            if camera is not None:
                try:
                    camera.close()
                except Exception as exc:
                    result = f"关闭虚拟摄像头时出错：{exc}"
            self.events.put(("finished", result))


class App:
    BG = "#f3f6f4"
    INK = "#20372f"
    MUTED = "#60776d"
    GREEN = "#158461"
    WHITE = "#ffffff"

    def __init__(self, root):
        self.root = root
        self.root.title("视频画面助手 · 手机摄像头")
        self.root.geometry("800x900")
        self.root.minsize(740, 760)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.session = None
        self.closing = False
        self.photo = None
        self.selecting = False
        self.preview_source_size = None
        self.drag_start = None
        self.drag_source_size = None
        self.mode = tk.StringVar(value="虚拟摄像头")
        self.resolution = tk.StringVar(value="720 × 1280")
        self.fps = tk.StringVar(value="15")
        self.backend = tk.StringVar(value="USB/IP 双路")
        self.source = tk.StringVar(value="微信窗口")
        self.turn_url = tk.StringVar()
        self.turn_user = tk.StringVar()
        self.turn_password = tk.StringVar()
        self.phone_public_base = tk.StringVar()
        self.phone_url = None
        self.phone_dialog = None
        self.mirror = tk.BooleanVar(value=False)
        self.fill = tk.BooleanVar(value=True)
        self.zoom = tk.DoubleVar(value=1.0)
        self.resolution.trace_add("write", self._resolution_changed)
        self.status = tk.StringVar(value="准备就绪")
        self.device = tk.StringVar(value="启动后选择微信的视频通话窗口")
        self.source_info = tk.StringVar(value="等待画面 · 不采集声音")
        self._build()
        self.root.after(50, self._poll)

    def _build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Capture.TCombobox", fieldbackground=self.WHITE, background=self.WHITE,
                        foreground=self.INK, bordercolor="#d5e1db", arrowsize=14, padding=7)
        outer = tk.Frame(self.root, bg=self.BG, padx=30, pady=22)
        outer.pack(fill="both", expand=True)
        top = tk.Frame(outer, bg=self.BG)
        top.pack(fill="x")
        tk.Label(top, text="●", fg=self.GREEN, bg=self.BG, font=("Microsoft YaHei UI", 15)).pack(side="left", padx=(0, 8))
        tk.Label(top, text="视频画面助手", fg=self.INK, bg=self.BG,
                 font=("Microsoft YaHei UI", 19, "bold")).pack(side="left")
        tk.Label(outer, text="将窗口或手机摄像头画面发送到摄像头，或在本机预览。", fg=self.MUTED,
                 bg=self.BG, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(5, 18))

        card = tk.Frame(outer, bg=self.WHITE, padx=18, pady=15, highlightbackground="#dfe9e3", highlightthickness=1)
        card.pack(fill="x")
        tk.Label(card, text="输出设置", fg=self.INK, bg=self.WHITE,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 10))
        settings = tk.Frame(card, bg=self.WHITE)
        settings.pack(fill="x")
        self.controls = []
        for label, variable, values, width in [
            ("输出方式", self.mode, ("虚拟摄像头", "仅预览"), 14),
            ("画面尺寸", self.resolution, ("1920 × 1080", "1080 × 1920", "720 × 1280", "2560 × 1440", "3840 × 2160", "1280 × 720", "640 × 480"), 15),
            ("帧率", self.fps, ("30", "15", "60"), 7),
            ("摄像头", self.backend, ("USB/IP 双路", "USB/IP UVC", "Unity Capture", "自动选择", "OBS"), 14),
        ]:
            part = tk.Frame(settings, bg=self.WHITE)
            part.pack(side="left", padx=(0, 13), anchor="w")
            tk.Label(part, text=label, fg=self.MUTED, bg=self.WHITE,
                     font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 4))
            control = ttk.Combobox(part, textvariable=variable, values=values, width=width,
                                   state="readonly", style="Capture.TCombobox")
            control.pack(anchor="w")
            self.controls.append(control)

        https_row = tk.Frame(card, bg=self.WHITE)
        https_row.pack(fill='x', pady=(8, 0))
        tk.Label(https_row, text='自有 HTTPS 入口（可选）', fg=self.MUTED, bg=self.WHITE,
                 font=('Microsoft YaHei UI', 9)).pack(side='left')
        self.https_entry = ttk.Entry(https_row, textvariable=self.phone_public_base, width=36)
        self.https_entry.pack(side='left', padx=8)
        self.controls.append(self.https_entry)
        tk.Label(https_row, text='留空使用临时入口', fg=self.MUTED, bg=self.WHITE,
                 font=('Microsoft YaHei UI', 9)).pack(side='left')

        phone_settings = tk.Frame(card, bg=self.WHITE)
        phone_settings.pack(fill="x", pady=(12, 0))
        for label, variable, width, show in [
            ("画面来源", self.source, 13, None),
            ("TURN 地址（可选）", self.turn_url, 21, ''),
            ("用户名", self.turn_user, 12, ''),
            ("密码", self.turn_password, 12, '●'),
        ]:
            part = tk.Frame(phone_settings, bg=self.WHITE)
            part.pack(side="left", padx=(0, 9), anchor="w")
            tk.Label(part, text=label, fg=self.MUTED, bg=self.WHITE,
                     font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 4))
            if show is None:
                control = ttk.Combobox(part, textvariable=variable, values=("微信窗口", "手机 WebRTC"),
                                       width=width, state="readonly", style="Capture.TCombobox")
            else:
                control = ttk.Entry(part, textvariable=variable, width=width, show=show)
            control.pack(anchor="w")
            self.controls.append(control)

        framing = tk.Frame(outer, bg=self.BG)
        framing.pack(fill="x", pady=(10, 0))
        tk.Checkbutton(framing, text="填满画面（裁剪边缘）", variable=self.fill,
                       command=self.update_framing, bg=self.BG, fg=self.INK, activebackground=self.BG,
                       selectcolor=self.WHITE, font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Label(framing, text="缩放", fg=self.MUTED, bg=self.BG,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(20, 4))
        tk.Scale(framing, variable=self.zoom, from_=1.0, to=3.0, resolution=0.1, orient="horizontal",
                 length=180, showvalue=True, command=self.update_framing, bg=self.BG, fg=self.INK,
                 highlightthickness=0, troughcolor="#d5e1db").pack(side="left")
        tk.Label(framing, text="1×–3×，以中心为准", fg=self.MUTED, bg=self.BG,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(10, 0))

        preview_header = tk.Frame(outer, bg=self.BG)
        preview_header.pack(fill="x", pady=(20, 8))
        tk.Label(preview_header, text="画面预览", fg=self.INK, bg=self.BG,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        tk.Label(preview_header, textvariable=self.source_info, fg=self.MUTED, bg=self.BG,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")
        self.preview = tk.Canvas(outer, bg="#1d3029", highlightthickness=0, width=1, height=1)
        self.preview.pack(fill="both", expand=True)
        self.preview.bind("<Configure>", self._draw_preview)
        self.preview.bind("<ButtonPress-1>", self._drag_start)
        self.preview.bind("<B1-Motion>", self._drag_move)
        self.preview.bind("<ButtonRelease-1>", self._drag_end)
        self._draw_preview()

        selection_bar = tk.Frame(outer, bg=self.BG)
        selection_bar.pack(fill="x", pady=(10, 0))
        self.select_button = tk.Button(selection_bar, text="框选画面区域", command=self.select_region,
                                       bg=self.WHITE, fg=self.INK, relief="flat", padx=14, pady=5,
                                       font=("Microsoft YaHei UI", 9), state="disabled")
        self.select_button.pack(side="left")
        self.reset_button = tk.Button(selection_bar, text="恢复完整画面", command=self.reset_region,
                                      bg=self.WHITE, fg=self.INK, relief="flat", padx=14, pady=5,
                                      font=("Microsoft YaHei UI", 9), state="disabled")
        self.reset_button.pack(side="left", padx=(8, 0))
        self.phone_button = tk.Button(selection_bar, text="手机连接二维码", command=self.show_phone_url,
                                      bg=self.WHITE, fg=self.INK, relief="flat", padx=10, pady=5,
                                      font=("Microsoft YaHei UI", 9), state="disabled")
        self.phone_button.pack(side="left", padx=(8, 0))
        self.mirror_button = tk.Checkbutton(selection_bar, text="水平镜像", variable=self.mirror,
                                            command=self.toggle_mirror, bg=self.BG, fg=self.INK,
                                            activebackground=self.BG, selectcolor=self.WHITE,
                                            font=("Microsoft YaHei UI", 9), cursor="hand2")
        self.mirror_button.pack(side="right")
        self.selection_hint = tk.StringVar(value="默认输出完整窗口")
        tk.Label(selection_bar, textvariable=self.selection_hint, bg=self.BG, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(12, 0))

        status_box = tk.Frame(outer, bg=self.BG)
        status_box.pack(fill="x", pady=(16, 12))
        tk.Label(status_box, textvariable=self.status, fg=self.GREEN, bg=self.BG,
                 font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(status_box, textvariable=self.device, fg=self.MUTED, bg=self.BG,
                 font=("Microsoft YaHei UI", 9), anchor="w", wraplength=700, justify="left").pack(fill="x", pady=(4, 0))
        actions = tk.Frame(outer, bg=self.BG)
        actions.pack(fill="x")
        self.start_button = tk.Button(actions, text="开始采集", command=self.start, bg=self.GREEN, fg="white",
                                      activebackground="#116d51", activeforeground="white", relief="flat",
                                      font=("Microsoft YaHei UI", 10, "bold"), padx=23, pady=8, cursor="hand2")
        self.start_button.pack(side="left")
        self.stop_button = tk.Button(actions, text="停止", command=self.stop, bg="#e3ebe6", fg=self.INK,
                                     relief="flat", font=("Microsoft YaHei UI", 10), padx=23, pady=8, state="disabled")
        self.stop_button.pack(side="left", padx=(10, 0))
        tk.Label(actions, text="双路：选择 Hi Camara 和 Pure Camara", fg=self.MUTED, bg=self.BG,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

    def start(self):
        if self.session is not None:
            return
        width, height = (int(v.strip()) for v in self.resolution.get().split("×"))
        backend = {"USB/IP 双路": "usbip_pair", "USB/IP UVC": "usbip", "自动选择": None, "OBS": "obs", "Unity Capture": "unitycapture"}[self.backend.get()]
        mode = "camera" if self.mode.get() == "虚拟摄像头" else "preview"
        source_kind = 'phone' if self.source.get() == '手机 WebRTC' else 'window'
        phone_ice = None
        if source_kind == 'phone' and any((self.turn_url.get(), self.turn_user.get(), self.turn_password.get())):
            url, username, password = self.turn_url.get().strip(), self.turn_user.get().strip(), self.turn_password.get()
            if not url.startswith(('turn:', 'turns:')) or not username or not password:
                self.status.set('请填写有效的 TURN 地址、用户名和密码')
                self.device.set('例如 turn:服务器地址:3478；凭据仅保存在本次程序内存中')
                return
            phone_ice = [{'urls': ['stun:stun.l.google.com:19302']},
                         {'urls': [url], 'username': username, 'credential': password}]
        self.session = Session(mode, width, height, int(self.fps.get()), backend,
                               source_kind=source_kind, phone_ice=phone_ice,
                               phone_public_base=self.phone_public_base.get().strip() or None)
        self.session.set_mirror(self.mirror.get())
        self.session.set_framing(self.fill.get(), self.zoom.get())
        self.selecting = False
        self.preview_source_size = None
        self.drag_start = None
        self.selection_hint.set("默认输出完整窗口")
        self.status.set("正在启动…")
        self.device.set("正在启动手机连接服务" if source_kind == 'phone' else "请等待系统窗口选择器出现")
        self.source_info.set("等待画面 · 不采集声音")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.select_button.configure(state="disabled", text="框选画面区域")
        self.reset_button.configure(state="disabled")
        for control in self.controls:
            control.configure(state="disabled")
        self.session.start()

    def _resolution_changed(self, *_):
        try:
            width, height = (int(value.strip()) for value in self.resolution.get().split("×"))
        except (ValueError, AttributeError):
            return
        self.fill.set(height > width)
        self.update_framing()

    def update_framing(self, *_):
        if self.session is not None:
            self.session.set_framing(self.fill.get(), self.zoom.get())

    def toggle_mirror(self):
        if self.session is not None:
            self.session.set_mirror(self.mirror.get())

    def stop(self):
        if self.session is not None:
            self.status.set("正在停止…")
            self.stop_button.configure(state="disabled")
            self.session.stop()

    def _poll(self):
        if self.session is not None:
            while True:
                try:
                    kind, value = self.session.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "status":
                    if not self.session._stop.is_set():
                        self.status.set(value)
                elif kind == "device":
                    self.device.set("虚拟摄像头：" + value)
                elif kind == 'phone_url':
                    if not self.session._stop.is_set():
                        self.phone_url = value
                        self.phone_button.configure(state='normal')
                        self.show_phone_url()
                elif kind == "source_size":
                    self.source_info.set(f"源画面 {value[0]} × {value[1]} · 不采集声音")
                elif kind == "finished":
                    self.status.set(value.split("\n", 1)[0])
                    self.device.set(value.split("\n", 1)[1] if "\n" in value else (value if "失败" in value or "出错" in value else "可重新开始采集"))
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.select_button.configure(state="disabled", text="框选画面区域")
                    self.reset_button.configure(state="disabled")
                    self.selecting = False
                    self.preview_source_size = None
                    self.drag_start = None
                    self.selection_hint.set("默认输出完整窗口")
                    self.source_info.set("等待画面 · 不采集声音")
                    self.phone_url = None
                    self.phone_button.configure(state='disabled')
                    if self.phone_dialog is not None:
                        self.phone_dialog.destroy()
                        self.phone_dialog = None
                    for control in self.controls:
                        control.configure(state="readonly" if isinstance(control, ttk.Combobox) else "normal")
                    self.session = None
                    self.photo = None
                    self._draw_preview()
                    break
            if self.session is not None:
                preview = self.session.take_preview()
                if preview is not None:
                    image, is_source, source_size = preview
                    if is_source == self.selecting:
                        self.preview_source_size = source_size
                        self.photo = ImageTk.PhotoImage(Image.fromarray(image))
                        self.select_button.configure(state="normal")
                        self.reset_button.configure(state="normal")
                        self._draw_preview()
        if self.closing and self.session is None:
            self.root.destroy()
        else:
            self.root.after(50, self._poll)

    def _draw_preview(self, event=None):
        canvas = self.preview
        width, height = canvas.winfo_width(), canvas.winfo_height()
        canvas.delete("image", "placeholder", "selection")
        if self.photo is None:
            canvas.create_text(width / 2, height / 2, text="启动后选择通话窗口\n画面将在这里显示",
                               fill="#a4bdb2", font=("Microsoft YaHei UI", 11), justify="center", tags="placeholder")
        else:
            canvas.create_image(width / 2, height / 2, image=self.photo, tags="image")
        if self.drag_start is not None and hasattr(self, "drag_current"):
            canvas.create_rectangle(*self.drag_start, *self.drag_current, outline="#30d6a0", width=3,
                                    dash=(6, 3), tags="selection")

    def select_region(self):
        if self.session is None:
            return
        if self.selecting:
            self.selecting = False
            self.session.set_selection_mode(False)
            self.selection_hint.set("已取消框选")
            self.select_button.configure(text="框选画面区域")
        else:
            self.selecting = True
            self.session.set_selection_mode(True)
            self.selection_hint.set("在原始窗口画面上拖动鼠标框选")
            self.select_button.configure(text="取消框选")
        self.preview_source_size = None
        self.drag_start = None
        self.photo = None
        self._draw_preview()

    def show_phone_url(self):
        if not self.phone_url:
            return
        if self.phone_dialog is not None and self.phone_dialog.winfo_exists():
            self.phone_dialog.lift()
            return
        import qrcode
        dialog = tk.Toplevel(self.root)
        self.phone_dialog = dialog
        dialog.title('手机摄像头连接')
        dialog.configure(bg=self.BG)
        dialog.resizable(False, False)
        tk.Label(dialog, text='用 iPhone 相机扫码，在 Safari 中打开', bg=self.BG, fg=self.INK,
                 font=('Microsoft YaHei UI', 11, 'bold')).pack(padx=20, pady=(18, 8))
        qr = qrcode.make(self.phone_url).convert('RGB').resize((250, 250), Image.Resampling.NEAREST)
        dialog.qr_image = ImageTk.PhotoImage(qr)
        tk.Label(dialog, image=dialog.qr_image, bg=self.BG).pack(padx=20)
        tk.Label(dialog, text='链接包含本次连接凭据，请勿分享给他人。', bg=self.BG, fg=self.MUTED,
                 font=('Microsoft YaHei UI', 9)).pack(pady=(8, 4))
        def copy_url():
            dialog.clipboard_clear()
            dialog.clipboard_append(self.phone_url)
        tk.Button(dialog, text='复制手机连接链接', command=copy_url, bg=self.WHITE, fg=self.INK,
                  relief='flat', padx=12, pady=5).pack(pady=(4, 16))
        def close_dialog():
            dialog.destroy()
            self.phone_dialog = None
        dialog.protocol('WM_DELETE_WINDOW', close_dialog)

    def reset_region(self):
        if self.session is None:
            return
        self.session.set_crop(None)
        self.selecting = False
        self.preview_source_size = None
        self.drag_start = None
        self.selection_hint.set("已恢复完整窗口")
        self.select_button.configure(text="框选画面区域")
        self.photo = None
        self._draw_preview()

    def _drag_start(self, event):
        if not self.selecting or self.preview_source_size is None:
            return
        self.drag_start = (event.x, event.y)
        self.drag_current = self.drag_start
        self.drag_source_size = self.preview_source_size
        self._draw_preview()

    def _drag_move(self, event):
        if self.drag_start is not None:
            self.drag_current = (event.x, event.y)
            self._draw_preview()

    def _drag_end(self, event):
        if self.drag_start is None or self.session is None:
            return
        if self.preview_source_size != self.drag_source_size:
            self.selection_hint.set("窗口尺寸已变化，请重新框选")
            self.drag_start = None
            self._draw_preview()
            return
        image_x = (self.preview.winfo_width() - 640) / 2
        image_y = (self.preview.winfo_height() - 360) / 2
        left, top, right, bottom = preview_image_bounds(*self.preview_source_size)
        bounds = (left + image_x, top + image_y, right + image_x, bottom + image_y)
        crop = selection_from_drag(self.drag_start, (event.x, event.y), bounds)
        self.drag_start = None
        if crop is None:
            self.selection_hint.set("请在画面内拖动，框选一个区域")
            self._draw_preview()
            return
        self.session.set_crop(crop)
        self.selecting = False
        self.preview_source_size = None
        self.selection_hint.set("已应用选区，可重新框选或恢复")
        self.select_button.configure(text="框选画面区域")
        self.photo = None
        self._draw_preview()

    def close(self):
        self.closing = True
        if self.session is None:
            self.root.destroy()
        else:
            self.stop()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
