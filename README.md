# 工厂 ESOP · 中心、Windows 中转与安卓屏

可运行的 FastAPI + SQLite 中心，简单中文管理页面，单车间、单中转、多登记屏幕。
**center、relay 和原生 Kotlin Android 播放 App 已实现。** Android 最低支持6.0（API23），不依赖Google Play服务。
VPN 由外部提供；中转主动访问中心，中心不能主动连接车间设备。

Windows中转安装、后台服务、失败恢复、防火墙、访问和备份见[中转部署说明](relay/README.md)。
APK构建、安装、屏幕编号、管理员设置及离线播放说明见[安卓部署说明](android/README.md)。
当前正式安装包：[Android1.0.4 Release](android/dist/AP-ESOP-Player-1.0.4-release.apk)。可覆盖1.0.3正式版；正式签名与旧调试版不同，迁移规则见安卓部署说明。
本次中心同步接口增加manifest摘要、关联PDF、历史revision和设备原始状态；部署中转前同步更新中心Web镜像。
已有manifest与资料库不改写，SOP/SIP四模块、16:9、自动编号和已发布不可变语义继续沿用。

本机当前测试：中心http://127.0.0.1:18080/，中转http://127.0.0.1:18081/；
Windows服务AP-ESOP-Relay已运行。状态页使用独立管理员账号登录，设备配置文件位于
`test-output/local-relay/relay.env`（仅SYSTEM/管理员可读）；本机服务管理命令需追加
`-ServiceDir C:\project\AP-esop\test-output\local-relay\service`。
这些是本地测试路径；迁移工控机按relay/README.md使用独立ProgramData目录和车间实际IP。
注意：当前`test-output`包含实际运行的中心/中转数据，不能整目录删除；清理时保护LOCAL_INSTANCE.json、
LOCAL_RELAY_INSTANCE.json指向的配置/数据目录。后续安卓验证使用真实设备，项目专用模拟器数据已清理。

左侧导航切换三个独立视图，右侧显示当前内容：**工艺资料库**（`/#library`）、**屏幕与下发**（`/#screens`）、
**下发历史**（`/#history`），每次只显示一个模块。资料录入与版本维护在资料库完成，
设备统计只在屏幕页显示；切换保留未保存编辑，刷新后恢复当前视图及12小时内有效登录。

版本号区分同一份工艺资料的内容快照：v1发布后固定，修改时复制成v2，下发绑定具体版本。
版本选择旁支持“删除当前版本”：屏幕目标/实际/待更新或运行派发引用时阻止并提示；
已发布版本以归档保留清单/文件/历史，草稿清理无其他引用的文件。至少保留一个版本，
需要全部移除时删除整个资料条目。删除后版本号不复用，已归档版本不能再次编辑或下发。

当前按录入阶段需求，页面暂时隐藏版本选择、复制/删除版本控件及版本说明，底层记录/API仍保留。
从中心v1.1.0起，每份资料最多保留两个可用已发布版本：新版加一个旧版。
旧版优先保留仍被屏幕目标/实际/待更新或执行任务引用的版本；没有引用时保留最近旧版。
若两种不同旧版同时被引用，会阻止再发布并列出屏幕，请先同步到已发布内容，不能强删现场仍需要的文件。
其余旧版过期，释放其独占原件/PDF/图片/视频；共用文件与草稿引用仍受保护。历史清单、任务和文件描述仅保留记录，
不再保证过期内容可重新下载（410）。草稿和转换临时文件另外占用空间，不能为了配额丢弃未发布修改。
worker约每30秒重试收敛/清理，存量计划可在鉴权API `/api/v1/retention` 查看。
页面不再显示版本号或版本数量；资料库每份资料进入最新内容，下发只列每份资料最新已发布内容。
资料库和下发资料表格支持编号/名称/说明搜索、每页10条分页；下发通过单选框选资料，再多选屏幕。
有更新草稿时提示“有待发布修改”，草稿不直接下发；已选内容再发布更新时需重新选择，不能静默替换。
已发布内容需维护时点击普通“编辑资料”按钮进入内部草稿，再保存/发布，无需操作版本控件。

## 群晖部署

需要支持 Docker / Container Manager 的群晖（建议 x86_64，至少预留 2GB worker 内存和媒体空间）。
构建需要 BuildKit（Docker 20.10+可开启），用于系统依赖下载缓存；旧版群晖先在SSH启用并构建。
以下命令在群晖 SSH 的本仓库目录执行；也可在 Container Manager「项目」导入 compose.yaml。

1. 把本仓库复制到 `/volume1/docker/esop/app`，创建数据目录并赋予容器 UID 10001 读写权限：

   ```sh
   sudo mkdir -p /volume1/docker/esop/data
   sudo chown 10001:10001 /volume1/docker/esop/data
   cp .env.example .env
   chmod 600 .env
   ```

2. 编辑 `.env`，将两把密钥换成不同的随机值（至少24字符），设置实际数据路径。
   可用 `openssl rand -hex 32` 分别生成两次。不要把 `.env` 提交到版本库。

3. 构建并启动，**只允许一个 Web 进程 + 一个转换 worker**：

   ```sh
   export DOCKER_BUILDKIT=1
   docker compose config --quiet
   docker compose up -d --build
   docker compose ps
   docker compose logs --tail=80 web worker
   curl -f http://127.0.0.1:8080/api/v1/health
   ```

   旧版群晖可能使用 `docker-compose` 命令，并需 `export COMPOSE_DOCKER_CLI_BUILD=1`。
   首次构建需访问镜像源和 Debian/PyPI 软件源。
   若现有镜像源不可达，先修复 NAS 的网络/镜像仓库配置；不要禁用下载鉴权。

4. 管理地址：`http://<NAS局域网IP>:8080/`；使用账号密码登录。首次账号admin，随机密码见数据目录initial-login.txt。
   生产通过群晖反向代理配置 `https://<你的域名>/`，保留 Authorization 头，上传限制至少为
   `max(UPLOAD_MAX_MB, VIDEO_MAX_MB) + 1MB`，超时大于视频探测的30秒。
   按实际访问范围设置 NAS 防火墙；不需要中心访问车间的任何入站设备地址。

5. 创建任务单（名称、自动编号、可选说明）→ 进入生产作业指导书、检验作业指导书、点检视频、自定义
   四个模块直接上传 → 转换成功自动按页加入 → 调整模块顺序/秒数 → 预览各模块 → 发布 → 登记并多选屏幕下发。
   中转未部署或安卓尚未回报时，任务会超时、目标仍保留；中转缓存ack不代表屏幕已播放。

容器 `restart: unless-stopped`、健康检查、日志轮转（每服务 10MB×3）已配置。
Docker 健康状态 unhealthy 本身**不会**重启容器；进程退出才触发重启，需监控 health 状态。
worker 禁止联网，系统字体含 Noto CJK / Liberation；若与原 Excel 字体不同，版式仍可能变化。
可通过派生镜像安装有合法授权的原字体，重新上传转换；已发布版本不原地重算。

WPS的`.xlsx`单元格图片（`DISPIMG("ID",1)`）已增加兼容转换，支持文件内PNG/JPEG，
保留原件并显示还原张数；引用损坏或不支持的图片公式会报错。`.et/.ett`需先在WPS另存为`.xlsx`。
新增转换会忽略明确零尺寸、无文字的填充残留图形及相关孤立线，避免已隐藏的旧图形再次显示；
保留正常箭头、照片和表格底色，不改Excel原件或旧发布预览。
范围、真实样本检查和剩余版式问题见 [WPS图片兼容说明](docs/WPS_COMPATIBILITY.md)。

新任务单模块内上传Excel/PDF默认适配1920×1080、16:9，完整显示，允许留白、不裁切。
每张可见工作表生成一张1920×1080图片，等比居中留白，不拉伸、不裁切；多工作表按顺序播放。
此模式忽略打印区域，整表较长时文字会缩小，必须检查分页图片预览后再发布。
旧通用上传API仍保留原分页选项；新页面不再要求先导入资料库。旧任务内容在自定义模块显示。
新发布manifest为schema2，包含四模块及contain显示规则；原件和旧发布版本保持不变。
安卓按四个分类按钮选择并仅循环当前模块，支持上一项、暂停/继续、下一项、重播及图片缩放；中心提供同模块预览。

对于这类WPS浮动流程图，建议先在 **WPS打印设置中选中指导书区域、横向、缩放为1页宽×1页高**，
检查打印预览的图形、照片、页数和文字，再导出PDF上传。导出前原表内容过多时，单页文字会较小。
中心现有Excel处理本来就经过PDF；关键是由WPS先固定原版式，避免LibreOffice重新解析浮动图形。
PDF上传默认每页等比适配1920×1080，保留原件、PDF预览和分页PNG；多页PDF仍按页循环，
不会自动拼成一页。加密、损坏或超过MAX_PAGES的PDF处理失败，不可发布。
上传后已有的错位内容不会自动修复。Web和worker需同步更新；启动以事务升级SQLite schema1→2→3→4，
增加任务单编号/说明/归档字段；schema3补齐旧空编号并持久化流水号，schema4增加独立版本归档表，保留已有非空编号。
编号格式`ESOP-YYYYMMDD-000001`，按北京时间日期，全局流水跨日/重启/删除不复用，创建表单无需手填。
升级前备份；不能将旧镜像直接回滚到新版库，须恢复匹配的升级前备份。

## 数据和备份

任务单各模块以列表展示名称、类型/大小、转换状态、播放信息、上传时间、提示与操作。
模块内删除文件会移出该草稿及全部分页；若其他版本仍引用，保留历史字节并提示。发布版本先复制再修改。
整个任务单删除前检查目标/实际/待更新屏幕及运行任务，命中时阻止并提示先改派；
无发布历史时清理无其他引用的专属文件，有发布历史则归档保留版本、manifest和文件。
以下严格引用保护规则仍适用于旧的独立资料DELETE接口：
可查看引用、预览、下载和删除。删除会先检查所有已保存草稿、发布版本（包括历史版本）：
存在引用时阻止删除，并提示具体列表/版本、关联屏幕和任务记录；草稿先移除媒体并保存。
已发布/历史版本不可强制删除其媒体，转换中的资料需等待完成。
无引用资料确认删除后，清理原件、PDF、分页及转换记录。删除文件失败会显示“删除未完成”，
该资料停止下载和编排，支持重试；worker重启或后续轮询会补清理，不会恢复成可用资料。
其他浏览器中未保存的旧选择可能已失效，保存时会明确提示资料已删除，请刷新后重新选择。

宿主机数据目录默认 `/volume1/docker/esop/data`，容器内 `/data`：

|路径|内容|
|---|---|
|center.sqlite3 / -wal / -shm|资产、队列、不可变版本、任务、目标、设备、事件与同步确认|
|originals/|Excel 原件、上传图片和 MP4|
|derived/|转换 PDF、按页 PNG|
|manifests/|发布时落盘的不可变 JSON，数据库同时保存|
|tmp/|转换临时目录和独立 LO profile，可在停机后清理|
|worker.lock / worker-heartbeat|单 worker 锁和健康信号|

**必须使用 NAS 本机卷挂载，不能把 SQLite 放在 SMB/NFS 网络共享上。**
WAL、FULL 同步、15秒 busy_timeout、BEGIN IMMEDIATE 写事务已启用。
不支持多 Web worker、多副本或跨主机共享数据库。

首次使用建议采用停机一致性备份（原件、派生文件和数据库必须同一时间点）：

```sh
docker compose stop web worker
sudo tar -C /volume1/docker/esop -czf /volume1/backup/esop-$(date -u +%Y%m%dT%H%M%SZ).tgz data
docker compose start web worker
```

先创建 `/volume1/backup`；检查 tar 成功后再认为备份完成。
另行加密备份 `.env`；不要将密钥混入公开日志。恢复到**空数据目录**，保持 UID 10001 权限，
再启动相同应用版本；确认 health、资产预览、发布版本、设备最新目标。恢复时未完成转换
自动回队一次，第二次中断标失败。群晖快照/Hyper Backup 也应在停止两个服务后取一致快照。
V1 不自动清理历史/媒体，需监测磁盘空间。

## 开发与验证

```sh
python -m venv .venv
# Linux: . .venv/bin/activate ; Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m scripts.generate_samples
```

本地运行设置环境变量 `ADMIN_API_KEY`、`RELAY_API_KEY`、`DATA_DIR` 后执行：

```sh
uvicorn center.app:create_app --factory --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
python -m center.worker
```

完整媒体处理优先在镜像内验证；Windows 本机不具备 LO UNO 的系统 Python 环境时，使用 Docker。
详细命令和实际结果见 [开发说明](docs/DEVELOPMENT.md)、[验证记录](docs/VALIDATION.md)。

## 仓库与范围

中心与中转各自独立管理员账号、服务器持久化会话，刷新和服务重启保持登录（默认12小时）。
首次随机密码仅写本机数据目录initial-login.txt，登录后可用“修改密码”；改密撤销其他会话并删除初始文件。
没有通用默认密码。密码只保存scrypt校验值，浏览器不保存API Key；中转/安卓仍用各自配置密钥通信。
会话数据库为数据目录web-auth.sqlite3，备份时一并保留；首次登录凭据不要提交到代码仓库或共享到普通用户目录。
忘记密码在本机执行`python -m shared.auth --data-dir <数据目录> --username admin`交互重设，无匿名重置接口。
群晖容器使用`docker compose exec web python -m shared.auth --data-dir /data --username admin`。
生产使用HTTPS并设AUTH_COOKIE_SECURE=true；账号登录失败有限流，反向代理须保留真实Host、Origin、Cookie和X-CSRF-Token。
反向代理需发送X-Forwarded-Proto=https，并设置FORWARDED_ALLOW_IPS为其真实来源IP；不要将任意客户端都设为可信代理。

`center/` 中心和静态管理界面；`relay/` Windows后台服务；`android/` Kotlin原生播放器；`docs/` 接口和架构。
接口先于实现定义在 [API_CONTRACT_V1.md](docs/API_CONTRACT_V1.md)，所有业务 API 为 `/api/v1`。
支持 xlsx/xls → LibreOffice PDF → 清晰分页 PNG，图片，H.264/AAC（或无声）MP4；
默认视频180秒，超限拒绝，不截断、不提供转码。Excel 失败不能编排/发布。

未实现：PDA 下发、管理员取消抢占、Office 在线编辑、审批、
多车间、MES、视频自动转码。真实工艺 Excel 版式、群晖现场部署、VPN 和三端实机联调仍需验收。
后续计划见 [ACTIVE.md](docs/plans/ACTIVE.md)。
