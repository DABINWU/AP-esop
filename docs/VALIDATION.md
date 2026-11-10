# V1 验证记录

验证日期：2026-11-06（用户时区 Asia/Shanghai）。仓库初始为空，没有覆盖已有实现。

## 已完成

|检查|实际结果 / 证据|
|---|---|
|Windows Python 3.13 API/SQLite测试|`25 passed`；`tests/test_center.py` + `tests/test_edge_cases.py`|
|Linux Python 3.11 同源镜像测试|`25 passed`；最终镜像内运行，报告 `test-output/pytest-linux.xml`|
|鉴权|管理员/中转角色隔离，下载也需密钥；前端无固定密钥与localStorage|
|manifest|文件摘要、大小、顺序、图片默认20秒、发布后拒绝修改、新草稿版本递增；篡改文件拒绝发布|
|互斥|6个线程真实 SQLite 竞争，1个创建成功、5个409；request_id相同重试同任务|
|持久目标|模拟中心重建App/数据库连接后目标仍在；旧任务超时不撤目标，新改派保留最新revision|
|同步与设备|完整快照重复读取不删除，ack非播放确认；多屏统计、待同步、中转失联全未知|
|回报|event去重/冲突、seq过旧忽略、批量回滚、迟到不改终态；旧任务回报不能确认新任务；暂停非成功|
|失败/恢复|转换模拟故障不能编排/发布；running恢复回队一次，再次中断失败；进程超时被结束，OS锁拒绝第二worker|
|视频边界单元测试|probe替身验证180秒上限、编码/音轨/像素格式、无效数值；不等同于真实视频处理|
|上传边界|伪图片、损坏xlsx、空文件、类型拒绝、大小拒绝；chunked流式请求也有上限|
|前端JS语法|本机 Node `--check center/static/app.js` 通过|
|Compose语法|`docker compose --env-file .env.example config --quiet` 通过（仅验证插值/语法；示例密钥不可用于启动）|
|开发Web启动|本机127.0.0.1:8088成功启动；隔离测试资料位于test-output，不是生产部署|
|基础Excel样例|`python -m scripts.generate_samples` 生成成功；含xlsx/xls、中文、合并、表格、嵌图、长表、分页、无打印区域|

测试存在1条第三方 Starlette TestClient 对 httpx 的弃用警告，不影响上述断言。
运行依赖已核对 PyPI 官方版本并锁定，`pip check` 无依赖冲突。

## 真实镜像 / 媒体

以下为本机 Docker Desktop 的 Linux/amd64 实测，不是群晖或三端实机测试。
生产镜像 `ap-esop-center:v1` 构建成功，ID：
`sha256:9de6b191c075ae24ada431e4fd537d61929855cfe3f5b6e6736ce47381788054`。
基础镜像 Python 3.11.15，LibreOffice 25.2.3.2，Poppler 25.03；安装了 Noto CJK 中文字体。
构建途中 Docker Hub EOF 和 Debian 单包502已通过缓存基础镜像、HTTPS与重试解决。

|真实验收|结果|
|---|---|
|basic-layout.xlsx|6页PDF + 6张160 DPI PNG，中文、合并标题、表格、嵌图、重复表头和手工分页可见，73条工序完整|
|basic-legacy.xls|3页PDF + 3张PNG，60行完整；页脚保留原上传文件名|
|no-print-area.xlsx|6页，明确提示未设置打印区域；保持长表多页|
|逐页视觉检查|三份样例共15页已检查，中文清晰，表格/嵌图无截断；最终XLSX页图与已核对页图SHA一致，XLS页脚修复后再次逐页核对|
|损坏XLS|failed，不生成可发布媒体；实际导入过滤器检查阻止文本/CSV兜底被误当Excel成功|
|视频时长|真实生成MP4：2秒与180秒接受，181秒返回422，不截断|
|视频兼容|H.264无声和H.264/AAC接受；MPEG-4 Part 2返回415；低配置大小上限返回413|
|manifest|Excel按页序展开 + 独立图片12秒 + 视频once，共8项；下载字节大小/SHA-256逐项一致|
|实际worker中断|正在processing时SIGKILL，再启动服务，持久队列恢复一次，attempts=2，最终ready|
|实际服务重启|Web/worker重启后版本、清单、任务、每屏最新目标仍存在|
|HTTP模拟中转|真实HTTP验证多屏目标、幂等、409互斥、超时保留安排、最新revision、ack非播放、迟到回报及中转失联unknown|
|停机备份/空目录恢复|两个服务停止后复制全部数据，恢复到新空目录后重建服务；SQLite integrity_check=ok，全部媒体摘要一致，目标/实际状态保留，两个服务healthy|

保留的本机证据（`test-output/`已忽略，不含生产数据）：

- `test-output/pytest-windows.xml`、`test-output/pytest-linux.xml`。
- `test-output/media/1793934247213507973/result.json` 与其 samples、data/derived 下PDF/PNG。
- `test-output/compose-1793934246986250500/result.json` 与 data/backup/restored。
- `test-output/samples/` 基础可上传样例。

上轮验收容器与临时Web服务已停止；数据与报告保留。重新运行命令见DEVELOPMENT.md。

## 用户允许 Docker Desktop 后的本地复测 / 2026-11-06

本轮重新执行全部验收，结果均通过：Linux镜像内25项测试、三份Excel真实转换、损坏XLS拒绝、
180秒视频接受/181秒拒绝、编码/大小拒绝、manifest、任务互斥、持久目标、worker强制中断恢复、
Web重启和停机备份到空目录恢复。两个容器均healthy。

- 自动化报告：`test-output/pytest-linux-local-rerun.xml`。
- 媒体报告：`test-output/media/1793935149731705466/result.json`。
- Compose报告：`test-output/compose-1793935147715679300/result.json`。
- 当前实例说明：`test-output/LOCAL_INSTANCE.json`，不含密钥值。

本轮保留仅绑定本机的测试实例 **http://127.0.0.1:18080/**，项目 `esop-check-716019500`。
配置文件 `test-output/compose-1793935147715679300/test.env`；管理员使用其中的ADMIN_API_KEY登录。
数据位于该目录的 `restored/`，与生产配置隔离。验收阶段使用1秒/4秒加快超时场景，完成后
已恢复默认心跳10秒、离线阈值40秒并重建服务、重新确认健康。

页面已备好xlsx、xls、无打印区域工作簿、损坏XLS、图片、2秒与180秒视频，以及
「本地预览样例」草稿（Excel6页→图片12秒→短视频，共8项），供人工预览/排序/发布。
两台登记屏幕的回报为测试脚本模拟；中转未连接后显示unknown符合预期。
本轮未进行浏览器自动交互验收，也不是三端实机验证。

停止保留实例（在仓库根目录）：

```powershell
docker compose -p esop-check-716019500 --env-file test-output/compose-1793935147715679300/test.env down
```

## 尚待验收与具体限制

- 浏览器交互验收未完成。browser access host不可用；computer-use尝试选择Chrome窗口时，
  工具因无法可靠确定当前URL而停止Windows UI操作。因此不能声称登录、PDF iframe和循环预览已实测。
- 已收到一份真实WPS工艺Excel并完成10张单元格图片恢复；可选16:9模式解决横向拆页，流程图位置仍待核对。
  不能据此证明所有真实字体、图表、打印区域、分页和屏幕可读性均已通过。详见WPS_COMPATIBILITY.md。
- 没有实际群晖NAS部署、VPN网络、中转程序或安卓APK；没有三端实机、多屏物理播放、背光或断电恢复测试。
- API回报和离线补同步测试是中心语义验证，不能作为Windows/安卓本地缓存与播放器的验证。
- 不支持视频自动转码、PDA、取消抢占、Office在线编辑、审批、多车间、MES。

## WPS真实样本缺图修复 / 2026-11-06

收到用户真实xlsx后，复现10张`DISPIMG`单元格图片缺失；原始图片数据均在文件中。
增加WPS图片兼容层，仅修改隔离渲染模型，保留原件、布局设置和已发布版本。
该次生产镜像：`sha256:3fed48f7cbca47a5d67cf05ae1da76610e2c9bde9de5c793fa9d2be0f36c34bf`。

|检查|结果|
|---|---|
|Windows / Linux自动化|各45项通过，新增图片ID/路径/模式/丢图/损坏PNG等测试|
|真实样本实际PDF|修复前仅1张Logo，修复后11张图片（Logo + 10张工艺照片），无DISPIMG占位文字|
|本地真实上传链|最新资产`asset_e9952826da074dc981c52567c0bb04a7` ready，metadata.wps_cell_images_restored=10，6页PNG|
|文件完整性|原件SHA不变；原件、PDF和分页下载大小/摘要均匹配；已发布manifest保持不变|
|用户预览|本地18080已部署新版，草稿「自锁端盖（WPS图片修复验证）」已更新，无自动下发|
|可重复媒体回归|普通xlsx/xls、无打印区域、合成WPS图片均转换成功；损坏xls与缺少WPS图片关系均failed；实际PDF图片和占位文本断言通过|
|视频与manifest回归|180秒接受，181秒拒绝，编码/大小拒绝；顺序/时长/once与文件摘要验证通过|
|独立Compose回归|18081独立项目通过worker SIGKILL恢复、重启持久化、互斥与备份恢复，结束后down；18080保留运行|

证据：`test-output/pytest-wps.xml`、`test-output/pytest-wps-linux.xml`、
`test-output/wps-real/live-result.json`、`test-output/wps-real/live-pdf-check.json`、
`test-output/media/1793937341964427465/result.json`、`test-output/compose-1793937237659121700/result.json`。
真实客户文件/图片未纳入合成测试样例或Docker构建上下文。

仍存在明确版式限制：样本没有打印区域，按现有设置横向拆成6页；流程图遮挡/位置需与WPS原始
预览核对。这是**图片恢复通过、完整版式待调整**，不能声称该文件全部版式或安卓实机已经通过。
兼容边界详见WPS_COMPATIBILITY.md。

## 1920×1080 整表单页 / 2026-11-06

用户明确要求16:9整表单页，新增可选`render_mode=screen_16_9`，默认保留`print`。
最终本地镜像：`sha256:a6e90c2a383258cd2119057e532f9b32d01f9fdb5668186952125273621cb1ff`。

|检查|结果|
|---|---|
|Windows / Linux自动化|各53项通过；新增等比完整缩放、居中留白、模式校验、队列恢复持久化|
|真实用户样本|资产`asset_c9dcfe17c8b443adb6dc2ba4ed0eea29`，实际1页PNG、1920×1080，WPS照片还原10张|
|实际PDF和视觉检查|PDF共11张图片（Logo和10张工艺照片）；已查看完整PNG，整表无横向拆页、不拉伸；黄色流程图仍有放大遮挡及越界|
|多表与隐藏表|合成三表含中间隐藏表，最终PDF和PNG仅2页，按原可见顺序，无隐藏标记文字|
|单页模式回归|原本6页的合成WPS工艺表转为1页1920×1080；普通原分页xlsx/xls仍分别6/3页|
|失败、视频及manifest|损坏xls/缺图均failed；180秒接受、181秒拒绝，MP4编码/大小限制与有序manifest通过|
|真实本地HTTP与重启|原件及已发布manifest不变，下载摘要全部匹配；重启最终镜像后单页资产与模式仍在，Web/worker均healthy|
|预览入口|http://127.0.0.1:18080/ ，草稿「自锁端盖（1920×1080 单页播放）」；单图片项默认20秒循环，未自动发布/下发|

LibreOffice实际参数必须使用明确的PropertyValue序列类型；未类型化的Python tuple会被静默忽略。
SinglePageSheets会导出隐藏表且忽略PageRange，因此先在私有目录过滤PDF，再公开派生文件；
核对总表数、可见页数，不符合则failed。栅格最大边3840后等比适配1920×1080，避免长表PDF导致无界像素渲染。
PDF保留整表比例，PNG才是16:9播放画布。不能将本次中心转换验证称为安卓实机验收，流程图兼容问题仍未解决。

证据：`test-output/pytest-screen.xml`、`test-output/pytest-screen-linux.xml`、
`test-output/media/1793939344737379025/result.json`、
`test-output/screen-real/live-result.json`、`test-output/screen-real/pdf-check.json`、
`test-output/screen-real/screen-1920x1080.png`。
实际媒体回归通过只读挂载最终center/scripts运行；客户文件仅位于忽略的test-output和原件目录。

## PDF直接输入 / 2026-11-06

用户提出先从Excel导出单页PDF。中心已有Excel→LibreOffice PDF→PNG流程，
这次新增接收管理员从WPS导出的PDF以绕过Excel图形解析。不会修复已有错位PDF。
当前本地镜像：`sha256:57e0b3a19029b05a2880067726f91d2e693057cbc793d972fad4052d09d59a6d`。

|检查|结果|
|---|---|
|Windows / Linux自动化|各58项通过；PDF上传模式、原件鉴权下载、恢复保留模式、未就绪不可发布、加密失败|
|真实PDF工具链|合成横/竖两页矢量PDF默认1920×1080，原分页模式保持横竖比；原件和预览PDF字节相同；下载大小/摘要匹配|
|不调用Office|使用明确不存在的UNO路径，PDF处理仍成功，验证输入绕过Excel解析器|
|实际拒绝|损坏PDF、LibreOffice生成的带密码PDF、2页但上限配置为1页均failed，仅保留原件，无可播放文件|
|manifest|Excel六页+PDF两页+图片+视频共10项，PDF按1、2页展开、默认20秒；发布摘要及视频once通过|
|Excel/视频回归|原分页、单页、WPS图像、隐藏表、缺图/损坏、180/181秒、编码及大小验证通过|
|本地实际上传|合成单页PDF资产`asset_14857fcf024f4947a3507079d4f9eb7d` ready，1页1920×1080，原件与预览相同，未鉴权下载401|
|部署与既有数据|18080新版Web/worker均healthy；原有资产完整响应和已发布manifest保持不变|
|视觉检查|已查看单页测试PNG，矢量文字/色块完整且无重叠；该样例不含用户内容|

证据：`test-output/pytest-pdf.xml`、`test-output/pytest-pdf-linux.xml`、
`test-output/media/1793942603463086779/result.json`、`test-output/pdf-direct/live-result.json`、
`test-output/pdf-direct/page-1920x1080.png`。实际媒体回归只读挂载最终center/scripts到验证镜像。
**尚未收到从WPS导出的真实用户PDF，不能声称黄色流程图已修复或真实版式已通过。**
此次为中心功能与实际媒体处理验证，不是中转/安卓实机验收。

## 媒体列表与删除保护 / 2026-11-06

媒体资料改为表格列表，显示文件名、类型/大小、状态、播放信息、本地上传时间、折叠提示/错误和操作。
支持查看引用及确认删除；未保存草稿在当前页面使用资料时先提示处理草稿。
当前本地镜像：`sha256:94ad69f2cb6cb1b85323f7a4788f4a767896c67235e6f91006f6972735dd3565`。

|检查|结果|
|---|---|
|Windows / Linux自动化|各69项通过；删除API鉴权、文件及记录删除、失效选择的可操作提示|
|引用保护|草稿、已发布及历史版本、独立manifest引用均阻止删除409；返回列表名/版本、目标/实际屏幕及任务记录数|
|原流程保护|阻止删除后媒体仍可下载，manifest/目标安排/任务记录不变；草稿移除并保存后才能删除|
|转换保护与并发|queued/running阻止删除；草稿保存与删除由SQLite写事务串行，不能留下悬空引用|
|清理失败与恢复|模拟文件占用及多文件删除到一半失败，保持deleting、下载/编排被拒绝；重试/重新创建DB实例补清理成功|
|范围与残留|拒绝其他资产范围的路径；删除失败资产时，连同转换任务遗留的私有副本、派生文件及原件清理|
|实际媒体工具回归|无引用PDF的原件、PDF副本、两张PNG及队列记录物理删除；发布PDF阻止409且manifest保持不变；Excel/PDF/视频回归通过|
|实际Compose/HTTP|仅新建测试PNG进行删除200，旧下载404；模拟提交deleting后中断，实际停止/启动worker后补清理成功|
|既有数据与服务|本地18080已部署；既有资产完整响应和已发布manifest前后相同；Web/worker均healthy|

证据：`test-output/pytest-delete.xml`、`test-output/pytest-delete-linux.xml`、
`test-output/media/1793943596633870157/result.json`、`test-output/asset-delete/live-result.json`。
最后增加的转换临时目录清理由Windows/Linux完整回归覆盖；实际媒体回归使用只读源码挂载运行。
浏览器交互/视觉验收仍未完成：本次浏览器inventory再次返回“browser access host is unavailable”。
HTML列表结构和JS语法已检查，但不能将接口或静态检查声称为实际浏览器交互验收。
没有删除用户既有业务资料，没有三端实机验证。

## 任务单前置与四模块 / 2026-11-06

用户确认继续16:9、1920×1080完整显示，允许留白、不裁切。新入口先建任务单，四模块直传并自动加入。
最终本地镜像：`sha256:d077428721084307c30237ec7f6f7704f71bc25f0348be57e237eec4cb99f78c`。

|检查|结果|
|---|---|
|Windows / Linux自动化|各78项通过；四模块归属、自动加入、跨任务隔离、点检仅MP4、转换/失败阻止发布、归档保护及旧文件重试|
|真实worker|合成WPS Excel→生产1页，PDF→检验2页，MP4→点检1项，图片→自定义1项；文档PNG为1920×1080，摘要正确|
|转换恢复|模拟中断后恢复一次，自动加入成功；重复attach不重复加页|
|manifest|schema2四模块数量1/2/1/1；contain显示，图片秒数12、视频once；无内容模块保留空数组|
|删除|克隆草稿删除PDF会移除两页，原发布文件/manifest不变；历史任务单归档保留字节；活动设备关联阻止409|
|迁移|事务升级schema1→2，重复初始化安全；旧manifest不改；本地升级前SQLite快照已保存|
|前端|Node DOM冒烟通过启动、四模块切换/筛选、旧项归入自定义、发布版隐藏上传；JS语法通过|
|本地Compose|实际四模块上传后自动加入，单文件删除和无发布历史整单删除清理通过；既有版本与资产响应不变，两个服务healthy|
|旧媒体回归|真实xlsx/xls、WPS/隐藏表、PDF、加密/损坏/超页数、视频时长/编码、manifest和原删除接口回归通过|

首次迁移前快照：容器`/data/backup-before-modules-1793945450530152600.sqlite3`，
对应宿主机数据目录下同名文件。这是数据库升级快照，完整灾备仍需按README备份整套媒体和数据库。
证据：`test-output/pytest-modules.xml`、`test-output/pytest-modules-linux.xml`、
`test-output/modules/1793945233110518222/result.json`、`test-output/media/1793945591551170752/result.json`、
`test-output/modules-live/migration-result.json`与`test-output/modules-live/live-result.json`。
本地示例为人工合成；测试结束归档/清理自己的测试任务，未删用户原有文件。
没有真实浏览器视觉/交互验收（browser host不可用）；DOM测试不能替代浏览器。
安卓四按钮仅契约与未来实现要求，尚无Android APK/实机，不能把中心模块预览说成安卓验证。

## 任务单自动编号 / 2026-11-06

编号改为服务端生成`ESOP-YYYYMMDD-000001`，日期UTC+8、全局递增流水；表单移除手填框，API拒绝手填。
数据库schema3保留非空旧号，并为旧空号补号；与任务创建共用写事务，删除/重启不复用、复制版本不另占任务号。

- Windows/Linux各83项通过，含12次并发创建唯一、重启/归档不复用、旧号保留及补号、北京时间午夜边界。
- Node DOM冒烟和真实媒体回归通过，旧Excel/PDF/视频、manifest和删除语义未退化。
- 本地已备份后从schema2升级3，补齐5条空编号；原有非空编号、资产、版本和manifest保持不变。
- 当前任务单列表仍为空，没有为验证生成新任务；手填请求实际422，两个Compose服务healthy。

镜像：`sha256:b6d60194b07c69e03707ff6cf6d6f85d310c2e9723736675764e03511762f457`。
数据库快照：容器`/data/backup-before-auto-number-1793946635356245700.sqlite3`。
证据：`test-output/pytest-numbers.xml`、`test-output/pytest-numbers-linux.xml`、
`test-output/auto-number/result.json`、`test-output/media/1793946574930161014/result.json`。
DOM检查不是实际浏览器视觉/交互验收，未进行安卓实机验证。

## 三个导航子视图 / 2026-11-06

管理页改为工艺资料库/#library、屏幕与下发/#screens、下发历史/#history，每次只显示一个视图。
设备概览随屏幕页显示；旧#playlists/#devices/#tasks地址兼容，未知地址返回资料库。
切换仅调整可见性，不重新载入应用，保留草稿修改、上传选择和未提交表单；打开的预览切换时关闭。
资料库主文案与可复用工艺资料的定位一致，API和数据库不变。

- Node DOM冒烟通过三个视图互斥、导航高亮、hash历史切换/旧地址兼容、未知地址安全回退及编辑状态保留。
- Windows83项回归和JS语法检查通过；真实媒体回归通过，Excel/PDF/视频、清单和删除未退化。
- 本地仅重建Web，验证worker容器ID和启动时间不变；两个服务healthy，现有资料编号/内容/摘要和发布清单保持。
- 仍未完成真实浏览器视觉/交互验收；DOM测试不能当作浏览器或安卓实机验证。

Web镜像：`sha256:1d726040cc2b983dc20892842a1c75ef1ce74d3ec88287ed45bbf3df86b66354`。
证据：`test-output/pytest-nav.xml`、`test-output/navigation/result.json`、
`test-output/media/1793947281863039623/result.json`；DOM命令为`node scripts/validate_ui.mjs`。

## 版本删除 / 2026-11-06

资料编号标识资料条目，v1/v2标识该资料的不同内容快照。已在选择框旁加入“删除当前版本”及用途说明。
数据库schema4用独立version_archives记录归档时间，不修改已发布版本的items/manifest或磁盘清单。

|检查|结果|
|---|---|
|Windows / Linux|各92项通过，新增版本删除鉴权、最后版本、目标/实际/待更新屏幕引用、转换保护、共享文件及并发删除|
|归档防重用|归档后上传/编辑/发布/克隆/新下发409；重复删除200；删除v2后复制为v3，不复用版本号|
|真实转换文件|四模块Excel/PDF/视频转换后，删除克隆草稿、归档发布版本，历史manifest和下载文件保留；整单归档通过|
|UI DOM|归档版本从版本与下发下拉框过滤，保留历史标签；其余导航/模块冒烟和JS语法通过|
|媒体回归|原Excel/PDF/视频、时长/编码限制、分页、manifest和原文件删除验证通过|
|本地升级|备份后schema3→4，原版本/manifest/资产及编号计数器不变，未删除用户现有版本；Web/worker均healthy|

镜像：`sha256:11c60a345b10ed781a3a14deffed10536e1d801307ab69b1dc174114df2bf35d`。
快照：容器`/data/backup-before-version-delete-1793948330215098600.sqlite3`。
证据：`test-output/pytest-version.xml`、`test-output/pytest-version-linux.xml`、
`test-output/modules/1793948232870183967/result.json`、`test-output/media/1793948230673723754/result.json`、
`test-output/version-delete/result.json`。没有真实浏览器视觉验收，DOM检查不代表浏览器或安卓实机验证。

## 左侧导航布局 / 2026-11-06

三个主模块导航移至左侧aside，右侧workspace-content显示当前子视图；桌面菜单200px，
小屏118/104px，内容列minmax(0,1fr)，表格保持内部横向滚动。未改业务API、数据库或worker逻辑。
Node DOM导航/模块/归档筛选冒烟、Windows92项回归及真实媒体回归通过。
本地仅更新Web，worker容器ID/启动时间不变，两个服务healthy；原资料与发布清单保留。
没有真实浏览器视觉验收，HTTP/CSS结构和DOM测试不能替代浏览器或安卓实机。

Web镜像：`sha256:c5d957a89138caac8946ce684533fefd254ea47bf5fbb75addff3e4af7e95166`。
证据：`test-output/pytest-sidebar.xml`、`test-output/sidebar/result.json`、
`test-output/media/1793948642528022807/result.json`；导航DOM命令`node scripts/validate_ui.mjs`。

## 暂时隐藏版本控件 / 2026-11-06

页面隐藏版本选择框、复制/删除版本按钮及其说明，保留底层版本和API。
普通“编辑资料”入口对已发布内容创建/复用内部草稿，避免隐藏控件后无法维护；提示文字同步指向编辑入口。
发布按钮显示“发布资料”。不修改任何既有版本或文件，不移除后端不可变保护。
DOM冒烟覆盖隐藏状态、发布资料正常进入编辑及复用既有工作草稿（第二次不重复克隆）；
Windows92项回归、JS语法和真实媒体回归通过。本地只更新Web，worker未重启，两个服务healthy，原资料/清单保留。
没有真实浏览器视觉或安卓实机验收。

Web镜像：`sha256:ef1454e45a5906363497fd90a23008ad04cfb72f0efca0484d3634440537d43a`。
证据：`test-output/pytest-hidden-versions.xml`、`test-output/hidden-versions/result.json`、
`test-output/media/1793949067976985323/result.json`；DOM命令`node scripts/validate_ui.mjs`。

## WPS蓝箭头新版的黄色残留图形修复 / 2026-11-06

确认当前新版原件包含蓝箭头及5个宽度0的旧填充图形、1条关联单端线；LibreOffice错误显示残留对象。
新增DrawingML定向兼容处理，不按颜色删除。99项Windows测试通过（包含7项新增图形兼容测试）；
真实媒体回归和四模块worker回归通过，证据分别在`test-output/media/1793950244162351701/result.json`、
`test-output/modules/1793950330920777600/result.json`。
真实用户新版通过完整worker/API流程，metadata实际忽略6个对象、还原10张WPS照片、输出1页1920×1080。
已查看实际PNG，蓝箭头、照片、黄色单元格底色保留，旧黄色三角/方块及越界线消失，原件摘要不变。
证据：`test-output/shape-investigation/pytest.xml`、`fixed-result.json`及`fixed-production-page.png`。
已备份SQLite并同步更新本地Web/worker，两者healthy；镜像配置摘要
`sha256:3056886c9d4559f449c2e0e1298d343f1f48d5800c8b95e3c07d6f66f0be8163`。
检测到用户期间已发布原版本并在新草稿上传其他文件，草稿更新脚本在状态检查时退出，未替换其正在编辑的内容。
旧发布文件保持不变，新上传应用修复；未进行真实浏览器视觉或安卓实机验证。

## 公司品牌与蓝色左侧导航 / 2026-11-06

采用用户提供的Logo原图（静态资源SHA-256与下载文件一致），通高固定深蓝左侧栏、品牌区与三个模块导航；
主内容独立位于右侧，整体按钮、表格、分类及卡片改为蓝色。窄屏缩小侧栏但保持左侧布局。
Node DOM导航/编辑保留/隐藏版本控件检查通过，Windows99项回归通过。
本地仅重建Web，原worker容器ID仍为ad335d7e5974；Web及worker健康，页面/Logo/CSS HTTP检查通过。
真实媒体回归通过：`test-output/media/1793950761499110776/result.json`。
证据：`test-output/pytest-blue-sidebar.xml`。Web镜像配置摘要
`sha256:328fb03f191647cdd75ecd0722f3d93532c0b22c95600f282bd2754474cdeec8`。
未做真实浏览器视觉或安卓实机验收；DOM与HTTP检查不能替代视觉验收。

## SOP/SIP标签及四模块加粗 / 2026-11-06

仅页面标签改为SOP（生产作业指导书）、SIP（检验作业指导书），四模块按钮font-weight:700。
分类ID及历史manifest不变。DOM检查、Windows99项测试、线上静态资源检查通过；
真实媒体回归通过，证据`test-output/media/1793951098022850435/result.json`。
本地仅更新Web，无浏览器视觉或安卓实机验收。

## Windows中转实现与真实验证 / 2026-11-06

实现relay/下FastAPI/SQLite服务及shared/reports.py共用模型。中心同步接口兼容扩展摘要、附加PDF、
历史revision及原始设备状态，保留schema1/2、四模块/contain、自动编号、归档和发布不可变语义。
没有新增PDA创建/取消任务、媒体转换或Android播放器。

Windows Python 3.13全量116项测试通过（其中17项中转测试）：文件/manifest校验、Range/206/416/HEAD/If-Range、
鉴权、目标持久化、改派中断旧下载、旧目标激活拦截、初次同步旧实际状态、乱序/重复报告、outbox满载保护、
恶意下载路径、缓存清理和磁盘不足。中心DOM检查通过。证据：`test-output/pytest-relay.xml`。
真实中心媒体回归通过：`test-output/media/1793953918526468916/result.json`。

真实HTTP联调使用隔离Docker中心及Windows本机relay进程，非MockTransport：

- 实际LibreOffice多页Excel生成6页PNG，加1份PDF和1段视频，共8个缓存文件，逐一核对大小/SHA-256。
- 中转manifest摘要与中心一致，schema2内容原样保留，两屏目标独立；真实MP4 Range数据与原字节一致，416正确。
- 停止中心Web后，中转继续提供文件和目标；终端HTTP报告先存outbox。强制终止中转后重启，事件重复提交duplicate。
- 注入.partial恢复样例被安全清理，完整缓存仍可用；中心恢复后同事件补传，保留终端5分钟/2分钟前observed_at，未伪造在线。
- 任务超时保留目标；离线screen-b跨多次改派只取最新revision，旧activation-check返回409，最新准备完整后允许。
- 配置磁盘保留量大于实际可用空间，明确报磁盘不足且旧视频可读；恢复空间配置后重新准备成功。
- 原发布manifest完全不变。终端请求由HTTP脚本模拟，不代表Android播放实机或VPN现场联调。

成功证据：`test-output/relay-http/1793954243044794100/result.json`、`status.json`。
前次联调脚本发现中心重启期间HTTP就绪等待和跨两次sync查询的断言竞态，已修正后完整重跑通过；
中转在目标未准备时拒绝激活是预期行为。测试容器和进程结束后停止，数据证据保留。

Windows SCM实际验证使用官方WinSW.NET4.exe 2.12.0、固定SHA-256，在本机管理员环境执行：
安装临时服务→自动延迟启动配置→启动→Session 0与健康接口→强杀Python子进程→SCM恢复新PID并健康→
Stop/Start→卸载，保留数据；临时服务已清理，没有修改防火墙。
首次验证发现WinSW stop命令异步返回，安装脚本已增加WaitForStatus并修正finally确保卸载；再次完整通过。
证据：`test-output/relay-service-validation/run-1793953309448/result.json`及包装器日志。
没有重启整台Windows主机，不能将Session 0测试声称为开机无人登录现场验收。

本地已部署常驻服务`AP-ESOP-Relay`，SCM状态Running/Auto/LocalSystem，状态页http://127.0.0.1:18081/。
配置`test-output/local-relay/relay.env`仅SYSTEM/管理员可读，数据`test-output/local-relay/data`，
服务目录`test-output/local-relay/service`；三把密钥不在前端、日志或本记录。
中心只更新Web，同步中转成功，2台登记屏幕，outbox pending/blocked均0；现有6份发布manifest摘要不变。
证据：`test-output/LOCAL_RELAY_INSTANCE.json`、`test-output/local-relay/verification.json`。
现场VPN、固定IP/防火墙、整机重启和安卓实际播放仍需验收。部署步骤见relay/README.md。

## 编辑器底部操作分组 / 2026-11-07

“预览当前模块”放底部左侧，“保存排序与秒数”和“发布资料”独立分组并右对齐，窄屏允许换行。
保存范围、预览门槛和发布逻辑不变。DOM冒烟、线上HTML/CSS检查及116项测试通过，
真实媒体回归通过：`test-output/media/1794032546576431440/result.json`；
单测证据：`test-output/pytest-editor-actions.xml`。只更新Web，不重启worker或中转，无浏览器视觉验收。

## 最新资料表格选择 / 2026-11-07

页面隐藏版本号和版本数量，资料库仅进入最新内容，下发每份资料只列最新未归档已发布内容。
资料库与下发均搜索编号/名称/说明、每页10条。下发表格单选，显示发布时间、播放项数及待发布修改提示。
选中项在发布更新/归档后清空，搜索翻页保留有效选择；发送前重新查询发布资料，过期选择不创建任务。
底层版本ID、manifest、历史派发、已有屏幕安排和中转同步保持不变，不自动改派已运行屏幕。
DOM测试以23份资料、多次发布及草稿覆盖最新筛选、搜索、分页、选择保留和过期发送拦截，全部通过。
116项回归通过：`test-output/pytest-catalog-table.xml`；真实媒体回归通过：
`test-output/media/1794033924375065149/result.json`。本地仅更新Web，线上表格资源检查通过。
没有真实浏览器视觉或安卓实机验收。

## 中心与中转账号登录 / 2026-11-07

两端网页均移除API Key输入，使用独立账号密码和共享会话实现；设备Bearer密钥及脚本接口保留。
新增账号库web-auth.sqlite3，不改中心/中转业务schema、发布文件、目标revision或事件outbox。
初始密码随机生成且文件限定权限；密码只存scrypt校验，原始session token不落数据库，Cookie为HttpOnly/Strict。
刷新和服务重启保留默认12小时会话；主动退出、过期及密码重置撤销，页面支持改密。

127项测试通过，证据`test-output/pytest-accounts.xml`；新增11项覆盖登录错误/限流持久化、Cookie属性、
CSRF上传预检、跨源拒绝、刷新/数据库重启、过期、改密撤销、初始文件移除和中心/中转/设备角色隔离。
两端共享登录表单DOM测试通过：`node scripts/validate_accounts_ui.mjs`；中心原有DOM检查通过。
真实媒体回归通过：`test-output/media/1794034949102128827/result.json`。

真实Docker中心→Windows中转HTTP验证通过：`test-output/relay-http/1794035050766522000/result.json`。
实际账号密码登录、复用Cookie恢复会话，强制重启relay及停止/恢复中心后会话仍有效；
两端实际改密撤销旧Cookie并重新建立当前会话，退出后401。
同时验证6页PNG+PDF+视频、Range、断线报告、最新目标及磁盘保护，不将模拟终端请求宣称Android实机。

本地中心Web和Windows服务已更新，真实登录/刷新/退出检查通过，证据`test-output/local-accounts-verification.json`。
两边初始账号admin，随机初始密码分别位于中心数据目录initial-login.txt与中转数据目录initial-login.txt，
不在日志、前端、URL或本记录输出。旧发布清单和设备通信不受账号改动影响。
未进行真实浏览器视觉或群晖HTTPS反向代理现场验收；生产须配置可信代理来源和Secure Cookie。

## 屏幕页操作优先 / 2026-11-07

将devices操作区移到overview统计区之前，资料选择、屏幕登记和下发在上，登记/在线/离线/未知/待同步统计及中转状态在下。
只改变HTML区块顺序，不改账号、派发或同步逻辑。中心与账号DOM检查、127项回归和线上区块顺序检查通过。
证据`test-output/pytest-screens-order.xml`；真实媒体回归`test-output/media/1794036183672281839/result.json`。
本地仅更新Web，worker和Windows中转未重启；未做真实浏览器视觉验收。

## 2026-11-07 Android APK、AOSP双模拟器及真实HTTP三端联调

本次是**真实Android App运行于两台Android10/API29 AOSP x86_64模拟器**，通过本机HTTP故障代理访问
Windows Python中转及独立Docker中心；无Google Play服务。不是联发科一体机实机验证，也没有现场VPN。
原用户中心资料、密码和派发未用于联调；测试中心/中转使用隔离目录与临时密钥。

- Gradle `assembleDebug assembleDebugAndroidTest testDebugUnitTest lintDebug`成功，12项JVM测试通过，Lint 0错误/13警告。
  警告主要为固定target、局域网HTTP和中文字符串国际化等；没有跳过TLS证书验证。
- APK：`android/dist/AP-ESOP-Player-debug.apk`，com.ap.esop，1.0.0/code1，minSDK23/target34，
  SHA256 `874dd27e3618e976f6239071a2612467b8023c2dd384a7f61e9947420819aaab`。
  apksigner验证v1/v2签名成功。Debug签名仅供侧载验证，后续更新需保留同一签名。
- Python全量132项通过，`test-output/pytest-android-final.xml`。首次默认发现因旧测试目录ACL失败，
  改用显式tests目录及独立basetemp后通过；未删除旧证据目录。
- 中心与账号DOM冒烟均通过；不作为浏览器视觉验收。
- 真实LibreOffice/Poppler/ffprobe媒体回归通过：`test-output/media/1794040268504350157/result.json`。

端到端结果：`test-output/android-http/1794040447559641800/result.json`；
`instrumentation.txt`两项通过（Room事务/实际控件混合播放），`second-screen-instrumentation.txt`一项通过。
`center-status.json`保留2台在线、待同步0、各屏实际版本/项的真实回报。
图片/视频截图为`new-target-blocked.png`、`final-playback.png`；`controls-playback.png`是启动瞬间桌面，
不作为App画面证据。后续脚本已将截图移至UI稳定检查之后。

|验收项|本次结果与边界|
|---|---|
|Excel分页图片与视频循环|真实转换基础样例xlsx六页，SOP七项含MP4；实际Media3首帧、自动翻页及循环通过；不是客户工艺版式验收|
|上一项/下一项/暂停/继续/重播/缩放|原生View仪器测试通过；缩放暂停、退出不自动恢复，视频暂停位置与续播通过|
|暂停仍心跳|暂停保持12秒，真实中心收到paused；正常报告序号持续增长|
|离线继续/关闭重开|停止中转、force-stop/reopen后恢复已验证本地内容和暂停状态，并显示最新安排待核对|
|离线多次改派|App停止期间v2超时后改v3，重开仅激活v3，新安排恢复正常循环|
|不完整新版本|故障代理对新文件返回503，实际版本保持v3；解除后校验、核对revision、首图成功才激活v4|
|视频新版本生效|视频单项v5首次Media3首帧后中心收到actual版本|
|中转重启与报告补传|实际中转进程停止/重启后恢复同步、App本地队列补传；没有伪造终端联系时间|
|多屏中心状态|两个独立AOSP实例使用不同device_id，中心online=2、实际版本分别正确|
|断点/错误/磁盘不足|JVM HTTP测试覆盖206续传、200重下、完整partial恢复、SHA错误及预留空间拒绝；不是模拟器灌满磁盘或硬断电测试|
|实机断电/长期运行|未完成；需联发科一体机核对硬解、Android6、触屏、固件开机、自启权限、温度与内存|

本轮发现并修复SQLite busy_timeout PRAGMA需读取返回游标、首次系统全屏提示影响自动化、
频繁重复更新文本影响UI稳定检查等问题，修复后重跑通过。测试与构建日志保存在`test-output/android-tools/`。
本机中心Web镜像及Windows中转同步更新；原6份发布manifest摘要核对不变，不更改账号密码/目标安排。

## 2026-11-07 Android1.0.1首次连接空清单修复

用户手机照片反馈：首次连接报`Expected a com.google.gson.JsonObject but was JsonNull; at path $.manifest`。
共享契约允许无安排或缓存未就绪时manifest:null；Gson的JsonObject适配器不因Kotlin可空声明自动接受JSON null。
修复device/sync读取：显式取出manifest，JSON null归一为Kotlin null，非空仍要求对象，再解析其余字段。
没有修改中心/中转协议，也不要求先下发资料才能登记/保存配置。

版本1.0.1/code2，新增两项JVM用例验证无安排及等待缓存→清单就绪，现14项通过；Lint 0错误/13警告。
后台132项通过：`test-output/pytest-android-null-manifest.xml`。构建日志`test-output/android-tools/build-null-manifest.log`。
双AOSP模拟器→真实Windows中转→Docker中心回归证据：`test-output/android-http/1794041858122720500/`。
新增`unassigned-connection.txt`验证无下发的assignment:null/manifest:null实际响应可连接；原播放、离线、改派测试一并重跑。
未进行用户手机远程安装或联发科实机测试；照片仅作为问题证据。

交付`android/dist/AP-ESOP-Player-1.0.1-debug.apk`，SHA256
`420ebd522d330387c64dc1290453bb029569b6fc95c970e54d5e172436178770`。
签名验证通过且与1.0.0相同，可覆盖安装保留本地配置/报告序号，不要求卸载。

## 2026-11-07 Android1.0.2右侧控制栏

仅调整原生界面：左侧媒体区使用完整窗口高度，右侧滚动栏集中Logo、资料名称、连接/下载/错误、
当前项、四分类、上一项/下一项/暂停续播/重播/退出放大及管理员按钮。矮屏可独立滚动侧栏。
图片/视频仍contain等比完整显示；右栏占据固定空间，不承诺16:9内容在剩余区域无留白。
没有更改管理员验证、配置加密、返回键、播放计时、接口、缓存或下发逻辑；此前暂停的验证弹窗调整未包含。

1.0.2/code3：`android/dist/AP-ESOP-Player-1.0.2-debug.apk`，SHA256
`0e605e9dc0e40e21f2cc8ec1251158ab8bc85db7f0f6413874c0c1f65bc7dc3e`。
签名与1.0.1一致，覆盖安装保留配置。Gradle构建、14项JVM、Lint 0错误/13警告通过；
Python132项通过（`test-output/pytest-android-right-rail.xml`）。
新增仪器断言媒体占满根布局高度、侧栏紧邻媒体、所有状态及播放控件位于右侧；实际按钮/缩放/视频测试通过。
两台AOSP模拟器→Windows中转→Docker中心的完整回归证据：
`test-output/android-http/1794047953309678600/`，原生控件仪器测试2项及无安排连接、第二屏测试各1项通过。
真实截图`controls-playback.png`、`final-playback.png`，已查看1920×1080画面，无上下工具栏或控件遮挡。
首次联调因启动瞬间uiautomator没有生成XML失败，添加有限重试后重新执行；不改产品代码规避验证。
没有向用户手机自动安装，没有做联发科实机视觉/触摸验收，也未更新中心/中转或账号密码。
1440×720/density320手机比例模拟器检查通过，右栏可滚动且管理员可达，截图
`test-output/android-tools/right-rail-phone-top.png`、`right-rail-phone-bottom.png`。
检查中发现窄按钮文字截断，最终仅缩小按钮内边距/字号并限制两行，重新构建/JVM/Lint及手机比例检查通过，已查看截图。
完整HTTP联调结果中的SHA为这次字号微调前的包；最终交付包SHA以上述值为准。

## 2026-11-07 名称列前置

工艺资料库：名称/说明、资料编号、状态、操作；屏幕下发：选择、资料名称/说明、资料编号、发布时间、播放项数、状态。
HTML表头与JavaScript行渲染同步交换，不改数据、搜索、选择或下发逻辑。
中心/账号DOM检查通过，Python132项通过（`test-output/pytest-name-columns.xml`）。
镜像构建并更新本机Web，HTTP核对两张表的名称均在编号前。首次重启瞬间核对遇502，等待健康后重试。
没有改动worker、中转、安卓、账号及任务；DOM/HTTP检查不作为浏览器视觉验收。

## 2026-11-09 离线下发等待状态核对

实际中心记录：09:28:25向离线screen-a创建120秒任务，截止09:30:25；09:30:39又成功为screen-01创建新任务。
说明前一任务已按期释放，用户遇到的是每次新任务重新进入120秒等待窗口，管理页缺少明确执行中提示。
未取消/抢占运行任务，也未把屏幕离线作为绕过全车间任务互斥的理由。

屏幕下发区增加当前执行任务的屏幕数、截止本地时间、约剩余秒数和超时释放说明；随原10秒轮询刷新。
409错误提供具体截止UTC时间；只有持续有效的待同步安排时明确提示不阻塞新任务。
新增回归在无终端报告时，截止前1毫秒409、截止时可重新下发，旧任务终态和最新目标保留。
Python133项通过：`test-output/pytest-offline-task-hint.xml`；中心/账号DOM检查通过，覆盖运行/超时等待提示及选择按钮可用。
本机仅更新中心Web；不改中转、安卓、凭据、发布清单和目标安排。此轮没有新增实机或媒体转换验证。

## 2026-11-09 中心系统v1.0.0版本标识

左侧导航底部新增系统版本，唯一来源`center/version.py`，首页替换版本占位符，FastAPI版本元信息共用常量。
从v1.0.0建立中心发布基线，后续程序/界面交付递增，规则已加入AGENTS与DEVELOPMENT；不影响资料/Android版本。
中心DOM检查通过，Python133项通过（`test-output/pytest-system-version.xml`）。镜像构建并更新本机Web。
HTTP确认首页含v1.0.0且没有未替换占位符；不改数据库、资料、派发、账号、中转或安卓。
此轮未新增真实浏览器视觉或实机验收。

## 2026-11-09 中心v1.0.1操作按钮顺序

编辑器底部统一从左至右显示：保存排序与秒数 → 预览当前模块 → 发布资料，蓝色箭头标注顺序。
窄屏整组横向滚动，避免换行后箭头顺序混乱；原按钮ID、保存/预览/发布处理逻辑保持不变。
系统版本递增v1.0.1，中心/账号DOM检查通过；Python133项通过（`test-output/pytest-workflow-buttons.xml`）。
构建并更新本机中心Web，HTTP核对按钮顺序、两处箭头及左下角v1.0.1；未新增浏览器视觉验收。

## 2026-11-09 中转历史missing误列为下载

用户截图5个missing文件逐一与本机中转SQLite核对：全部为不再被目标、当前/上一有效、实际/待更新、outbox引用的历史缓存；
5条记录error均为空，15个缓存ready，受保护版本缺失文件数0。是过期历史缓存正常释放后，状态页仍将所有非ready记录列成下载。

local/status的downloads改为仅列受保护版本未就绪及正在下载的文件，历史身份映射与missing记录保持不变。
页面missing翻译“待下载”，空队列说明当前所需缓存已就绪、已释放历史缓存不列入待下载。没有更改下载/清理/激活语义。
新增回归验证历史释放隐藏、当前缺失保留、在途下载保留；Python134项通过（`test-output/pytest-relay-cache-status.xml`）。
真实Docker中心→Windows中转HTTP验证通过：`test-output/relay-http/1794190154342871700/result.json`，
含真实Excel/PDF/视频、Range、断线队列、重启、最新目标和账号会话。终端请求模拟，不作为安卓实机验收。
中转后台服务重启后健康、中心在线，实际downloads为空；SQLite仍保留missing5/ready15，
证据`test-output/relay-cache-status-local.json`。原6份发布清单核对不变，无账号/目标改动。
本轮仅改中转及共享状态展示说明，中心系统版本/安卓APK未更新。

## 2026-11-09 中心v1.0.2登记表单与状态标签

固定device_id、屏幕名称、登记按钮整行移到overview内，位于五个统计块上方；接收屏幕区域只保留下发操作和设备表。
设备连接状态加粗并使用带边框色块：在线绿色、离线红色、未知灰色；待同步独立黄色，保留文字避免仅靠颜色辨识。
未修改状态计算、离线阈值、心跳或登记/下发接口。系统版本递增v1.0.2。
中心/账号DOM检查通过，Python134项通过（`test-output/pytest-screen-badges.xml`）。
构建更新本机Web，HTTP核对表单位于统计块上方、标签样式与版本生效；未新增真实浏览器视觉验收。

## 2026-11-09 Android1.0.3正式Release、信息卡与通信灯

右侧所有自定义按钮采用圆角/按压反馈，分类和播放操作移到信息卡下方。卡片显示登记屏幕名、device_id、
当前资料/图片视频项数、资料发布与目标下发本地时间、最近通信。目标下发对应最新assignment.assigned_at，
不冒充旧资料的发布日期。device_name/offline_seconds为中转兼容新增字段，旧中转回退编号/40秒。
通信灯仅表示App↔中转：启动灰、仅同步待回报确认黄、同步及报告近期均成功绿、失败或超时红；离线继续本地播放。
135项Python、18项JVM和Release Lint 0错误/13警告通过。真实中心→中转回归：
`test-output/relay-http/1794192400165435000/result.json`。本机中转已更新，原6份发布清单摘要不变，凭据及目标不改。

正式包`android/dist/AP-ESOP-Player-1.0.3-release.apk`，com.ap.esop，code4，min23/target34，非debuggable，5109276字节。
SHA256 `94885595f5413d471a6370fdae014f2346aaf60990cddd674dea2c57a5b707c9`。
专用Release证书SHA256 `59f6df3cf95e351abb5246158b6c0cc7773513b8ac9903d45dfeaccfa3d346a4`，
key/password在受限android/signing目录，Git与Docker均排除。用户要求正式包，未交付并存预览版。
旧debug签名已丢失，用户确认没有/不确定备份；新正式签名不能覆盖旧调试版。安装需卸载旧版时必须新登记device_id，
不得重置旧编号报告序号。未来正式更新沿用新签名。用户当前emulator-5554只读核对APK证书，没有卸载、清数据或安装。

Release本体使用同正式签名仪器测试包进行完整联调，通过记录：
`test-output/android-http/1794193713199350000/result.json`（SHA与交付包一致）。
测试AVD仅ESOP_API29_A/B，独立端口5560/5562；安装前校验AVD名，禁止清理用户其他模拟器。
仪器测试覆盖屏幕名称、日期、绿灯以及原按钮/缩放/循环，真实中转停机后红灯且恢复本地内容；
多次改派、新下载503保留旧内容、视频首帧及双屏在线通过。对应实际截图controls-playback.png与offline-restored.png。
JDK25不兼容既有Gradle8.7，另装校验官方SHA的JDK21后构建。Release签名/元信息证据：
`test-output/android-tools/release-signature.json`与release-verification.json。
仍未完成用户真实一体机、Android6、断电/温度/长期运行验收；正式构建不代表上述实机验收已完成。
Release另在专用AVD切换1440×720/density320检查信息区和侧栏独立滚动、管理员可达，通过；
`test-output/android-tools/compact-player-release.log`及right-rail-phone-top/bottom.png，检查后恢复分辨率。

## 2026-11-09 清理不用的测试数据

按用户明确授权删除项目内专用AVD、系统镜像解压副本/压缩包、工具安装包、旧测试数据库及根目录pytest缓存。
删除前核对Docker绑定目录与本机服务配置，逐项验证目标绝对路径在工作区且不与保留路径相交；仅清理项目内专用模拟器，用户当前模拟器不动。
清理142项目标，0失败。test-output逻辑文件大小22.93GiB降至约0.574GiB；实际磁盘释放10.55GiB，
差异主要来自模拟器稀疏虚拟磁盘。结果`test-output/cleanup-report.json`。
保留当前中心完整目录、中转完整目录、正式APK/签名、JDK21、一套媒体夹具和服务托管工具。
旧result.json及正式版关键截图/仪器日志存入`test-output/validation-records/`，保留原相对层级；
本文此前的原测试数据路径是历史记录，多数旧完整目录已删除，不能再假定图片/数据库仍在原位置。
清理后Web/worker健康、中转在线，原6份发布清单摘要不变、正式APK哈希不变，签名及JDK仍存在。
此轮未重新生成测试缓存。后续安卓设备验证只用真实设备，规则已写入AGENTS与开发文档。

## 2026-11-09 中心v1.1.0两版本保留

每份资料最多两个可用已发布版本：新发布+一个旧版；旧版优先选仍被目标/实际/待更新/执行任务引用的版本，
不一定是序号紧邻的上一版。若同时存在两种受保护旧版则409/version_retention_blocked，事务拒绝新发布并列出屏幕，
不改现有目标、不强删离线内容。草稿另外保留；过期版仅留下不可变items/manifest、文件描述和任务历史。
原件/PDF/分页/视频的独占字节通过retiring→expired清理，共用及草稿引用保护，worker约30秒收敛/重试。
schema5只新增独立过期记录表；旧文件接口410，过期版本禁止克隆/下发，迟到实际回报及relay/sync仍正常。

144项回归通过（test-output/pytest-retention-release.xml），新增9项保留策略专项；中心/账号DOM通过。
真实中心→中转HTTP与真实Excel/PDF清理通过，记录`test-output/validation-records/relay-http/1794197296463054500/result.json`。
实际LibreOffice/ffprobe回归`test-output/media/1794197540954696391/result.json`通过。未启动模拟器，未做安卓实机验证。
真实HTTP验证了：离线旧Excel目标优先保留；实际确认新目标后，旧原件/PDF/页图物理消失，共用媒体仍可下载；
旧目标与旧实际不同版本时新发布被阻止，而不是删除现场文件。存储失败重试、并发发布及迟到回报另有单测。

运行数据库只读副本预检查13份资料，计划淘汰3版，无占用冲突，独占文件约23.90MiB。
停机备份后同时更新Web/worker；备份位置记录test-output/retention-deployment.json，位于当前中心受保护目录，
单次升级备份不参与线上版本配额，也不会每次发布重复创建备份。
上线核对schema5、过期3版/3个独占资产、可用发布数最大2、cleanup_pending=0；所有14份发布清单内容不变，目标安排不变，
原6份基线清单核对不变，中转在线。GET /retention结果见test-output/retention-live-plan.json。
旧实际状态仅以最后收到报告为依据，未来迟到或本地回退到已过期版本仍可能发生；已缓存可继续用，中心不承诺恢复过期字节，
此时应补最新目标。历史记录不是完整备份，不能把配额理解为只剩两条数据库历史。
本轮临时测试数据按用户要求清理，仅保留结果记录与一套媒体夹具；当前运行数据、正式APK/签名和升级备份保留。

## 2026-11-09 Android1.0.4侧栏精简

按用户截图隐藏当前资料/图片视频数量、正常连接重复说明、当前项/页码/循环说明，GONE不占布局空间；
顶部资料名、屏幕/日期、通信灯及操作按钮保留。下载progress或error存在时状态提示按需显示，不隐藏故障。
仅改显示，播放计时/按钮语义/报告内容不变；仪器测试源更新为隐藏字段及跳过不可见字段位置检查。
Release构建、18项JVM、Lint 0错误/13警告通过；Python144项通过（test-output/pytest-player-clean-info.xml）。
无已连接真实设备，遵循用户要求未启动/操作模拟器，未宣称实机显示已验收。
交付android/dist/AP-ESOP-Player-1.0.4-release.apk，code5，非debuggable，5109413字节。
SHA256 `4d747ff23808c03a4679364fed3864902d5ae258e918d62531fb2e44ae885431`，
apksigner确认与1.0.3正式版同证书，可覆盖保留配置；证据test-output/android-tools/release-1.0.4-verification.json。
未更新中心/中转/协议或凭据，临时pytest数据在保留XML报告后清理。

## 现场验收建议

1. 在群晖按README部署，确认两个health状态、单Web worker、目录权限和日志轮转。
2. 提供至少一份xlsx与一份xls真实工艺文件，逐页对照Excel打印预览：分页、字体、嵌图、边界、清晰度。
3. 用真实H.264/AAC及无声视频验证≤180秒接受、>180秒拒绝，目标安卓硬件再验证解码。
4. 在真实一体机断网改派多次、重启中心/中转/屏幕，确认只应用最新revision、校验后原子切换，旧版本离线循环。
5. 执行停机备份和空目录恢复，核对资产摘要、manifest、目标安排、历史任务和事件序号。


## Center v1.1.1 — 2026-11-09

接收屏幕和全选框改为 18px 圆形，保持原生 checkbox 多选与键盘焦点；与资料单选框大小一致。已部署本地中心，HTTP 核对 CSS 与版本通过。默认 pytest 临时目录权限失败，改为仓库专用目录执行，结果见 test-output/pytest-circle-ui.log。未进行浏览器截图验收；未改变下发协议或任务数据。


## Center v1.1.2 — 2026-11-09

下发历史按创建时间倒序仅展示最近10条，底层历史及运行任务判定保持完整，未删除任务或目标。144项回归通过，见 test-output/pytest-history-ten.log。

## Center v1.1.3 — 2026-11-10

登记区增加默认折叠的屏幕删除管理，确认后持久注销，取消目标但保留任务与事件；编号不复用。运行任务409，重复删除幂等，已删设备从统计和中转快照移除，迟到报告stale不更新状态，迟到ack忽略。独立deleted_devices表启动时增量创建，不改既有清单或revision。

145项回归通过（test-output/pytest-delete-device.log），覆盖鉴权、任务互斥、超时删除、历史保留、重启、统计、禁止编号复用/改派及迟到报告。真实媒体转换通过：test-output/media/1794283431828299738/result.json。真实HTTP联调记录在test-output/validate-delete-relay-final.log；终端请求为模拟HTTP，不等同实机测试。首次隔离脚本把注销检查放在中心断网阶段，随后移到恢复阶段并补齐必填revision后重跑。
## Center v1.1.4 — 2026-11-10

按截图隐藏资料库导语、模块显示/转换说明、文件数量摘要、发布常驻说明、版本保留说明、下发操作说明、空闲任务提示和在线含义说明。保留未保存改动提醒、运行任务进度、实际连接状态、转换警告与错误。只调整展示，不改变业务规则。回归见test-output/pytest-clean-hints.log。

