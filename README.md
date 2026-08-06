# 广交会次日通勤提醒

这是一个运行在 Ubuntu 20.04 服务器上的轻量提醒服务。它使用确定性的 HTML/JSON-LD/正则解析和严格日期校验抓取广交会官方日程，在正式展期前一天通过 SMTP 邮件和/或企业微信群机器人提醒订阅者调整通勤方式。

运行时不调用任何 LLM 或 AI API，LLM token 成本为 **0**。服务不监听公网端口，不依赖手机、Mac 或桌面应用在线。

## 快速部署

```bash
git clone <repository-url>
cd canton-fair-commute-alert
sudo ./scripts/deploy.sh
```

脚本会互动询问时区、每日抓取时间、提醒时间、SMTP、管理员邮箱和首个订阅者信息；密码和 Webhook 使用隐藏输入。安装位置为：

- 应用：`/opt/canton-fair-alert/app`
- 配置：`/etc/canton-fair-alert/app.env` 和 `sources.json`
- 数据库与失败快照：`/var/lib/canton-fair-alert`
- 定时任务：`canton-fair-refresh.timer`、`canton-fair-alert.timer`

非互动部署可使用 `sudo ./scripts/deploy.sh --config ./deploy.env --non-interactive`。配置文件必须由管理员保护；不允许在命令行中传入 SMTP 密码。

## 架构与安全边界

每天的 refresh 任务按优先级请求 `sources.json` 中的官方来源，支持 ETag/Last-Modified、最多三次重试、5 MB 限制、TLS 校验和重定向域名校验。解析器依次检查显式 DOM、JSON-LD、中文“第一期”及英文 “Phase 1” 日期。只有完整通过三期、五天、顺序、间隔、季节和年份校验的数据才会在 SQLite 事务内切换为活动版本。

解析失败时旧日程保持不变，失败 HTML 以 `0600` 权限保存（最多 10 份），管理员告警按指纹在 24 小时内去重。日程过期时提醒任务拒绝发送可能错误的消息。投递由 `(subscriber, target_date, event_key, channel)` 唯一索引保证幂等。

密钥只存放在 root 管理的环境文件和 SQLite 中；列表和详情默认掩码 Webhook。systemd 服务以无登录用户 `cantonfair` 运行，并启用 `ProtectSystem=strict`、`ProtectHome=true`、`PrivateTmp=true`。

## SMTP 和企业微信准备

准备 SMTP 服务器地址、端口、加密方式（`starttls`、`ssl` 或显式开发用 `plain`）、发件人和管理员邮箱。用户名/密码可按邮件服务要求留空。部署后重新配置：

```bash
sudo canton-fair-admin-email
sudo canton-fair-admin-email show
sudo canton-fair-admin-email test
```

自动化配置时，密码应写入权限受限的文件或通过环境传递：

```bash
sudo canton-fair-admin-email set \
  --smtp-host smtp.example.com --smtp-port 587 --smtp-security starttls \
  --smtp-username alerts@example.com --smtp-password-file /root/smtp-password.txt \
  --smtp-from-email alerts@example.com --smtp-from-name "广交会通勤提醒" \
  --admin-email admin@example.com --send-test
```

企业微信群中选择“群机器人”，复制以 `https://qyapi.weixin.qq.com/...` 开头的 Webhook。完整地址不要提交到 Git、聊天记录或工单。

## 订阅者 CRUD

不带参数进入互动菜单：

```bash
sudo canton-fair-subscribers
```

非互动示例：

```bash
sudo canton-fair-subscribers add --name "张三" \
  --email zhangsan@example.com --enable-email \
  --wecom-webhook 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...' --enable-wecom
sudo canton-fair-subscribers list
sudo canton-fair-subscribers show 1
sudo canton-fair-subscribers update 1 --email new@example.com --disable-wecom
sudo canton-fair-subscribers disable 1
sudo canton-fair-subscribers enable 1
sudo canton-fair-subscribers test 1 --channel email
sudo canton-fair-subscribers delete 1 --yes
```

删除默认要求再次输入 `yes`；只有 root 使用 `show ID --show-secrets` 才能显示完整 Webhook。

## 日程与人工降级

```bash
sudo canton-fair-schedule show
sudo canton-fair-schedule refresh --force
sudo canton-fair-schedule validate-json ./schedule.json
sudo canton-fair-schedule import-json ./schedule.json
sudo canton-fair-schedule export-json ./backup.json
```

人工 JSON 必须包含 `edition`、`season`、以 `manual` 开头的 `source_name`，以及三段 `windows`。每段含 `phase`、ISO 格式的 `start_date`/`end_date`，可选 `event_type`。人工数据同样经过严格校验。人工版本活动时，官方刷新不会静默覆盖；管理员确认后执行：

```bash
sudo canton-fair-schedule refresh --force --accept-official
```

完整 JSON 示例见 [运维手册](docs/operations.md)。

## 运行、日志与诊断

```bash
sudo canton-fair-doctor
canton-fair-alert status
canton-fair-alert alert --date 2026-10-14 --dry-run
systemctl list-timers 'canton-fair-*'
journalctl -u canton-fair-refresh.service
journalctl -u canton-fair-alert.service
```

timer 使用 `Persistent=true`，服务器错过定时点后会补执行；应用层幂等约束避免重复通知。`doctor` 返回 `0` 表示正常、`1` 表示 warning、`2` 表示 failure。

## 更新与卸载

从新版本 checkout 中执行：

```bash
sudo ./scripts/update.sh
sudo ./scripts/uninstall.sh
```

更新前自动备份配置和数据库。卸载默认保留数据库、配置和备份。

## 开发和测试

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check .
mypy src
shellcheck scripts/*.sh scripts/lib/*.sh
systemd-analyze verify systemd/*.service systemd/*.timer
```

测试不发送真实邮件或企业微信消息。部署验证需要一台干净的 Ubuntu 20.04 主机和测试 SMTP/Webhook，步骤见 [运维手册](docs/operations.md)。故障处理见 [故障排查](docs/troubleshooting.md)。

## 隐私与成本

系统只存储管理员配置、订阅者名称/通知地址、日程和投递状态；不收集住址、定位或实时路况。正常运行成本仅包括少量官方 HTTP 请求、SMTP/企业微信请求和本地 SQLite I/O。无收费数据库、队列或云函数依赖，运行时 LLM token 使用量为 0。
