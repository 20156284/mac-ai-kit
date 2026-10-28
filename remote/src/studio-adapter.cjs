'use strict';
// Remotion 内部协议适配器：严格限制版本，不属于官方公共接口。
const http=require('node:http');const fs=require('node:fs').promises;const path=require('node:path');const crypto=require('node:crypto');
const SUPPORTED=['4.0.518','4.0.534'];
const outputJobs=new Map();const activeDownloads=new Map();let currentConfig,ownerToken;
async function cleanup(c){if(!Number.isFinite(c.retentionHours)||c.retentionHours<=0)return;await verifyOutput(c);for(const job of outputJobs.values()){if(job.status!=='done'||!job.completedAt||!Number.isFinite(Date.parse(job.completedAt))||activeDownloads.has(job.id)||Date.now()<Date.parse(job.completedAt)+c.retentionHours*3600000)continue;try{if(await fs.realpath(job.path)!==job.path)continue;const st=await fs.lstat(job.path);if(!st.isFile()||st.isSymbolicLink())continue;if(activeDownloads.has(job.id))continue;job.status='expiring';await fs.unlink(job.path);job.status='expired';await saveRecord(job);}catch(e){if(e.code!=='ENOENT')throw e;}}}
async function saveRecord(job){const dest=path.join(path.dirname(job.path),'remote-job.json'),temp=dest+'.tmp';await fs.writeFile(temp,JSON.stringify(job),{mode:0o600});await fs.rename(temp,dest);}
async function initialize(c){currentConfig=c;await verifyOutput(c);await fs.mkdir(c.stateDir,{recursive:true,mode:0o700});const tokenFile=path.join(c.stateDir,'output-owner');try{ownerToken=await fs.readFile(tokenFile,'utf8');}catch(e){if(e.code!=='ENOENT')throw e;ownerToken=crypto.randomBytes(32).toString('hex');await fs.writeFile(tokenFile,ownerToken,{flag:'wx',mode:0o600});}if(!/^[a-f0-9]{64}$/.test(ownerToken))throw Error('Invalid output ownership record');const base=path.join(c.outputDir,'remotion-remote-jobs');let dirs;try{dirs=await fs.readdir(base,{withFileTypes:true});}catch(e){if(e.code==='ENOENT')return;throw e;}for(const d of dirs.slice(0,10000)){if(!d.isDirectory()||!/^[a-f0-9-]{36}$/.test(d.name))continue;try{const p=path.join(base,d.name,'remote-job.json');if(await fs.realpath(p)!==p)continue;const job=JSON.parse(await fs.readFile(p,'utf8'));if(job.owner!==ownerToken||job.id!==d.name||path.dirname(job.path)!==path.join(base,d.name)||!/^output\.[a-z0-9]+$/.test(path.basename(job.path)))continue;if(!['done','expired'].includes(job.status))job.status='unknown-after-restart';outputJobs.set(job.id,job);}catch{}}}
function get(port,url,max=8*1024*1024){return new Promise((resolve,reject)=>{const q=http.get({host:'127.0.0.1',port,path:url,timeout:5000},r=>{if(r.statusCode!==200){r.resume();reject(Error('Studio not ready'));return;}let size=0;const parts=[];r.on('data',b=>{size+=b.length;if(size>max){r.destroy();reject(Error('Response too large'));}else parts.push(b);});r.on('end',()=>resolve(Buffer.concat(parts).toString('utf8')));r.on('error',reject);});q.on('timeout',()=>q.destroy(Error('Timeout')));q.on('error',reject);});}
async function inspect(port){
 const html=await get(port,'/');const match=html.match(/window\.remotion_initialRenderQueue\s*=\s*(\[[^\n]*\]);/);
 if(!match)return {ready:true,known:false,idle:false};
 let queue;try{queue=JSON.parse(match[1]);}catch{return {ready:true,known:false,idle:false};}
 if(!Array.isArray(queue)||queue.length>10000||queue.some(j=>!j||typeof j.id!=='string'||typeof j.outName!=='string'||typeof j.compositionId!=='string'||!['video','still','sequence'].includes(j.type)||!['idle','running','done','failed'].includes(j.status)))return {ready:true,known:false,idle:false};
 for(const job of queue){for(const ours of outputJobs.values())if(!['expired','expiring'].includes(ours.status)&&job.outName===ours.path&&ours.status!==job.status){ours.status=job.status;if(job.status==='done'&&!ours.completedAt)ours.completedAt=new Date().toISOString();await saveRecord(ours);}}
 return {ready:true,known:true,idle:queue.every(j=>j.status==='done')};
}
async function readBody(req){let size=0;const parts=[];for await(const b of req){size+=b.length;if(size>1024*1024)throw Error('Render request too large');parts.push(b);}return Buffer.concat(parts);}
async function verifyOutput(c){const real=await fs.realpath(c.outputDir),st=await fs.stat(c.outputDir);if(!c.outputIdentity||real!==c.outputIdentity.real||st.dev!==c.outputIdentity.dev)throw Error('Output disk changed or disconnected');}
async function mutate(req,res,c){
 const pathname=new URL(req.url,'http://localhost').pathname;
 if(!SUPPORTED.includes(c.remotionVersion)){res.writeHead(503);res.end('Unverified Remotion version; rendering disabled');return false;}
 if(req.method!=='POST'||!['/api/render','/api/cancel','/api/subscribe-to-file-existence','/api/unsubscribe-from-file-existence'].includes(pathname)){res.writeHead(403);res.end('This Studio mutation is not enabled by the remote safety adapter');return false;}
 const raw=await readBody(req);if(pathname!=='/api/render')return raw;
 const body=JSON.parse(raw.toString('utf8'));
 if(body.type==='sequence')throw Error('Image sequence output is not yet supported by the controlled download adapter');
 if(!body||typeof body!=='object'||Array.isArray(body)||typeof body.outName!=='string')throw Error('Unrecognized render schema');
 await verifyOutput(c);
 const stat=await fs.statfs(c.outputDir);if(stat.bavail*stat.bsize<1024*1024*1024)throw Error('Low output disk space');
 if(!ownerToken||currentConfig?.stateDir!==c.stateDir)await initialize(c);
 const base=path.join(c.outputDir,'remotion-remote-jobs');await fs.mkdir(base,{recursive:true,mode:0o700});if(await fs.realpath(base)!==base)throw Error('Symlink output base denied');
 const id=crypto.randomUUID();const dir=path.join(base,id);await fs.mkdir(dir,{mode:0o700});if(await fs.realpath(dir)!==dir)throw Error('Symlink output directory denied');
 // 不保留请求中的目录，不允许额外音轨写入其他位置。
 const ext=path.extname(body.outName).toLowerCase();if(!/^\.(mp4|webm|mov|mkv|mp3|wav|aac|gif|png|jpeg|jpg|pdf)$/.test(ext))throw Error('Unsupported output extension');
 body.outName=path.join(dir,'output'+ext);body.separateAudioTo=null;
 const record={owner:ownerToken,id,path:body.outName,status:'submitted',createdAt:new Date().toISOString()};
 await fs.writeFile(path.join(dir,'remote-job.json'),JSON.stringify(record),{flag:'wx',mode:0o600});outputJobs.set(id,record);
 return Buffer.from(JSON.stringify(body));
}
function allowWebSocket(){return false;} // 已验证版本使用 SSE；未知 WebSocket 请求一律拒绝。
async function ownRoute(req,res,c){
 const pathname=new URL(req.url,'http://localhost').pathname;
 if(pathname==='/_remote/jobs'){res.writeHead(200,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify([...outputJobs.values()].slice(-100).map(j=>({id:j.id,status:j.status,createdAt:j.createdAt,expiresAt:j.completedAt&&Number.isFinite(Date.parse(j.completedAt))&&c.retentionHours>0?new Date(Date.parse(j.completedAt)+c.retentionHours*3600000).toISOString():null,download:j.status==='done'?`/_remote/download/${j.id}`:null}))));return true;}
 const match=pathname.match(/^\/_remote\/download\/([a-f0-9-]{36})$/);if(!match)return false;
 await verifyOutput(c);
 const job=outputJobs.get(match[1]);if(!job||job.status!=='done'){res.writeHead(404);res.end('Completed output not found');return true;}
 activeDownloads.set(job.id,(activeDownloads.get(job.id)||0)+1);let released=false;const release=()=>{if(released)return;released=true;const n=(activeDownloads.get(job.id)||1)-1;if(n)activeDownloads.set(job.id,n);else activeDownloads.delete(job.id);};res.once('close',release);
 const real=await fs.realpath(job.path);if(real!==job.path){res.writeHead(403);res.end('Symlink output denied');return true;}
 const file=await fs.open(job.path,require('node:fs').constants.O_RDONLY|require('node:fs').constants.O_NOFOLLOW);const st=await file.stat();if(!st.isFile()){await file.close();res.writeHead(404);res.end();return true;}
 let start=0,end=st.size-1,status=200;const range=req.headers.range;
 if(range){const r=range.match(/^bytes=(\d*)-(\d*)$/);if(!r||(!r[1]&&!r[2])){await file.close();res.writeHead(416,{'content-range':`bytes */${st.size}`});res.end();return true;}
 if(!r[1])start=Math.max(0,st.size-Number(r[2]));else start=Number(r[1]);if(r[1]&&r[2])end=Math.min(end,Number(r[2]));if(start>end||start>=st.size){await file.close();res.writeHead(416,{'content-range':`bytes */${st.size}`});res.end();return true;}status=206;}
 const headers={'content-type':'application/octet-stream','content-disposition':`attachment; filename="${path.basename(job.path)}"`,'accept-ranges':'bytes','content-length':Math.max(0,end-start+1),'cache-control':'private, no-store'};if(status===206)headers['content-range']=`bytes ${start}-${end}/${st.size}`;
 if(job.status!=='done'){await file.close();res.writeHead(410);res.end('Output expired');return true;}
 res.writeHead(status,headers);if(req.method==='HEAD'||!st.size){await file.close();res.end();return true;}const stream=file.createReadStream({start,end,autoClose:true});stream.once('close',release);stream.on('error',()=>res.destroy());res.on('close',()=>stream.destroy());stream.pipe(res);return true;
}
module.exports={cleanup,initialize,verifyOutput,SUPPORTED,inspect,mutate,allowWebSocket,ownRoute,outputJobs};
