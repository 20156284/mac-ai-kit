#!/bin/bash
# 兼容 macOS Bash 3.2；发布时嵌入完整运行时，无需另装服务依赖。
set -eu
umask 077
PROJECT="$PWD"
PROJECT_SET=0
ACTION="menu"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --project) [ "$#" -ge 2 ] || { echo '缺少 --project 路径' >&2; exit 1; }; PROJECT="$2"; PROJECT_SET=1; shift 2 ;;
    --configure|--foreground|--install|--status|--stop|--uninstall) ACTION="${1#--}"; shift ;;
    --help|-h) echo '用法：bash start-remotion-remote.sh [--project 项目目录] [--configure|--foreground|--install|--status|--stop|--uninstall]'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done
# 只去除首尾空白；不执行粘贴内容、不拆分路径内部空格。
trim_input() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}
check_directory_input() {
  case "$1" in
    *://*|git@*:*) fail '这里需要已有本地目录，不接受 URL；仓库 URL 请先自行 clone，再填写本地路径。无需加引号或反斜杠转义空格。';;
  esac
}
fail() { printf '%s\n' "$*" >&2; exit 1; }
[ "$(uname -s)" = Darwin ] || fail '本脚本仅支持 macOS；未执行任何服务安装。'
if [ "$PROJECT_SET" = 0 ]; then
  printf '已有 Remotion Git 项目目录（无需引号；回车使用 %s；输入 q 取消）：' "$PROJECT"
  IFS= read -r PROJECT_REPLY || exit 0
  PROJECT_REPLY="$(trim_input "$PROJECT_REPLY")"
  [ "$PROJECT_REPLY" != q ] || exit 0
  [ -z "$PROJECT_REPLY" ] || PROJECT="$PROJECT_REPLY"
fi
PROJECT="$(trim_input "$PROJECT")"
check_directory_input "$PROJECT"
[ -d "$PROJECT" ] || fail '项目目录不存在。'
PROJECT="$(cd "$PROJECT" && pwd -P)"
NODE="$(command -v node || true)"
[ -n "$NODE" ] || fail '请先在当前终端启用已经安装的 Node.js 24+。'
NODE="$("$NODE" -p 'process.execPath')"
"$NODE" -e 'if(Number(process.versions.node.split(".")[0]) < 24) process.exit(1)' || fail '需要已经安装的 Node.js 24+。'
ID="$("$NODE" -e 'process.stdout.write(require("crypto").createHash("sha256").update(process.argv[1]).digest("hex").slice(0,16))' "$PROJECT")"
LABEL="local.remotion.remote.$ID"
STATE="$HOME/Library/Application Support/RemotionRemote/$ID"
CONFIG="$STATE/config.json"
RUNTIME="$STATE/runtime.cjs"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
SELF="$(cd "$(dirname "$0")" && pwd -P)/$(basename "$0")"
confirm() { printf '%s [输入 YES 确认]：' "$1"; IFS= read -r REPLY || return 1; [ "$REPLY" = YES ]; }
owned_plist() {
  [ -f "$PLIST" ] || return 1
  "$NODE" -e 'const fs=require("fs");const s=fs.readFileSync(process.argv[1],"utf8");const e=x=>x.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/\x27/g,"&apos;");if(!s.includes("<!-- RemotionRemote owner: "+process.argv[2]+" -->")||!s.includes("<string>"+e(process.argv[3])+"</string>"))process.exit(1)' "$PLIST" "$ID" "$RUNTIME"
}
status() {
  printf '项目：%s\n配置：%s\n服务：%s\n' "$PROJECT" "$CONFIG" "$LABEL"
  if launchctl print "$DOMAIN/$LABEL"; then :; else echo '未发现已加载的登录服务。'; fi
}
stop_service() {
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    owned_plist || fail '无法确认服务归属，未停止。'
    launchctl bootout "$DOMAIN/$LABEL" || fail '停止服务失败，请查看 launchctl 状态。'
  else echo '服务未加载。'; fi
}
extract_runtime() {
  "$NODE" -e 'const fs=require("fs");const s=fs.readFileSync(process.argv[1],"utf8");const marker="\n__REMOTION_RUNTIME_BASE64__\n";const i=s.lastIndexOf(marker);if(i<0)throw Error("缺少内嵌 runtime；请使用完整发布脚本");const b=s.slice(i+marker.length).split("\n__REMOTION_GUARD_BASE64__\n")[0].trim();if(!b||b.includes("PAYLOAD"))throw Error("runtime 未打包");fs.writeFileSync(process.argv[2],Buffer.from(b,"base64"),{mode:0o600});' "$SELF" "$RUNTIME.tmp"
  mv "$RUNTIME.tmp" "$RUNTIME"
  "$NODE" -e 'const fs=require("fs"),s=fs.readFileSync(process.argv[1],"utf8"),m="\n__REMOTION_GUARD_BASE64__\n",i=s.lastIndexOf(m);if(i<0)throw Error("缺少 guard");const b=s.slice(i+m.length).split("\n__REMOTION_ADAPTER_BASE64__\n")[0].trim();if(!b||b.includes("PAYLOAD"))throw Error("guard 未打包");fs.writeFileSync(process.argv[2],Buffer.from(b,"base64"),{mode:0o600});' "$SELF" "$STATE/bind-guard.cjs.tmp"
  mv "$STATE/bind-guard.cjs.tmp" "$STATE/bind-guard.cjs"
  "$NODE" -e 'const fs=require("fs"),s=fs.readFileSync(process.argv[1],"utf8"),m="\n__REMOTION_ADAPTER_BASE64__\n",i=s.lastIndexOf(m);if(i<0)throw Error("缺少 adapter");const b=s.slice(i+m.length).trim();if(!b||b.includes("PAYLOAD"))throw Error("adapter 未打包");fs.writeFileSync(process.argv[2],Buffer.from(b,"base64"),{mode:0o600});' "$SELF" "$STATE/studio-adapter.cjs.tmp"
  mv "$STATE/studio-adapter.cjs.tmp" "$STATE/studio-adapter.cjs"
}
runtime_alive() {
  "$NODE" -e 'const fs=require("fs");try{const p=JSON.parse(fs.readFileSync(process.argv[1],"utf8")).pid;if(!Number.isInteger(p)||p<2)process.exit(1);process.kill(p,0);}catch(e){if(e.code==="EPERM")process.exit(0);process.exit(1);}' "$STATE/runtime.lock"
}
configure_idle() {
  # Read-only checks: preparation must never stop or replace a live service.
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || runtime_alive; then
    fail '已有 Remote 服务正在运行；未停止进程、未变更配置。请在工具包外自行停止后重试。'
  fi
  # An unloaded LaunchAgent could still start at the next login. Never update
  # its live configuration under the guise of configuration-only preparation.
  [ ! -e "$PLIST" ] || fail '已有登录启动 plist；配置模式不会改动或启用它。请在工具包外自行卸载登录服务后重试（保留配置和输出）。'
}
setup() {
  if [ "$ACTION" = configure ]; then configure_idle; fi
  if runtime_alive && ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then fail '已有前台服务正在运行，请先在原终端按 Ctrl-C 停止；未变更配置。'; fi
  [ -f "$PROJECT/package.json" ] || fail '选定目录缺少 package.json。'
  PNPM="$(command -v pnpm || true)"
  [ -n "$PNPM" ] || fail '未找到已安装的 pnpm。不会自动安装。'
  PNPM="$("$NODE" -e 'process.stdout.write(require("path").resolve(process.argv[1]))' "$PNPM")"
  CLI="$("$NODE" -e 'const fs=require("fs"),path=require("path");const p=require.resolve("@remotion/cli/package.json",{paths:[process.argv[1]]});const j=JSON.parse(fs.readFileSync(p,"utf8"));const b=typeof j.bin==="string"?j.bin:j.bin&&j.bin.remotion;if(!b)throw Error("找不到 Remotion CLI");const f=path.resolve(path.dirname(p),b);fs.accessSync(f);process.stdout.write(f);' "$PROJECT")" || fail '项目中没有可解析的已安装 Remotion CLI；不会安装依赖。'
  VERSION="$("$NODE" -e 'const fs=require("fs");const p=require.resolve("@remotion/cli/package.json",{paths:[process.argv[1]]});const v=JSON.parse(fs.readFileSync(p,"utf8")).version;if(!["4.0.518","4.0.534"].includes(v))throw Error("未验证的 Remotion 版本："+v);process.stdout.write(v);' "$PROJECT")" || fail '仅支持已核对源码的 Remotion 4.0.518 / 4.0.534；不会自动升级。'
  git -C "$PROJECT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail '项目不是 Git 工作目录。'
  BRANCH="$(git -C "$PROJECT" symbolic-ref --quiet --short HEAD)" || fail '当前为 detached HEAD。请先切换到要跟踪的本地分支。'
  REMOTE="$(git -C "$PROJECT" config --get "branch.$BRANCH.remote" || true)"
  REF="$(git -C "$PROJECT" config --get "branch.$BRANCH.merge" || true)"
  [ -n "$REMOTE" ] && [ "$REMOTE" != . ] && [ -n "$REF" ] || fail '当前分支未配置远端跟踪分支；请先配置 upstream。'
  git -C "$PROJECT" remote get-url "$REMOTE" >/dev/null || fail '跟踪分支的 remote 不存在。'
  case "$REF" in refs/heads/*) ;; *) fail '只支持跟踪远端分支。';; esac
  printf '检查现有 Git 非交互读取权限（不 pull、不修改工作区）…\n'
  "$NODE" -e 'const cp=require("child_process");const r=cp.spawnSync("git",["-C",process.argv[1],"ls-remote","--exit-code","--",process.argv[2],process.argv[3]],{env:{...process.env,GIT_TERMINAL_PROMPT:"0",GIT_ASKPASS:"/usr/bin/false",SSH_ASKPASS:"/usr/bin/false",GIT_SSH_COMMAND:"ssh -o BatchMode=yes"},encoding:"utf8",timeout:45000,stdio:["ignore","pipe","pipe"]});if(r.error||r.status!==0||!r.stdout.trim()){console.error("Git 远端分支不能非交互读取；请自行检查现有网络和认证。");process.exit(1);}' "$PROJECT" "$REMOTE" "$REF" || fail '未启用服务；不会创建或修改 Git 凭据。'
  printf '输出目录（已存在的完整磁盘目录，无需引号）：'; IFS= read -r OUTPUT
  OUTPUT="$(trim_input "$OUTPUT")"
  check_directory_input "$OUTPUT"
  [ -d "$OUTPUT" ] || fail '输出目录不存在；不会创建目录或未挂载的磁盘挂载点。'
  OUTPUT="$(cd "$OUTPUT" && pwd -P)"
  "$NODE" -e 'const fs=require("fs"),p=process.argv[1];if(p.startsWith("/Volumes/")){const root=p.split("/").slice(0,3).join("/");if(fs.statSync(root).dev===fs.statSync("/Volumes").dev)throw Error("输出磁盘未确认为独立挂载卷");}if(p==="/")throw Error("不能使用根目录");fs.accessSync(p,fs.constants.W_OK);' "$OUTPUT" || fail '输出目录不可写或磁盘未挂载。'
  printf '已完成输出保留小时（0 不自动清理，默认 0）：'; IFS= read -r RETENTION
  RETENTION="$(trim_input "$RETENTION")"
  RETENTION="${RETENTION:-0}"
  case "$RETENTION" in *[!0-9]*|'') fail '保留小时必须是非负整数。';; esac
  "$NODE" -e 'const n=Number(process.argv[1]);if(!Number.isSafeInteger(n)||n<0||n>87600)process.exit(1)' "$RETENTION" || fail '保留小时须在 0 到 87600 之间。'
  printf '浏览器登录用户名（至少 1 字符）：'; IFS= read -r USERNAME
  USERNAME="$(trim_input "$USERNAME")"
  [ -n "$USERNAME" ] || fail '用户名不能为空。'
  case "$USERNAME" in *[[:cntrl:]]*) fail '用户名不能包含控制字符。';; esac
  case "$USERNAME" in *:*) fail '用户名不能包含冒号。';; esac
  printf '浏览器登录密码（至少 6 字符，不回显）：'; IFS= read -r -s PASSWORD; printf '\n'
  [ "${#PASSWORD}" -ge 6 ] || fail '密码至少需要 6 字符。'
  printf '再次输入密码：'; IFS= read -r -s PASSWORD2; printf '\n'
  [ "$PASSWORD" = "$PASSWORD2" ] || fail '两次密码不一致。'
  if [ "$ACTION" = configure ]; then
    echo '本次只保存远程渲染配置和内嵌代码，不启动监听、Studio 或渲染，不安装登录自启动。以下为将来显式启动时的网络设置：'
  fi
  printf '\n显式启动后将监听所有网卡 0.0.0.0:43871；子进程只监听 127.0.0.1:43872。\nHTTP Basic 不加密密码，只能用于可信局域网或私有 VPN，不能直接暴露公网。\n不会配置防火墙、VPN、TLS 或路由器。\n'
  if [ "$RETENTION" -gt 0 ]; then
    printf '本服务已完成输出满 %s 小时后永久删除，即使尚未下载；不清理其他文件。\n' "$RETENTION"
  else echo '输出只手动清理，不会自动删除。'; fi
  if [ "$ACTION" = configure ]; then
    confirm '确认仅保存带盐密码哈希、配置和内嵌代码，不启动任何服务？' || fail '已取消。'
  elif [ "$ACTION" = install ]; then
    confirm '确认保存带盐密码哈希并安装当前用户登录后自动启动的服务？' || fail '已取消。'
  else
    confirm '确认保存带盐密码哈希并在前台开放上述监听？' || fail '已取消。'
  fi
  if [ "$ACTION" = install ] && launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    owned_plist || fail '无法确认旧服务归属，未变更配置。'
    launchctl bootout "$DOMAIN/$LABEL" || fail '旧服务停止失败，未变更配置。'
  fi
  if [ "$ACTION" = configure ]; then configure_idle; fi
  ATTEMPT=0
  while runtime_alive; do
    ATTEMPT=$((ATTEMPT + 1))
    [ "$ATTEMPT" -le 15 ] || fail '旧进程仍未退出；未变更配置。请检查状态后重试。'
    sleep 1
  done
  mkdir -p "$STATE"; chmod 700 "$STATE"
  printf '%s' "$PASSWORD" | "$NODE" -e 'const fs=require("fs"),c=require("crypto");let p="";process.stdin.setEncoding("utf8");process.stdin.on("data",d=>p+=d);process.stdin.on("end",()=>{const a=process.argv.slice(1),salt=c.randomBytes(16).toString("hex");const v={project:a[0],node:a[1],pnpm:a[2],stateDir:a[3],outputDir:a[4],gitRemote:a[5],gitRef:a[6],publicHost:"0.0.0.0",publicPort:43871,internalHost:"127.0.0.1",internalPort:43872,pollIntervalMs:60000,auth:{username:a[7],salt,hash:c.scryptSync(p,salt,32).toString("hex")},retention:{mode:"manual"}};Object.assign(v,{repo:v.project,host:v.publicHost,port:v.publicPort,studioPort:v.internalPort,username:v.auth.username,passwordHash:"scrypt$"+salt+"$"+v.auth.hash,remote:v.gitRemote,branch:v.gitRef.replace(/^refs\/heads\//,""),pollMs:60000,retentionHours:Number(a[11]),outputIdentity:{real:fs.realpathSync(v.outputDir),dev:fs.statSync(v.outputDir).dev},remotionVersion:a[10],studioCommand:v.node,studioArgs:[a[9],"studio","--port","{port}","--no-open"]});fs.writeFileSync(a[8],JSON.stringify(v,null,2)+"\n",{mode:0o600});});' "$PROJECT" "$NODE" "$PNPM" "$STATE" "$OUTPUT" "$REMOTE" "$REF" "$USERNAME" "$CONFIG.tmp" "$CLI" "$VERSION" "$RETENTION"
  unset PASSWORD PASSWORD2
  chmod 600 "$CONFIG.tmp"; mv "$CONFIG.tmp" "$CONFIG"
  extract_runtime
}
install_service() {
  if [ -e "$PLIST" ]; then owned_plist || fail '同名 plist 不属于本脚本，拒绝覆盖。'; fi
  setup
  mkdir -p "$HOME/Library/LaunchAgents"
  "$NODE" -e 'const fs=require("fs"),path=require("path"),a=process.argv.slice(1);const x=s=>s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/\x27/g,"&apos;");const str=s=>"<string>"+x(s)+"</string>";const args=[a[1],a[2],"--config",a[3]].map(str).join("");const env=[path.dirname(a[1]),path.dirname(a[4]),"/opt/homebrew/bin","/usr/local/bin","/usr/bin","/bin","/usr/sbin","/sbin"].join(":");fs.writeFileSync(a[7],`<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<!-- RemotionRemote owner: ${a[8]} -->\n<plist version="1.0"><dict><key>Label</key>${str(a[0])}<key>ProgramArguments</key><array>${args}</array><key>WorkingDirectory</key>${str(a[5])}<key>EnvironmentVariables</key><dict><key>PATH</key>${str(env)}<key>HOME</key>${str(process.env.HOME)}</dict><key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>15</integer><key>StandardOutPath</key>${str(a[6]+"/service.log")}<key>StandardErrorPath</key>${str(a[6]+"/service-error.log")}</dict></plist>\n`,{mode:0o600});' "$LABEL" "$NODE" "$RUNTIME" "$CONFIG" "$PNPM" "$PROJECT" "$STATE" "$PLIST.tmp" "$ID"
  /usr/bin/plutil -lint "$PLIST.tmp" >/dev/null || fail 'plist 校验失败，未加载。'
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then launchctl bootout "$DOMAIN/$LABEL" || fail '旧服务停止失败。'; fi
  mv "$PLIST.tmp" "$PLIST"
  launchctl bootstrap "$DOMAIN" "$PLIST" || fail '加载失败；配置已保留，可用 --status 检查。'
  echo '已安装登录启动服务（不是开机未登录服务）。日志位于配置目录。'
  status
}
if [ "$ACTION" = menu ]; then
  printf 'Remotion 远程模块（工具包仅使用配置模式）\n6) 仅配置远程渲染，不启动\n1) 配置并前台运行\n2) 配置并安装登录自启动\n3) 查看状态\n4) 停止服务\n5) 卸载登录服务（保留配置和输出）\n0) 退出\n选择：'
  IFS= read -r CHOICE
  case "$CHOICE" in 6) ACTION=configure;; 1) ACTION=foreground;; 2) ACTION=install;; 3) ACTION=status;; 4) ACTION=stop;; 5) ACTION=uninstall;; 0) exit 0;; *) fail '无效选择。';; esac
fi
case "$ACTION" in
  configure)
    setup
    printf '已保存远程渲染配置：%s\n输出目录：%s\n未来显式启动后访问：http://Mac的私网地址:43871/\n状态与成品下载：http://Mac的私网地址:43871/_remote/\n未启动 Remote、Studio 或渲染；未创建登录启动 plist，未改变已有进程。\n' "$CONFIG" "$OUTPUT" ;;
  foreground)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then fail '登录服务已运行；请先用 --stop 停止，避免端口冲突。'; fi
    setup; exec "$NODE" "$RUNTIME" --config "$CONFIG" ;;
  install) install_service ;;
  status) status ;;
  stop) stop_service ;;
  uninstall)
    if [ -e "$PLIST" ]; then owned_plist || fail '同名 plist 不属于本脚本，拒绝删除。'; fi
    confirm '确认卸载登录服务？配置、代码缓存和用户输出均保留。' || exit 0
    stop_service
    if [ -f "$PLIST" ]; then rm "$PLIST"; fi
    echo '已卸载登录服务；未删除配置和任何输出。' ;;
esac
exit 0
__REMOTION_RUNTIME_BASE64__
RUNTIME_PAYLOAD_PLACEHOLDER
__REMOTION_GUARD_BASE64__
GUARD_PAYLOAD_PLACEHOLDER
__REMOTION_ADAPTER_BASE64__
ADAPTER_PAYLOAD_PLACEHOLDER
