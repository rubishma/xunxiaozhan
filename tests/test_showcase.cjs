// Optional offline demo acceptance: Node.js 22+ and installed Edge/Chromium; no npm dependencies.
// node tests/test_showcase.cjs [browser executable path]
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {pathToFileURL} = require('node:url');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const root = path.resolve(__dirname, '..');
const browserPath = process.argv[2] || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const sleep = ms => new Promise(r => setTimeout(r, ms));
let browser, socket;
(async () => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'xun-showcase-test-'));
  const profile = path.join(temp, 'profile');
  let sequence = 0;
  const pending = new Map(), errors = [], requests = [];
  const command = (method, params = {}) => new Promise((resolve,reject) => {
    const id=++sequence;
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP timeout: '+method));},10000);
    pending.set(id,msg=>{clearTimeout(timer);msg.error?reject(new Error(JSON.stringify(msg.error))):resolve(msg.result);});
    socket.send(JSON.stringify({id,method,params}));
  });
  try {
    await fs.access(browserPath);
    browser=spawn(browserPath,['--headless=new','--no-first-run','--disable-background-networking','--disable-extensions',
      '--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:'ignore'});
    let launchError;
    browser.on('error',e=>{launchError=e;});
    let port;
    const deadline=Date.now()+15000;
    while(Date.now()<deadline){
      if(launchError)throw launchError;
      try{port=(await fs.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split(/\r?\n/)[0];break;}catch{}
      await sleep(50);
    }
    assert.ok(port,'Browser did not launch');
    const pages=await (await fetch('http://127.0.0.1:'+port+'/json/list')).json();
    socket=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
    socket.addEventListener('message',event=>{
      const msg=JSON.parse(event.data);
      if(pending.has(msg.id)){pending.get(msg.id)(msg);pending.delete(msg.id);}
      if(msg.method==='Runtime.exceptionThrown')errors.push(msg.params.exceptionDetails);
      if(msg.method==='Network.requestWillBeSent')requests.push(msg.params.request.url);
    });
    const evaluate=async expression=>{
      const r=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
      if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));
      return r.result.value;
    };
    await command('Page.enable');await command('Runtime.enable');await command('Network.enable');
    await command('Network.setBlockedURLs',{urls:['http://*','https://*']});
    await command('Emulation.setFocusEmulationEnabled',{enabled:true});
    await command('Emulation.setDeviceMetricsOverride',{width:1280,height:1050,deviceScaleFactor:1,mobile:false});
    await command('Page.navigate',{url:pathToFileURL(path.join(root,'docs/demo/index.html')).href});
    const ready=Date.now()+8000;
    while(Date.now()<ready&&!await evaluate('document.querySelectorAll(".chart-point").length===5'))await sleep(50);
    assert.equal(await evaluate('document.querySelectorAll(".chart-point").length'),5);
    assert.match(await evaluate('document.querySelector(".notice").textContent'),/全部.*模拟数据/);
    assert.equal(await evaluate('document.querySelectorAll(".chart-segment").length'),1,'Do not connect across failures');
    await evaluate('document.querySelectorAll(".chart-point")[2].focus()');
    await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    await command('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    assert.match(await evaluate('document.querySelector("#point-detail").textContent'),/异常.*2040 ms/);
    await evaluate('document.querySelectorAll(".history-button")[0].click()');
    assert.match(await evaluate('document.querySelector("#point-detail").textContent'),/14:04:00.*27 ms/);
    await evaluate('document.querySelectorAll(".target")[1].click()');
    assert.equal(await evaluate('document.querySelectorAll(".chart-point").length'),2);
    assert.equal(await evaluate('document.querySelectorAll(".chart-segment").length'),0);
    assert.equal(await evaluate('document.querySelector("#monitor-state").textContent'),'已暂停');
    assert.match(await evaluate('document.querySelector("#latest-note").textContent'),/状态未知/);
    assert.match(await evaluate('document.querySelector("#incident-content").textContent'),/未确认恢复/);
    await evaluate('document.querySelectorAll(".target")[0].click();document.activeElement.blur();window.scrollTo(0,0)');
    await sleep(100);
    const output=path.join(root,'docs/assets');await fs.mkdir(output,{recursive:true});
    async function capture(filename){
      const metrics=await command('Page.getLayoutMetrics');
      const size=metrics.cssContentSize;
      const shot=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:size.width,height:size.height,scale:1}});
      await fs.writeFile(path.join(output,filename),Buffer.from(shot.data,'base64'));
    }
    await capture('demo-desktop.png');
    await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await sleep(150);
    assert.equal(await evaluate('document.documentElement.scrollWidth<=window.innerWidth'),true,'No horizontal overflow at 390px');
    assert.ok(await evaluate('Number(document.querySelector("#chart").getAttribute("viewBox").split(" ")[2])<390'));
    await evaluate('document.querySelectorAll(".chart-point")[1].focus()');
    assert.match(await evaluate('document.querySelector("#point-detail").textContent'),/2020 ms/);
    await evaluate('document.activeElement.blur();window.scrollTo(0,0)');
    await capture('demo-mobile.png');
    await evaluate('document.querySelectorAll(".target")[1].click()');
    assert.equal(await evaluate('document.documentElement.scrollWidth<=window.innerWidth'),true,'Paused sample must fit mobile viewport');
    assert.equal(errors.length,0,JSON.stringify(errors));
    assert.ok(requests.every(url=>url.startsWith('file:')||url.startsWith('data:')),'Offline demo must not request HTTP endpoints');
    assert.equal(await evaluate('localStorage.length'),0,'Demo does not persist sample interactions');
    console.log('PASS: offline file URL, sample switching, keyboard details, history selection, chart gaps, paused state, 390px layout, no HTTP requests or JS exceptions.');
    console.log('Simulated screenshots: '+output);
  } finally {
    if(socket?.readyState===WebSocket.OPEN){await command('Browser.close').catch(()=>{});socket.close();}
    if(browser&&browser.exitCode===null){await Promise.race([once(browser,'exit'),sleep(3000)]);if(browser.exitCode===null)browser.kill();}
    assert.ok(path.resolve(temp).startsWith(path.resolve(os.tmpdir())+path.sep));
    assert.ok(path.basename(temp).startsWith('xun-showcase-test-'));
    await fs.rm(temp,{recursive:true,force:true,maxRetries:5,retryDelay:200});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
