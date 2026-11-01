"""Check the installed Unity camera with a local synthetic image and DirectShow."""
import subprocess
import threading
import time
import numpy as np
import pyvirtualcam

stop = threading.Event()
image = np.empty((480, 640, 3), dtype=np.uint8)
image[:] = (10, 90, 220)  # BGR
with pyvirtualcam.Camera(width=640, height=480, fps=30, fmt=pyvirtualcam.PixelFormat.BGR,
                         backend='unitycapture') as camera:
    def send():
        while not stop.is_set():
            camera.send(image)
            camera.sleep_until_next_frame()
    sender = threading.Thread(target=send, daemon=True)
    sender.start()
    try:
        time.sleep(.5)
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'dshow',
                                 '-video_size', '640x480', '-framerate', '30', '-i',
                                 f'video={camera.device}', '-frames:v', '5',
                                 '-f', 'rawvideo', '-pix_fmt', 'bgr24', 'pipe:1'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace'))
        frame_size = 640 * 480 * 3
        if len(result.stdout) < frame_size:
            raise RuntimeError('No frame received from Unity Video Capture')
        frame = np.frombuffer(result.stdout[-frame_size:], np.uint8).reshape(480, 640, 3)
        pixel = frame[240, 320].astype(int)
        if np.max(np.abs(pixel - [10, 90, 220])) > 12:
            raise RuntimeError(f'Unexpected center pixel: {pixel.tolist()}')
        print(f'{camera.device} end-to-end output verified; BGR pixel:', pixel.tolist())
    finally:
        stop.set()
        sender.join(timeout=2)
