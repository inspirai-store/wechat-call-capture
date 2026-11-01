import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from wechat_capture import CaptureEnded, CaptureError, WindowCapture

HELPER = pathlib.Path(__file__).with_name("fake_helper.py")

class CaptureTests(unittest.TestCase):
    def capture(self, mode):
        return WindowCapture([sys.executable, str(HELPER), mode])

    def test_frames_resize_latest_and_timestamp(self):
        with self.capture("frames") as capture:
            first = capture.read(3)
            self.assertEqual((first.width, first.height, first.timestamp_100ns), (2, 2, 10))
            self.assertEqual(first.image[0, 0].tolist(), [1, 2, 3, 255])
            time.sleep(.7)  # Consumer is slower than the producer.
            latest = capture.read(1)
            self.assertEqual((latest.width, latest.height, latest.timestamp_100ns), (3, 2, 30))
            self.assertGreaterEqual(latest.sequence - first.sequence, 2)
            self.assertEqual(latest.image[0, 0].tolist(), [3, 4, 5, 255])
            self.assertIsNone(capture.read(.05))

    def test_selection_canceled(self):
        with self.capture("cancel") as capture:
            with self.assertRaisesRegex(CaptureError, "取消"):
                capture.read(3)

    def test_backend_error(self):
        with self.capture("error") as capture:
            with self.assertRaisesRegex(CaptureError, "simulated backend error"):
                capture.read(3)

    def test_truncated_frame(self):
        with self.capture("partial") as capture:
            with self.assertRaisesRegex(CaptureError, "不完整"):
                capture.read(3)

    def test_close_interrupts_reader(self):
        capture = self.capture("sleep")
        self.assertIsNone(capture.read(.05))
        capture.close()
        capture.close()
        with self.assertRaises(CaptureEnded):
            capture.read(0)

if __name__ == "__main__":
    unittest.main()
