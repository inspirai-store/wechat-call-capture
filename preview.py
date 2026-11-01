"""Interactive preview: select a WeChat video-call window and press q to exit."""
import cv2
from wechat_capture import CaptureEnded, CaptureError, WindowCapture


def main():
    print("请在系统选择器中选择微信的视频通话窗口。按 q 或 Esc 退出。", flush=True)
    try:
        with WindowCapture() as capture:
            while True:
                frame = capture.read(timeout=0.1)
                if frame is not None:
                    cv2.imshow("WeChat call capture (q to exit)", frame.image)
                if cv2.waitKey(1) & 0xff in (27, ord("q")):
                    break
    except CaptureEnded:
        print("窗口已关闭，采集结束。")
    except CaptureError as exc:
        print(f"采集未能继续：{exc}")
        return 1
    finally:
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
