# 子午汐律共享资料

`profiles/index.json` 是 APP 的一次批量读取入口，以 GitHub numeric user ID 为键；`profiles/<id>/profile.json` 是正式资料，`avatar.webp` 是同路径覆盖的 256×256 WebP（不超过 100 KB）。尚未设置资料时目录不存在，APP 回退 GitHub login/avatar。初始化不公开任何旧本机资料。

索引中的每份资料包含 `version`、`github_id`、`github_login`、`nickname`、`nickname_key`、`avatar_path`（可为 null）、`avatar_sha256`、`updated_at`、`next_edit_at` 和 `last_request_id`。日期包含时区；冷却按成功时间加 30×24 小时计算。

更新协议：用户以自己的 Issues 权限创建 `ZIWUXILV_PROFILE_REQUEST_V1` 请求，正文为 version=1、32 位十六进制 request_id、nickname、avatar_action（keep/replace/remove）、avatar_parts 和 avatar_sha256。头像 Base64 按 48000 字符分块提交为同 Issue 的 `ZIWUXILV_PROFILE_AVATAR_V1` 评论。最后提交 `ZIWUXILV_PROFILE_SUBMIT_V1` 评论，包含 request_id 与完整 Issue 正文的 SHA-256。草稿及分块不会触发写入。

Action 运行可信 main 中的代码，通过 GitHub API 重读真实 Issue 和封口评论，核验作者/sender numeric ID，所有头像块也只能来自同一真实作者。自填 github_id 不具有授权效力，伪造其他人的 ID 被拒绝；目标目录只由 API 作者 ID 决定。请求正文和头像均当作数据，不执行，不转成 shell 命令。管理员例外固定对应既有管理员 ID 205125766，不支持替他人修改。

写入使用 `profile-registry-write` 全局串行组、`cancel-in-progress: false`、`queue: max`。后者避免普通单个 pending 槽位替换先前请求；GitHub 最多允许 100 个等待任务，超额或异常中止可用原 request_id 重试。[GitHub 并发队列说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)。每次 push 前 fetch 最新 main，重新读取索引、校验占用和冷却，非快进时重试四次。无同 ID 冲突豁免以外的重名例外，管理员也不能占用别人昵称。

昵称 NFKC 规范化，明显 Unicode 空白映射普通空格，连续空格合并并 trim，格式控制字符清除；按 Unicode code point 计 2～16 个字符，必须含文字或数字。唯一键为规范化后的不区分大小写形式。系统保留词及空格、标点、全角等明显变体被限制。管理员仍受长度和唯一性约束。

服务端将头像再次解码并重新编码去除元数据，验证 WebP、256×256、单帧和 100 KB 上限。只写 profiles 目录。一次 Git commit 同时提交头像、单用户资料、索引和 `profiles/requests/<id>/<request_id>.json` 收据。push 完成才返回成功评论和 commit_sha；APP 从这个不可变提交读取索引并核对 last_request_id 后生效。重复执行从收据恢复，不再次刷新冷却。

错误有 NICKNAME_ALREADY_USED、PROFILE_EDIT_COOLDOWN（附 next_edit_at）、INVALID_NICKNAME、RESERVED_NICKNAME、AVATAR_TOO_LARGE、PROFILE_AUTH_FAILED、PROFILE_UPDATE_FAILED、PROFILE_NO_CHANGE。认证/头像阶段的拒绝通过可信 GitHub Actions bot 评论返回，不修改用户资料。提交后网络中断时 APP 保留请求和草稿，不自动创建第二个 Issue。

用户只有 Issues 权限；APP 不持有 Contents Write、管理员 PAT 或服务端 token。Action 的 GITHUB_TOKEN 仅在公共仓库服务端使用。现有 APK 发布文件、签名仓库和私有策略数据库不参与资料存储。

本地测试：`python -m unittest discover -s tests -p 'test_profile*.py' -v`。Profile Registry tests 在 PR/main 对这套流程执行测试；Profile Registry update 的手动运行只验证，不替用户公开任何资料。
