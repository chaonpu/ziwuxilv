# 子午汐律 Android 私有签名 → 公开发布交接协议

**实施状态：发布校验和候选清单已实现并通过单独测试；自动跨公开仓库传输仍未启用。**

## 权限与源码边界

- `chaonpu/Runner`：仅派发 `Test`、`Stock`、`ziwuxilv-android` 私有工作流，不派发本公开仓库。
- `CHAONPU_ACTIONS_READ_TOKEN`：虽然具备私有 Actions 写权限，但 **只覆盖私有仓库**，不新增 `chaonpu/ziwuxilv` 权限。
- `chaonpu/ziwuxilv-android`：验证 Test 私有 Run / SHA、完成生产证书签名，保存私有签名 APK。**绝不将 Test 源码压缩包、keystore 或签名 Secrets 带到公开仓库。**
- `chaonpu/ziwuxilv`：只有得到被审核的公开发布请求时，才使用本仓库自身的 `GITHUB_TOKEN` 发布签名 APK 与更新 `android-native/version.json`。

## 私有签名生成候选清单（已实现，供下一次成功签名使用）

`windows-signing-verified-handoff.yml` 在签名证书、APK 包名、versionCode、versionName 和 SHA-256 都核验成功后，新增：

`private-signed/publish-candidate-metadata.json`

其中记录：`testRunId`、`signingRunId`、`sourceCommit`、`versionCode`、`versionName`、`apkSha256`、`signingCertificateSha256` 和文件名。它位于私有 `windows-signed-release` Artifact 内，内容只涉及将来 APK 本身可以公开的最小发布元信息；`publicationAuthorized` 和 `publicTransportReady` 均为 `false`。

独立候选清单测试：私有 Run `38953659076`，6 项成功，**无密钥、无签名、无公开发布**。以前签出的 APK 不会因添加本功能被自动补清单、替换或发布。

## 公开发布请求与验证（已实现）

现有 `android-publish-request.json` 格式继续使用，必要信息：

- `sourceUrl`：允许**匿名 HTTPS 下载、永久或足够稳定**的已签名 APK ZIP 下载地址。不能使用私有仓库 Actions Artifact 的需认证地址，也不能在公开 JSON 中嵌入临时签名链接、token 或 URL 密钥。
- `artifactSha256`：上述**外层 ZIP 原始字节**的 SHA-256；不能误用 APK SHA-256。
- `apkSha256`：内层签名 APK 的 SHA-256。
- `sourceCommit`、`versionName`、`versionCode`、`signingCertificateSha256`、`updateLog`；`forceUpdate` 可选。

外层 ZIP 必须在根目录包含：
`ziwuxilv-native-v<VERSION>.apk`、同名 `.sha256`、`source-commit.txt`、`signing-cert-sha256.txt`；额外的候选元数据允许存在，但不是信任依据。

发布工作流 `publish-android-release.yml` 在修改仓库状态**之前**先运行 `tools/android_publish_guard.py`：禁止与当前版本相同或更旧的 versionCode、过期/带凭据的 URL、证书不匹配和无效字段。下载后既验 ZIP 与 APK 两重哈希，又调用 **Android SDK 的 apksigner / aapt** 直接核对 APK 真正的签名证书、包名与版本，成功才创建 Release 和更新站点版本清单。

真实公开版 v2.3.4 的重新验签及只读测试：Run `38952795677`、`38953371827`，均成功。旧 v2.3.4 发布请求被测试确认为不可重复发布。

## 仍未接通的受控传输

GitHub **私有 Actions Artifact** 不等于公共可匿名下载的 ZIP。没有批准的公开传输端点或发布专用授权，不能宣称已实现“私有签名完成后全自动公开发布”。

下一阶段需明确允许的方式：仅将最终签名 APK 与最小校验文件（**不含源码或密钥**）上传到经过授权的公开只读下载地址，再由公开仓库自身验证发布请求。上传本身会使 APK 提前公开，应设置独立发布批准条件、版本去重和错误恢复。

在此之前，**保持私有签名产物、公开发布和站点版本清单三者隔离，不擅自发布 v2.3.4 或新版本。**
