import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from virtual_camera import fit_frame, main, stream
from wechat_capture import CaptureEnded


class VirtualCameraTests(unittest.TestCase):
    def test_aspect_ratio_and_colors(self):
        image = np.zeros((2, 2, 4), dtype=np.uint8)
        image[:] = (10, 20, 200, 255)
        output = fit_frame(image, 8, 4)
        self.assertEqual(output.shape, (4, 8, 3))
        self.assertTrue(np.all(output[:, :2] == 0))
        self.assertTrue(np.all(output[:, 6:] == 0))
        self.assertEqual(output[2, 3].tolist(), [10, 20, 200])
        self.assertTrue(output.flags.c_contiguous)

    def test_portrait_output_preserves_landscape_and_portrait_aspect(self):
        landscape = np.full((4, 8, 4), (10, 20, 200, 255), dtype=np.uint8)
        portrait = fit_frame(landscape, 8, 16)
        self.assertEqual(portrait.shape, (16, 8, 3))
        self.assertTrue(np.all(portrait[:6] == 0))
        self.assertTrue(np.all(portrait[10:] == 0))
        self.assertEqual(portrait[8, 4].tolist(), [10, 20, 200])
        cropped_vertical = np.full((8, 4, 4), (3, 4, 5, 255), dtype=np.uint8)
        full_portrait = fit_frame(cropped_vertical, 8, 16)
        self.assertEqual(full_portrait[0, 0].tolist(), [3, 4, 5])
        self.assertEqual(full_portrait[-1, -1].tolist(), [3, 4, 5])

    def test_fill_portrait_crops_sides_without_black_bars(self):
        image = np.zeros((8, 16, 4), np.uint8)
        image[:, :, 0] = np.arange(16) * 10
        image[:, :, 3] = 255
        output = fit_frame(image, 4, 8, fill=True)
        self.assertEqual(output.shape, (8, 4, 3))
        self.assertEqual(output[4, 0, 0], 60)
        self.assertEqual(output[4, -1, 0], 90)
        self.assertTrue(np.all(output[:, :, 0] > 0))

    def test_zoom_fills_and_crops_center(self):
        image = np.zeros((8, 8, 4), np.uint8)
        image[:, :, 0] = np.arange(8) * 20
        image[:, :, 3] = 255
        output = fit_frame(image, 8, 8, fill=True, zoom=2)
        self.assertGreaterEqual(output[4, 0, 0], 35)
        self.assertLessEqual(output[4, -1, 0], 105)
        with self.assertRaises(ValueError):
            fit_frame(image, 8, 8, fill=True, zoom=0.5)

    def test_repeats_frame_and_handles_resize(self):
        a = SimpleNamespace(image=np.full((2, 2, 4), (1, 2, 3, 255), dtype=np.uint8))
        b = SimpleNamespace(image=np.full((1, 4, 4), (4, 5, 6, 255), dtype=np.uint8))

        class Capture:
            items = iter((a, None, b))
            def read(self, timeout):
                try:
                    return next(self.items)
                except StopIteration:
                    raise CaptureEnded()

        class Camera:
            width, height = 8, 4
            def __init__(self): self.frames = []
            def send(self, frame): self.frames.append(frame.copy())
            def sleep_until_next_frame(self): pass

        camera = Camera()
        with self.assertRaises(CaptureEnded):
            stream(Capture(), camera)
        self.assertEqual(len(camera.frames), 3)
        np.testing.assert_array_equal(camera.frames[0], camera.frames[1])
        self.assertEqual(camera.frames[0][2, 4].tolist(), [1, 2, 3])
        self.assertEqual(camera.frames[2][2, 4].tolist(), [4, 5, 6])

    def test_camera_initialization_error_is_reported(self):
        with patch("virtual_camera.pyvirtualcam.Camera", side_effect=RuntimeError("device missing")):
            self.assertEqual(main([]), 1)

    def test_reject_odd_dimensions_and_high_fps(self):
        for args in (["--width", "1279"], ["--fps", "61"]):
            with self.assertRaises(SystemExit):
                main(args)

if __name__ == "__main__":
    unittest.main()
