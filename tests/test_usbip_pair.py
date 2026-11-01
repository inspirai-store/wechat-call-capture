import unittest
from unittest.mock import patch

import numpy as np

from usbip_camera import UsbipCameraPair


class PairTests(unittest.TestCase):
    def test_both_receive_same_encoded_frame_and_close(self):
        made = []
        class Camera:
            def __init__(self, **kwargs):
                self.received = []
                self.closed = False
                made.append(self)
            def send_encoded(self, payload): self.received.append(payload)
            def sleep_until_next_frame(self): pass
            def close(self): self.closed = True
        pair = UsbipCameraPair(4, 4, 15, camera_factory=Camera)
        frame = np.zeros((4, 4, 3), np.uint8)
        with patch('usbip_camera.encode_frame', return_value=b'frame') as encode:
            pair.send(frame)
            pair.send(frame)
            pair.send(frame.copy())
        self.assertEqual(encode.call_count, 2)
        self.assertEqual([c.received for c in made], [[b'frame', b'frame']] * 2)
        pair.close()
        self.assertTrue(all(c.closed for c in made))

    def test_second_start_failure_releases_first(self):
        made = []
        class Camera:
            def __init__(self, **kwargs):
                if made:
                    raise RuntimeError('second failed')
                self.closed = False
                made.append(self)
            def close(self): self.closed = True
        with self.assertRaisesRegex(RuntimeError, 'second failed'):
            UsbipCameraPair(4, 4, 15, camera_factory=Camera)
        self.assertTrue(made[0].closed)

    def test_close_attempts_both_even_if_one_fails(self):
        made = []
        class Camera:
            def __init__(self, **kwargs):
                self.closed = False
                made.append(self)
            def close(self):
                self.closed = True
                if self is made[1]: raise RuntimeError('detach failed')
        pair = UsbipCameraPair(4, 4, 15, camera_factory=Camera)
        with self.assertRaisesRegex(RuntimeError, 'detach failed'):
            pair.close()
        self.assertTrue(all(c.closed for c in made))


if __name__ == '__main__':
    unittest.main()
