"""Publish a selected window to an existing Windows virtual camera."""
from __future__ import annotations

import argparse

import cv2
import numpy as np
import pyvirtualcam

from wechat_capture import CaptureEnded, CaptureError, WindowCapture


def fit_frame(image: np.ndarray, width: int, height: int, *, fill: bool = False, zoom: float = 1.0) -> np.ndarray:
    """Convert BGRA to BGR, fitting or filling the output; zoom is centered."""
    if zoom < 1.0 or zoom > 3.0:
        raise ValueError("zoom must be between 1 and 3")
    source_height, source_width = image.shape[:2]
    fit_scale = min(width / source_width, height / source_height)
    scale = (max(width / source_width, height / source_height) if fill else fit_scale) * zoom
    # Crop only source pixels needed for the output, avoiding enormous intermediate images.
    visible_width = min(source_width, width / scale)
    visible_height = min(source_height, height / scale)
    left = max(0, int((source_width - visible_width) / 2))
    top = max(0, int((source_height - visible_height) / 2))
    right = min(source_width, int(np.ceil((source_width + visible_width) / 2)))
    bottom = min(source_height, int(np.ceil((source_height + visible_height) / 2)))
    source = image[top:bottom, left:right]
    bgr = cv2.cvtColor(source, cv2.COLOR_BGRA2BGR)
    out_width = max(1, round((right - left) * scale))
    out_height = max(1, round((bottom - top) * scale))
    resized = cv2.resize(bgr, (out_width, out_height), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    # Center and trim rounding overflow, or letterbox where the image is smaller.
    copy_width, copy_height = min(width, out_width), min(height, out_height)
    in_x, in_y = max(0, (out_width - width) // 2), max(0, (out_height - height) // 2)
    out_x, out_y = max(0, (width - out_width) // 2), max(0, (height - out_height) // 2)
    output = np.zeros((height, width, 3), dtype=np.uint8)
    output[out_y:out_y + copy_height, out_x:out_x + copy_width] = resized[in_y:in_y + copy_height, in_x:in_x + copy_width]
    return output


def stream(capture, camera, *, preview=False):
    """Repeat the most recent frame at the camera rate without queuing old frames."""
    output = np.zeros((camera.height, camera.width, 3), dtype=np.uint8)
    while True:
        frame = capture.read(timeout=0)
        if frame is not None:
            output = fit_frame(frame.image, camera.width, camera.height)
        camera.send(output)
        if preview:
            cv2.imshow("Virtual camera output (q to exit)", output)
            if cv2.waitKey(1) & 0xff in (27, ord("q")):
                break
        camera.sleep_until_next_frame()


def positive_int(value):
    try:
        value = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是整数") from exc
    if value <= 0 or value > 3840:
        raise argparse.ArgumentTypeError("必须在 1 到 3840 之间")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="将微信通话窗口画面输出为虚拟摄像头。")
    parser.add_argument("--width", type=positive_int, default=1280, help="输出宽度（默认 1280）")
    parser.add_argument("--height", type=positive_int, default=720, help="输出高度（默认 720）")
    parser.add_argument("--fps", type=positive_int, default=30, help="输出帧率（默认 30，最高 60）")
    parser.add_argument("--backend", choices=("obs", "unitycapture"), help="指定虚拟摄像头后端")
    parser.add_argument("--preview", action="store_true", help="显示发送给虚拟摄像头的画面")
    args = parser.parse_args(argv)
    if args.width % 2 or args.height % 2:
        parser.error("输出宽度和高度必须为偶数")
    if args.fps > 60:
        parser.error("帧率不能超过 60")
    try:
        with pyvirtualcam.Camera(width=args.width, height=args.height, fps=args.fps,
                                 fmt=pyvirtualcam.PixelFormat.BGR, backend=args.backend) as camera:
            print(f"虚拟摄像头：{camera.device}；{args.width}x{args.height}，{args.fps} fps", flush=True)
            print("请在系统选择器中选择微信视频通话窗口；按 Ctrl+C 停止。", flush=True)
            with WindowCapture() as capture:
                stream(capture, camera, preview=args.preview)
    except CaptureEnded:
        print("通话窗口已关闭，虚拟摄像头输出结束。")
    except CaptureError as exc:
        print(f"窗口采集失败：{exc}")
        return 1
    except RuntimeError as exc:
        print(f"虚拟摄像头无法启动或输出：{exc}\n请确认已安装 OBS Studio 或 Unity Capture，且其虚拟摄像头未被其他程序占用。")
        return 1
    except KeyboardInterrupt:
        print("\n虚拟摄像头输出已停止。")
    finally:
        if args.preview:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
