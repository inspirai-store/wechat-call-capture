"""Exercise two isolated UVC devices concurrently without touching a running session."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usbip_camera import UsbipCameraPair

profiles = (
    (53251, 53252, '1-11', 'WeChat USBIP Test A', 0x5691),
    (53253, 53254, '1-12', 'WeChat USBIP Test B', 0x5692),
)
width, height, fps = 720, 1280, 15
pair = UsbipCameraPair(width, height, fps, profiles=profiles)
stop = threading.Event()
color = [0, 180, 0]

def produce():
    while not stop.is_set():
        pair.send(np.full((height, width, 3), tuple(color), dtype=np.uint8))
        pair.sleep_until_next_frame()

def read_camera(name):
    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'dshow',
        '-video_size', f'{width}x{height}', '-framerate', str(fps), '-i', f'video={name}',
        '-frames:v', '3', '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35)
    if result.returncode or len(result.stdout) < width * height * 3:
        raise RuntimeError(f'{name}: {result.stderr.decode(errors="replace")}')
    frame = np.frombuffer(result.stdout[-width*height*3:], np.uint8).reshape(height, width, 3)
    return frame[height//2, width//2].tolist()

thread = threading.Thread(target=produce, daemon=True)
thread.start()
try:
    for expected in ([0, 180, 0], [180, 0, 0]):
        color[:] = expected
        time.sleep(1)
        with ThreadPoolExecutor(max_workers=2) as executor:
            pixels = list(executor.map(read_camera, (p[3] for p in profiles)))
        for pixel in pixels:
            if max(abs(a-b) for a, b in zip(pixel, expected)) > 15:
                raise AssertionError(f'Expected {expected}, got {pixels}')
        print(f'Both 720x1280 MJPEG devices concurrently received {expected}: {pixels}', flush=True)
finally:
    stop.set()
    thread.join(timeout=5)
    pair.close()
