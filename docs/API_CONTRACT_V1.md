# ESOP 共享接口契约 V1

状态：中心和Windows relay已实现；Android仅约定契约，尚无播放客户端实现。
本文先于中心代码创建。所有接口前缀 `/api/v1`，JSON UTF-8，时间 UTC ISO 8601
（如 `2026-11-06T03:00:00.000Z`），ID 为不透明字符串，禁止以 IP 识别屏幕。

## 安全和传输

中心管理员接口用 `Authorization: Bearer <ADMIN_API_KEY>`，中转接口用
`Authorization: Bearer <RELAY_API_KEY>`。两把配置密钥必须不同，至少 24 个可打印 ASCII 字符，无空白。
下载也检查角色，URL 不携带密钥。中心及中转浏览器改用各自独立的账号密码及服务器持久化会话，
不再让操作员输入API Key；原Bearer管理员接口保留供脚本兼容，设备通信仍用配置密钥。
密码仅保存scrypt盐化校验值；会话以随机token的SHA-256保存于独立web-auth.sqlite3，Cookie为HttpOnly、
SameSite=Strict、Path=/api/v1，默认12小时有效，刷新及服务重启保持，主动退出/过期/重置密码后失效。
中心Cookie名esop_center_session，中转esop_relay_session；账号/会话相互隔离，密钥不写浏览器存储、HTML或日志。
Cookie写操作必须X-CSRF-Token匹配会话，并拒绝非同源Origin；上传在解析文件前验证会话及CSRF。
HTTPS请求自动设置Secure，也可AUTH_COOKIE_SECURE=true强制开启；HTTP本地测试允许非Secure。
首次启动仅在数据目录生成管理员账号admin及随机初始密码文件initial-login.txt（限定本机账户/SYSTEM/管理员读取），
改密后删除初始密码文件；无出厂通用密码。生产通过HTTPS反向代理访问；VPN外部配置。

两服务共用以下/api/v1接口（JSON，禁止额外字段）：

|方法 路径|请求 / 响应|
|---|---|
|POST /auth/login|{"username":"admin","password":"用户密码"} → {"username":"admin","csrf_token":"...","expires_at":"UTC ISO 8601"}，设置Cookie|
|GET /auth/session|凭Cookie恢复相同响应；无效/过期401|
|POST /auth/logout|凭Cookie和X-CSRF-Token退出，返回{"logged_out":true}，删除Cookie/服务器会话|
|POST /auth/password|{"current_password":"旧密码","new_password":"至少10字符的新密码"}，需会话/CSRF；更改后撤销全部旧会话并建立当前新会话，返回登录响应|

账号密码错误统一401，不泄露账号存在性；同IP 5分钟内失败10次返回429，计数持久化。
账号初始化/密码恢复仅本机交互命令python -m shared.auth --data-dir <数据目录> --username admin，不提供匿名重置。
AUTH_SESSION_HOURS可配1–168小时。登录有Origin检查，JSON格式且X-ESOP-Login:1；密码/会话不写URL或日志。
中转—安卓使用独立 `DEVICE_API_KEY`（中转配置），下载同样鉴权；密钥通过部署配置安全注入。
所有设备只访问中转；中心不向设备发起连接。密钥不能放在访问日志、查询串或 manifest。

错误统一 `{"error":{"code":"conflict","message":"..."}}`。
400 无效参数；401 缺少/错误鉴权；404 不存在；409 幂等冲突、版本状态冲突、任务互斥；
413 超过文件上限；415 文件/编码不支持；422 字段或媒体校验失败；503 存储/依赖未就绪。
未知字段拒绝（422）。所有下载路径是相对 API 路径，无 `..`；接收方不能执行 manifest 中的任意 URL。

## 对象

### 任务单优先、四模块扩展（任务单字段v2，自动编号v3）

先创建任务单/播放列表，再在其四个模块上传：`production`生产作业指导书、`inspection`检验作业指导书、
`check_video`点检视频（仅MP4）、`custom`自定义。`POST /playlists`只提交名称和可选description；task_number改为服务端只读字段，手填返回422。
创建时自动生成`ESOP-YYYYMMDD-000001`格式编号，日期按Asia/Shanghai，流水号全局递增、跨日期/重启/删除不复用。
生成与创建共用BEGIN IMMEDIATE事务，并避开所有历史非空编号。POST返回初始版本及task_number，GET任务单返回相同编号。
数据库schema2→3为旧空编号补号，保留所有非空编号（包括历史手填编号），不修改旧manifest和文件。
`GET /playlists`返回`playlists[]`（未归档任务单）及`versions[]`（含历史版本用于状态追溯）。
`POST /versions/{version_id}/modules/{category}/assets`：管理员multipart `file,duration_seconds=20`；
只允许活动任务单的草稿。直接上传到模块，图片/视频立即加入，Excel/PDF转换成功后自动按页加入，无需去资料库再组合。
文档默认screen_16_9；图片/视频按manifest的contain显示；目标画布固定1920×1080、16:9，允许留白、不裁切。
资产metadata保存`playlist_id,category,attach_version_id,image_duration_seconds`；归属不可被其他任务单冒用。
转换中/失败的上传会阻止该草稿发布，需等待或删除失败文件。worker重启恢复不得重复添加页面。
播放项新增`category,asset_id`，PUT items可传category；旧项无category视作custom，旧发布清单不改写。
新发布清单`schema_version=2`保留有序`items`，新增
`display={"width":1920,"height":1080,"aspect_ratio":"16:9","fit":"contain"}`及四个`modules`：
`{"id":"production","label":"生产作业指导书","items":[...],"loop":true}`。模块按items数组顺序播放，
项ID和position保留根items的全局值，模块内position不要求从0连续；不得据此漏掉该模块页面。
空模块items=[]；默认选择第一个有内容模块；按钮始终显示四类，空模块禁用。切换模块从其第一项开始并仅循环该模块。
安卓四按钮、选中状态和离线切换属于后续客户端实现；本阶段中心提供模块预览及不可变清单，不宣称安卓已实现。
旧schema1视为custom单模块。切换类别不创建现场下发任务、不改变assignment_revision；回报current_item_id仍须属于当前版本。

`DELETE /versions/{id}/assets/{asset_id}`：从该草稿移除该文件展开的所有页面。若无其他版本引用且非转换中，
物理删除资产；其他版本仍引用时仅移出本草稿，保留字节并返回`retained=true`及引用提示。
发布版本不可移除，返回409需先复制新版本；转换中不能删除。独立资料DELETE仍使用严格引用保护。
`DELETE /playlists/{id}`：先检查所有版本是否为屏幕目标/实际/待更新或存在running任务，命中409并返回屏幕/任务提示；
转换中同样409。通过后将任务单归档，不再可上传、编辑、发布或下发；无发布历史的草稿项清空并清理无其他引用的专属文件，
有发布历史的版本、文件、manifest及任务记录保留，不提供破坏历史的强制删除。返回`archived=true,retained_history,cleanup_pending[]`。
归档是可追溯删除，GET列表隐藏；历史版本仍保留用于状态展示。并发创建任务、上传、保存及删除均由写事务复核。
数据库1→2→3以事务增加任务单表单/归档字段并补齐空编号，不修改旧版本和旧manifest；升级前备份，旧镜像不能直接回滚到新版数据库。

`asset`：`id, kind(excel/pdf/image/video), original_name, status(queued/processing/ready/failed/deleting),
created_at, warnings[], error, metadata, files[]`。保留原文件。视频 metadata 包括
`duration_seconds,width,height,video_codec,audio_codecs`。失败资产不能发布。
Excel metadata包含`page_count,render_dpi,wps_cell_images_restored`；最后一个字段为实际还原的
WPS单元格图片数，无此类图片时为0。warnings会显示兼容处理与分页提示，旧资产可能无该字段。
Excel `.xlsx`还会检查DrawingML中独立twoCellAnchor填充图形：明确宽/高为0、无文字、非线条，且未用作
双端连接线的锚点时，在隔离的LibreOffice渲染模型中移除；仅单端连接到此类对象、且有零轴尺寸的孤立连接线一并处理。
正常尺寸图形、蓝箭头、图片、文字、普通横/竖线均保留，不按颜色删除。只影响新转换，不改原件和旧发布文件。
metadata新增`ignored_degenerate_shapes`记录实际处理数（包括相关孤立线），warnings提示数量及人工核对；无法唯一映射时转换失败。
Excel 上传可选 multipart `render_mode`：`print`（默认，尊重原打印分页）或
`screen_16_9`（管理员明确选择：每张可见工作表缩为一页，忽略打印区域，不导出隐藏工作表）。
模式入队即持久化，重启恢复保留；旧资产没有模式时按 `print` 处理。
屏幕模式先由 LibreOffice 生成整表 PDF，再等比居中到白色 1920×1080 PNG 画布，
不拉伸、不裁切；PDF保留整表原始比例，播放使用PNG。多工作表仍按可见工作表顺序生成多项。
屏幕模式 metadata 示例：`{"render_mode":"screen_16_9","page_count":1,"render_dpi":null,
"output_width":1920,"output_height":1080,"wps_cell_images_restored":10}`。
整表过长时文字会缩小，发布前必须检查分页图片清晰度；切换模式需重新上传生成新资产，不改变已有发布。

PDF上传扩展名`.pdf`，保留原件，返回`kind=pdf,status=queued`，使用同一持久化conversion_job。
省略`render_mode`时PDF默认`screen_16_9`（Excel仍默认`print`）；PDF的`print`保留已有分页并按DPI生成PNG，
`screen_16_9`将**每个已有PDF页**等比适配1920×1080，不重排内容、不合并多页为一页。
应先在WPS中设定每张工作表缩放至一页，核对打印预览并导出PDF，再上传以避开中心的Excel绘图解析。
直接上传由中心生成且已经错位的PDF不能修复错位。加密PDF、无效PDF、零页或超过MAX_PAGES均失败，不可发布。
PDF metadata含`render_mode,page_count,render_dpi,output_width,output_height`，不包含WPS还原张数。
原PDF文件role为`original`，完成后另提供role=`pdf`的预览副本（字节/摘要相同）和role=`page`分页图片。
PDF原件/预览/分页下载均鉴权；整个处理只栅格化页面，不运行PDF脚本或打开外部链接。
示例返回：`{"id":"asset_pdf","kind":"pdf","status":"queued","metadata":{"render_mode":"screen_16_9"},
"conversion_job":{"status":"queued","attempts":0}}`（省略通用字段）。

媒体删除仅管理员可操作。`GET /assets/{id}/usage`返回
`{"asset_id":"asset_1","can_delete":false,"blockers":["被播放列表引用"],"references":[
{"playlist_version_id":"ver_1","name":"装配工序","version":1,"status":"published",
"desired_device_ids":["screen-a"],"actual_device_ids":[],"pending_device_ids":[],"task_count":1}]}`。
检查所有草稿items和发布版本items/manifest中的文件ID，包含历史版本，不能只检查当前目标。
排队或转换中返回can_delete=false。已保存的草稿引用需先移除并保存；发布/历史版本不可强制删除。
`DELETE /assets/{id}`重新在BEGIN IMMEDIATE事务中核对引用/转换状态；冲突409，原资料保持不变。
示例：`{"error":{"code":"asset_in_use","message":"无法删除：装配工序 · v1（已发布）正在引用此资料。",
"details":{"references":[...]}}}`；转换冲突code=`asset_processing`。
无引用资料先持久化status=`deleting`和metadata.delete_requested_at，再清理原件/PDF/分页及队列记录，
同时清理该转换任务遗留的私有临时目录；完成返回`{"asset_id":"asset_1","deleted":true}`。删除后文件及资产404。
磁盘清理失败返回503 `asset_delete_failed`，资料保留deleting状态及可见错误，不能预览下载/编排/发布；
管理员可重试删除，worker启动及每30秒轮询检查补清理（转换忙时延至本次转换结束），重启不会将半删除资料恢复为ready。
此时并非已经完全删除，列表明确显示“删除未完成”。不存在返回404；下载错误提示资料已删除或不存在。
删除不会改写任何播放列表、manifest、任务、目标安排或实际状态，也不提供强制删除接口。

`conversion_job`：`id,asset_id,status(queued/running/succeeded/failed),attempts,
created_at,started_at,finished_at,error`。进程重启时 running 回队一次，超过恢复次数标 failed。

文件：`id,download_path,media_type,size_bytes,sha256,role,page_number`。
`role=original/pdf/page/image/video`；非分页文件的 page_number 为 null。
例：`{"id":"file_1","download_path":"/api/v1/files/file_1","media_type":"image/png",
"size_bytes":34567,"sha256":"64位小写十六进制","role":"page","page_number":1}`。
SHA-256 按实际字节计算；下载接收方必须验证大小和摘要，全部完成后原子切换播放目录。

`playlist_version`：`id,playlist_id,name,version,status(draft/published),created_at,published_at,archived_at,
items[],manifest`。发布后版本和清单不可修改；编辑创建新草稿（version 递增）。

`manifest` 不可变，以下为兼容的旧schema1示例；新发布采用上文schema2四模块扩展（摘要仅示例缩略）：

```json
{
  "schema_version": 1,
  "playlist_version_id": "ver_1",
  "name": "装配工序",
  "version": 1,
  "published_at": "2026-11-06T03:00:00.000Z",
  "loop": true,
  "items": [
    {"id":"item_1","position":0,"type":"page","duration_seconds":20,"playback":"timed","file":{"id":"file_1","download_path":"/api/v1/files/file_1","media_type":"image/png","size_bytes":34567,"sha256":"...","role":"page","page_number":1}},
    {"id":"item_2","position":1,"type":"image","duration_seconds":15,"playback":"timed","file":{"id":"file_2","download_path":"/api/v1/files/file_2","media_type":"image/jpeg","size_bytes":12345,"sha256":"...","role":"image","page_number":null}},
    {"id":"item_3","position":2,"type":"video","duration_seconds":null,"playback":"once","file":{"id":"file_3","download_path":"/api/v1/files/file_3","media_type":"video/mp4","size_bytes":123456,"sha256":"...","role":"video","page_number":null}}
  ]
}
```

position 从 0 连续递增；page/image 默认 20 秒，可设 1–3600 秒；video 自然播放一次到结束，
duration_seconds 必须 null；最后一项后回第一项。文件下载不意味着播放成功。
终端及预览按比例完整显示图片/视频（contain），不得拉伸或裁切。1920×1080 屏幕可直接播放
屏幕模式生成的 page 文件；播放语义与默认停留20秒保持一致。

`deployment_task`：`id,request_id,playlist_version_id,status(running/succeeded/failed/timed_out),
created_at,expires_at,finished_at,targets[]`；target 有 `device_id,assignment_revision,status(pending/succeeded/failed),error`。
终态不可回退，超时释放全车间任务锁但不撤销安排。目标全成功任务成功；目标全部结束且有失败则任务失败。
超时任务仍可有 pending target，这是历史快照，不再推进。

`desired_assignment`：`device_id,assignment_revision,playlist_version_id,task_id,assigned_at,relay_acked_at`。
revision 是中心持久化全局递增整数；接收方逐设备仅接受大于已保存 revision 的安排，
相等幂等，小于忽略。一次改派为每台屏幕分配新的 revision。任务历史不等于当前安排。

`device_report` 示例：

```json
{"event_id":"screen-a-000123","device_id":"screen-a","report_seq":123,
 "observed_at":"2026-11-06T03:00:10.000Z","assignment_revision":17,
 "actual_version_id":"ver_1","current_item_id":"item_1","playback_state":"playing",
 "pending_version_id":null,"error":null}
```

playback_state=`idle/downloading/playing/paused/error`；actual/version/item 可 null。
每台设备 report_seq 必须跨进程重启持久化、单调递增。event_id 全中转唯一且不可复用。
同 event_id 同载荷重试为 duplicate；不同载荷 409；较小/相等 seq 标 stale，不覆盖新状态。
observed_at 是终端实际采样时间，不是补传时间；中心拒绝超过 30 秒的未来时间。
迟到回报可更新设备实际状态（只要 seq 更新），但不能使终态任务回退。
revision 和 actual_version/item 必须相容；回报旧安排合法，但不能确认新任务。

## 中心管理员（已实现）

`DELETE /versions/{id}`为管理员“删除当前版本”：至少保留一个未删除版本，最后一个返回409
`last_version`并提示删除整个资料条目。目标/实际/待更新屏幕或running派发任务引用时返回409
`version_in_use`，details列出device_ids/task_ids；目标版本即使任务超时仍受保护。
该版本有转换中的上传时409。删除通过后以独立`version_archives`记录归档时间（数据库schema4），
GET /playlists的versions仍返回历史及archived_at，UI版本/下发下拉框隐藏归档项；禁止继续克隆、编辑、发布和新下发。
已发布版本的items/manifest/文件与任务历史完全保留；草稿清空播放项、清理无其他版本引用的文件，
磁盘失败返回cleanup_pending并由worker补清理。返回`{version_id,archived:true,retained_history,cleanup_pending:[]}`。
重复删除同版本幂等返回归档结果；新版本号继续MAX(version)+1，不复用删除的版本号。
此接口不取消派发，不解除屏幕安排，也不删除整个资料条目。

管理界面分为三个互斥显示的子视图：`/#library`工艺资料库、`/#screens`屏幕与下发、
`/#history`下发历史。左侧导航切换，右侧只显示当前模块；浏览器前进/后退及刷新地址可恢复视图。
切换不重新载入应用，保留资料草稿、上传选择及屏幕勾选；旧`#playlists/#devices/#tasks`地址兼容。
此项只改变页面导航与名称，不改变鉴权、媒体、发布版本或派发API。
当前管理页临时隐藏版本选择、复制/删除版本入口及版本说明；底层版本和API保留。
管理页不展示版本号或版本数量，资料库每份资料仅进入最新内容；下发改用可搜索、每页10条的单选表格，
每份资料只列最新未归档已发布内容。若有更新草稿，提示“有待发布修改”；草稿不能下发。
刷新发现所选发布内容已被更新或归档则清空选择，需重新选择，不静默改派。搜索/翻页保留有效选择。
底层playlist_version_id、历史清单/任务及revision语义不变，历史不是删除；发布不自动下发。
已发布资料通过普通“编辑资料”入口进入内部草稿，不让隐藏版本控件造成无法维护；发布仍生成不可变清单。

|方法 路径|请求 / 响应|
|---|---|
|GET `/health`|无需密钥，DB/存储探活；不泄露业务数据|
|GET `/settings`|默认停留/心跳/离线阈值/上传上限|
|POST `/assets`|multipart `file`（xlsx/xls/pdf/png/jpg/jpeg/mp4），可选 `render_mode=print/screen_16_9`；仅Excel/PDF可用屏幕模式，非法模式422；Excel/PDF返回queued；图片/视频同步验证，返回asset|
|GET `/assets`|`{"assets":[asset...]}`（含转换任务）|
|GET `/assets/{id}/usage`|管理员查看删除阻止原因及引用版本/设备，200；未知404|
|DELETE `/assets/{id}`|管理员删除无引用且不在转换中的资料；200完成，409引用/转换冲突，503文件清理失败，404不存在|
|GET `/files/{id}`|管理员或中转鉴权，返回文件流，ETag=SHA-256|
|POST `/playlists`|`{"name":"装配工序","description":"可选说明"}` → 初始draft及服务端自动task_number；description可省略|
|GET `/playlists`|`{"playlists":[活动任务单...],"versions":[含归档历史的版本...],"modules":{...}}`|
|POST `/versions/{id}/clone`|未归档版本生成同playlist的新draft，复制项；删除的版本不可克隆|
|DELETE `/versions/{id}`|删除/归档当前版本，引用/转换中/最后版本409；200返回archived与cleanup_pending；需管理员|
|PUT `/versions/{id}/items`|`{"items":[{"asset_id":"...","duration_seconds":20},{"file_id":"...","duration_seconds":15}]}`；Excel/PDF资产按PDF页序展开；视频duration留空/null；数组即顺序，返回版本|
|POST `/versions/{id}/publish`|无需 body；验证非空和 ready 文件，落盘不可变 manifest，返回版本；重复发布返回同一版本|
|POST `/devices`|`{"device_id":"screen-a","name":"1号屏"}` → 登记屏幕；ID 1–64 字符 `[A-Za-z0-9_-]`，重复 409|
|GET `/devices`|`{"summary":{"registered":1,"online":0,"offline":0,"unknown":1,"pending":1},"relay":{"state":"unknown","last_seen_at":null},"devices":[...]}`；每设备含 desired、actual、online_state、last_contact_at、error|
|POST `/deployments`|`{"request_id":"UUID或唯一字符串","playlist_version_id":"...","device_ids":["screen-a"],"timeout_seconds":120}` → task；request_id 同内容返回原任务，异内容409；车间另有 running 409|
|GET `/deployments`|`{"tasks":[...]}`，保留历史|

GET devices 设备实际字段为 `actual_version_id,current_item_id,playback_state,pending_version_id,
reported_assignment_revision,report_seq,last_contact_at,error`。online_state=online/offline/unknown。
中转过离线阈值时全部屏幕 unknown，不能沿用旧在线统计。心跳只表明 App 通信，不保证物理面板亮屏。

## 中心—中转

单车间固定中转 `RELAY_ID`。中转所有请求主动连接中心。

`POST /relay/sync` 请求 `{"relay_id":"workshop-1"}`，每 10 秒（可配置）调用。
返回完整快照，**读取不删除**，重复可读；没有分页/增量游标，不存在丢游标漏目标：

```json
{"server_time":"2026-11-06T03:00:00.000Z","heartbeat_seconds":10,"offline_seconds":40,
 "assignments":[{"device_id":"screen-a","assignment_revision":17,"playlist_version_id":"ver_1","task_id":"task_1","assigned_at":"2026-11-06T03:00:00.000Z","relay_acked_at":null}],
 "versions":[{"id":"ver_1","manifest":{"schema_version":1,"items":[]}}],
 "tasks":[{"id":"task_1","status":"running","targets":[]}],
 "devices":[{"device_id":"screen-a","name":"1号屏"}]}
```

示例 manifest/items 为空只是缩略，发布版本一定非空。versions 包含所有当前安排引用的完整 manifest。
中转实现补充（兼容增加，不重写既有manifest）：versions还包含设备actual/pending引用版本。
每个versions条目新增`manifest_sha256`及`files[]`。摘要为manifest按UTF-8、sort_keys=true、
ensure_ascii=false、separators=(',',':')序列化后的SHA-256；files为播放项文件与所属资产PDF预览的去重并集，
沿用文件对象结构，不含Excel原件，每版本最多4000个文件。中转必须验证清单摘要、结构、重复ID及全部文件。
devices新增既有设备实际字段`actual_version_id,current_item_id,playback_state,pending_version_id,
reported_assignment_revision,report_seq,last_contact_at,error`，初次同步恢复原始联系时间和序号。
新增`assignment_history[]`，结构同desired_assignment，保存历史revision到版本映射；仅供校验迟到报告，
不能作为待执行目标。相同revision不同内容或相同版本不同manifest摘要为409，不能覆盖本地。
tasks 返回运行中以及最近100条历史，不承担目标同步职责；目标始终由 assignments 全量快照提供。
中转必须先将最新安排、manifest 及其校验文件持久化，再发
`POST /relay/acks`：`{"relay_id":"workshop-1","assignments":[{"device_id":"screen-a","assignment_revision":17}]}`。
返回 `{"accepted":1}`；旧 revision 忽略，重试幂等，未来 revision 409。
ack 只说明中转接收，不表示屏幕已播放。

`POST /relay/reports`：`{"relay_id":"workshop-1","reports":[device_report]}`（最多500条）。
响应 `{"results":[{"event_id":"screen-a-000123","status":"accepted"}]}`，status 也可 duplicate/stale。
整批事务，任一校验失败整批回滚。中转必须使用 outbox 持久化事件，中心确认后才删除 outbox。
网络失败重传相同 event_id/载荷；重复判定记录长期持久化，V1 无自动清理。
reports、sync、acks 都更新中转联系时间；只有 report 更新屏幕联系时间。

## 中转—Android（中转已实现，Android客户端未实现）

Android实现补充（兼容增加）：device/sync新增`manifest_sha256`（ready时同中心清单规范JSON摘要，
否则null）、`report_seq_floor`（该屏中转已接受最大report_seq）。终端验证清单摘要、不可变ID/文件映射，
只下载manifest根items引用的图片和视频，不下载PDF或Excel原件。
首次安装本地seq=0但report_seq_floor>0时拒绝冒用已使用device_id，要求管理员另登记新编号；不静默重置/跳号。
device_report可选`download_progress`，结构为
`{"playlist_version_id":"ver_2","received_bytes":1024,"total_bytes":4096,"completed_files":0,"total_files":2}`；
字节/文件数非负且不得超过总数。下载新版本但仍播旧版时actual/revision维持旧值，pending指向新版本，
playback_state仍反映真实playing/paused；无可见有效内容时才downloading。可见首图绘制或视频首帧后才报告新actual。
无进度时省略download_progress，规范化事件不增加null字段，确保升级前event_id重试仍幂等。
中心、中转持久保存并在状态API提供该字段；只表示传输进度，不代表播放成功。

安卓本地按模块循环，底部上一项/暂停继续/下一项/重播；暂停手动翻页仍暂停、图停初始计时、视频停起点。
图片缩放触发暂停，退出缩放仍暂停；新安排成功切换后恢复正常循环。恢复本地缓存先标“离线/待核对”，
不宣称匹配最新安排。同revision同版本不重启当前项，同版本更高revision重新确认后应用。
下载断点基于不可变sha命名.partial，使用Range/If-Range；200安全重写，206校验Content-Range，416安全核验或重下。
全部文件校验并复核最新revision及activation-check后，首帧显示成功才事务提交active；失败恢复旧内容。

同 `/api/v1`，Bearer DEVICE_API_KEY；relay 只接受中心已登记 device_id。

|方法 路径|语义|
|---|---|
|POST `/device/sync`|`{"device_id":"screen-a","known_assignment_revision":16}` → `{"server_time":"...","heartbeat_seconds":10,"assignment":desired_assignment或null,"manifest":manifest或null}`；始终返回当前最新，不读取即删除|
|GET `/files/{id}`|鉴权下载已缓存媒体；文件未准备好503，未知文件404|
|POST `/device/reports`|`{"report":device_report}` → `{"event_id":"...","status":"accepted或duplicate或stale"}`；先写 relay 本地事务/outbox，再确认；序号去重同中心|
|GET `/local/status`|只读查看中转连接、缓存、设备和安排；使用中转管理员配置密钥，不允许创建/取消现场任务|
|POST `/device/activation-check`|`{"device_id":"screen-a","assignment_revision":17,"playlist_version_id":"ver_1"}` → `{"allowed":true,"assignment":desired_assignment}`；仅最新revision且全部校验文件已准备才通过，否则409/503|
|GET `/health`|免鉴权，仅探活数据库、目录和同步线程；中心断线不影响本地健康，卡死线程503|

中转具体响应：device/sync增加`ready`和`error`；目标尚未完整缓存时返回最新assignment、
`manifest:null,ready:false,error:"..."`，终端保留旧播放；无安排时assignment/manifest均null、ready=false。
known_assignment_revision大于中转已知值时503（中转可能暂时落后），绝不返回旧目标作为可激活内容。
即使ready=true，终端提交本地切换前须再次activation-check；检查与回报不是物理切换的分布式事务，
检查之后新目标到达时仍须下一轮同步纠正。迟到实际报告如实保存，不视作新目标确认。
文件支持单段Range、206、Content-Range、Accept-Ranges、416（带bytes */size）、HEAD及If-Range；
未知文件404、尚未完整或校验失效503；下载和激活检查也要求DEVICE_API_KEY。
本地管理员网页使用独立账号会话；兼容脚本仍可用LOCAL_ADMIN_API_KEY，不得与DEVICE_API_KEY或中心密钥相同。
GET `/`为只读状态页外壳，账号登录后使用Cookie恢复状态；GET `/local/status`响应：
`{"server_time":"...","relay_id":"workshop-1","center":{"state":"online或offline或unknown","last_seen_at":null,"error":null},
"configuration":{"configured":true,"heartbeat_seconds":10,"offline_seconds":40},
"disk":{"total_bytes":1000,"free_bytes":500,"reserve_bytes":100},"downloads":[{"file_id":"file_1","status":"downloading","received_bytes":10,"size_bytes":100,"error":null}],
"outbox":{"pending":0,"blocked":0},"devices":[{"device_id":"screen-a","name":"1号屏","online_state":"unknown",
"last_contact_at":null,"desired":null,"actual_version_id":null,"current_item_id":null,"playback_state":"idle",
"pending_version_id":null,"report_seq":0,"reported_assignment_revision":0,"error":null,"sync_error":null}]}`。
无心跳unknown，超过offline_seconds为offline；中心失联不伪造设备失联，使用本地原始心跳判定。
事件先持久化再返回。中心确认accepted/duplicate/stale后标为已送达，保留去重事件；
单事件永久错误单独隔离为blocked并显示，不阻塞其他屏幕；网络/5xx按退避重试。
中转向中心下载采用单线程、流式、限时、有限次重试；局域网同时最多4条文件响应（MAX_LOCAL_DOWNLOADS可配），
超出503/download_busy。重启清除.partial后安全重新下载，不承诺断点续传。
缓存清理保护最新目标、实际播放、待更新、每设备当前及上一准备版本、outbox引用版本、正在下载文件。
其他缓存按最近准备时间和CACHE_RETENTION_HOURS清理；磁盘不足只清理不受保护缓存，仍不足则503/disk_full，
保留旧播放。清单/文件身份映射和报告去重不随缓存清理丢失，重新使用时重新下载校验。

Android 开机读取本地已验证 manifest 循环播放；离线继续旧版本；有新 revision 时后台下载，
先核对全部 size/sha256、保留旧缓存，再原子提交安排，切换到新版本第一项；下载失败保留旧播放并上报 error。
改派期间又来更高 revision：只提交最新 revision，丢弃旧待切换；先存 revision 再回报。
暂停时回报 paused，不把它当作目标已开始播放。播放成功报告带实际 revision/version/current_item。
同版本重新下发也必须确认新的 revision。断网多次改派后上线只获取最新目标，无需依次重放旧任务。
App 必须保留 report_seq；重装需管理员重新分配 device_id，不能静默重置序号。

## 兼容性及未来

V1 中心是唯一任务创建方。后续 PDA 需要由中转统一仲裁、统一任务锁和离线冲突解决，
不能简单增加第二个中心任务入口。PDA、取消抢占、Office在线编辑、审批、多车间、MES均未实现。
