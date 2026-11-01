"""End-to-end test: own synthetic window -> Windows capture -> USB/IP UVC."""
import ctypes
from pathlib import Path
import subprocess
import sys
import threading
import time
import tkinter as tk
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Session
from usbip_camera import UsbipCamera
from wechat_capture import WindowCapture

root = tk.Tk()
root.title('USB/IP capture diagnostic')
root.geometry('320x500+100+100')
canvas = tk.Canvas(root, bg='#00b000', highlightthickness=0)
canvas.pack(fill='both', expand=True)
root.update()
def animate():
    canvas.configure(bg='#00b000' if int(time.monotonic()*4) % 2 else '#009000')
    root.after(100, animate)
animate()
ctypes.windll.user32.GetParent.argtypes = [ctypes.c_void_p]
ctypes.windll.user32.GetParent.restype = ctypes.c_void_p
hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
helper = Path(__file__).resolve().parents[1] / 'native' / 'target' / 'release' / 'wechat-capture-helper.exe'
session = Session('camera', 720, 1280, 15, 'usbip', capture_factory=lambda: WindowCapture([str(helper), '--diagnostic-window', str(hwnd)]))
session.set_framing(True, 1.0)
session.start()
state = {'error': None, 'ok': False}

def verify():
    try:
        end = time.monotonic() + 15
        while time.monotonic() < end:
            preview = session.take_preview()
            if preview is not None and preview[0][180,320,1] > 110 and preview[0][180,320,0] < 50:
                print("preview center RGB:", preview[0][180,320].tolist(), flush=True)
                break
            if not session.thread.is_alive():
                raise RuntimeError('Session ended: ' + repr(list(session.events.queue)))
            time.sleep(.1)
        else:
            raise RuntimeError('Timed out waiting for capture frame')
        time.sleep(.3)
        result = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','dshow',
            '-video_size','720x1280','-framerate','15','-i','video=Hi Camara',
            '-frames:v','3','-pix_fmt','bgr24','-f','rawvideo','pipe:1'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25)
        if result.returncode or len(result.stdout) < 720 * 1280 * 3:
            raise RuntimeError('Device read failed: ' + result.stderr.decode(errors='replace'))
        frame=np.frombuffer(result.stdout[-720*1280*3:],np.uint8).reshape(1280,720,3)
        pixel=frame[640,360].tolist()
        print('samples', frame[100,100].tolist(),frame[640,360].tolist(),frame[1100,400].tolist(), flush=True)
        if pixel[1] < 110 or pixel[0] > 50 or pixel[2] > 50:
            raise AssertionError(f'Expected green test window, got {pixel}')
        state['ok'] = True
        print('WGC -> processing -> USB/IP UVC -> DirectShow verified: 720x1280, center BGR', pixel, flush=True)
    except BaseException as exc:
        state['error'] = repr(exc)
    finally:
        session.stop()

threading.Thread(target=verify,daemon=True).start()
def check():
    if session.thread.is_alive():
        root.after(100,check)
    else:
        root.destroy()
root.after(100,check)
root.mainloop()
session.thread.join(timeout=5)
if state['error']:
    raise SystemExit(state['error'])
if not state['ok']:
    raise SystemExit('Capture session ended before verification')
