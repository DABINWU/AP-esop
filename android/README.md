# Android 触控一体机播放器

正式版1.0.5在右侧播放操作区新增“开启声音/静音”。默认静音，首次创建播放器前即设置音量0，避免视频起播瞬间出声。点击开启后，翻页、重播、模块/资料切换继续沿用当前会话声音选择；不改变暂停状态、播放位置或系统媒体音量。App重新创建播放页面/进程启动恢复默认静音，不持久保存开启声音。开启后实际音量仍受设备系统媒体音量与音频硬件影响。

Kotlin原生View + Room + 本地文件 + Media3。只访问管理员配置的车间中转，不访问中心、不安装VPN，
不依赖Google Play服务、WebView、Office或PDF渲染。播放中心产出的分页PNG/JPEG与H.264/AAC MP4。
四模块SOP/SIP/点检视频/自定义沿用manifest中的ID、全局item ID与顺序，模块各自循环；旧schema1作为自定义模块。

## 兼容范围与构建

最低Android 6.0 / API23，compile/target API34。用户暂不清楚一体机系统，先按此下限；
MediaTek芯片不能说明Android系统版本和硬解能力，必须现场核对系统/API、内存、屏幕密度和H.264解码。
这是侧载验证版，不宣称满足Google Play最新target要求；未使用厂商系统权限或root。

固定Gradle8.7、AGP8.5.2、Kotlin1.9.24、Room2.6.1、Media3 1.4.1，选择可复现且兼容API23的工具组合，
不是“最新版依赖”。官方资料：[AGP兼容表](https://developer.android.com/build/releases/agp-8-5-0-release-notes)、
[Room](https://developer.android.com/jetpack/androidx/releases/room)、[Media3](https://developer.android.com/jetpack/androidx/releases/media3)。
需要JDK17或21、Android SDK Platform34/BuildTools34、可访问Google Maven和Maven Central的构建环境。
工程含标准gradlew/gradlew.bat和Wrapper Jar，发行包SHA-256固定；不要提交local.properties、签名文件或密钥。

```powershell
# 在android目录，JAVA_HOME指向本机JDK17/21
$env:JAVA_HOME='C:\path\to\jdk-21'
.\gradlew.bat assembleDebug testDebugUnitTest lintDebug
# 构建模拟器/实机仪器测试APK
.\gradlew.bat assembleDebugAndroidTest
```

Android Studio打开本目录即可；SDK路径写本机local.properties的sdk.dir，或配置ANDROID_HOME。
Linux/macOS可chmod +x gradlew后执行./gradlew。Debug产物：app/build/outputs/apk/debug/app-debug.apk。
本机交付副本：[AP-ESOP-Player-debug.apk](dist/AP-ESOP-Player-debug.apk)，生成文件不提交Git，重新构建可恢复。
1.0.1/code2修复首次连接无下发资料时manifest:null解析失败；旧版可直接覆盖安装，保持原签名，不必卸载或清除数据。
1.0.2/code3将Logo、资料名称、连接/下载状态、分类、播放操作及管理员入口统一放到右侧控制栏，左侧媒体使用完整高度。
1.0.3/code4：圆角按钮下移，上方信息卡显示屏幕名称/编号、当前资料及图片视频项数、资料发布/目标下发本地时间。
屏幕名称来自中转device_name，本地保留供离线显示；旧中转回退device_id。
通信灯：灰色尚未核对、黄色报告待确认、绿色近期同步和报告均成功、红色失败或超时。
阈值沿用中转offline_seconds（默认40秒）；仅表示App与中转通信，不证明中心或物理背光正常。
1.0.4/code5：隐藏重复的当前资料/图片视频数量、正常连接说明、当前项/循环说明，保留顶部资料名、通信灯、屏幕与日期。
下载进度和错误提示按需显示；仅隐藏界面，不改变播放/暂停、报告内容或同步逻辑。

## 正式Release安装与签名（从1.0.3开始）

当前交付`dist/AP-ESOP-Player-1.0.4-release.apk`，com.ap.esop，Android6.0+，非debuggable；可覆盖1.0.3正式版并保留配置。
本机旧调试签名已丢失且无备份，新正式签名无法覆盖11月7日旧调试版；签名冲突时需先卸载旧版再安装。
卸载会删除App配置、缓存和报告序号。请先记下中转地址/终端密钥，在中心登记新的唯一device_id后配置新版，
不能继续冒用有历史序号的旧编号。旧屏幕历史记录保留；项目不提供匿名重置序号接口。

正式签名保存在`android/signing/esop-release.jks`和`release.properties`，已限制本机权限，并从Git/Docker构建上下文排除。
管理员必须安全备份整个signing目录，不能提交Git、分享给普通用户或随程序清理；丢失后同样无法覆盖升级。
后续正式版沿用该签名，并增加versionCode，即可覆盖安装保留配置；不再依赖自动生成的debug.keystore。
构建：使用JDK17/21运行`gradlew.bat assembleRelease lintRelease`；缺少签名配置时Release任务明确拒绝。
密码仅在本机受限配置文件中，不写入代码、APK业务配置或构建日志。
侧栏宽度随屏幕调整（176–280dp）；高度不足时侧栏独立滚动，媒体区不随之滚动。仍按contain完整等比显示。
Debug以本机构建密钥签名，仅用于验证；后续更新必须保持同一签名，生产签名密钥应由管理员专门保管，不提交仓库。
卸载/清除App数据会丢失设备序号，不能用原device_id静默重新登记。

## 安装与首次设置

1. 在中心为每台屏幕登记唯一device_id，例如screen-01、screen-02；不要把同一编号配给两台机器。
2. 在一体机允许从受信任来源安装APK，再安装Debug APK；或调试授权后adb install -r app-debug.apk。
3. 首次打开填写中转LAN地址，如http://192.168.10.20:18081，device_id及中转DEVICE_API_KEY。
   不填中心地址、管理员网页登录密码或127.0.0.1（后者指安卓自身）。模拟器访问开发机可用10.0.2.2。
4. 设置本机“管理员设置密码”（至少6位），与中心/中转网页账号密码分别管理。
5. 连接测试会验证中转鉴权及device_id登记。编号若已有历史报告而本地seq为0，会拒绝复用，请另登记新编号。
6. 保存后自动查询安排；中心发布资料→选择屏幕下发→中转准备完整→App下载并验证→首图/首帧成功后生效。

本机开发中转若仅监听127.0.0.1，真实屏幕无法访问。现场按[中转部署说明](../relay/README.md)将
RELAY_HOST配置为车间LAN地址或0.0.0.0，固定IP/DHCP保留，并只放行配置的单个TCP端口；不要关闭整体防火墙。

管理员入口独立放在右侧栏底部，也可长按Logo/标题；配置后须验证设置密码，连续5次失败锁定1分钟。
密钥输入始终遮掩，打开设置时不回显；留空保留原密钥。配置用Android Keystore AES-GCM加密保存在App私有目录。
设置支持中转连接测试、设备型号/系统信息、立即同步、系统设置和申请屏幕固定模式。
正常播放按钮没有密钥或配置入口。已配置device_id不可改写；更换设备/卸载后应使用新编号。

## 播放与触控语义

- 右侧四分类按钮仅循环当前模块，空模块禁用，默认第一个有内容模块。界面显示资料名称、当前项/总项数与暂停状态。
- 上一项/下一项循环翻页；下一项是跳过，不删除文件。视频自然结束后下一项，最后一项回第一项。
- 暂停图片保存剩余时间；暂停视频停在当前位置。继续使用剩余时间/当前位置。
- 暂停时手动翻页或重播，仍保持暂停；新图片计时从完整停留时间起，视频停在起点。
- 播放中手动切换，新项正常开始。暂停不会因计时、网络恢复或分类切换而自行解除。
- 图片初始contain完整显示，不拉伸/裁切。双指缩放和拖动会暂停；双击或“退出放大”还原视图，仍保持暂停。
- 新安排成功接管后，从首个有内容模块第一项开始，重置为正常循环播放（包括同版本新的revision）。
- 相同安排不重复下载或重置当前项。页面关闭/重开恢复项、暂停、图片剩余时间和视频位置；位置每5秒及操作时保存。

## 缓存、状态与离线

Room保存最新目标、当前/上一有效快照、播放状态、递增report_seq和待回报事件；无破坏性fallback迁移。
先恢复并验证完整本地清单，再查询中转。恢复时标“离线/最新安排待核对”，不假装已与中心同步。
单下载协程、独立同步/心跳/报告补传，暂停不停止通信。下载进度、错误及实际版本/项按契约回报。
事件与序号同一事务写入outbox，失败保留原event_id/observed_at，收到accepted/duplicate/stale才移出。
永久拒绝事件隔离并提示，不阻塞其他事件；outbox上限100000，满时明确提示不静默丢弃。

清单SHA及结构校验，文件按ID+SHA命名；.partial可恢复Range/If-Range。206核对起点/终点/总大小，
200重写，416核验或重下。文件大小和SHA-256正确后fsync+同目录rename；HTTPS不忽略证书。
全部根items文件完成后再次查询最新revision并activation-check，首图绘制/视频首帧后才事务提交active并报告生效。
失败或被替换不提交，保留旧内容；先显示再事务保存的极短窗口不保证与远程改派物理原子一致，仍持续轮询纠正。
PDF只留在中心/中转预览，不下载至App；不会在安卓转换Excel、拼接页面或转码视频。

缓存清理保护当前、上一有效、最新目标及未送达报告引用；下载锁串行清理与下载。其余字节可清理，身份快照保留。
磁盘预留64MB，仍不足明确报错、保留旧播放；网络错误不停止已有完整内容。
图片只按当前页采样解码（最多约400万像素），串行解码；不整份文档常驻内存。
ExoPlayer在前台期间复用，离开页面释放播放器/图片/任务；媒体失败有限重试后停留并提示，可手动跳过。
App不在后台持续播放/轮询；退到后台会保存暂停报告，回到前台恢复。前台播放保持亮屏，不保证物理背光或供电正常。

## HTTP、开机与设备管理边界

V1允许管理员配置的局域网HTTP，network_security_config允许cleartext，所有URL限制在该中转主机相对API路径，
不跟随重定向，不访问manifest任意URL。HTTP仍会明文传输设备Key，只能部署于受控车间LAN；生产建议HTTPS可信证书。
API23忽略network_security_config，但使用系统默认TLS验证；没有trust-all或跳过证书的代码。

默认全屏横屏，系统手势仍可能显示导航；不承诺普通APK能强制锁住设备。
可在管理员设置勾选开机启动适配：Android6–9尝试BOOT_COMPLETED启动Activity；Android10+因系统后台启动限制，
提供启动提醒通知，需允许通知。无人值守全自动启动需厂商固件自启动白名单/指定启动App或受管设备策略。
参考[Android后台启动限制](https://developer.android.com/guide/components/activities/background-starts)。
“申请屏幕固定模式”调用系统screen pinning，普通设备可能要求用户确认；Device Owner白名单须由设备管理员外部配置。
不要求root，不假设静默安装、Device Owner、系统权限、忽略电池优化或后台永久保活已获得。

## 验证

按用户2026-11-09要求，今后设备验证使用真实安卓设备，不再创建或启动测试模拟器。
下述AOSP脚本仅作为历史验证方法保留，项目专用AVD和安装镜像已清理；不得自动重建。

JVM测试覆盖计时暂停、重播/新安排、清单规范JSON、Range/200回退、校验失败、磁盘不足和完整断点恢复。
Android仪器测试覆盖Room事务/序号以及实际View按钮、缩放、Media3首帧与混合循环；需真实中转测试参数。
`python -m scripts.validate_android`使用独立AOSP模拟器、故障代理、真实Windows中转及隔离Docker中心。
测试只清理指定模拟器App数据，不操作真实一体机；实际证据及未完成项见../docs/VALIDATION.md。
实机待验收：MediaTek解码/首帧、Android6最低版本、触屏手势、设备掉电、固件自启动、温度/内存与长时间稳定性。
