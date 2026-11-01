"""Exercise the region-selection UI against a synthetic image, without a picker."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk

import numpy as np
from PIL import ImageTk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

class PatternCapture:
    def read(self, timeout):
        time.sleep(.03)
        image = np.zeros((360, 640, 4), np.uint8)
        image[:, :320] = (0, 0, 220, 255)
        image[:, 320:] = (220, 0, 0, 255)
        return SimpleNamespace(image=image)
    def close(self):
        pass

RealSession = app.Session
class TestSession(RealSession):
    def __init__(self, *args, **kwargs):
        kwargs['capture_factory'] = PatternCapture
        super().__init__(*args, **kwargs)
app.Session = TestSession
root = tk.Tk()
ui = app.App(root)
root.geometry('800x830+5000+5000')
ui.mode.set('仅预览')
ui.resolution.set('1920 × 1080')
state = {'phase': 'full', 'error': None}
started = time.monotonic()


def check():
    try:
        if time.monotonic() - started > 8:
            raise AssertionError('Timed out in ' + state['phase'])
        if ui.photo is None or ui.session is None:
            root.after(50, check)
            return
        phase = state['phase']
        if phase == 'full':
            image = ImageTk.getimage(ui.photo).convert("RGB")
            if image.getpixel((100, 180)) == (220, 0, 0) and image.getpixel((540, 180)) == (0, 0, 220):
                ui.mirror_button.invoke()
                state['phase'] = 'mirrored'
        elif phase == 'mirrored':
            image = ImageTk.getimage(ui.photo).convert("RGB")
            if image.getpixel((100, 180)) == (0, 0, 220) and image.getpixel((540, 180)) == (220, 0, 0):
                ui.mirror_button.invoke()
                state['phase'] = 'unmirrored'
        elif phase == 'unmirrored':
            image = ImageTk.getimage(ui.photo).convert("RGB")
            if image.getpixel((100, 180)) == (220, 0, 0) and image.getpixel((540, 180)) == (0, 0, 220):
                ui.select_region()
                state['phase'] = 'select'
        elif phase == 'select' and ui.preview_source_size is not None:
            offset_x = (ui.preview.winfo_width() - 640) / 2
            offset_y = (ui.preview.winfo_height() - 360) / 2
            ui._drag_start(SimpleNamespace(x=int(offset_x + 32), y=int(offset_y + 18)))
            ui._drag_move(SimpleNamespace(x=int(offset_x + 288), y=int(offset_y + 342)))
            ui._drag_end(SimpleNamespace(x=int(offset_x + 288), y=int(offset_y + 342)))
            assert not ui.selecting and ui.session._crop is not None
            state['phase'] = 'cropped'
        elif phase == 'cropped':
            image = ImageTk.getimage(ui.photo).convert("RGB")
            if image.getpixel((320, 180)) == (220, 0, 0):
                ui.reset_region()
                assert ui.session._crop is None
                state['phase'] = 'reset'
        elif phase == 'reset':
            image = ImageTk.getimage(ui.photo).convert("RGB")
            if image.getpixel((100, 180)) == (220, 0, 0) and image.getpixel((540, 180)) == (0, 0, 220):
                ui.close()
                return
        root.after(50, check)
    except BaseException as exc:
        state['error'] = repr(exc)
        ui.close()

ui.start()
root.after(50, check)
root.mainloop()
if state['error']:
    raise SystemExit(state['error'])
assert state['phase'] == 'reset', state
print('GUI mirror, selection, crop preview and reset passed')
