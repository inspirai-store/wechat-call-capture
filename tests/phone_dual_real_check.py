"""Synthetic WebRTC sender -> Session processing -> two real UVC devices."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
import subprocess
import sys
from urllib.parse import urlsplit

import aiohttp
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Session
from phone_capture import PhoneCapture
from usbip_camera import UsbipCameraPair

profiles = ((53251, 53252, '1-11', 'Phone Test A', 0x5695),
            (53253, 53254, '1-12', 'Phone Test B', 0x5696))

class TestTrack(VideoStreamTrack):
    async def recv(self):
        pts, base = await self.next_timestamp()
        image = np.full((320, 180, 3), (0, 180, 0), np.uint8)
        frame = VideoFrame.from_ndarray(image, format='bgr24')
        frame.pts, frame.time_base = pts, base
        return frame

def read_camera(name):
    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'dshow',
        '-video_size', '720x1280', '-framerate', '15', '-i', 'video=' + name, '-frames:v', '3',
        '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1'], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=35)
    if result.returncode or len(result.stdout) < 720 * 1280 * 3:
        raise RuntimeError(name + ': ' + result.stderr.decode(errors='replace'))
    frame = np.frombuffer(result.stdout[-720*1280*3:], np.uint8).reshape(1280,720,3)
    pixel = frame[640,360].tolist()
    if pixel[1] < 130 or pixel[0] > 40 or pixel[2] > 40:
        raise AssertionError(name + ' unexpected pixel: ' + str(pixel))
    return name, pixel

async def main():
    events = Queue()
    capture = PhoneCapture(tunnel=False, ice_servers=[], on_event=lambda *e: events.put(e))
    session = Session('camera', 720, 1280, 15, 'usbip_pair', source_kind='phone',
        camera_factory=lambda **kw: UsbipCameraPair(profiles=profiles, **kw), capture_factory=lambda: capture)
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    session.set_framing(True, 1.0)
    session.start()
    try:
        _, pair = await asyncio.to_thread(events.get, True, 10)
        url = pair['url']
        parts = urlsplit(url)
        headers = {'Authorization': 'Bearer ' + parts.fragment}
        base = f'{parts.scheme}://{parts.netloc}'
        pc.addTrack(TestTrack())
        await pc.setLocalDescription(await pc.createOffer())
        async with aiohttp.ClientSession() as client:
            async with client.post(base + '/api/offer', headers=headers,
                    json={'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type}) as response:
                if response.status != 200: raise RuntimeError(f'offer: {response.status}')
                answer = await response.json()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'], type=answer['type']))
        for _ in range(150):
            if not session.thread.is_alive(): raise RuntimeError(str(list(session.events.queue)))
            preview = session.take_preview()
            if preview is not None and preview[0][180,320,1] > 130:
                break
            await asyncio.sleep(.1)
        else: raise RuntimeError('Phone preview timed out')
        for _ in range(30):
            listed = await asyncio.to_thread(subprocess.run,
                ['ffmpeg', '-hide_banner', '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            names = listed.stderr.decode(errors='replace')
            if all(p[3] in names for p in profiles): break
            await asyncio.sleep(.5)
        else: raise RuntimeError('Test devices were not enumerated')
        def read_both():
            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(read_camera, (p[3] for p in profiles)))
        print('WebRTC -> processing -> two UVC cameras:', await asyncio.to_thread(read_both), flush=True)
    finally:
        await pc.close()
        session.stop()
        await asyncio.to_thread(session.thread.join, 12)
        if session.thread.is_alive(): raise RuntimeError('Session did not stop')

asyncio.run(main())
