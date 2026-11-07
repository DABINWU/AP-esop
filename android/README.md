# Android 一体机（待实现）

V1 只交付中心。本目录保留后续客户端位置，没有 APK，也未作安卓实机验证。
按 [共享接口契约](../docs/API_CONTRACT_V1.md) 实现：固定 device_id、只访问中转、
已校验媒体离线循环、完整下载后原子切换、持久化 revision/report_seq、按项回报播放和暂停。
不得将心跳等同于物理亮屏。
