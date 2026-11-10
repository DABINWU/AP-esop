# ESOP 中心与 Windows 中转部署手册

适用交付：中心 v1.1.4、当前 Windows 中转、Android 1.0.4 正式版。编写日期：2026-11-10。

以下地址均为示例，需要替换成现场地址。本机测试用的18080端口及test-output目录不作为正式部署默认配置。

## 1. 部署关系与准备

```text
群晖/支持Docker Compose的Linux服务器：中心 Web + 转换worker
                     ↑ Windows中转通过VPN主动连接
Windows车间中转：后台服务 + 本地数据库 + 媒体缓存
                     ↑ 安卓通过车间局域网访问
安卓一体屏：下载完整内容后本地播放
```

中心不主动连接中转或屏幕；安卓不连接中心、不装VPN。中转电脑不需要Docker，也不需要一直打开浏览器。VPN由现场另行部署，须支持无人登录时联网。

建议先填好部署信息：

| 项目 | 本手册示例 | 现场填写 |
|---|---|---|
| 中心局域网IP | 10.10.0.10 | |
| 中转通过VPN访问中心地址 | http://10.10.0.10:8080 | |
| 中心数据目录 | /volume1/docker/esop/data | |
| 中转局域网固定IP | 192.168.10.20 | |
| 车间局域网网段 | 192.168.10.0/24 | |
| 中转端口 | 18081 | |
| relay_id（两端一致） | workshop-1 | |

中心使用支持Container Manager/Docker Compose的服务器，建议x86_64，至少为worker预留2GB内存，另给Web和系统留余量；磁盘需覆盖原件、PDF、高清图片、视频和备份。SQLite必须放服务器本机磁盘，不用SMB/NFS。

中转使用Windows 10/11或能运行Python 3.11及以上的Windows Server、NTFS本地盘、.NET Framework 4.x。下文统一以64位Python 3.13举例。Python应安装到所有用户可用的位置，避免仅安装在当前用户AppData中。

复制项目时保留center、relay、shared、scripts、相关requirements、Dockerfile和compose.yaml；不要把本地.venv、test-output、.git、安卓签名私钥复制成服务器运行环境。安卓最终APK单独交给设备安装人员。

## 2. 密码与通信密钥的区别

| 内容 | 用途 | 对应关系 |
|---|---|---|
| 中心网页账号密码 | 管理员浏览器登录中心 | 首次随机生成，可在网页修改 |
| 中转网页账号密码 | 查看中转本地状态 | 与中心账号独立 |
| 中心ADMIN_API_KEY | 管理脚本兼容接口 | 不填入安卓 |
| 中心RELAY_API_KEY | 中转访问中心 | 必须等于中转CENTER_API_KEY |
| 中转DEVICE_API_KEY | 安卓访问中转及下载 | 填安卓“终端访问密钥” |
| 中转LOCAL_ADMIN_API_KEY | 本地脚本接口兼容 | 不用作网页登录密码 |
| 安卓管理员设置密码 | 进入该屏幕的管理设置 | 首次在屏幕上自行设置 |

各用途密钥使用不同随机值，至少24个无空白的ASCII字符；只有RELAY_API_KEY与CENTER_API_KEY这一对必须相同。不要用示例占位值，不要将密钥放在网址、截图、公开日志或版本库。

## 3. 中心安装（群晖）

### 3.1 放置程序与数据

在群晖安装Container Manager，临时启用SSH用于部署。将程序放到`/volume1/docker/esop/app`，SSH进入该目录：

```sh
cd /volume1/docker/esop/app
sudo mkdir -p /volume1/docker/esop/data /volume1/backup
sudo chown 10001:10001 /volume1/docker/esop/data
cp .env.example .env
chmod 600 .env
```

普通Linux服务器也可按此流程执行，但须将所有`/volume1/...`路径改为该服务器的本机路径。

### 3.2 配置.env

分别执行两次`openssl rand -hex 32`生成管理员接口密钥和中转通信密钥，安全保存。编辑`.env`，关键内容如下：

```dotenv
ADMIN_API_KEY=填写第一把随机密钥
RELAY_API_KEY=填写第二把随机密钥
RELAY_ID=workshop-1
ESOP_DATA_DIR=/volume1/docker/esop/data
ESOP_BIND_IP=10.10.0.10
ESOP_PORT=8080
HEARTBEAT_SECONDS=10
OFFLINE_SECONDS=40
UPLOAD_MAX_MB=100
VIDEO_MAX_MB=200
VIDEO_MAX_SECONDS=180
AUTH_SESSION_HOURS=12
AUTH_COOKIE_SECURE=false
```

保留.env.example中其他配置即可。ESOP_BIND_IP填写服务器实际需要提供服务的本机IP，不能填安卓或中转IP；设成127.0.0.1会导致其他电脑无法直接访问。多网卡访问应按现场网络明确开放范围。Docker端口映射不能只依赖主机防火墙，应结合NAS防火墙、网关和允许来源验证实际访问范围。

如果通过HTTPS反向代理访问，设置`AUTH_COOKIE_SECURE=true`，可信代理来源`FORWARDED_ALLOW_IPS`填真实代理IP。代理保留Host、Origin、Cookie、Authorization、X-CSRF-Token，发送`X-Forwarded-Proto=https`；上传限制大于最大文件上限，处理超时至少覆盖请求探测时间。中转CENTER_URL直接填写最终地址，不依赖HTTP跳转HTTPS。

HTTP直连测试时不要启用Secure Cookie，否则浏览器可能无法保持登录。正式网络优先使用可信证书的HTTPS，不关闭证书校验。

### 3.3 构建和启动

```sh
export DOCKER_BUILDKIT=1
sudo docker compose config --quiet
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs --tail=80 web worker
curl -f http://10.10.0.10:8080/api/v1/health
```

若群晖使用旧命令，将`docker compose`换为`docker-compose`；构建要求支持BuildKit。首次构建需要访问镜像、Debian软件源及Python依赖源。Container Manager图形界面可用“项目”导入同一目录compose.yaml，确保项目使用同目录.env。

预期Web和worker均运行并最终healthy，健康接口返回`status: ok`。**只能运行一个Web进程、一个转换worker，不使用多副本或多个uvicorn workers。**

### 3.4 首次登录

浏览器访问`http://10.10.0.10:8080/`。首次管理员用户名为`admin`，随机初始密码在服务器数据目录：

```sh
sudo cat /volume1/docker/esop/data/initial-login.txt
```

只在管理员私密终端查看，不截图分享。登录后点击“修改密码”，中心和中转密码分别管理，没有统一默认密码。修改后初始凭据文件删除，账号数据库保存在`web-auth.sqlite3`。

### 3.5 中心开机启动及目录

服务器启动Docker/Container Manager后，容器按`restart: unless-stopped`恢复；手动停止的容器可能保持停止，需要`docker compose start`。不需要打开网页维持运行。

数据目录包含`center.sqlite3`及WAL文件、`web-auth.sqlite3`、originals、derived、manifests、tmp。镜像重建不会删除挂载数据，切勿改指空目录后误以为数据丢失。

Excel通过LibreOffice转PDF再生成图片，镜像包含中文字体。原字体不同仍可能改变版式，必须预览；WPS图形复杂时建议原生导出PDF后上传。PDF保留页序。MP4要求H.264、AAC或无声，默认最长180秒，不自动转码/截断。

## 4. Windows中转安装

以下命令都在**管理员PowerShell**执行。把完整程序放在`C:\AP-ESOP\releases\v1.1.4`，该目录应直接包含relay、shared等目录，不要再多套一层项目文件夹。

### 4.1 建立独立环境

先安装64位Python 3.13，确认`py -3.13 --version`成功。然后：

```powershell
py -3.13 -m venv C:\AP-ESOP\venv
C:\AP-ESOP\venv\Scripts\python.exe -m pip install -r C:\AP-ESOP\releases\v1.1.4\relay\requirements.txt
New-Item -ItemType Directory -Force C:\ProgramData\AP-ESOP | Out-Null
Copy-Item C:\AP-ESOP\releases\v1.1.4\relay\.env.example C:\ProgramData\AP-ESOP\relay.env
notepad C:\ProgramData\AP-ESOP\relay.env
```

程序更新不应覆盖relay.env或relay-data。不要从开发电脑直接复制.venv到另一台电脑。

### 4.2 配置relay.env

```dotenv
CENTER_URL=http://10.10.0.10:8080
CENTER_API_KEY=填写中心RELAY_API_KEY的同一个值
DEVICE_API_KEY=填写另一把安卓访问随机密钥
LOCAL_ADMIN_API_KEY=填写另一把本地脚本随机密钥
RELAY_ID=workshop-1
RELAY_HOST=192.168.10.20
RELAY_PORT=18081
RELAY_DATA_DIR=C:/ProgramData/AP-ESOP/relay-data
POLL_SECONDS=10
CONNECT_TIMEOUT_SECONDS=5
READ_TIMEOUT_SECONDS=20
DOWNLOAD_TIMEOUT_SECONDS=180
DOWNLOAD_RETRIES=3
RETRY_MAX_SECONDS=60
DISK_RESERVE_MB=512
CACHE_RETENTION_HOURS=24
AUTH_SESSION_HOURS=12
AUTH_COOKIE_SECURE=false
```

其余字段沿用示例。RELAY_HOST填中转真实LAN固定IP，上面写法只监听该网卡；本机也用该IP访问，不能用127.0.0.1。如果按示例文件使用0.0.0.0则会监听所有网卡，必须结合防火墙限定局域网，不向公网开放。配置文件写绝对路径，不支持变量插值；系统环境变量优先，排查旧配置时检查同名环境变量。

路由器为中转设置DHCP保留或配置固定IP。确认这个地址不会被其他设备占用。中转和安卓必须局域网可达；Wi-Fi访客隔离、AP隔离可能阻止设备通信。

### 4.3 VPN和配置检查

```powershell
Test-NetConnection 10.10.0.10 -Port 8080
cd C:\AP-ESOP\releases\v1.1.4
C:\AP-ESOP\venv\Scripts\python.exe -m relay --config C:\ProgramData\AP-ESOP\relay.env --check
```

配置检查通过不等于中心通信通过，还需启动后查看中心连接。VPN须在系统启动、用户未登录时仍存在；仅登录用户手动拨号的VPN不满足后台服务要求。VPN配置由现场网络人员负责。

### 4.4 安装为后台服务

使用现有WinSW托管脚本，固定版本2.12.0，脚本会校验SHA256，文件错误时不要跳过校验。

```powershell
Invoke-WebRequest https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET4.exe -OutFile C:\AP-ESOP\WinSW.NET4.exe
cd C:\AP-ESOP\releases\v1.1.4
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Install -ProgramRoot C:\AP-ESOP\releases\v1.1.4 -PythonExe C:\AP-ESOP\venv\Scripts\python.exe -ConfigFile C:\ProgramData\AP-ESOP\relay.env -WinSWPath C:\AP-ESOP\WinSW.NET4.exe
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Start
```

Bypass只作用于此脚本进程，不改系统策略；组织策略要求签名时按组织要求执行。服务默认名`AP-ESOP-Relay`，LocalSystem账户、Session 0、延迟自动启动。**电脑开机即可后台运行，不需要用户登录、浏览器、命令窗口或Docker。** 初次启动网络尚未就绪会重试连接。

限制目录权限，防止普通用户修改SYSTEM服务执行的程序：

```powershell
icacls C:\ProgramData\AP-ESOP /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F"
icacls C:\AP-ESOP /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F"
```

如果改专用服务账户，另授予配置/数据、程序/Python权限和“作为服务登录”权限，并验证其VPN路由。

### 4.5 防火墙只放行中转端口

按现场LAN地址、网段替换：

```powershell
New-NetFirewallRule -DisplayName 'AP ESOP Relay LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 18081 -LocalAddress 192.168.10.20 -RemoteAddress 192.168.10.0/24 -Profile Private,Domain
```

不要关闭整个防火墙。先检查`Get-NetConnectionProfile`，确认车间网络配置文件与规则匹配；如网络被标为Public，由现场管理员确认可信范围后调整网络类别或制定受限规则。不要盲目对所有来源放行。

在**车间另一台电脑**执行`Test-NetConnection 192.168.10.20 -Port 18081`，而不是只在中转本机检查。中转访问中心还需要中心侧允许VPN来源访问8080或HTTPS端口。

### 4.6 登录和健康检查

访问`http://192.168.10.20:18081/`，首次admin密码在：

```powershell
Get-Content C:\ProgramData\AP-ESOP\relay-data\initial-login.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\AP-ESOP\releases\v1.1.4\relay\windows\Health-Check.ps1 -Url http://192.168.10.20:18081
```

登录后修改中转网页密码。状态页确认“中心连接在线”、最后成功同步时间持续更新、磁盘空间正常。页面只读，不提供现场创建任务。

### 4.7 服务管理与恢复

在对应程序根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Restart
```

异常进程退出后按10/30/60秒重启，1小时重置计数；正常停止不触发恢复。HTTP unhealthy本身不触发SCM自动重启；需监控并排查日志。中心失联时中转仍可健康地服务缓存，不应因此反复重启。

卸载：先Stop，再执行同脚本`-Action Uninstall`。仅卸载服务注册，不删除数据、密钥和缓存。使用自定义ServiceDir时，后续管理命令必须传相同目录。

## 5. 首次三端配置和业务验收

1. 中心“屏幕与下发”下方登记屏幕，例如`screen-01`、名称“一线装配屏”。每台编号唯一；删除后的编号不能复用。
2. 安装Android正式APK：`android/dist/AP-ESOP-Player-1.0.4-release.apk`。可覆盖1.0.3正式版；旧调试版签名不同不能直接覆盖保留数据。卸载重装需用新的device_id，避免报告序号冲突。
3. 安卓管理员设置填中转地址`http://192.168.10.20:18081`、中心登记的device_id、中转DEVICE_API_KEY，另设置此屏的管理员密码。不是中心账号密码，也不是CENTER_API_KEY。
4. 连接测试后保存；中心默认10秒心跳、40秒离线阈值。确认中转和中心看得到屏幕状态。
5. 中心创建资料，四模块上传真实Excel/PDF/图片/合规短视频，等待转换成功。先保存排序与秒数，再预览所有有内容的模块，最后发布。
6. “屏幕与下发”选择资料、多选屏幕、下发；确认实际版本和当前播放项回报，而不仅是中转缓存下载完成。
7. 检查SOP/SIP/点检视频/自定义、上一项/下一项/暂停/继续/重播、双指缩放。暂停时仍须正常心跳。
8. 断开中心连接，确认本地内容继续播放；恢复后状态补传。新内容未完整下载前应继续旧内容。
9. 屏幕关机期间改派多次，开机只拿最新目标；重启App并断网，已有完整缓存应能恢复播放。
10. 重启Windows中转电脑，**不登录Windows**也应恢复服务和VPN；用另一设备访问状态页验证。完成实机断电恢复、长时间播放与温度验收。

任务默认120秒超时仅结束本次执行，不撤回持续目标。离线屏幕不会永久占住任务锁，上线后补最新安排。在线仅说明App通信正常，不证明物理屏幕亮屏。

## 6. 备份、更新和迁移

### 6.1 中心备份与更新

所有命令从中心app目录执行，停服务后做完整一致性备份：

```sh
sudo docker compose stop web worker
sudo tar -C /volume1/docker/esop -czf /volume1/backup/esop-$(date -u +%Y%m%dT%H%M%SZ).tgz data
# 确认tar成功，再启动服务
sudo docker compose start web worker
```

另行加密保存.env和对应版本程序/镜像。备份同时包含业务库、web-auth.sqlite3、媒体和清单，不能只复制正在使用的.sqlite3文件。

更新：先按上面停机备份，保存旧程序/镜像，再更新app目录源文件，保留.env及数据目录，执行`docker compose up -d --build`，检查两个服务健康、登录、预览、设备目标及中转同步。有数据库迁移时不得直接用旧镜像打开新库，回滚应恢复同一时间点的完整数据及对应程序版本。

本机测试数据如果需要迁到NAS：先停止本地中心两个容器，将`LOCAL_INSTANCE.json`指向的**实际数据目录**完整备份并迁移至NAS数据目录，保留匹配的.env通信密钥，调整路径/监听地址并修正UID权限；不要复制整个test-output。迁移期间只运行一套权威中心，重新指定中转CENTER_URL。首次随机密码只针对全新数据目录，迁移现有账号后仍使用原密码。

### 6.2 中转备份与更新

停止服务后备份整个`C:\ProgramData\AP-ESOP`（包括relay.env、relay-data、账号库和服务配置），并安全保存当前程序版本。

更新步骤：Stop → 备份 → 新程序放新release目录 → Uninstall旧服务注册 → 从新目录按4.4重新Install并指定新ProgramRoot → Start。保持原数据目录、密钥、事件队列、device_id与revision，不清空数据库。恢复时数据、cache、manifests及账号库一并恢复。

### 6.3 空间管理

中心每份资料最多两个可用发布版本；正在使用的旧版优先保护，多个旧版同时占用会阻止发布。过期版独占媒体自动释放，历史记录仍保留；草稿、共享文件及备份另外占空间。下发历史页面显示最近10条，底层历史保留。

中转有缓存清理规则，当前目标、实际、待更新、当前/上一准备版本和未送达报告引用受保护，非保护缓存默认24小时后可清理。512MB为最低预留而非总容量上限。长期离线屏幕、待上报数据和受保护内容仍可能占空间，需监测容量，不手工删除SQLite、受保护文件或outbox。屏幕删除入口默认折叠，删除不远程擦除安卓缓存。

## 7. 常见故障

| 现象 | 检查与处理 |
|---|---|
| 本机能开，其他电脑打不开中心 | ESOP_BIND_IP是否127.0.0.1、Docker映射、NAS防火墙、地址是否同网段/VPN可达 |
| 中转网页打不开 | 服务状态、RELAY_HOST与实际IP、18081端口、防火墙网络类别、AP隔离 |
| 中转无法连接中心 | CENTER_URL最终地址、VPN是否对SYSTEM生效、两端RELAY_ID及配对密钥、证书和端口 |
| 网页改密后登不上 | 中心与中转账号独立；核对访问的是哪台服务，避免误用API Key；多次失败等5分钟 |
| 登录后仍反复退出 | HTTP是否误开Secure Cookie、代理Host/Origin/HTTPS转发和可信代理来源是否正确 |
| 安卓401 | 检查DEVICE_API_KEY，不要填网页密码或中心密钥 |
| 安卓404 | device_id未登记、已删除或中转尚未同步；重新安装使用新编号 |
| 待下载/下载失败 | 看具体错误；检查中心鉴权、源文件、磁盘和网络，不把待下载状态当成必然丢失 |
| 409无法下发/删除屏幕 | 当前任务仍在执行，等完成或截止超时；不手工删除任务锁 |
| 发布被旧版占用阻止 | 先让相关屏幕同步到保留版本，勿强删仍需下载的文件 |
| Excel错位 | 检查分页、字体及WPS浮动图形，必要时从WPS导出PDF后重新上传预览 |

中心忘记密码，在app目录执行：

```sh
sudo docker compose exec web python -m shared.auth --data-dir /data --username admin
```

中转忘记密码，管理员PowerShell进入中转程序目录：

```powershell
C:\AP-ESOP\venv\Scripts\python.exe -m shared.auth --data-dir C:\ProgramData\AP-ESOP\relay-data --username admin
```

交互输入并确认新密码（10～128字符），会撤销旧登录会话；不在命令行写密码。不删除账号库来重置。

中心日志：`docker compose logs --tail=100 web worker`，每服务10MB×3。中转日志：`relay-data\logs\relay.log`和`relay-service\logs`，分别轮转。分享日志前检查并移除现场敏感信息。

## 8. 验证边界

本项目已有构建、自动化回归、真实Docker中心—Windows中转HTTP和媒体转换记录，参见[VALIDATION.md](VALIDATION.md)。这份手册核对了现有配置及服务脚本，并不表示已在你的正式NAS、现场VPN或联发科一体机完成部署验收。现场需逐项完成第5节；后续Android验证使用真实设备，不以历史模拟器测试代替实机结论。
