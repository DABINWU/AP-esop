# 架构与关键约束

## 边界

```text
浏览器管理员 ──HTTPS──> 群晖中心 Web (FastAPI, 1进程)
                            │ 本机卷 SQLite / originals / derived / manifests
                       独立 worker (同镜像, 1进程, 无网络)

Windows 中转 ──外部 VPN，主动轮询/确认/回报──> 中心
     ▲
Android 屏幕 ──局域网主动同步、鉴权下载──┘
     └─ 下载完毕/校验成功 → 本地原子切换 → 循环播放
```

中心、Windows中转及Android播放器均已有程序，Android实机需单独验收。中心没有设备 IP、推送连接、VPN 配置或物理亮屏检测。
单中转 ID 配置式，设备以登记的固定 device_id 标识；中转接口只接受该 relay_id。

## 三种状态

|对象|持久化内容|何时终止/替换|
|---|---|---|
|deployment_task + task_targets|一次操作历史、期限、每屏 revision、执行结果|running → succeeded/failed/timed_out；终态不回退|
|assignments|每屏当前持续目标，revision、不可变版本|下一次改派覆盖该屏目标；超时不撤销|
|devices|最后回报的实际版本、项、暂停/播放、pending、错误|只接受更新 report_seq；可与目标不同|

下发使用 BEGIN IMMEDIATE；先检查 request_id 幂等指纹，再检查任务锁与版本、设备。
事务内创建任务、递增全局 revision、写每屏安排。SQLite 唯一部分索引强制全车间最多1个
running 任务。幂等指纹含标准化屏幕集合、版本和超时；同 ID 异内容409。
读取历史、同步、回报、创建任务和 worker 轮询都会检查超时；没有后台秒级 SLA。

中转接收/缓存 ack 与实际播放分离；ack 不结束任务。全部 target 成功任务成功；全部终结
且有失败任务失败；否则到期 timed_out。迟到事件仍可更新设备实际状态，但只推进 running 的 pending target。
旧任务和旧 revision 不会重新覆盖最新安排。离线期间多次下发，上线只读 assignments 最新快照。
报告 revision 为已应用安排；下载新版本但仍播旧版时回报旧 revision/actual，pending_version_id 指向新版本。
无旧版时可回报新 revision、actual=null、downloading/error；只有 playing + 匹配版本且无错误才成功。

回报事件编号全局唯一，规范化载荷同编号重试 duplicate，异载荷409；更旧 seq 为 stale。
设备 seq 跨重启持久化，中心保存事件去重记录；整批事务。observed_at 为实际采样时间，
不能用中转补传到达时间伪造上线；未来时间容忍30秒。设备联系时间取已存和新采样时间的较大值。

## 发布与数据安全

Excel 转换先保存原件和队列；worker 单进程顺序认领。媒体文件完成后才在同一数据库事务
写 PDF/分页元数据并标 ready。渲染期间不是可发布资产。任务出错标 failed，显示错误。
发布再次核对文件大小和 SHA-256，清单写临时文件、fsync、原子更名，再提交 DB。
发布版本有数据库 UPDATE 保护，版本API只允许受保护的归档删除。磁盘 JSON 与 DB 都保存清单；下载内容也鉴权。
崩溃可能留下未引用的原件/manifest 临时文件，不会把半成品发布；V1 不自动垃圾回收。

媒体删除独立于版本生命周期：检查所有版本items和manifest文件ID，不仅检查当前安排。
BEGIN IMMEDIATE将引用检查、转换检查和deleting标记序列化，与草稿保存/发布/worker认领互斥。
引用冲突不改动原资料；允许删除后先提交非可播放状态deleting，再在独立写事务内验证绝对路径并清理文件。
清理成功才删文件/转换任务/资产记录；中断或磁盘失败保持deleting及错误，下载/编排被拒绝，worker补清理。
文件清理幂等，删除自己资产目录前核对ID、绝对路径和符号链接，不允许影响其他资产或数据根目录。
独立资料删除不修改既有版本、manifest、下发任务、目标和设备状态；模块删除可修改当前草稿，发布历史不变。
删除状态使用既有status/metadata字段持久化；任务单表单/归档另有下面的v2迁移。

SQLite WAL + synchronous FULL + 15秒忙等待，写入用 BEGIN IMMEDIATE；网络挂载不支持。
一个 Web 进程，一个 worker（独立 OS 文件锁，跨容器同本地卷互斥）。扩容需要迁移数据库、
队列和锁机制，不能只增加 uvicorn workers。数据库schema_version=4，启动事务迁移1→2→3→4：
v2增加任务单表单/归档字段，v3补齐空编号、持久化自动编号计数器，v4独立version_archives表；不改旧资产和manifest，非空编号保留。
新编号日期按UTC+8，计数器全局递增，与创建事务同锁，归档/删除/克隆版本都不重置或复用编号。
升级前备份，不支持旧镜像直接读新版本库。

版本删除在同一写事务检查设备/运行任务、转换上传和至少一个存续版本的约束，防止并发全删。
归档时间写独立表，避免触碰已发布版本的数据库不可变触发器。草稿归档清空items并清理无引用文件，
发布版仅归档；后台及UI都阻止继续编辑/克隆/新下发。迟到实际回报仍可如实反映历史版本，不回退任务终态。

## 任务单优先与四模块

先创建任务单，再向草稿production/inspection/check_video/custom模块上传。
资产metadata保存归属、分类、目标草稿和秒数；图片/视频同事务加入，文档由worker在ready事务中自动按页加入。
重复恢复按file_id去重，转换中/失败阻止该草稿发布。新schema2清单包含四modules和1920×1080/contain规则。
旧schema1和无category项视为custom，旧发布字节不改；下发任务与assignment_revision语义保持不变。
单文件删除只操作可编辑草稿，其他版本引用时保留文件；整个任务单归档前检查设备及运行任务，归档后不可再编辑/下发。

## Excel 转换

### 直接PDF输入

PDF资产采用`kind=pdf`，与Excel复用持久化队列、单worker、恢复和失败保护。
上传验证PDF头，worker用pdfinfo确认有效页数并拒绝加密，Poppler逐页生成PNG。
省略模式时PDF默认screen_16_9，保留现有PDF页数，等比适配1920×1080；不调用LibreOffice，
不执行PDF脚本或访问外部链接。原件和预览PDF字节一致，均鉴权，manifest只引用分页PNG。
适用于管理员在WPS先完成单页排版和PDF导出，避免中心Excel转换器再次解释浮动图形。
不会从已错位PDF还原正确版式，也不会把多页文件自动拼成一个播放项。
数据库kind无枚举约束，新增类型无需schema迁移，Web和worker需同步更新。

### Excel输入

上传时将`render_mode`存入资产metadata，队列恢复不丢失。默认`print`保留原打印分页。
管理员明确选择`screen_16_9`时，LibreOffice SinglePageSheets生成每表一页的矢量PDF，
worker核对工作表数量，并使用Poppler pdfseparate/pdfunite在私有临时目录移除隐藏工作表页。
未过滤PDF不进入可下载目录。随后以最大边3840像素有界栅格化，等比缩放、白底居中到1920×1080。
PDF保留整表比例，PNG为实际播放画布，不改变原Excel或已发布资产。多可见表按原顺序各占一项。
导出参数使用明确的UNO PropertyValue序列类型，避免被LibreOffice静默忽略。
单页模式必须满足最终页数等于可见表数，否则失败；不会把多页结果标称为单页成功。

worker 用发行版 `/usr/bin/python3` 和 python3-uno 启动 LibreOffice，通过命名管道连接。
每次单独的用户 profile 和UNO管道，转换读取保留原文件名的隔离副本（原件字节不变），
使 `&F` 页眉/页脚不显示内部资产ID。宏级别Very High、空可信路径，明确 DisableMacrosExecution；加载参数
MacroExecutionMode=NEVER_EXECUTE、UpdateDocMode=NO_UPDATE、Hidden、Silent。
普通文档ReadOnly加载；含WPS DISPIMG图片或零尺寸残留图形的隔离副本在内存中允许修改，验证图片后清除占位公式、
用UNO图片对象按对应单元格/合并范围还原，只导出PDF，从不保存回原Excel。图片解码和还原张数不完整则失败。
DrawingML兼容仅忽略独立twoCellAnchor中明确零宽/高、带填充、无文字的非线图形及其单端孤立连接线；
双端连接锚点、普通横竖线、正常尺寸图形和照片保留。按源名称唯一映射后从内存DrawPage移除；
数量不符或名称歧义使转换失败。记录ignored_degenerate_shapes及警告，不重写旧资产或发布清单。
Calc profile 的 Content/Update/Link=1（Never），不调用 refreshAll 或主动刷新外部数据。
Compose worker `network_mode: none` 进一步阻止网络资源读取。导出 SinglePageSheets=false；
不修改工作簿打印区域、纸张、分页和缩放。原文件自身设为缩成一页时保留并提示人工检查。
图片使用无损PDF压缩，不主动降采样。WPS兼容边界与实际样本限制见WPS_COMPATIBILITY.md。
加载后核对实际 Excel 导入过滤器，防止损坏 XLS 被 LibreOffice 当作文本/CSV 兜底导入并误报转换成功。

每阶段有超时；超时杀死 Linux 进程组（含 LO 子进程），清理本次 profile/半成品。
PDF 页数1–MAX_PAGES，默认最多200页；超过 PAGE_WARNING_COUNT 提示。
Poppler 以默认160 DPI 输出PNG，范围72–300可配。XLSX预检打印区域、单页高度和外部连接；
XLS 提示打印区域尚不能静态预检。内置中文字体并不能保证原版式，必须逐页预览真实样本。

worker 启动先获得 OS 锁；将中断的 running 工作回队（attempts<2），第二次中断标 failed。
已完成任务不重跑；普通转换错误直接 failed，修复原文件后重新上传。
进程退出自动重启；unhealthy 只用于告警，Compose 不负责自动重启仍存活但不健康的进程。

配置值参考：[LibreOffice Calc schema](https://github.com/LibreOffice/core/blob/master/officecfg/registry/schema/org/openoffice/Office/Calc.xcs)、
[UNO UpdateDocMode](https://api.libreoffice.org/docs/idl/ref/namespacecom_1_1sun_1_1star_1_1document_1_1UpdateDocMode.html)。
不能混淆 profile 的 Link=1 与 UNO 的 NO_UPDATE=0（代码使用常量名）。

## 设备页面的可信范围

默认心跳10秒、离线40秒，可配。relay 超过离线阈值，所有屏幕 unknown，在线/离线统计均不复用
历史值，显示中转最后联系UTC。relay 新鲜时，按各屏 observed_at 判断 online/offline。
App 心跳不能保证背光、HDMI、面板供电正常。页面保留实际状态和历史联系时间用于排查。

## Windows中转实现

中心与中转人机界面使用shared/auth.py统一实现、各自web-auth.sqlite3独立账号及会话。
首次随机admin凭据仅写受保护initial-login.txt，密码存scrypt盐化校验，随机会话只存SHA-256。
HttpOnly/SameSite Strict Cookie默认12小时，刷新和重启可恢复；CSRF用于写操作和上传，非同源Origin拒绝。
登录失败计数持久化，改密撤销旧会话并删除初始文件；密码恢复仅本机交互命令。
程序间Bearer接口保持兼容，Cookie管理员身份不能冒充中转或安卓；中转页面仍只有只读状态功能。

relay单进程FastAPI，独立三个线程：中心sync/ack、单线程下载、报告outbox补传。
中心sync增加manifest_sha256、播放文件与PDF并集、assignment_history及原始设备状态，不修改旧manifest。
SQLite恢复最新安排、设备序号、原始observed_at、历史revision和去重；事务校验不可变身份，仅高revision覆盖，
同revision只允许receipt改变。schema1/2原样保存，编号和页面标签不构成新版本。

流式临时下载，限制连接/读取/总时间/重试，大小和SHA-256验证后fsync+原子重命名。
全部文件就绪才写原子manifest、标ready和ack；PDF不进入播放项；中断清理.partial后安全重下。
缓存保护最新目标、实际、pending、每屏当前/上一准备版本、未送达报告引用、正在下载及文件流租约。
其他按24小时过期，空间不足可提前清非保护内容；仍不足记录disk_full，不清空当前内容。
清理只删字节，保留清单/文件身份、事件和revision；再次使用须重新验证/下载。

device/sync返回最新安排，未ready不给manifest；activation-check本地锁内复核最新revision。
快照提交、清理、ready和激活检查共用缓存锁，阻止本地并发旧激活。检查之后仍可能有远程改派，
安卓须持续轮询纠正，不宣称物理切换与中心事务原子一致。迟到实际报告可匹配历史revision，不回退终态任务。
先写report/outbox再accepted，网络失败原载荷重试；中心确认标sent，永久拒绝单事件blocked并显示，不阻塞其他屏。
中心断线继续本地缓存，不创建现场任务；文件鉴权、单段Range/HEAD/If-Range、256KB流式及下载租约。

WinSW 2.12.0托管Python，SCM延迟自动启动、Session 0、进程失败按10/30/60秒恢复，日志轮转。
健康接口检查DB、目录和线程，中心断线不算本地宕机；SCM不是HTTP健康监控，unhealthy应告警后重启。
本地管理员网页及PDA只有只读入口。安装与网络要求见relay/README.md。

## Android 原生播放端

Android原生端：Kotlin View/Room/本地文件/Media3，min23、compile/target34，不依赖WebView/Office/GMS。
配置Keystore AES-GCM加密，管理员设置密码与播放按钮分离。Room状态、报告序号及outbox事务持久化，WAL/FULL同步；
清单摘要/结构及文件大小/SHA验证，Range/If-Range恢复.partial，全部完成后复核最新安排和activation-check。
首图onDraw或Media3首帧（关联实际mediaId的analytics事件）后才提交active、确认实际播放；失败回到旧缓存。
旧内容离线恢复先标待核对，相同安排不重置，新revision成功后首项正常循环；前台期间独立轮询/下载/心跳/补传。
图片单页采样和串行解码，播放器复用，页面离开释放资源。管理员开机适配/固定屏幕入口受系统与固件限制，不假定系统权限。
详情见android/README.md；断电、联发科硬解和长时间温度/内存验收仍需真实一体机。

## 后续演进

中心v1.1.0/schema5采用两发布版本策略，version_retirements与version_archives独立于不可变versions表。
发布及存量收敛在BEGIN IMMEDIATE内计算引用：新发布+最多一个受保护旧版；两种旧版同时占用则拒绝发布。
过期版items/manifest、任务、文件描述保留用于迟到报告及relay/sync；独占媒体通过retiring→expired清理字节。
共用/草稿引用保留，删除失败可重试。历史文件描述不可一起删除，否则迟到旧实际回报会使中转同步查询404。
旧缓存可能继续在离线端播放，但过期版本无法从中心重下；设备应补最新目标，不能把历史记录当备份。

PDA 下发引入第二个任务来源，必须把车间统一仲裁移到中转（含中心意图与本地意图），
重建单一任务锁、revision 分配/映射、冲突与断网语义；当前中心事务锁不能跨离线中转仲裁。
管理员取消抢占需要独立的取消/替代安排语义，不能把超时当取消。
Office在线编辑、审批流、多车间、MES、视频转码均未实现；不可在UI中显示为可用能力。
