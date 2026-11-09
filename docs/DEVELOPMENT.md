# 开发说明

## 运行

Python 3.11+。安装 `requirements-dev.txt`；生产依赖 `requirements.txt`。
配置 `ADMIN_API_KEY`、`RELAY_API_KEY`（不同且至少24字符）、可写的本地 `DATA_DIR`。
命令见 README。Windows 示例（PowerShell）：

```powershell
$env:ADMIN_API_KEY = 'dev-admin-use-your-own-random-ascii-key'
$env:RELAY_API_KEY = 'dev-relay-use-another-random-ascii-key'
$env:DATA_DIR = "$PWD/data"
.\.venv\Scripts\python.exe -m uvicorn center.app:create_app --factory --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

环境变量从操作系统读；裸 Python **不会**自动读 `.env`，Compose 会读。
LO 转换需 Linux 镜像内的 libreoffice-calc、python3-uno、poppler-utils、字体。
ffprobe 负责视频探测，不会自动转码。`UNO_PYTHON/SOFFICE_BIN/FFPROBE_BIN/PDFINFO_BIN/PDFTOPPM_BIN`
可用于本地路径覆写；Compose 默认不需要改。

## 验证层级

中转入口：`python -m relay --config <绝对路径relay.env>`，自带.env读取，`--check`不输出密钥。
只部署中转安装relay/requirements.txt并携带relay/、shared/，无需LibreOffice/FFmpeg。
共享报告模型shared/reports.py由中心/中转同时引用；协议先更新API_CONTRACT_V1.md，中心镜像须复制shared。
中转单测：`python -m pytest tests/test_relay.py -q`；真实HTTP：`python -m scripts.validate_relay`，
后者创建隔离Docker中心和Windows中转，保留证据后停止，需已有媒体验证样例，不访问用户资料。
管理员运行scripts/validate_relay_service.ps1验证唯一命名临时SCM服务，finally卸载、不改防火墙。
完整部署与配置说明见relay/README.md。

1. `python -m pytest -q`：真实 SQLite 临时库、HTTP TestClient 和线程并发测试；媒体检测边界用
   可控 probe 数据，worker失败/恢复场景用明确的模拟故障。不需要安装 LO。
2. `python -m scripts.generate_samples`：生成中文、表格、合并单元格、嵌入图片、分页/打印设置的
   xlsx，长表无打印区域样例，xls，以及损坏 xls。写入 `test-output/samples/`。
   同时生成不含客户数据的`wps-cell-images.xlsx`和`wps-missing-image.xlsx`，用于回归WPS图片兼容。
3. 真实媒体验证需要镜像和开发依赖：

   ```sh
   docker build -t ap-esop-center:v1 .
   docker build -f Dockerfile.validation -t ap-esop-validation:v1 .
   mkdir -p test-output
   docker run --rm --network none -v "$PWD/test-output:/app/test-output" ap-esop-validation:v1 python -m scripts.validate_media
   docker run --rm --network none ap-esop-validation:v1 python -m pytest -q -p no:cacheprovider --basetemp=/tmp/esop-pytest
   ```

   验证镜像继承中心镜像，仅另加测试依赖；生产 worker 仍然不联网。
   Linux 绑定输出目录需赋予UID10001写权限。PowerShell使用 `${PWD}/test-output` 形成绝对挂载路径。
   输出目录每次新建，保留 samples、PDF、PNG、result.json。
   WPS回归不只检查返回张数：还检查实际PDF图片条目、占位公式文字已清除、缺失图片引用使任务失败。
   `tests/test_drawing_compat.py`覆盖零尺寸残留、正常箭头/线条/文字/双端连接保护、名称歧义及原件不变。
   客户样本只在忽略的本地QA目录验证，禁止复制入测试样例或镜像；新转换才应用修复。
   另验证`screen_16_9`下每张可见表一页、隐藏表不导出、顺序正确、PNG实际1920×1080及下载摘要。
   PDF回归使用人工矢量样例，含横/竖两页、默认16:9/原分页、原件与预览字节一致、
   明确不存在的UNO路径以证明不调用Office、实际加密/损坏/超页数拒绝，PDF按序展开进入manifest。
   加密PDF由LibreOffice对无客户数据的合成Excel导出，仅作拒绝样例。
   单元测试检查宽表/长表/方形表的等比居中、不裁切，以及上传模式的持久化和恢复。
   推荐使用模块命令以保证导入路径。

4. `docker compose --env-file .env config --quiet` 检查部署语法，`up -d` 后检查两个服务健康。
   可运行 `python -m scripts.validate_compose`，自动创建独立Compose项目和随机测试密钥（不输出），
   验证实际worker SIGKILL恢复、Web重启、HTTP多屏语义和停机备份到空目录恢复。
   测试后自动down，保留test-output证据；`--keep-running` 可保留仅绑定127.0.0.1:18080的测试实例。
   其中relay回报仍是HTTP模拟，没有Windows中转或安卓实机。
5. 浏览器手动核对登录、上传、PDF/分页、编排、排序、循环、暂停、发布和多屏状态。
6. 群晖、Windows中转和安卓实机验收另行执行，不能把 TestClient 或回报样例当实机验证。

受限Windows环境可用 `--basetemp=test-output/pytest`，先创建父目录。
若历史pytest目录权限阻止仓库扫描，指定测试目录：`python -m pytest tests -q -p no:cacheprovider
--basetemp=test-output/pytest-新编号`，无需删除已有验收产物。
不要在日志/命令参数中打印真实密钥。`docker compose config` 不带 `--quiet` 会展开密钥，避免共享输出。
测试中的 test/integration 密钥仅用于隔离临时数据，不作为默认生产密钥。

## 代码导航

`center/app.py` 路由与鉴权；`service.py` 下发/回报事务；`db.py` schema；`media.py` 检测；
`worker.py` 队列认领/恢复；`lo_convert.py` UNO版式转换；`static/` 中文管理页。
`asset_delete.py`引用检查、删除状态持久化与文件清理；测试覆盖草稿/发布/历史引用、设备关联、
转换互斥、文件占用后恢复、路径范围和并发保存/删除，真实媒体回归核对PDF原件/预览/PNG的物理删除。
直接PDF输入仅调用Poppler；`scripts/pdf_samples.py`提供无客户数据的矢量PDF夹具。
前端使用HttpOnly Cookie登录，CSRF token仅在内存；fetch获取鉴权blob用于图片、PDF、视频和下载，无公开媒体目录。
共享账号实现shared/auth.py和shared/web/accounts.js；测试tests/test_accounts.py及scripts/validate_accounts_ui.mjs。
两服务使用各自web-auth.sqlite3，不修改业务库schema；初始随机凭据只写受保护initial-login.txt，不进日志/镜像/版本库。
后续契约变更先更新API文档，再实现，并补必要的状态语义测试。

任务单四模块：`categories.py`稳定分类及manifest分组，`playlist_workflow.py`自动加入与删除/归档，
`db.py`事务迁移1→2。镜像内`python -m scripts.validate_modules`实际转换四类内容，核对恢复去重、
1920×1080页面、分组清单和克隆删除历史保留；`node scripts/validate_ui.mjs`为DOM冒烟测试，
覆盖启动、模块切换/筛选和旧项兼容，不能当作真实浏览器/安卓验收。

自动任务编号由`db.next_task_number`在写事务内生成，schema3迁移保留非空旧号、为空号补号。
回归包含并发唯一、重启/归档不复用、跨北京时间午夜、克隆不消耗新单号、API拒绝手填编号。

管理页使用hash视图切换：library/screens/history，旧playlists/devices/tasks地址兼容。
Node DOM冒烟覆盖互斥可见、导航当前状态、历史地址/未知地址回退、切换保留草稿和表单输入。
这仍不代表真实浏览器视觉验收；页面改动不涉及API、数据库和worker转换逻辑。

版本删除测试覆盖鉴权、最后版本、目标/实际/待更新引用、转换互斥、历史清单字节不变、
共享文件保留、并发不能全删以及归档后上传/编辑/发布/克隆/下发被拒绝。schema4以独立表持久化归档。

## 中心发布版本

v1.1.0/schema5增加version_retirements；published_immutable不改，历史items/manifest与文件描述保留。
retention.plan/enforce/retire在同一写事务中选择新版+最多一个受保护旧版，冲突409；独占资产进入retiring→expired，
worker约30秒重试。清理只删除字节，避免迟到旧实际回报使relay/sync依赖的文件描述消失。
tests/test_retention.py验证配额/旧目标与实际保护/共用与草稿/重启恢复/迟到报告/并发。
scripts.validate_relay的离线跨资料改派使用不同资料条目，另外专门验证同条目保留策略及真实Excel字节释放；不启动模拟器。

中心系统版本唯一维护在`center/version.py`的CENTER_VERSION，左下角与FastAPI元信息共用此值。
从v1.0.0开始记录；每次交付中心程序/界面更新，在构建镜像前递增版本并记录VALIDATION。
一般修复递增末位（1.0.0→1.0.1），较大功能递增次位；上传资料、发布播放内容不改变系统版本。
系统版本与Android APK及不可变playlist版本各自独立，禁止将发布资料的版本号填入系统版本。

## Android 原生播放器

当前规则：2026-11-09起设备验证仅用用户真实安卓设备。以下模拟器流程为历史记录，不再默认执行或重建AVD。

最低API23（Android6.0），compile/target34；Kotlin、Room和Media3，Gradle Wrapper固定版本与分发SHA。
本机使用Android Studio自带JDK21、SDK34；JVM字节码目标17。详细安装步骤见`android/README.md`。

```powershell
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
.\android\gradlew.bat -p android assembleDebug assembleDebugAndroidTest testDebugUnitTest lintDebug --no-daemon
```

`Protocol.kt`验证共享清单；`Transport.kt`实现同中转鉴权、Range及流式校验；
`Database.kt`保存当前/上一内容、最新revision与单调事件outbox；`PlayerEngine.kt`管理下载、首帧激活、心跳和循环。
`MainActivity.kt`和`TouchImageView.kt`提供原生控件、Media3、采样图片和缩放，无WebView/GMS。
配置用Android Keystore AES-GCM保护，设置入口另有本地密码。关闭后台任务并持久保存，重开恢复后重新核对安排。

契约新增可选download_progress，以及device/sync的manifest_sha256、report_seq_floor；
旧报告无进度时沿用原规范JSON，防止升级后同event_id重试被误判冲突。中心与中转需同步更新。

`python -m scripts.validate_android`在两台预先启动的、可清空的AOSP模拟器emulator-5554/5556中安装APK和测试包；
创建独立Docker中心、Windows中转和HTTP故障代理，不修改本地正式测试实例的任务/文件/密码。
SDK通过ANDROID_SDK_ROOT指定，运行需Docker、adb和已生成的媒体夹具。模拟器分辨率建议1920×1080。
脚本记录实际中心状态、仪器测试和画面；不等同联发科硬件、Android6或长时间播放验收。

当前测试脚本默认端口5560/5562，可用ESOP_ANDROID_SERIAL_A/B覆盖；执行前要求AVD名为ESOP_API29_A/B，
避免清空用户正在使用的其他模拟器。旧端口示例仅作历史说明。
Gradle8.7使用JDK17/21；新版Android Studio若自带JBR25，在Gradle JDK设置中选择17/21，不能直接沿用JBR25。
本机测试JDK21单独下载至test-output/android-tools/jdk21并校验官方SHA，不修改系统默认Java。
