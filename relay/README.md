# Windows 车间中转

已实现 Python FastAPI + SQLite 中转，可用 Windows SCM 服务运行，无需登录、浏览器或命令窗口。
只主动访问中心，经局域网服务屏幕；不创建总部任务，不转换Excel/PDF/视频，不实现VPN或安卓播放器。
SOP/SIP/点检视频/自定义沿用中心schema2模块ID、顺序、1920×1080/contain规则；旧schema1兼容。
页面改名、蓝色主题、自动资料编号、归档/隐藏版本控件均不改变播放接口。

## 安装程序与配置

状态页的“缓存下载”仅列当前仍需准备及正在下载的文件，missing显示为“待下载”。
过期且不再引用的历史缓存可释放磁盘空间，其身份记录仍保留，不作为当前下载故障展示；再次安排时重新校验/下载。


Windows 10/11 或支持 Python 3.11+ 的 Windows Server，建议x64、固定IP或DHCP保留。
在“所有用户”范围安装Python，准备本机NTFS目录，不用网络共享、用户桌面或映射盘保存SQLite。
程序目录与配置、数据分开，例如：

```text
C:\AP-ESOP\releases\2026-11-06\    程序根目录（包含relay/和shared/）
C:\AP-ESOP\venv\                   Python虚拟环境
C:\ProgramData\AP-ESOP\relay.env   密钥及配置（不在发布包中）
C:\ProgramData\AP-ESOP\relay-data\ relay.sqlite3、cache/、manifests/、tmp/、logs/
C:\ProgramData\AP-ESOP\relay-service\ WinSW及XML、包装器日志
```

在管理员PowerShell中安装依赖，复制示例后编辑真实值：

```powershell
py -3.13 -m venv C:\AP-ESOP\venv
C:\AP-ESOP\venv\Scripts\python.exe -m pip install -r C:\AP-ESOP\releases\2026-11-06\relay\requirements.txt
New-Item -ItemType Directory -Force C:\ProgramData\AP-ESOP | Out-Null
Copy-Item C:\AP-ESOP\releases\2026-11-06\relay\.env.example C:\ProgramData\AP-ESOP\relay.env
notepad C:\ProgramData\AP-ESOP\relay.env
```

CENTER_URL为VPN可达中心地址，如http://10.10.0.10:8080；HTTPS验证证书，不跟随重定向。
CENTER_API_KEY使用中心RELAY_API_KEY，不要用中心管理员密钥。
DEVICE_API_KEY用于安卓接口和媒体，LOCAL_ADMIN_API_KEY仅保留脚本兼容；电脑/PDA网页改用独立账号密码。
三把Key不同、至少24字符ASCII、无空白。密钥不写前端、URL或日志。
RELAY_ID必须与中心相同，默认workshop-1。配置文件不做变量插值，写绝对路径；系统环境变量优先。

默认监听0.0.0.0:18081，中心轮询10秒、连接超时5秒、读取超时20秒、单文件总时间180秒、
每轮最多3次尝试，跨轮退避最多60秒。单线程下载、独立中心同步和报告线程。
预留磁盘512MB，默认保留24小时非保护缓存；目标/实际/待更新/当前和上一准备版本/未送达报告引用均受保护。
MAX_FILE_MB默认2048、JSON上限32MB、outbox未完成事件最多100000；超限明确失败，不静默丢报告。
局域网文件流默认最多4条（MAX_LOCAL_DOWNLOADS可配），超出503/download_busy。
日志10MB×5轮转；包装器stdout/stderr另作10MB×5轮转。无访问日志。

## Windows 服务托管（WinSW 2.12.0）

采用固定版[官方WinSW](https://github.com/winsw/winsw/releases/tag/v2.12.0)的WinSW.NET4.exe，
Windows需可用.NET Framework 4.x。参考[官方日志轮转说明](https://winsw.github.io/v2/doc/loggingAndErrorReporting/)。
包装器不放源码仓库，安装器核对固定SHA-256：
`923111c7142b3dc783a3c722b19b8a21bcb78222d7a136ac33f0ca8a29f4cb66`。

```powershell
Invoke-WebRequest https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET4.exe -OutFile C:\AP-ESOP\WinSW.NET4.exe
cd C:\AP-ESOP\releases\2026-11-06
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Install -PythonExe C:\AP-ESOP\venv\Scripts\python.exe -ConfigFile C:\ProgramData\AP-ESOP\relay.env -WinSWPath C:\AP-ESOP\WinSW.NET4.exe
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Start
```

Bypass只适用于这个已审核脚本的进程，不更改系统执行策略；组织策略不允许时按组织脚本签名流程部署。
默认服务名AP-ESOP-Relay，SCM延迟自动启动、Session 0、LocalSystem账户，不依赖当前用户登录。
路径写入XML，密钥仍只在.env；不要把Key放命令行。
使用目录安全设置仅给SYSTEM和管理员配置/数据权限，程序目录也不能让普通用户写入：

```powershell
icacls C:\ProgramData\AP-ESOP /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F"
```

如改为专用服务账户，需要目录读写、Python读取、作为服务登录和VPN路由访问权限。
默认LocalSystem无需用户密码但权限较大，不能让普通用户改其程序或配置。
管理命令均在管理员PowerShell中执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Restart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Manage-Service.ps1 -Action Uninstall
```

卸载前先Stop；卸载仅移除服务注册，不删除配置、数据、历史或缓存。
异常进程退出后SCM按10/30/60秒恢复，1小时重置计数；正常Stop不触发恢复，不重启整台电脑。
health检查后台线程，中心断线不等于本地不健康；SCM恢复针对进程退出，不因HTTP unhealthy自动重启。
监控发现503应检查日志后Restart；健康检测脚本非零退出表示故障。

## 防火墙与网络

工控机固定IP或DHCP保留，如192.168.10.20。屏幕/PDA访问此地址；设备仍以中心登记device_id识别。
只对车间网段、LAN地址和Private/Domain配置文件放行一个TCP端口，不关闭整体防火墙：

```powershell
New-NetFirewallRule -DisplayName 'AP ESOP Relay LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 18081 -LocalAddress 192.168.10.20 -RemoteAddress 192.168.10.0/24 -Profile Private,Domain
Test-NetConnection 10.10.0.10 -Port 8080
Test-NetConnection 192.168.10.20 -Port 18081
```

第一条连通性检查在中转机测中心VPN地址；第二条在车间另一台电脑/PDA网段测中转。
VPN外部配置，须是服务账户/系统启动后也存在的路由；仅当前登录用户连接的VPN不满足无人值守。
同时查看服务状态页中心连接和最后成功同步，不只测试登录用户浏览器能否打开中心。
不在公网暴露中转；不可信LAN应加HTTPS反向代理或受控网络，Bearer不应走不可信明文链路。

## 浏览器和安卓接入

- 本机http://127.0.0.1:18081/，车间电脑/PDA http://192.168.10.20:18081/。
- 使用独立账号密码登录，只读查看中心、磁盘、下载、outbox和屏幕；刷新保持登录，默认12小时过期。
- 首次账号admin，随机密码在数据目录initial-login.txt；“修改密码”后删除初始文件并撤销其他登录。
- 忘记密码：本机`python -m shared.auth --data-dir <数据目录> --username admin`，交互输入，无匿名网页重置。
- 账号数据库web-auth.sqlite3随数据备份，不依赖中心在线；Cookie与中心分开，局域网HTTPS部署设置AUTH_COOKIE_SECURE=true。
- 安卓使用/api/v1/device/sync、/device/activation-check、/device/reports和/files/{id}，Bearer DEVICE_API_KEY。
- 最新目标先持久化；未完整准备时返回最新assignment、manifest=null/ready=false，保留旧播放。
- 临时下载→大小/SHA-256→fsync→os.replace→版本ready；重启清理.partial安全重下，不承诺断点续传。
- schema1/2原样保存；附加PDF不插入播放顺序。终端切换前activation-check，旧revision返回409。
- 检查不是跨网络原子物理切换，安卓仍需持续同步；迟到实际状态如实保存。
- 原始observed_at及递增report_seq写SQLite/outbox再确认，恢复后原event_id补传，不用转发时间冒充心跳。
- 任务超时不撤回目标，中转断线不创建本地任务，离线屏幕上线只拿最新目标。
- 在线只表示App通信；无联系时间unknown，超过中心配置40秒阈值offline，不证明面板亮屏。

字段见[共享契约](../docs/API_CONTRACT_V1.md)。401鉴权、404未登记/未知、409旧目标/事件冲突、
422字段无效、503未准备/磁盘不足/存储错误，Range错误416。

## 更新、备份和排错

每份数据目录只运行一个进程（OS文件锁、SQLite WAL/FULL/15秒忙等待、BEGIN IMMEDIATE）。不增加uvicorn workers。
更新：Stop→备份整个配置/数据目录→部署新release→Uninstall旧服务注册→以新ProgramRoot重新Install→Start。
配置与数据沿用，绝不清空事件队列；保留旧程序，relay schema目前1，未来升级先检查迁移兼容性。
备份在停止后复制整个relay-data及relay.env，不只复制活动中的.sqlite3；恢复连同cache/manifests一起恢复。

日志在relay-data/logs/relay.log和relay-service/logs/。blocked事件不阻塞其他屏幕，状态页显示数量和设备错误；
修正报告/校时后发送新序号，不手工删去重或重置revision。磁盘不足先释放外部空间，不删受保护内容。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File relay\windows\Health-Check.ps1
```

验证：仓库根目录python -m pytest tests -q、python -m scripts.validate_relay（需Docker及已有媒体样例）。
管理员运行scripts/validate_relay_service.ps1创建并移除临时服务。结果见[验证记录](../docs/VALIDATION.md)。
现场VPN、防火墙、Windows整机重启和Android硬件播放仍待验收，HTTP终端请求不等于安卓实机。
