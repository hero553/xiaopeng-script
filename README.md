# 小鹏后台车系库存巡检工具

这是一个 Python + Playwright 工具，用 Chrome 调试端口连接已登录的后台页面，在你手动设置好筛选条件后，自动巡检 4 个栏目并点击查询，在查到数据时发送微信通知。

## 功能

- 自动打开或连接 Chrome 调试端口。
- 复用你手动登录后的页面，不处理账号、密码、扫码、验证码。
- 依次巡检 `普通库存`、`限量车`、`专项车`、`可售展车`。
- 筛选条件由你手动维护，脚本只负责切换栏目并点击查询。
- 查询到表格数据后发送个人微信通知。
- 支持通知去重，避免同一个栏目下相同结果重复提醒。
- 预留 macOS 和 Windows 打包脚本。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## 配置

主配置文件是 `config/config.json`。

默认推荐同时开启 `Bark + ntfy`：

- iOS 用 `Bark`，系统推送体验更好。
- 安卓用 `ntfy`，兼容性更好。

它们可以同时收到同一条通知，不需要来回切换。

默认配置里保留 ntfy 免费通知。它不是微信通知，需要在手机或电脑上安装 ntfy App，订阅同一个 topic 后接收提醒。

如果 iPhone 上 ntfy 只能在 App 内看到消息、没有系统弹窗和角标，可以改用 Bark。Bark 是 iOS 推送工具，更适合需要 iPhone 弹窗提醒的场景。

### Bark 通知

1. iPhone 安装 Bark。
2. 打开 Bark 首页，复制类似 `https://api.day.app/你的key` 的测试地址。
3. 把最后的 key 填到 `config/bark_keys.txt`，也可以直接粘完整 URL。
4. 在 `config/config.json` 里保留 `providers: ["bark", "ntfy"]`，这样 iOS 和安卓都能收到。

```json
{
  "wechat": {
    "enabled": true,
    "provider": "bark",
    "providers": ["bark", "ntfy"],
    "bark_server_url": "https://api.day.app",
    "bark_title": "小鹏库存提醒",
    "bark_device_key": "",
    "bark_device_keys_file": "config/bark_keys.txt",
    "bark_group": "小鹏库存",
    "bark_level": "timeSensitive",
    "bark_sound": "alarm",
    "bark_badge": 1
  }
}
```

程序会按 Bark 的带标题接口发送，格式等价于：

```text
https://api.day.app/你的key/小鹏库存提醒/这里是推送内容
```

测试 Bark 通知：

```bash
xiaopeng-monitor --test-notify --test-message "小鹏库存巡检测试通知"
```

### ntfy 通知

1. 手机安装 ntfy App。
2. 自己想一个足够随机的 topic，比如 `xp-lock-你的名字-一串随机数字`。
3. 在 ntfy App 里订阅这个 topic。
4. 把同一个 topic 填到 `config/config.json`。

公共服务地址是 `https://ntfy.sh`。topic 相当于收件地址，建议取得随机一些，不要用姓名、手机号这种容易猜到的内容。

```json
{
  "wechat": {
    "enabled": true,
    "provider": "bark",
    "providers": ["bark", "ntfy"],
    "ntfy_server_url": "https://ntfy.sh",
    "ntfy_topic": "xp-lock-your-random-topic",
    "ntfy_token": "",
    "ntfy_priority": "high",
    "ntfy_tags": "car,warning",
    "pushplus_token": "你的PushPlus Token",
    "pushplus_topic": "",
    "serverchan_sendkey": "",
    "work_wechat_webhook_url": "",
    "users_file": "config/wechat_users.txt"
  }
}
```

只测试当前已启用的通知渠道，不启动浏览器：

```bash
xiaopeng-monitor --test-notify --test-message "小鹏库存巡检测试通知"
```

如果你后面想改回普通微信公众号类通知，可以使用 PushPlus。先打开 `https://www.pushplus.plus/`，微信扫码登录后复制自己的 Token，然后把 `providers` 改成只保留 `pushplus`，再填 `pushplus_token`。

```json
{
  "wechat": {
    "enabled": true,
    "provider": "pushplus",
    "providers": ["pushplus"],
    "pushplus_token": "你的PushPlus Token",
    "pushplus_topic": ""
  }
}
```

如果你想用 Server酱，把 `providers` 改成只保留 `serverchan`，再填 `serverchan_sendkey`。Server酱地址是 `https://sct.ftqq.com/`。

```json
{
  "wechat": {
    "enabled": true,
    "provider": "serverchan",
    "providers": ["serverchan"],
    "pushplus_token": "",
    "serverchan_sendkey": "你的Server酱SendKey"
  }
}
```

`config/wechat_users.txt` 只在 `providers` 包含 `work_wechat` 企业微信机器人模式时需要。ntfy、PushPlus、Server酱、Bark 模式不用维护这个文件。

如果后面还要用企业微信机器人，可以这样配置：

```json
{
  "wechat": {
    "enabled": true,
    "provider": "work_wechat",
    "providers": ["work_wechat"],
    "work_wechat_webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的key",
    "users_file": "config/wechat_users.txt"
  }
}
```

企业微信接收人维护在 `config/wechat_users.txt`，每行一个企业微信 `user_id`、手机号，或 `@all`。

```text
@all
13800138000
zhangsan
```

如果要用无痕窗口，把 `config/config.json` 里的 `chrome.incognito` 改为 `true`。无痕窗口关闭后登录状态不会保留。

## 运行

macOS 可以直接用脚本启动：

```bash
bash scripts/run_macos.sh
```

macOS 持续并行巡检可以直接用这个脚本：

```bash
bash scripts/run_macos_loop.sh
```

Windows PowerShell 可以直接用脚本启动：

```powershell
.\scripts\run_windows.ps1
```

第一次建议这样运行：

```bash
xiaopeng-monitor --wait-login
```

程序会打开一个带调试端口的 Chrome。你手动登录后台，确认页面能看到车系筛选和查询按钮后，在终端按回车，程序开始巡检。

`monitor.manual_query_only` 默认为 `true`，表示由你手动设置筛选条件，脚本只在单页面里依次点击 4 个栏目下的查询按钮。

- 这个模式下，`parallel_tabs` 和 `parallel_series_per_tab` 不再参与巡检流程。
- 如果以后想恢复旧的“按车系自动轮询”模式，再把 `manual_query_only` 改成 `false`。

`monitor.poll_interval_min_seconds` 和 `monitor.poll_interval_max_seconds` 用来控制每轮之间的随机等待时间。

- 例如最小 `1`、最大 `2`，就表示每轮结束后随机等待 `1` 到 `2` 秒再开始下一轮。

`monitor.notify_cooldown_seconds` 用来控制相同数据的重复提醒冷却时间。

- 例如设置为 `7200`，表示同一批完全相同的数据，2 小时后才会再次提醒。
- 如果同一个 tab 的结果内容发生变化，会立刻按新数据再次提醒。
- 通知标题会区分为：
  - `抢到了`：首次出现
  - `库存变更`：同一车系的数据内容发生变化
  - `库存再次提醒`：内容没变，但冷却时间已到

只巡检一轮用于调试：

```bash
xiaopeng-monitor --wait-login --once -v
```

只读取 4 个栏目的车系列表，不查询、不通知：

```bash
xiaopeng-monitor --wait-login --list-series -v
```

诊断页面识别情况，不发通知：

```bash
xiaopeng-monitor --wait-login --diagnose -v
```

只测试微信通知，不启动浏览器：

```bash
xiaopeng-monitor --test-notify --test-message "小鹏库存巡检测试通知"
```

正常持续巡检：

```bash
xiaopeng-monitor --wait-login
```

## 打包

macOS:

```bash
bash scripts/build_macos.sh
```

Windows PowerShell:

```powershell
.\scripts\build_windows.ps1
```

打包后产物在 `dist/` 目录。

## 重要说明

- 程序连接的是 `config/config.json` 里配置的 Chrome 调试端口，默认 `9222`。
- 程序启动的 Chrome 使用独立用户目录 `data/chrome-profile`，不会直接操作你的日常 Chrome 个人目录。
- 如果你的页面组件或表格结构和默认判断不一致，第一次运行时需要根据日志微调选择器。
