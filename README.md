# 子午汐律原生版更新源

本仓库用于发布独立原生 Android 应用的更新清单与签名 APK。应用包名为 `com.ziwuxilv.nativeapp`；旧 WebView 版不在此仓库中。

发布新版时，保持包名与 Release 签名证书不变，并递增 `versionCode`。先上传新版 APK、核对公开下载地址，再更新 `android-native/version.json`。
