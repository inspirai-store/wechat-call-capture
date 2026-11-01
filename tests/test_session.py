import time
import unittest
from types import SimpleNamespace

import numpy as np

from app import Session, make_preview, preview_image_bounds, crop_frame, selection_from_drag
from wechat_capture import CaptureEnded


class FakeCamera:
    def __init__(self, **kwargs):
        self.width = kwargs['width']
        self.height = kwargs['height']
        self.device = 'Test camera'
        self.frames = []
        self.closed = False
    def send(self, image):
        self.frames.append(image.copy())
    def sleep_until_next_frame(self):
        time.sleep(.01)
    def close(self):
        self.closed = True


class FakeCapture:
    def __init__(self):
        self.closed = False
        self.count = 0
    def read(self, timeout):
        self.count += 1
        if self.count == 1:
            return SimpleNamespace(image=np.full((2, 2, 4), (10, 20, 30, 255), np.uint8))
        time.sleep(.01)
        return None
    def close(self):
        self.closed = True


class SessionTests(unittest.TestCase):
    def test_preview_preserves_output_aspect_ratio(self):
        image = np.full((480, 640, 3), (10, 20, 30), np.uint8)
        preview = make_preview(image)
        self.assertTrue(np.all(preview[:, :80] == 0))
        self.assertTrue(np.all(preview[:, 560:] == 0))
        self.assertEqual(preview[180, 320].tolist(), [30, 20, 10])

    def test_camera_preview_and_stop_release_resources(self):
        cameras, captures = [], []
        def camera_factory(**kwargs):
            camera = FakeCamera(**kwargs)
            cameras.append(camera)
            return camera
        def capture_factory():
            capture = FakeCapture()
            captures.append(capture)
            return capture
        session = Session('camera', 16, 8, 30, camera_factory=camera_factory, capture_factory=capture_factory)
        session.start()
        deadline = time.monotonic() + 2
        preview = None
        while time.monotonic() < deadline and preview is None:
            preview = session.take_preview()
            time.sleep(.02)
        session.stop()
        session.thread.join(2)
        self.assertFalse(session.thread.is_alive())
        self.assertIsNotNone(preview)
        preview_image, is_source, source_size = preview
        self.assertFalse(is_source)
        self.assertIsNone(source_size)
        self.assertEqual(preview_image.shape, (360, 640, 3))
        self.assertEqual(preview_image[180, 320].tolist(), [30, 20, 10])
        self.assertGreaterEqual(len(cameras[0].frames), 1)
        self.assertTrue(cameras[0].closed)
        self.assertTrue(captures[0].closed)
        self.assertEqual(list(session.events.queue)[-1], ('finished', '已停止'))

    def test_preview_does_not_open_virtual_camera(self):
        def no_camera(**kwargs):
            self.fail('preview must not open camera')
        session = Session('preview', 16, 8, 30, camera_factory=no_camera, capture_factory=FakeCapture)
        session.start()
        time.sleep(.1)
        session.stop()
        session.thread.join(2)
        self.assertFalse(session.thread.is_alive())
        self.assertIsNotNone(session.take_preview())

    def test_selection_maps_padding_reverse_drag_clamping_and_resize(self):
        bounds = preview_image_bounds(400, 400)
        self.assertEqual(bounds, (140, 0, 500, 360))
        self.assertIsNone(selection_from_drag((50, 40), (300, 300), bounds))
        self.assertIsNone(selection_from_drag((200, 40), (203, 42), bounds))
        crop = selection_from_drag((410, 270), (230, 90), bounds)
        np.testing.assert_allclose(crop, (.25, .25, .75, .75))
        clamped = selection_from_drag((410, 270), (0, -100), bounds)
        np.testing.assert_allclose(clamped, (0, 0, .75, .75))
        image = np.zeros((40, 40, 4), dtype=np.uint8)
        self.assertEqual(crop_frame(image, crop).shape, (20, 20, 4))
        self.assertEqual(crop_frame(np.zeros((80, 120, 4), np.uint8), crop).shape, (40, 60, 4))

    def test_changing_crop_updates_static_frame_and_reset(self):
        cameras = []
        def camera_factory(**kwargs):
            camera = FakeCamera(**kwargs)
            cameras.append(camera)
            return camera
        class TwoColors(FakeCapture):
            def read(self, timeout):
                self.count += 1
                if self.count == 1:
                    img = np.zeros((4, 8, 4), np.uint8)
                    img[:, :4] = (0, 0, 220, 255)
                    img[:, 4:] = (220, 0, 0, 255)
                    return SimpleNamespace(image=img)
                time.sleep(.005)
                return None
        session = Session('camera', 16, 8, 30, camera_factory=camera_factory, capture_factory=TwoColors)
        session.start()
        try:
            deadline = time.monotonic() + 2
            while (not cameras or not cameras[0].frames) and time.monotonic() < deadline:
                time.sleep(.01)
            session.set_mirror(True)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                frame = cameras[0].frames[-1]
                if frame[4, 2].tolist() == [220, 0, 0] and frame[4, 13].tolist() == [0, 0, 220]:
                    break
                time.sleep(.01)
            else:
                self.fail('mirror did not update without a new frame')
            session.set_mirror(False)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                frame = cameras[0].frames[-1]
                if frame[4, 2].tolist() == [0, 0, 220] and frame[4, 13].tolist() == [220, 0, 0]:
                    break
                time.sleep(.01)
            else:
                self.fail('disabling mirror did not restore the image')
            session.set_crop((0, 0, .5, 1))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                frame = cameras[0].frames[-1]
                if frame[4, 8].tolist() == [0, 0, 220]:
                    break
                time.sleep(.01)
            else:
                self.fail('crop did not update without a new frame')
            session.set_selection_mode(True)
            deadline = time.monotonic() + 2
            selected_preview = None
            while time.monotonic() < deadline:
                preview = session.take_preview()
                if preview is not None and preview[1]:
                    selected_preview = preview
                    break
                time.sleep(.02)
            self.assertIsNotNone(selected_preview)
            self.assertEqual(selected_preview[2], (8, 4))
            self.assertEqual(selected_preview[0][180, 100].tolist(), [220, 0, 0])
            self.assertEqual(selected_preview[0][180, 540].tolist(), [0, 0, 220])
            session.set_crop(None)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                frame = cameras[0].frames[-1]
                if frame[4, 2].tolist() == [0, 0, 220] and frame[4, 13].tolist() == [220, 0, 0]:
                    break
                time.sleep(.01)
            else:
                self.fail('reset did not restore the full image')
        finally:
            session.stop()
            session.thread.join(2)
        self.assertFalse(session.thread.is_alive())

    def test_missing_camera_reports_guidance(self):
        def no_camera(**kwargs):
            raise RuntimeError('OBS Virtual Camera device not found')
        session = Session('camera', 16, 8, 30, camera_factory=no_camera)
        session.start()
        session.thread.join(2)
        events = list(session.events.queue)
        self.assertIn('未检测到虚拟摄像头', events[-1][1])
        self.assertIn('注册', events[-1][1])

    def test_closed_window_reports_and_closes_camera(self):
        camera = FakeCamera(width=16, height=8)
        class ClosedCapture(FakeCapture):
            def read(self, timeout): raise CaptureEnded()
        session = Session('camera', 16, 8, 30, camera_factory=lambda **kw: camera, capture_factory=ClosedCapture)
        session.start()
        session.thread.join(2)
        self.assertTrue(camera.closed)
        self.assertEqual(list(session.events.queue)[-1], ('finished', '采集窗口已关闭'))

if __name__ == '__main__':
    unittest.main()
