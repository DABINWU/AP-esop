# 中转一键安装包

交付文件：`relay/dist/AP-ESOP-Relay-1.0.0-Setup.exe`，Windows 10/11 x64（Intel/AMD）。无需另装Python、Docker或Git。运行需要管理员同意UAC。安装包未做商业代码签名，Windows可能显示“未知发布者”；应核对随包SHA256及来源，遵循单位的软件安装策略。

## 使用：只需运行安装向导

1. 把EXE复制到**正式中转电脑**，双击运行，允许管理员权限。不要装在群晖上。
2. 安装目录保持默认。中心地址填写 **http://192.168.0.186:18080**（仅适用于当前网络；正式跨车间时填VPN可达地址）。
3. 中心通信密钥填写群晖中心`.env`里的`RELAY_API_KEY`。这不是网页登录密码。在群晖SSH项目目录执行`grep '^RELAY_API_KEY=' .env`，只复制等号后面的值，勿发到聊天或截图中。
4. relay_id默认`workshop-1`，端口默认`18081`。安卓终端访问密钥可自己填一把至少24位且与中心密钥不同的ASCII密钥；留空会自动生成。安卓配置必须填同一个终端密钥。
5. 设置中转网页管理员密码（至少10位，确认两次）。默认勾选放行局域网端口：仅Private/Domain网络、LocalSubnet来源，不关闭整体防火墙。
6. 点击安装，完成后后台服务自动运行并开机延迟自启。开始菜单“AP ESOP Relay → 中转状态网页”打开本机状态页，账号`admin`，密码是刚才设置的。关掉网页不会停服务。

安卓地址填写 `http://中转电脑的局域网IP:18081`，不是群晖地址。IP用Windows的`ipconfig`查看，并在路由器设置DHCP保留。若安卓密钥自动生成，在管理员记事本打开`C:\ProgramData\AP-ESOP\relay.env`查看`DEVICE_API_KEY`；该文件限制为管理员/SYSTEM读取，不能发给普通用户。

首次安卓连接前仍需在中心登记唯一device_id。安装器不创建屏幕或总部任务；VPN和中心服务不包含在本安装包内，中心离线也可安装，之后会自动重试。

## 更新和卸载

更新前备份`C:\ProgramData\AP-ESOP`，再运行新版安装包，选择原安装目录；向导中留空的配置/密码保留原值。不会重新生成已有密钥，不改relay_id，不清空数据库、缓存、revision或outbox。需要修改relay_id时不支持直接覆盖现有数据。

Windows“已安装的应用”中卸载AP ESOP Relay，或用开始菜单卸载入口。卸载移除后台服务和本安装器创建的防火墙规则，**保留ProgramData中的配置、账号、数据库和媒体**。重新安装到相同数据位置可恢复。卸载不会删除中心屏幕登记，也不会清空安卓屏缓存。

若电脑已有之前手动安装的`AP-ESOP-Relay`服务，安装器会阻止覆盖；先停服务并备份，按原部署脚本卸载旧服务注册，再安装。手动部署的`relay.env`和`relay-data`可沿用默认ProgramData位置；不要把开发电脑正在使用的服务直接覆盖。

## 运行位置及限制

- 程序：`C:\Program Files\AP-ESOP-Relay`，内置独立Python及锁定版本的依赖，不使用电脑原Python。
- 配置：`C:\ProgramData\AP-ESOP\relay.env`。
- 数据与日志：`C:\ProgramData\AP-ESOP\relay-data`，包装器日志在`service-logs`。
- 默认监听所有网卡，防火墙仅放行本地子网Private/Domain。网络被标记为Public时默认不会放行；请管理员确认车间网络可信后调整，勿关闭总防火墙。
- Windows服务`AP-ESOP-Relay`，LocalSystem，延迟自动启动，失败10/30/60秒恢复。无需登录Windows、命令窗口或浏览器；VPN也必须支持无人登录时运行。
- 安装完成以本地health成功为准，不等于中心已可达。电脑/PDA访问`http://中转LAN-IP:18081/`，先确认状态页中心在线。
- 本安装包不适用于Windows 7、32位Windows或Windows ARM；新现场机器的权限、VPN及整机重启仍需验收。

## 开发者重新打包

构建机用Windows x64、Python3.13及仓库依赖，安装包不需要目标机联网安装依赖。

1. 从Python官方下载固定`python-3.13.3-embed-amd64.zip`放入`test-output/relay-installer-tools`。build.py核对固定SHA256。
2. 准备Inno Setup 6.4.3编译器于该目录下`inno/ISCC.exe`，安装工具前检查官方签名。
3. 准备官方WinSW2.12.0 NET4二进制，脚本校验现有固定SHA256。
4. 执行：

```powershell
.venv\Scripts\python.exe relay\installer\build.py --winsw <WinSW.NET4.exe路径>
```

编译器只在开发机使用，运行时文件取自官方嵌入包，Python依赖来自构建环境并检查requirements直接版本，递归包含当前平台的运行依赖及随包license。`DEPENDENCIES.txt`记录全部实际版本。Python运行时升级时同步更新build.py校验值并重新实测，不声称此固定版本为最新Python。

`scripts.validate_relay_installer`必须以管理员运行：独立服务名和数据目录、隔离Docker中心，实际EXE静默安装/升级/卸载，模拟终端HTTP；不会使用或重置现场屏幕。安装器为Inno Setup生成的EXE，源脚本见setup.iss。测试应核对临时服务、防火墙规则和容器已清理。调试应答JSON包含秘密，只允许受限目录，不能提交。
