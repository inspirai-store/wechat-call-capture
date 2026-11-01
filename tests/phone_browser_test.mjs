// Runs with ego-browser against the loopback-only synthetic fixture.
const task = await taskSpace('phone capture synthetic test');
const page = task.page('p1');
const fixtureUrl = await (await fetch('http://127.0.0.1:38765/')).text();
const parsed = new URL(fixtureUrl);
const injected = `history.replaceState(null, '', location.pathname + ${JSON.stringify(parsed.hash)});
  const canvas = document.createElement('canvas'); canvas.width = 180; canvas.height = 320;
  const context = canvas.getContext('2d');
  const paint = () => { context.fillStyle = '#00b400'; context.fillRect(0, 0, canvas.width, canvas.height); };
  paint(); setInterval(paint, 100);
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {value: async () => canvas.captureStream(15)});`;
try {
  await page.cdp('Page.addScriptToEvaluateOnNewDocument', {source: injected});
  await page.goto(parsed.origin + '/');
  console.log(await page.snapshot());
  await page.click('#start');
  await page.waitForFunction(() => document.getElementById('status').textContent.includes('传输中'), undefined, {timeout: 30000});
  console.log('Browser status:', await page.evaluate(() => document.getElementById('status').textContent));
  await page.click('#stop');
  await page.waitForFunction(() => document.getElementById('status').textContent.includes('已停止传输'), undefined, {timeout: 10000});
  console.log('Browser stopped:', await page.evaluate(() => document.getElementById('status').textContent));
} finally {
  await task.finish({keep: []});
}
