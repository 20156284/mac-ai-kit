'use strict';
// 兼容性封装，不是 Remotion 官方接口；仅作用于被启动的子进程。
const net=require('node:net');const fs=require('node:fs');
const expected=Number(process.env.REMOTION_PRIVATE_PORT);
if(!Number.isInteger(expected)||expected<1024)throw Error('Missing private bind guard port');
const original=net.Server.prototype.listen;
net.Server.prototype.listen=function(...args){
  let options,cb;
  if(args[0]&&typeof args[0]==='object'&&!Array.isArray(args[0])){
    options={...args[0]};cb=args[1];
    if('path' in options||'fd' in options||'handle' in options||options.port===undefined||!Number.isInteger(Number(options.port))||Number(options.port)<0||Number(options.port)>65535)throw Error('Bind guard rejected unsupported listener');
  }else if(typeof args[0]==='number'){
    if(!Number.isInteger(args[0])||args[0]<0||args[0]>65535)throw Error('Bind guard rejected invalid TCP port');
    options={port:args[0]};
    for(const item of args.slice(1)){if(typeof item==='function')cb=item;else if(typeof item==='number')options.backlog=item;else if(typeof item!=='string')throw Error('Bind guard rejected listen signature');}
  }else throw Error('Bind guard rejected unsupported listen signature');
  const requestedPort=Number(options.port);options.port=requestedPort;
  options.host='127.0.0.1';options.ipv6Only=false;
  this.once('listening',()=>{const addr=this.address();if(!addr||addr.address!=='127.0.0.1'||(requestedPort!==0&&addr.port!==requestedPort)){this.close();throw Error('Bind guard verification failed');}if(requestedPort===expected&&process.env.REMOTION_BIND_PROOF)fs.writeFileSync(process.env.REMOTION_BIND_PROOF,JSON.stringify({pid:process.pid,host:addr.address,port:addr.port}),{mode:0o600});});
  return original.call(this,options,...(cb?[cb]:[]));
};
