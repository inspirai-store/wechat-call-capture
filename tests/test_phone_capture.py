import asyncio
from queue import Queue
import unittest
from urllib.parse import urlsplit

import aiohttp
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame as AvFrame
import numpy as np

from phone_capture import PhoneCapture


class TestTrack(VideoStreamTrack):
    async def recv(self):
        pts, time_base = await self.next_timestamp()
        data = np.full((120, 80, 3), (0, 180, 0), np.uint8)
        frame = AvFrame.from_ndarray(data, format='bgr24')
        frame.pts, frame.time_base = pts, time_base
        return frame


class PhoneCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_webrtc_frames_auth_busy_and_stop(self):
        events = Queue()
        capture = PhoneCapture(tunnel=False, ice_servers=[], on_event=lambda *e: events.put(e))
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        try:
            event, pair = await asyncio.to_thread(events.get, True, 5)
            self.assertEqual(event, 'phone_pair')
            url = pair['url']
            parts = urlsplit(url)
            base = f'{parts.scheme}://{parts.netloc}'
            headers = {'Authorization': 'Bearer ' + parts.fragment}
            async with aiohttp.ClientSession() as client:
                async with client.post(base + '/api/pair', json={'code': 'wrong'}) as response:
                    self.assertEqual(response.status, 401)
                async with client.post(base + '/api/pair', json={'code': pair['code']}) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual((await response.json())['token'], parts.fragment)
                async with client.get(base + '/api/ice') as response:
                    self.assertEqual(response.status, 401)
                async with client.get(base + '/api/health') as response:
                    self.assertEqual(response.status, 401)
                async with client.get(base + '/api/health', headers=headers) as response:
                    self.assertEqual(response.status, 204)
                async with client.get(base + '/api/ice', headers=headers) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual((await response.json())['iceServers'], [])
                pc.addTrack(TestTrack())
                await pc.setLocalDescription(await pc.createOffer())
                offer = {'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type}
                async with client.post(base + '/api/offer', headers=headers, json=offer) as response:
                    self.assertEqual(response.status, 200)
                    answer = await response.json()
                await pc.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'], type=answer['type']))
                async with client.post(base + '/api/offer', headers=headers, json=offer) as response:
                    self.assertEqual(response.status, 409)
                frame = None
                for _ in range(80):
                    frame = await asyncio.to_thread(capture.read, .1)
                    if frame is not None and frame.width == 80:
                        break
                self.assertIsNotNone(frame)
                self.assertEqual((frame.width, frame.height), (80, 120))
                self.assertGreater(int(frame.image[60, 40, 1]), 140)
                async with client.post(base + '/api/stop', headers=headers, json={'session': answer['session']}) as response:
                    self.assertEqual(response.status, 204)
                blank = await asyncio.to_thread(capture.read, 1)
                self.assertIsNotNone(blank)
                self.assertEqual((blank.width, blank.height), (2, 2))
                self.assertEqual(int(blank.image.max()), 0)
        finally:
            await pc.close()
            await asyncio.to_thread(capture.close)

    async def test_close_immediately(self):
        capture = PhoneCapture(tunnel=False, ice_servers=[])
        await asyncio.to_thread(capture.close)
        self.assertFalse(capture._thread.is_alive())

    async def test_pairing_rate_limit_and_expiry(self):
        events = Queue()
        capture = PhoneCapture(tunnel=False, ice_servers=[], on_event=lambda *e: events.put(e))
        try:
            _, pair = await asyncio.to_thread(events.get, True, 5)
            async with aiohttp.ClientSession() as client:
                for _ in range(5):
                    async with client.post(pair['base'] + '/api/pair', json={'code': 'incorrect'}) as response:
                        self.assertEqual(response.status, 401)
                async with client.post(pair['base'] + '/api/pair', json={'code': pair['code']}) as response:
                    self.assertEqual(response.status, 429)
                capture._pair_locked_until = 0
                capture._pair_expires = 0
                async with client.post(pair['base'] + '/api/pair', json={'code': pair['code']}) as response:
                    self.assertEqual(response.status, 410)
        finally:
            await asyncio.to_thread(capture.close)


if __name__ == '__main__':
    unittest.main()
