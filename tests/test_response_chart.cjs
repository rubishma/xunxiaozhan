// 可选浏览器验证：使用已有 Node.js 22+ 和 Edge/Chromium，无 npm 依赖。
// node tests/test_response_chart.cjs "浏览器可执行文件完整路径"
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const http = require("node:http");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const root = path.resolve(__dirname, "..");
const browserPath = process.argv[2] || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const temporary = fs.mkdtemp(path.join(os.tmpdir(), "xun-chart-test-"));
const target = { id: 1, name: "本机测试数据（模拟）", url: "http://127.0.0.1:8765/new", paused: false };
function row(id, ms, status = "up", url = "http://127.0.0.1:8765/") {
  return { id, target_id: 1, target_name: "模拟记录", target_url: url, checked_at: `2026-10-01T00:0${id - 1}:00+00:00`,
    response_ms: ms, status, status_code: status === "up" ? 200 : null,
    error: status === "up" ? null : "测试连接失败", source: "manual" };
}
const history = [row(6, 100, "up", target.url), row(5, 90), row(4, 80), row(3, 2100, "down"), row(2, 15), row(1, 10)];
const requests = [];
let server, browser, socket;
(async () => {
  const temporaryPath = await temporary;
  const profile = path.join(temporaryPath, "browser-profile");
  let id = 0;
  const pending = new Map();
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const number = ++id;
    const timer = setTimeout(() => { pending.delete(number); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
    pending.set(number, value => { clearTimeout(timer); value.error ? reject(new Error(JSON.stringify(value.error))) : resolve(value.result); });
    socket.send(JSON.stringify({ id: number, method, params }));
  });
  try {
    await fs.access(browserPath);
    server = http.createServer(async (req, res) => {
      requests.push({ method: req.method, url: req.url });
      try {
        if (req.url === "/" || req.url === "/response-chart.js") {
          const filename = req.url === "/" ? "index.html" : "response-chart.js";
          res.setHeader("Content-Type", filename.endsWith(".js") ? "application/javascript; charset=utf-8" : "text/html; charset=utf-8");
          res.end(await fs.readFile(path.join(root, "app/static", filename)));
          return;
        }
        const data = req.url === "/api/targets" ? [target] : req.url === "/api/scheduler" ? { running: true, interval_seconds: 30 } :
          req.url === "/api/targets/1/history" ? history : req.url === "/api/targets/1/incidents" ? [] : null;
        res.statusCode = data === null ? 404 : 200;
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify(data));
      } catch (error) { res.statusCode = 500; res.end(String(error)); }
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    browser = spawn(browserPath, ["--headless=new", "--no-first-run", "--disable-background-networking", "--disable-extensions",
      "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
      { windowsHide: true, stdio: "ignore" });
    let launchError;
    browser.on("error", error => { launchError = error; });
    const deadline = Date.now() + 15000;
    let port;
    while (Date.now() < deadline) {
      if (launchError) throw launchError;
      try { port = (await fs.readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split(/\r?\n/)[0]; break; } catch {}
      await sleep(50);
    }
    assert.ok(port, "浏览器调试端口未启动");
    const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const page = pages.find(item => item.type === "page");
    socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
    socket.addEventListener("message", event => {
      const value = JSON.parse(event.data);
      if (pending.has(value.id)) { pending.get(value.id)(value); pending.delete(value.id); }
    });
    const evaluate = async expression => {
      const result = await command("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    await command("Page.enable");
    await command("Emulation.setFocusEmulationEnabled", { enabled: true });
    await command("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await command("Page.navigate", { url: `http://127.0.0.1:${server.address().port}/` });
    const readyDeadline = Date.now() + 8000;
    while (Date.now() < readyDeadline && !await evaluate('document.querySelectorAll(".chart-point").length === 6')) await sleep(50);
    assert.equal(await evaluate('document.querySelectorAll(".chart-point").length'), 6);
    assert.equal(await evaluate('document.querySelectorAll(".chart-segment").length'), 2, "失败与网址变化处必须断线");
    assert.equal(await evaluate('[...document.querySelectorAll(".point-symbol")].filter(e => e.getAttribute("fill") === "#a22d2d").length'), 1);
    await sleep(100); // 等待首次尺寸观察完成，再验证键盘焦点。
    assert.ok(await evaluate('document.querySelector("#response-chart").getBoundingClientRect().height > 0'));
    await evaluate('document.querySelector("#chart-panel").scrollIntoView(); document.querySelectorAll(".chart-point")[2].focus()');
    assert.match(await evaluate('document.querySelector("#chart-detail").textContent'), /2100 ms.*未收到 HTTP 响应/);
    await fs.writeFile(path.join(temporaryPath, "desktop.png"), Buffer.from((await command("Page.captureScreenshot")).data, "base64"));
    await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await sleep(100);
    await evaluate('document.querySelector("#chart-panel").scrollIntoView()');
    assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true, "窄屏不应产生整页横向滚动");
    assert.equal(await evaluate('Number(document.querySelector("#response-chart").getAttribute("viewBox").split(" ")[2]) < 390'), true);
    await fs.writeFile(path.join(temporaryPath, "mobile.png"), Buffer.from((await command("Page.captureScreenshot")).data, "base64"));
    const draw = rows => evaluate(`renderResponseChart(${JSON.stringify(rows)}, "loaded", 1)`);
    await draw([]);
    assert.equal(await evaluate('document.querySelector("#response-chart").hasAttribute("hidden")'), true);
    await draw([row(1, 0)]);
    assert.equal(await evaluate('document.querySelectorAll(".chart-point").length'), 1);
    assert.equal(await evaluate('document.querySelector("#response-chart").innerHTML.includes("NaN")'), false);
    await draw([row(1, 0), row(2, 0)]);
    assert.equal(await evaluate('document.querySelectorAll(".chart-segment").length'), 1);
    await draw([row(1, 1500, "down"), row(2, 2200, "down")]);
    assert.equal(await evaluate('document.querySelectorAll(".chart-segment").length'), 0);
    await draw([{ ...row(1, 1), checked_at: "invalid" }]);
    assert.match(await evaluate('document.querySelector("#chart-summary").textContent'), /无效/);
    await evaluate('renderResponseChart([], "error", 1)');
    assert.match(await evaluate('document.querySelector("#chart-summary").textContent'), /读取失败/);
    await evaluate('renderResponseChart([], "loading", 1)');
    assert.match(await evaluate('document.querySelector("#chart-summary").textContent'), /正在读取/);
    await evaluate('renderResponseChart([], "empty", null)');
    assert.match(await evaluate('document.querySelector("#chart-summary").textContent'), /请选择/);
    await evaluate('document.querySelector("#refresh-history").click()');
    await sleep(100);
    assert.equal(await evaluate('document.querySelectorAll(".chart-point").length'), 6);
    assert.ok(requests.every(request => request.method === "GET"), "绘图和刷新不能发起 POST 检查");
    console.log("PASS: real browser chart, gaps, focus details, narrow viewport, empty/single/zero/failure/error/loading states, read-only refresh.");
    console.log("Screenshots (simulated data): " + temporaryPath);
  } finally {
    if (socket?.readyState === WebSocket.OPEN) {
      await command("Browser.close").catch(() => {});
      socket.close();
    }
    if (browser && browser.exitCode === null) {
      await Promise.race([once(browser, "exit"), sleep(3000)]);
      if (browser.exitCode === null) browser.kill();
    }
    if (server) await new Promise(resolve => server.close(resolve));
    // 仅清理本测试创建的浏览器配置目录，截图保留在临时目录供检查。
    assert.ok(path.resolve(profile).startsWith(path.resolve(temporaryPath) + path.sep));
    await fs.rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }).catch(() => {});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
