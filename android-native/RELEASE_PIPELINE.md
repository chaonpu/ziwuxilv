# Android release pipeline

Android 业务源码唯一来源：

`anwpustudent-lab/Test/main`

正式发布链路：

`Test/main`
→ 单元测试 / Kotlin 编译 / 行情 smoke
→ 导出精确提交的只读源码交接包
→ `chaonpu/ziwuxilv-android` 使用正式证书签名
→ 校验包名、versionCode、versionName、证书 SHA-256 和 APK SHA-256
→ 本仓库发布 APK
→ 最后更新 `android-native/version.json`

`chaonpu/ziwuxilv-android` 仅作为签名构建保险库，不再维护 Android 业务源码。

公开版本目录中的 `release-info.json` 记录该 APK 对应的 Test 源提交、APK SHA-256 与正式签名证书摘要，用于追溯正式发布来源。
