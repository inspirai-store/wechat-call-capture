"""Receive one phone's video over WebRTC; never queue old decoded frames."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
import threading
import time
from urllib.parse import urlsplit

import numpy as np
import aiohttp
from aiohttp import web
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

from wechat_capture import CaptureError, VideoFrame

ROOT = Path(__file__).resolve().parent
DEFAULT_ICE = [{'urls': 'stun:stun.l.google.com:19302'}]
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def ice_config():
    """Load optional ICE config from the process environment, without logging it."""
    raw = os.environ.get('PHONE_ICE_SERVERS')
    try:
        servers = json.loads(raw) if raw else DEFAULT_ICE
        if not isinstance(servers, list) or not servers or len(servers) > 8:
            raise ValueError()
        result = []
        for server in servers:
            if not isinstance(server, dict) or not isinstance(server.get('urls'), (str, list)):
                raise ValueError()
            urls = server['urls']
            if isinstance(urls, str):
                urls = [urls]
            if not urls or any(not isinstance(x, str) or not x.startswith(('stun:', 'stuns:', 'turn:', 'turns:')) for x in urls):
                raise ValueError()
            item = {'urls': urls}
            for key in ('username', 'credential'):
                if key in server:
                    if not isinstance(server[key], str):
                        raise ValueError()
                    item[key] = server[key]
            result.append(item)
        return result
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CaptureError('PHONE_ICE_SERVERS 格式无效，请检查 WebRTC 中继配置。') from exc


def cloudflared_path():
    configured = os.environ.get('CLOUDFLARED_PATH')
    if configured:
        return configured
    local = ROOT / 'tools' / 'cloudflared.exe'
    return str(local) if local.is_file() else shutil.which('cloudflared')


class PhoneCapture:
    """Thread-safe latest-frame interface matching WindowCapture."""
    def __init__(self, *, on_event=None, tunnel=True, ice_servers=None, public_base=None):
        self._on_event = on_event or (lambda *_: None)
        self._ice = ice_servers if ice_servers is not None else ice_config()
        self._tunnel_enabled = tunnel
        self._public_base = public_base
        if public_base:
            parsed = urlsplit(public_base)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                raise CaptureError('自有 HTTPS 地址必须是域名根地址，例如 https://camera.example.com。')
            self._public_base = public_base.rstrip('/')
        self._token = secrets.token_urlsafe(24)
        self._condition = threading.Condition()
        self._latest = None
        self._sequence = 0
        self._delivered = 0
        self._error = None
        self._closed = False
        self._loop = None
        self._task = None
        self._runner = None
        self._tunnel = None
        self._peer = None
        self._peer_id = None
        self._track_task = None
        self._thread = threading.Thread(target=self._thread_main, name='phone-webrtc', daemon=True)
        self._thread.start()

    def _emit(self, kind, value):
        self._on_event(kind, value)

    def _thread_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._task = loop.create_task(self._serve())
        with self._condition:
            if self._closed:
                self._task.cancel()
        try:
            loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            with self._condition:
                self._error = CaptureError(f'手机连接服务失败：{exc}')
                self._condition.notify_all()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _serve(self):
        try:
            app = web.Application(client_max_size=160 * 1024)
            app.router.add_get('/', self._index)
            app.router.add_get('/phone.js', self._javascript)
            app.router.add_get('/api/ice', self._get_ice)
            app.router.add_post('/api/offer', self._offer)
            app.router.add_post('/api/stop', self._stop_peer)
            app.router.add_get('/phone.css', self._stylesheet)
            self._runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
            await self._runner.setup()
            site = web.TCPSite(self._runner, '127.0.0.1', 8765 if self._public_base else 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            ready = True
            if self._public_base:
                base = self._public_base
            elif self._tunnel_enabled:
                executable = cloudflared_path()
                if not executable:
                    raise RuntimeError('未找到 cloudflared，请按 README 安装。')
                self._emit('status', '正在创建临时 HTTPS 连接…')
                self._tunnel = await asyncio.create_subprocess_exec(
                    executable, 'tunnel', '--no-autoupdate', '--protocol', 'http2', '--url', f'http://127.0.0.1:{port}',
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                    creationflags=CREATE_NO_WINDOW)
                base = await asyncio.wait_for(self._tunnel_url(), timeout=45)
                self._emit('status', '正在等待 HTTPS 地址生效…')
                ready = await self._wait_public(base)
            else:
                base = f'http://127.0.0.1:{port}'
            self._emit('phone_url', f'{base}/#{self._token}')
            self._emit('status', '请用手机打开连接并允许摄像头' if ready else
                       '公网入口未通过电脑网络检查，可用手机尝试或改用自有 HTTPS 入口')
            if self._tunnel:
                await self._tunnel.wait()
                raise RuntimeError('HTTPS 隧道已断开，请停止后重新开始。')
            await asyncio.Event().wait()
        except asyncio.TimeoutError as exc:
            raise RuntimeError('HTTPS 隧道连接超时，请检查网络或 Cloudflare 连接。') from exc
        finally:
            if self._peer:
                await self._drop_peer(self._peer, notify=False)
            if self._runner:
                await self._runner.cleanup()
            if self._tunnel and self._tunnel.returncode is None:
                self._tunnel.terminate()
                try:
                    await asyncio.wait_for(self._tunnel.wait(), 5)
                except asyncio.TimeoutError:
                    self._tunnel.kill()
                    await self._tunnel.wait()

    async def _tunnel_url(self):
        while True:
            line = await self._tunnel.stderr.readline()
            if not line:
                raise RuntimeError('HTTPS 隧道未能启动。')
            match = re.search(rb'https://[a-z0-9-]+\.trycloudflare\.com', line, re.I)
            if match:
                # Drain stderr so the subprocess cannot block on a full pipe.
                asyncio.create_task(self._drain_tunnel())
                return match.group().decode('ascii')

    async def _drain_tunnel(self):
        while await self._tunnel.stderr.read(4096):
            pass

    async def _wait_public(self, base):
        """Avoid presenting a new quick-tunnel URL before DNS and HTTPS are ready."""
        hostname = urlsplit(base).hostname
        end = time.monotonic() + 25
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            while time.monotonic() < end:
                if self._tunnel.returncode is not None:
                    raise RuntimeError('HTTPS 隧道已退出。')
                try:
                    async with client.get('https://cloudflare-dns.com/dns-query',
                            params={'name': hostname, 'type': 'A'},
                            headers={'Accept': 'application/dns-json', 'Cache-Control': 'no-cache'}) as response:
                        dns = await response.json(content_type=None)
                    addresses = [item['data'] for item in dns.get('Answer', []) if item.get('type') == 1]
                    if addresses:
                        reader, writer = await asyncio.wait_for(asyncio.open_connection(
                            addresses[0], 443, ssl=ssl.create_default_context(), server_hostname=hostname), 5)
                        try:
                            writer.write(f'GET / HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n'.encode('ascii'))
                            await writer.drain()
                            status = await asyncio.wait_for(reader.readline(), 5)
                            if re.match(rb'HTTP/1\.[01] 200\b', status):
                                return True
                        finally:
                            writer.close()
                            await writer.wait_closed()
                except (OSError, asyncio.TimeoutError, ValueError, aiohttp.ClientError):
                    pass
                await asyncio.sleep(2)
        return False

    def _authorized(self, request):
        return hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + self._token)

    @staticmethod
    def _headers():
        return {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                'X-Content-Type-Options': 'nosniff',
                'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'"}

    async def _index(self, request):
        return web.Response(text=(ROOT / 'phone_web' / 'index.html').read_text(encoding='utf-8'),
                            content_type='text/html', headers=self._headers())

    async def _javascript(self, request):
        return web.Response(text=(ROOT / 'phone_web' / 'phone.js').read_text(encoding='utf-8'),
                            content_type='application/javascript', headers=self._headers())

    async def _stylesheet(self, request):
        return web.Response(text=(ROOT / 'phone_web' / 'phone.css').read_text(encoding='utf-8'),
                            content_type='text/css', headers=self._headers())

    async def _get_ice(self, request):
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        return web.json_response({'iceServers': self._ice}, headers=self._headers())

    async def _offer(self, request):
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        if self._peer is not None:
            raise web.HTTPConflict(text='已有手机连接，请先断开。')
        try:
            data = await request.json()
            if data.get('type') != 'offer' or not isinstance(data.get('sdp'), str) or len(data['sdp']) > 128000:
                raise ValueError('invalid offer')
        except (ValueError, TypeError, AttributeError):
            raise web.HTTPBadRequest(text='无效的连接请求。')
        config = RTCConfiguration(iceServers=[RTCIceServer(**server) for server in self._ice])
        pc = RTCPeerConnection(configuration=config)
        self._peer = pc
        self._peer_id = secrets.token_urlsafe(16)

        @pc.on('track')
        def track_received(track):
            if track.kind == 'video' and self._track_task is None:
                self._track_task = asyncio.create_task(self._receive(track, pc))
            else:
                track.stop()

        @pc.on('connectionstatechange')
        async def state_changed():
            if pc.connectionState == 'connected':
                self._emit('status', '手机已连接，等待画面')
            elif pc.connectionState in ('failed', 'closed', 'disconnected'):
                await self._drop_peer(pc)

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=data['sdp'], type='offer'))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            asyncio.create_task(self._connection_timeout(pc))
            return web.json_response({'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type,
                                      'session': self._peer_id}, headers=self._headers())
        except Exception:
            await self._drop_peer(pc)
            raise web.HTTPBadRequest(text='WebRTC 协商失败。')

    async def _connection_timeout(self, pc):
        await asyncio.sleep(30)
        if self._peer is pc and pc.connectionState != 'connected':
            await self._drop_peer(pc)

    async def _stop_peer(self, request):
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        try:
            data = await request.json()
            session = data.get('session', '')
        except (ValueError, TypeError, AttributeError):
            raise web.HTTPBadRequest()
        if self._peer and isinstance(session, str) and hmac.compare_digest(session, self._peer_id or ''):
            await self._drop_peer(self._peer)
        return web.Response(status=204, headers=self._headers())

    async def _receive(self, track, pc):
        try:
            while self._peer is pc:
                frame = await track.recv()
                pixels = frame.to_ndarray(format='bgra')
                with self._condition:
                    self._sequence += 1
                    self._latest = VideoFrame(pixels, pixels.shape[1], pixels.shape[0],
                                              time.monotonic_ns() // 100, self._sequence)
                    self._condition.notify_all()
        except (MediaStreamError, asyncio.CancelledError):
            pass
        finally:
            await self._drop_peer(pc)

    async def _drop_peer(self, pc, notify=True):
        if self._peer is not pc:
            return
        self._peer = None
        self._peer_id = None
        task, self._track_task = self._track_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
        with self._condition:
            self._sequence += 1
            self._latest = VideoFrame(np.zeros((2, 2, 4), np.uint8), 2, 2,
                                      time.monotonic_ns() // 100, self._sequence)
            self._condition.notify_all()
        await pc.close()
        if notify and not self._closed:
            self._emit('status', '手机连接已断开，可重新打开链接连接')

    def read(self, timeout=0):
        with self._condition:
            self._condition.wait_for(lambda: self._sequence > self._delivered or self._error or self._closed, timeout)
            if self._error:
                raise self._error
            if self._sequence > self._delivered:
                self._delivered = self._sequence
                return self._latest
            return None

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if self._loop and self._task and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._task.cancel)
        self._thread.join(timeout=8)
        if self._thread.is_alive():
            raise RuntimeError('手机连接服务未能停止。')
