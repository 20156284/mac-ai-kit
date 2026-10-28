'use strict';
// 仅使用 Node 内置模块；未经验证的 Studio 监听不得进入此网关。
const fs = require('node:fs');
const fsp = fs.promises;
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const crypto = require('node:crypto');
const {spawn, execFile} = require('node:child_process');
const {promisify} = require('node:util');
const exec = promisify(execFile);
const HEX = /^[a-f0-9]{40,64}$/;
const sleep = ms => new Promise(r => setTimeout(r, ms));
function safeEqual(a,b) { const x=Buffer.from(a),y=Buffer.from(b); return x.length===y.length && crypto.timingSafeEqual(x,y); }
function passwordHash(password,salt=crypto.randomBytes(16).toString('hex')) { return `scrypt$${salt}$${crypto.scryptSync(password,salt,32).toString('hex')}`; }
function authenticate(req,config) {
  const auth=req.headers.authorization;
  if (!auth || auth.length>4096 || !auth.startsWith('Basic ')) return false;
  const decoded=Buffer.from(auth.slice(6),'base64').toString('utf8'), n=decoded.indexOf(':');
  if(n<0 || !safeEqual(decoded.slice(0,n),config.username)) return false;
  const [,salt,hash]=config.passwordHash.split('$');
  return safeEqual(crypto.scryptSync(decoded.slice(n+1),salt,32).toString('hex'),hash);
}
function validateConfig(c) {
  c={...c,repo:c.repo||c.project,port:c.port||c.publicPort,studioPort:c.studioPort||c.internalPort,username:c.username||c.auth?.username,passwordHash:c.passwordHash||(c.auth?`scrypt$${c.auth.salt}$${c.auth.hash}`:null),remote:c.remote||c.gitRemote,branch:c.branch||c.gitRef,pollMs:c.pollMs||c.pollIntervalMs,studioCommand:c.studioCommand||c.node};
  for(const key of ['repo','stateDir','outputDir','studioCommand']) if(typeof c[key]!=='string'||!path.isAbsolute(c[key])) throw Error(`${key} must be absolute`);
  if(typeof c.username!=='string' || !c.username.trim() || /[:\x00-\x1f\x7f]/.test(c.username)|| !/^scrypt\$[a-f0-9]{32}\$[a-f0-9]{64}$/.test(c.passwordHash||'')) throw Error('Valid username/passwordHash required');
  for(const key of ['port','studioPort']) if(!Number.isInteger(c[key])||c[key]<1024||c[key]>65535) throw Error(`Invalid ${key}`);
  if(c.port===c.studioPort) throw Error('Public and upstream ports must differ');
  if(!Array.isArray(c.studioArgs)||c.studioArgs.some(x=>typeof x!=='string')) throw Error('studioArgs must be string array');
  if(!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(c.remote||'')) throw Error('Invalid remote');
  if(!c.branch||c.branch.startsWith('-')||/[\s~^:?*\[\\]/.test(c.branch)||c.branch.includes('..')) throw Error('Invalid branch');
  if(!c.studioArgs?.[0]||!path.isAbsolute(c.studioArgs[0]))throw Error('Absolute project Remotion CLI script required');
  return {...c,host:c.host||'0.0.0.0',pollMs:Math.max(60000,c.pollMs||60000)};
}
async function git(repo,args) { return (await exec('git',['-C',repo,...args],{env:{...process.env,GIT_TERMINAL_PROMPT:'0',GIT_ASKPASS:'/usr/bin/false',SSH_ASKPASS:'/usr/bin/false',GIT_SSH_COMMAND:'ssh -o BatchMode=yes'},timeout:45000,maxBuffer:1024*1024})).stdout.trim(); }
async function fingerprint(dir) {
  const h=crypto.createHash('sha256');
  let manifests=['package.json','pnpm-lock.yaml','pnpm-workspace.yaml','.npmrc'];
  try{const tracked=await git(dir,['ls-files','-z']);manifests=[...new Set([...manifests,...tracked.split('\0').filter(n=>/(^|\/)(package\.json|pnpm-lock\.yaml|pnpm-workspace\.yaml|\.npmrc)$/.test(n))])].sort();}catch{}
  for(const name of manifests) {
    h.update(name+'\0'); try {h.update(await fsp.readFile(path.join(dir,name)));} catch(e){if(e.code!=='ENOENT')throw e;h.update('<missing>');}
  }
  return h.digest('hex');
}
async function portFree(port,host='127.0.0.1') { await new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',reject);s.listen({port,host},()=>s.close(resolve));}); }
function json(res,status,value) {res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'});res.end(JSON.stringify(value));}
const PAGE=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Remotion 远程服务</title><style>body{font:16px system-ui;max-width:900px;margin:40px auto;padding:20px;background:#101722;color:#e8edf6}a{color:#8ccaff}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1a2536;padding:20px;border-radius:12px}li{margin:12px 0}dt{color:#8ccaff;margin-top:16px}dd{overflow-wrap:anywhere;margin:5px 0}</style><h1>Remotion 远程服务</h1><p><a href="/">打开 Studio</a> · Git 每 60 秒检查，更新不会覆盖正在运行的版本。</p><p>仅在可信私网使用 HTTP Basic；公网必须另配 TLS。任务未完成或状态不明确时不会切换。</p><dl id="summary"></dl><h2>成品下载</h2><p>渲染完成后在这里下载。启用自动清理时，请在到期前保存；Studio 自带下载按钮可能不支持外置路径。</p><ul id="jobs"></ul><details><summary>完整服务状态</summary><pre id="s">加载中…</pre></details><script>async function refresh(){try{const r=await fetch("/_remote/status",{cache:"no-store"}),s=await r.json();document.getElementById("s").textContent=JSON.stringify(s,null,2);const summary=document.getElementById("summary");summary.replaceChildren();for(const [label,value] of [["当前运行提交",s.activeSha],["远端提交",s.remoteSha],["最后检查",s.lastCheck],["同步状态",s.error||s.blockedReason||(s.activeSha===s.remoteSha?"与最近成功检查的远端一致":"等待检查或更新")],["输出与清理",s.outputPolicy]]){const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=label;dd.textContent=value||"尚无数据";summary.append(dt,dd)}const jr=await fetch("/_remote/jobs",{cache:"no-store"}),jobs=await jr.json(),ul=document.getElementById("jobs");ul.replaceChildren();if(!jobs.length){const li=document.createElement("li");li.textContent="暂无本服务登记的任务";ul.append(li)}for(const job of jobs){const li=document.createElement("li");li.textContent=job.createdAt+" · "+({done:"已完成",submitted:"已提交",running:"渲染中",idle:"等待中",failed:"失败",expired:"已过期清理","unknown-after-restart":"重启后状态待确认"}[job.status]||"未知");if(job.download){const a=document.createElement("a");a.href=job.download;a.textContent=" 下载成品";li.append(a)}if(job.expiresAt)li.append(document.createTextNode(" · 到期："+job.expiresAt));ul.append(li)}}catch(e){document.getElementById("s").textContent="无法取得状态"}}refresh();setInterval(refresh,5000)</script></html>`;
class Runtime {
  constructor(config,adapter={}) { this.c=validateConfig(config);this.adapter=adapter;this.child=null;this.stopping=false;this.switching=false;this.polling=false;this.failures=0;this.inFlight=0;this.authCache=new Map();this.authFailures=new Map();this.status={activeSha:null,remoteSha:null,lastCheck:null,lastFetchSuccess:null,pendingSha:null,blockedReason:null,error:null,studio:'starting',jobs:'unknown',outputPolicy:'Not verified; rendering blocked until an output adapter is supplied'}; }
  async prepare() {
    const c=this.c;
    if(!(await fsp.stat(c.outputDir)).isDirectory()) throw Error('Output directory must already exist on the intended mounted disk');
    const identity={real:await fsp.realpath(c.outputDir),dev:(await fsp.stat(c.outputDir)).dev};if(c.outputIdentity&&(c.outputIdentity.real!==identity.real||c.outputIdentity.dev!==identity.dev))throw Error('Configured output disk is disconnected or changed');this.c.outputIdentity=identity;if(this.c.outputIdentity.real!==c.outputDir)throw Error('Output directory must be canonical, without symlink ancestors');
    const disk=await fsp.statfs(c.outputDir);if(disk.bavail*disk.bsize<1024*1024*1024) throw Error('Output disk has less than 1 GiB free');
    await fsp.access(path.join(c.repo,'node_modules'));
    await fsp.mkdir(c.stateDir,{recursive:true,mode:0o700});
    const lockPath=path.join(c.stateDir,'runtime.lock');
    try{this.lock=await fsp.open(lockPath,'wx',0o600);}catch(e){if(e.code!=='EEXIST')throw e;const old=JSON.parse(await fsp.readFile(lockPath,'utf8'));let alive=true;try{process.kill(old.pid,0);}catch(x){if(x.code==='ESRCH')alive=false;}if(alive)throw Error('Another runtime owns the service lock');await fsp.unlink(lockPath);this.lock=await fsp.open(lockPath,'wx',0o600);}
    await this.lock.writeFile(JSON.stringify({pid:process.pid,started:new Date().toISOString()}));
    await portFree(c.port,c.host);await portFree(c.studioPort);
    this.status.activeSha=await git(c.repo,['rev-parse','HEAD']);
    if(!HEX.test(this.status.activeSha))throw Error('Invalid local SHA');
    if(await git(c.repo,['status','--porcelain']))throw Error('Working tree must be clean before starting');
    if(this.adapter.SUPPORTED&&!this.adapter.SUPPORTED.includes(c.remotionVersion))throw Error('Unsupported Remotion version; verified versions: '+this.adapter.SUPPORTED.join(', '));
    this.status.outputPolicy=c.retentionHours>0?`外置唯一任务目录；成功完成 ${c.retentionHours} 小时后仅删除本服务登记的输出；下载中跳过。`:'外置唯一任务目录；单独音轨关闭；完成后通过 /_remote/jobs 下载；自动清理未启用。';
    this.baseFingerprint=await fingerprint(c.repo);
    this.activeDir=await this.stage(this.status.activeSha);await this.adapter.initialize?.(this.c);
  }
  async stage(sha) {
    if(!HEX.test(sha))throw Error('Invalid revision');
    const dir=path.join(this.c.stateDir,'releases',sha);
    await fsp.mkdir(path.dirname(dir),{recursive:true});
    try {await fsp.access(dir);if(await git(dir,['rev-parse','HEAD'])!==sha)throw Error('Existing release does not match SHA');if(await git(dir,['status','--porcelain','--untracked-files=all','--','.',':(exclude)node_modules']))throw Error('Existing release was modified');}
    catch(e){if(e.code!=='ENOENT')throw e;await git(this.c.repo,['worktree','add','--detach',dir,sha]);}
    if(await fingerprint(this.c.repo)!==this.baseFingerprint)throw Error('Dependencies/config changed in source; restart through maintenance');
    if(await fingerprint(dir)!==this.baseFingerprint)throw Error('Dependencies/config changed; prepare dependencies manually before maintenance update');
    try {const modules=path.join(dir,'node_modules');const st=await fsp.lstat(modules);if(!st.isSymbolicLink()||await fsp.readlink(modules)!==path.join(this.c.repo,'node_modules'))throw Error('Existing release dependency link is not owned by this service');}
    catch(e){if(e.code!=='ENOENT')throw e;await fsp.symlink(path.join(this.c.repo,'node_modules'),path.join(dir,'node_modules'),'dir');}
    return dir;
  }
  startChild() {
    if(this.stopping)return;
    if(this.adapter.SUPPORTED){let cursor=path.dirname(fs.realpathSync(this.c.studioArgs[0])),pkg=null;while(cursor!==path.dirname(cursor)){try{const p=JSON.parse(fs.readFileSync(path.join(cursor,'package.json'),'utf8'));if(p.name==='@remotion/cli'){pkg=p;break;}}catch{}cursor=path.dirname(cursor);}if(!pkg||pkg.version!==this.c.remotionVersion||!this.adapter.SUPPORTED.includes(pkg.version))throw Error('Installed Remotion CLI version changed or is unsupported');}
    const args=this.c.studioArgs.map(x=>x.replaceAll('{port}',String(this.c.studioPort)).replaceAll('{repo}',this.activeDir));
    this.proof=path.join(this.c.stateDir,'bind-proof.json');try{fs.unlinkSync(this.proof);}catch(e){if(e.code!=='ENOENT')throw e;}
    this.child=spawn(this.c.studioCommand,['--require',path.join(__dirname,'bind-guard.cjs'),...args],{cwd:this.activeDir,env:{...process.env,HOST:'127.0.0.1',BROWSER:'none',REMOTION_PRIVATE_PORT:String(this.c.studioPort),REMOTION_BIND_PROOF:this.proof},stdio:['ignore','ignore','ignore'],shell:false,detached:process.platform!=='win32'});
    const child=this.child;this.status.studio='starting';
    child.once('error',()=>{this.status.error='Studio 进程无法启动';});
    child.once('exit',()=>{if(this.child!==child)return;this.child=null;this.status.studio='stopped';if(!this.stopping&&!this.switching){this.failures++;this.restartTimer=setTimeout(()=>this.startChild(),Math.min(60000,1000*2**Math.min(this.failures,6)));}});
  }
  async stopChild() { const child=this.child;if(!child)return;await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Studio did not stop safely; no force kill performed')),15000);child.once('exit',()=>{clearTimeout(timer);resolve();});if(process.platform!=='win32'){try{process.kill(-child.pid,'SIGTERM');}catch(e){if(e.code!=='ESRCH')throw e;}}else child.kill('SIGTERM');});if(process.platform!=='win32'){for(let i=0;i<50;i++){try{process.kill(-child.pid,0);}catch(e){if(e.code==='ESRCH')return;throw e;}await sleep(100);}throw Error('Owned Studio process group still exists; deployment stopped');} }
  async waitReady(){for(let i=0;i<30;i++){if(!this.child)throw Error('Studio exited before readiness');const p=await this.probe();if(p.ready){this.status.studio='ready';return;}await sleep(1000);}throw Error('Studio readiness timeout');}
  async probe() {
    try {const proof=JSON.parse(await fsp.readFile(this.proof,'utf8'));if(proof.host!=='127.0.0.1'||proof.port!==this.c.studioPort||proof.pid!==this.child?.pid)return {ready:false,idle:false,known:false};const result=await this.adapter.inspect?.(this.c.studioPort);if(!result)return {ready:false,idle:false,known:false};return result;}catch{return {ready:false,idle:false,known:false};}
  }
  async poll() {
    if(this.polling||this.stopping)return;this.polling=true;this.status.lastCheck=new Date().toISOString();
    try {
      const probe=await this.probe();await this.adapter.cleanup?.(this.c);this.status.jobs=probe.known?(probe.idle?'idle-safe':'busy-or-cleanup-unverified'):'unknown';if(probe.ready){this.status.studio='ready';this.failures=0;}
      const serviceRef='refs/remotion-remote/'+crypto.createHash('sha256').update(this.c.stateDir).digest('hex').slice(0,16);
      await git(this.c.repo,['fetch','--no-tags','--',this.c.remote,`+refs/heads/${this.c.branch}:${serviceRef}`]);
      const sha=await git(this.c.repo,['rev-parse',serviceRef+'^{commit}']);if(!HEX.test(sha))throw Error('Invalid fetched SHA');
      this.status.remoteSha=sha;this.status.lastFetchSuccess=new Date().toISOString();this.status.error=null;
      if(sha===this.status.activeSha){this.status.pendingSha=null;this.status.blockedReason=null;return;}
      this.status.pendingSha=sha;
      if(!probe.known||!probe.idle){this.status.blockedReason='等待任务全部成功完成；运行中、等待中、失败或未知状态不会自动重启';return;}
      const dir=await this.stage(sha);
      // 先阻止新请求，再检查队列，避免请求与版本切换的竞态。
      this.switching=true;
      if(this.inFlight){this.status.blockedReason='正在等待已提交的 Studio 请求完成';return;}
      const final=await this.probe();if(!final.known||!final.idle){this.status.blockedReason='切换前检测到任务状态变化';return;}
      const previous=this.activeDir;await this.stopChild();
      try{this.activeDir=dir;this.startChild();await this.waitReady();this.status.activeSha=sha;this.status.pendingSha=null;this.status.blockedReason=null;}catch(e){await this.stopChild();this.activeDir=previous;this.startChild();await this.waitReady();throw Error('Candidate failed; prior version restored');}
    }catch(e){this.status.error='Git 或更新检查失败';this.status.blockedReason=String(e.message).includes('Dependencies/config')?e.message:'更新不可用，请检查本机服务日志与 Studio 状态。';}
    finally{this.switching=false;this.polling=false;}
  }
  authorize(req,res) {
    const now=Date.now(),ip=req.socket.remoteAddress||'unknown',key=crypto.createHash('sha256').update(req.headers.authorization||'').digest('hex');
    if(this.authCache.size>1024)this.authCache.clear();if(this.authFailures.size>1024)this.authFailures.clear();
    const failure=this.authFailures.get(ip);if(failure&&failure.until>now&&failure.count>=10){res.writeHead(429,{'retry-after':'60'});res.end('身份验证尝试过多，请稍后重试');return false;}
    const cached=(this.authCache.get(key)||0)>now;const ok=cached||authenticate(req,this.c);
    if(!ok){this.authFailures.set(ip,{count:failure&&failure.until>now?failure.count+1:1,until:now+60000});res.writeHead(401,{'www-authenticate':'Basic realm="Remotion private service", charset="UTF-8"','cache-control':'no-store'});res.end('需要身份验证');return false;}
    this.authCache.set(key,now+30000);this.authFailures.delete(ip);
    const origin=req.headers.origin;
    if(origin){let u;try{u=new URL(origin);}catch{} if(!u||u.host!==req.headers.host||!['http:','https:'].includes(u.protocol)){res.writeHead(403);res.end('已拒绝跨来源请求');return false;}}
    if(req.headers['sec-fetch-site']==='cross-site'){res.writeHead(403);res.end('已拒绝跨站请求');return false;}
    return true;
  }
  async handle(req,res) {
    if(!this.authorize(req,res))return;
    const pathname=new URL(req.url,'http://localhost').pathname;
    if(pathname==='/_remote/status')return json(res,200,this.status);
    if(pathname==='/_remote/'||pathname==='/_remote'){res.writeHead(200,{'content-type':'text/html; charset=utf-8','cache-control':'no-store','x-frame-options':'DENY'});res.end(PAGE);return;}
    if(await this.adapter.ownRoute?.(req,res,this.c))return;
    if(pathname.startsWith('/_remote/'))return json(res,404,{error:'Not found'});
    if(pathname.startsWith('/api/')&&['GET','HEAD','OPTIONS'].includes(req.method))return json(res,403,{error:'Studio API requires an explicitly permitted POST route'});
    if(this.status.studio!=='ready')return json(res,503,{error:'Studio 监听与就绪状态尚未验证'});
    if(this.switching)return json(res,503,{error:'版本正在切换，请稍后重试'});
    // 所有版本相关写操作必须经过已验证的适配器。
    let body=null;
    if(!['GET','HEAD','OPTIONS'].includes(req.method)){
      this.inFlight++;
      let counted=true;req.remoteUncount=()=>{if(counted){counted=false;this.inFlight--;}};
      if(!this.adapter.mutate){req.remoteUncount();return json(res,503,{error:'Rendering/mutations unavailable: installed Studio HTTP schema is not verified'});}
      try {body=await this.adapter.mutate(req,res,this.c);if(body===false){req.remoteUncount();return;}}catch{req.remoteUncount();return json(res,400,{error:'Rejected Studio mutation'});}
    }
    if(req.aborted||res.destroyed||this.switching){req.remoteUncount?.();if(!res.destroyed)return json(res,503,{error:'Request cancelled during deployment'});return;}
    const headers={...req.headers,host:`127.0.0.1:${this.c.studioPort}`};delete headers.authorization;delete headers.cookie;if(headers.origin)headers.origin=`http://127.0.0.1:${this.c.studioPort}`;if(body){delete headers['transfer-encoding'];headers['content-length']=String(body.length);}
    const upstream=http.request({host:'127.0.0.1',port:this.c.studioPort,method:req.method,path:req.url,headers},r=>{const out={...r.headers};delete out['set-cookie'];res.writeHead(r.statusCode,out);r.pipe(res);});
    upstream.on('close',()=>req.remoteUncount?.());
    upstream.on('error',()=>{if(!res.headersSent)json(res,503,{error:'Studio 尚未就绪'});else res.destroy();});
    req.on('aborted',()=>upstream.destroy());res.on('close',()=>upstream.destroy());if(body)upstream.end(body);else req.pipe(upstream);
  }
  upgrade(req,socket,head) {
    const mock={writeHead:(s,h)=>{socket.write(`HTTP/1.1 ${s} Rejected\r\nConnection: close\r\n\r\n`);},end:()=>socket.end()};
    if(!this.authorize(req,mock))return;
    if(this.switching){socket.end('HTTP/1.1 503 Busy\r\nConnection: close\r\n\r\n');return;}
    if(!this.adapter.allowWebSocket?.(req)){socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');return;}
    const headers={...req.headers,host:`127.0.0.1:${this.c.studioPort}`};delete headers.authorization;delete headers.cookie;
    const up=http.request({host:'127.0.0.1',port:this.c.studioPort,path:req.url,headers});
    up.on('upgrade',(r,s,h)=>{socket.write(`HTTP/1.1 101 Switching Protocols\r\n${Object.entries(r.headers).map(([k,v])=>`${k}: ${v}\r\n`).join('')}\r\n`);if(h.length)socket.write(h);if(head.length)s.write(head);s.pipe(socket).pipe(s);socket.on('error',()=>s.destroy());s.on('error',()=>socket.destroy());});
    up.on('error',()=>socket.destroy());up.on('response',()=>socket.destroy());up.end();
  }
  async start() {await this.prepare();this.startChild();await this.waitReady();this.server=http.createServer((q,s)=>this.handle(q,s).catch(()=>{if(!s.headersSent)json(s,500,{error:'请求失败'});else s.destroy();}));this.server.on('upgrade',(q,s,h)=>this.upgrade(q,s,h));await new Promise((resolve,reject)=>{this.server.once('error',reject);this.server.listen(this.c.port,this.c.host,resolve);});this.status.studio='ready';await this.poll();this.timer=setInterval(()=>this.poll(),this.c.pollMs);}
  async stop() {this.stopping=true;clearInterval(this.timer);clearTimeout(this.restartTimer);if(this.server){this.server.close();this.server.closeIdleConnections();}await this.stopChild();if(this.lock){await this.lock.close();await fsp.unlink(path.join(this.c.stateDir,'runtime.lock'));this.lock=null;}}
}
async function main() {const file=process.env.REMOTION_REMOTE_CONFIG||(process.argv[2]==='--config'?process.argv[3]:process.argv[2]);if(!file)throw Error('Set REMOTION_REMOTE_CONFIG or pass config JSON path');const c=JSON.parse(await fsp.readFile(file,'utf8'));const adapter=require('./studio-adapter.cjs');const runtime=new Runtime(c,adapter);let ending=false;const stop=async()=>{if(ending)return;ending=true;await runtime.stop();process.exit(0);};process.on('SIGTERM',stop);process.on('SIGINT',stop);try{await runtime.start();}catch(e){try{await runtime.stop();}catch{}throw e;}}
if(require.main===module)main().catch(e=>{console.error(`Remote service failed: ${e.message}`);process.exitCode=1;});
module.exports={Runtime,authenticate,passwordHash,validateConfig,fingerprint,git,portFree};
