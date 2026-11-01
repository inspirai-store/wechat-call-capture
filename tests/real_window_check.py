"""Windows-only end-to-end test against a window created by this process."""
import ctypes
import os
from pathlib import Path
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_capture import CaptureEnded, WindowCapture
from virtual_camera import fit_frame

root = tk.Tk()
root.title("Capture diagnostic window")
root.geometry("320x220+100+100")
canvas = tk.Canvas(root, bg="#00a000", highlightthickness=0)
canvas.pack(fill="both", expand=True)
state = {"phase": "initial", "error": None, "counts": {}}


def animate():
    color = "#00a000" if int(time.monotonic() * 4) % 2 else "#0000a0"
    canvas.configure(bg=color)
    root.after(100, animate)


def resize():
    state["phase"] = "resize"
    root.geometry("430x300+100+100")


def cover():
    state["phase"] = "covered"
    overlay = tk.Toplevel(root)
    overlay.title("Capture diagnostic overlay")
    overlay.geometry("500x400+90+90")
    overlay.configure(bg="#ff0000")
    state["overlay"] = overlay
    overlay.lift()


def close_window():
    state["phase"] = "closed"
    state["overlay"].destroy()
    root.destroy()


def worker(hwnd):
    helper = Path(__file__).resolve().parents[1] / "native" / "target" / "release" / "wechat-capture-helper.exe"
    try:
        last_sequence = 0
        last_stamp = 0
        slow_tested = False
        with WindowCapture([str(helper), "--diagnostic-window", str(hwnd)]) as capture:
            while True:
                try:
                    frame = capture.read(timeout=2)
                except CaptureEnded:
                    break
                if frame is None:
                    raise AssertionError("Timed out waiting for real capture frame")
                assert frame.sequence > last_sequence
                assert frame.timestamp_100ns >= last_stamp
                last_sequence, last_stamp = frame.sequence, frame.timestamp_100ns
                converted = fit_frame(frame.image, 640, 360)
                assert converted.shape == (360, 640, 3)
                assert converted.flags.c_contiguous
                phase = state["phase"]
                if phase == "initial" and not slow_tested:
                    # Let the capture reader continue while the consumer pauses.
                    time.sleep(.8)
                    fresh = capture.read(timeout=1)
                    assert fresh is not None and fresh.sequence > frame.sequence + 1
                    age_s = (time.perf_counter_ns() - fresh.timestamp_100ns * 100) / 1e9
                    assert -.1 < age_s < .7, f"stale frame: age={age_s:.3f}s"
                    last_sequence, last_stamp = fresh.sequence, fresh.timestamp_100ns
                    slow_tested = True
                state["counts"][phase] = state["counts"].get(phase, 0) + 1
                if phase == "initial":
                    state["initial_size"] = (frame.width, frame.height)
                if phase == "resize":
                    state["resize_size"] = (frame.width, frame.height)
                if phase == "covered":
                    pixel = frame.image[frame.height // 2, frame.width // 2, :3]
                    # green/blue animated canvas, never red overlay in BGRA order
                    assert int(pixel[2]) < 30 and max(int(pixel[0]), int(pixel[1])) > 80, pixel
            assert state["phase"] == "closed"
        assert state["counts"].get("initial", 0) > 1, state
        assert state["counts"].get("resize", 0) > 1, state
        assert state["counts"].get("covered", 0) > 1, state
        assert state["initial_size"] != state["resize_size"], state
    except BaseException as exc:
        state["error"] = repr(exc)

root.update()
ctypes.windll.user32.GetParent.argtypes = [ctypes.c_void_p]
ctypes.windll.user32.GetParent.restype = ctypes.c_void_p
hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
thread = threading.Thread(target=worker, args=(hwnd,), daemon=True)
thread.start()
animate()
root.after(2200, resize)
root.after(4400, cover)
root.after(6600, close_window)
root.mainloop()
thread.join(timeout=8)
if thread.is_alive():
    raise SystemExit("Capture did not stop after the test window closed")
if state["error"]:
    raise SystemExit(state["error"])
print("Real WGC integration passed:", state["counts"], state["initial_size"], "->", state["resize_size"])
