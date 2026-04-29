# macOS 平台权限与签名配置指南

> 基于多台机器 (macOS 10.15 ~ 15.x, Intel + Apple Silicon) 的实测经验总结。

## 核心结论

能否从微信进程获取访问凭据，取决于 **两个独立问题**：

| 问题 | 控制什么 | 关键因素 |
|------|---------|---------|
| `task_for_pid()` 能否成功 | 从进程获取凭据 | **目标 App 的代码签名** |
| `codesign` 能否重签名 | 修改 App 文件 | **调用者的完全磁盘访问** |

---

## 一、task_for_pid 权限（从微信进程获取访问凭据）

### 决定因素：微信 App 的 Hardened Runtime

```bash
# 检查微信签名状态
codesign -dv /Applications/WeChat.app 2>&1 | grep -E "Signature|flags"
```

#### 情况 A：Ad-hoc 签名（无 Hardened Runtime）

```
flags=0x2(adhoc)
Signature=adhoc
TeamIdentifier=not set
```

**原因**: App 经第三方工具重新签名（例如修改版客户端）。

**权限要求**: 只需 `sudo`，任何上下文（Terminal、SSH、cron）都能成功。

```bash
# SSH 远程直接可用
sudo ./find_all_keys_macos
```

#### 情况 B：Apple 官方签名（有 Hardened Runtime）

```
flags=0x10000(runtime)
Signature size=9092
Authority=...Apple...
```

**原因**: App Store 下载或官方 DMG 安装，未经修改。

**权限要求**: `sudo` + 本机 GUI 终端 + TCC "开发者工具"授权。SSH **不可行**。

---

## 二、codesign 权限（重签名微信 App）

如果微信是 Apple 官方签名，需要重签名为 ad-hoc 来解锁 SSH 提取。

### 问题：SSH 下 codesign 可能失败

```
$ sudo codesign --force --deep --sign - /Applications/WeChat.app
/Applications/WeChat.app: Operation not permitted
In subcomponent: /Applications/WeChat.app/Contents/MacOS/WeChatAppEx.app
```

**原因**: SSH 进程没有"完全磁盘访问"(Full Disk Access, FDA) 权限，无法修改 `/Applications` 下的 App bundle 文件。

### 给 SSH 授予完全磁盘访问

在目标机器的 **GUI** 上操作：

```
系统偏好设置 → 安全性与隐私 → 隐私 → 完全磁盘访问
点击 🔒 解锁 → 点 + 号 → Cmd+Shift+G 输入路径
```

**必须添加这两个**（缺一不可）：

| 路径 | 说明 |
|------|------|
| `/usr/sbin/sshd` | SSH 守护进程 |
| `/usr/libexec/sshd-keygen-wrapper` | SSH 的实际执行进程（负责应用） |

> ⚠️ 添加后必须**断开 SSH 重新连接**！TCC 权限在进程启动时检查，不会热更新。

### 验证 FDA 是否生效

```bash
# 重连 SSH 后执行
cat ~/Library/Application\ Support/com.apple.TCC/TCC.db > /dev/null 2>&1 && echo "FDA: YES" || echo "FDA: NO"
```

TCC.db 是受保护文件，只有 FDA 进程能读取。

### 远程场景下的权限准备

```bash
# 0. 前提：SSH 已有 FDA（上面的步骤）

# 1. 确认微信已退出
kill $(pgrep -x WeChat) 2>/dev/null
sleep 2
pgrep -x WeChat && echo "还在运行！" || echo "已退出"

# 2. 清除扩展属性（可选，防止干扰）
sudo xattr -cr /Applications/WeChat.app

# 3. Ad-hoc 重签名
sudo codesign --force --deep --sign - /Applications/WeChat.app

# 4. 验证签名
codesign -dv /Applications/WeChat.app 2>&1 | grep -E "Signature|flags"
# 期望: flags=0x2(adhoc), Signature=adhoc

# 5. 你需要在 GUI 上重新打开自己的微信并登录
# （或者 SSH 执行 open，但仍需在 GUI 上完成登录）
open /Applications/WeChat.app
```

### 注意事项

| 事项 | 说明 |
|------|------|
| 微信必须先退出 | 运行中的 App，其 dylib/binary 被占用，codesign 会报 `internal error` |
| **重签名后必须重启微信** | 已运行的进程仍使用旧签名的内存映像，task_for_pid 仍会失败。必须 kill 后重新启动 |
| 重签名后需重新登录微信 | 签名变更会使登录态失效 |
| 自动更新可能覆盖签名 | 微信更新后变回 Apple 签名，需要再次重签 |
| 小程序可能受影响 | 部分小程序校验签名，ad-hoc 可能报安全错误 |

---

## 三、权限矩阵总结

| 操作 | 需要的权限 | SSH 需要额外配置 |
|------|-----------|-----------------|
| 读取微信数据库文件 | 文件系统权限（通常有） | 无 |
| `task_for_pid` (ad-hoc App) | sudo | 无 |
| `task_for_pid` (Apple 签名 App) | sudo + TCC DeveloperTool | **不可行**，必须本机 Terminal |
| `codesign` 重签名 App | sudo + FDA | SSH 需添加 sshd + sshd-keygen-wrapper 到 FDA |

### 完全远程操作清单（一次性 GUI 配置）

只需在目标机器 GUI 上做一次，之后 SSH 永久可用：

1. **完全磁盘访问** → 添加 `/usr/sbin/sshd` 和 `/usr/libexec/sshd-keygen-wrapper`
2. SSH 连入 → `sudo codesign --force --deep --sign - /Applications/WeChat.app`
3. 在 GUI 上重开自己的微信并登录
4. 之后 SSH 永久可以 `sudo` 获取访问凭据，微信重启也不影响（除非更新覆盖签名）

---

## 四、常见误区

| 误区 | 真相 |
|------|------|
| "需要给终端完全磁盘访问才能访问进程" | ❌ FDA 控制文件访问，不控制进程访问 |
| "加了 sshd 到 FDA 就行" | ❌ 还需要加 `sshd-keygen-wrapper`，且要重连 SSH |
| "微信开着也能重签名" | ❌ 运行中的 binary/dylib 被占用，codesign 会失败 |
