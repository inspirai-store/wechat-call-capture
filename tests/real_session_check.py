import ctypes
from pathlib import Path
import sys
import time
import tkinter as tk
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Session
from wechat_capture import WindowCapture

class Camera:
    def __init__(self, **kw):
        self.width, self.height, self.device = kw['width'], kw['height'], 'test'
        self.frames = 0
    def send(self, image): self.frames += 1
    def sleep_until_next_frame(self): time.sleep(.03)
    def close(self): pass

root = tk.Tk()
root.title('Synthetic capture test')
root.geometry('300x500+5000+5000')
root.configure(bg='green')
root.update()
ctypes.windll.user32.GetParent.argtypes = [ctypes.c_void_p]
ctypes.windll.user32.GetParent.restype = ctypes.c_void_p
hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
helper = Path(__file__).resolve().parents[1] / 'native' / 'target' / 'release' / 'wechat-capture-helper.exe'
session = Session('camera', 1080, 1920, 30, camera_factory=Camera, capture_factory=lambda: WindowCapture([str(helper), '--diagnostic-window', str(hwnd)]))
session.set_framing(True, 1.0)
session.start()
start=time.monotonic()
state={'ok':False}
def check():
    if session.take_preview() is not None:
        state['ok'] = True
        session.stop()
        root.destroy()
    elif time.monotonic()-start > 8:
        session.stop()
        root.destroy()
    else: root.after(50, check)
root.after(50, check)
root.mainloop()
session.thread.join(timeout=5)
print('events:', list(session.events.queue))
if not state['ok']: raise SystemExit('No frame received')
print('Real capture with portrait fill and fake camera succeeded')
