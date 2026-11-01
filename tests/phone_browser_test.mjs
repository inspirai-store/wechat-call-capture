// Runs with ego-browser against the loopback-only synthetic fixture.
const task = await taskSpace('phone room pairing synthetic test');
const page = task.page('p1');
const fixture = await (await fetch('http://127.0.0.1:38765/')).json();
const parsed = new URL(fixture.url);
const injected = `const canvas = document.createElement('canvas'); canvas.width = 180; canvas.height = 320;
  const context = canvas.getContext('2d');
  const paint = () => { context.fillStyle = '#00b400'; context.fillRect(0, 0, canvas.width, canvas.height); };
  paint(); setInterval(paint, 100);
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {value: async () => canvas.captureStream(15)});`;
try {
  await page.cdp('Page.addScriptToEvaluateOnNewDocument', {source: injected});
  await page.goto(parsed.origin + '/');
  await page.fill('#room-code', fixture.code);
  await page.click('#pair');
  await page.waitForFunction(() => document.getElementById('status').textContent.includes('已加入房间'), undefined, {timeout: 10000});
  await page.click('#start');
  await page.waitForFunction(() => document.getElementById('status').textContent.includes('传输中'), undefined, {timeout: 30000});
  console.log('Manual room-code pairing: connected');
  await page.click('#stop');
  await page.waitForFunction(() => document.getElementById('status').textContent.includes('已停止传输'), undefined, {timeout: 10000});
  await page.cdp('Page.addScriptToEvaluateOnNewDocument', {source: `history.replaceState(null, '', location.pathname + ${JSON.stringify(parsed.hash)});`});
  await page.goto(parsed.origin + '/');
  await page.click('#start');
  await page.waitForFunction(() => document.getElementById('status').textContent.includes('传输中'), undefined, {timeout: 30000});
  console.log('QR-style direct pairing: connected');
  await page.click('#stop');
  await page.waitForFunction(() => document.getElementById('status').textContent.includes('已停止传输'), undefined, {timeout: 10000});
} finally {
  await task.finish({keep: []});
}
