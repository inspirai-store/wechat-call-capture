'use strict';
let token = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
const preview = document.getElementById('preview');
const status = document.getElementById('status');
const start = document.getElementById('start');
const stop = document.getElementById('stop');
const select = document.getElementById('camera');
const pairing = document.getElementById('pairing');
const pairButton = document.getElementById('pair');
const roomCode = document.getElementById('room-code');
let peer = null, stream = null, session = null;
const headers = () => ({Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'});
if (!token) { pairing.hidden = false; start.disabled = true; status.textContent = '请输入房间码加入房间。'; }
pairButton.addEventListener('click', async () => {
  pairButton.disabled = true;
  try {
    const code = roomCode.value.trim();
    if (!/^\d{8}$/.test(code)) throw new Error('请输入 8 位房间码');
    const response = await fetch('/api/pair', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code})});
    if (!response.ok) throw new Error(response.status >= 500 ? '电脑房间未启动或连接已断开' : response.status === 429 ? '尝试次数过多，请稍后再试' : response.status === 410 ? '房间码已过期，请重新开始采集' : '房间码不正确');
    token = (await response.json()).token;
    roomCode.value = ''; pairing.hidden = true; start.disabled = false;
    status.textContent = '已加入房间，点击开始并允许摄像头。';
  } catch (error) { status.textContent = error.message || '无法加入房间'; }
  finally { pairButton.disabled = false; }
});

async function stopCapture() {
  const oldSession = session;
  session = null;
  if (peer) { peer.close(); peer = null; }
  if (stream) { stream.getTracks().forEach(track => track.stop()); stream = null; }
  preview.srcObject = null;
  if (oldSession) {
    try { await fetch('/api/stop', {method: 'POST', headers: headers(), body: JSON.stringify({session: oldSession})}); } catch (_) {}
  }
  start.disabled = false; select.disabled = false; stop.disabled = true;
}

async function waitForIce(pc) {
  if (pc.iceGatheringState === 'complete') return;
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pc.removeEventListener('icegatheringstatechange', check); reject(new Error('网络候选地址收集超时')); }, 20000);
    function check() {
      if (pc.iceGatheringState === 'complete') { clearTimeout(timer); pc.removeEventListener('icegatheringstatechange', check); resolve(); }
    }
    pc.addEventListener('icegatheringstatechange', check);
    check();
  });
}

start.addEventListener('click', async () => {
  start.disabled = true; select.disabled = true;
  try {
    if (!token) throw new Error('链接缺少连接凭据，请重新扫描电脑上的二维码');
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error('浏览器不支持摄像头，请使用 iPhone Safari 并通过 HTTPS 打开');
    status.textContent = '等待摄像头授权…';
    stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {facingMode: {ideal: select.value}, width: {ideal: 1280}, height: {ideal: 720}, frameRate: {ideal: 30, max: 30}}});
    preview.srcObject = stream;
    const configResponse = await fetch('/api/ice', {headers: headers(), cache: 'no-store'});
    if (!configResponse.ok) throw new Error('连接已失效，请重新扫描电脑上的二维码');
    const config = await configResponse.json();
    peer = new RTCPeerConnection(config);
    const pc = peer;
    stream.getVideoTracks().forEach(track => pc.addTrack(track, stream));
    pc.onconnectionstatechange = () => {
      if (peer !== pc) return;
      if (pc.connectionState === 'connected') status.textContent = '传输中，请保持本页面打开。';
      if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
        status.textContent = '连接中断。跨网络连接可能需要配置 TURN 中继。';
        stopCapture();
      }
    };
    status.textContent = '正在连接电脑…';
    await pc.setLocalDescription(await pc.createOffer());
    await waitForIce(pc);
    const response = await fetch('/api/offer', {method: 'POST', headers: headers(), body: JSON.stringify(pc.localDescription)});
    if (!response.ok) throw new Error(response.status === 409 ? '电脑已连接另一台手机' : '电脑拒绝了连接，请重试');
    const answer = await response.json();
    session = answer.session;
    await pc.setRemoteDescription({type: answer.type, sdp: answer.sdp});
    stop.disabled = false;
    status.textContent = '正在建立视频连接…';
  } catch (error) {
    await stopCapture();
    status.textContent = '无法开始：' + (error.message || error.name || '未知错误');
  }
});
stop.addEventListener('click', async () => { await stopCapture(); status.textContent = '已停止传输。'; });
