# 故障排查

## 官网抓取或解析失败

```bash
journalctl -u canton-fair-refresh.service -n 200 --no-pager
sudo ls -l /var/lib/canton-fair-alert/snapshots
sudo canton-fair-schedule show
```

服务保留最后有效日程；不要删除数据库或快照。若日程已过期，提醒任务会停止用户投递。可核对官方公告后导入严格校验的人工 JSON。格式变化应通过新增确定性 selector/正则和脱敏 fixture 修复，不得接入 LLM 解析。

### 2026-09：旧来源返回 200 状态的 404 页面

原默认来源 `https://cief.cantonfair.org.cn/en/cfintro/cfintro.html` 已返回
“404 / Page not found”，但 HTTP 状态仍为 200。parser 1.0 会报告
`no deterministic phase/date ranges found`。快照哈希前缀 `26afb7a4b1c9`
对应此错误页。

parser 1.1 使用[官方香港办事处展览日历](https://hk.cantonfair.org.cn/en/)，
逐行提取日期、届次和期数，排除页面中的旧文章与其他展览，并兼容页面错误声明的字符编码。
更新后，配置加载器会将上述旧 URL 映射到新来源，无需重写服务器 `sources.json`；
自定义 URL、优先级与停用状态保持不变。新来源首次请求不会携带旧来源的 ETag。

将修复后的代码同步到服务器 checkout 后执行：

```bash
sudo ./scripts/update.sh
sudo canton-fair-schedule refresh --force
sudo canton-fair-schedule show
```

2026-09-24 实测可读取第 140 届秋季三期：10 月 15–19 日、10 月 23–27 日、
10 月 31 日–11 月 4 日。确认输出中的来源为 `https://hk.cantonfair.org.cn/en/`。
如果已有人工日程，默认仍保留人工版本；核对后使用 `refresh --force --accept-official`。
上述命令需要在服务器执行；本地源码验证不会修改已部署的服务。

## SMTP 失败

运行 `sudo canton-fair-admin-email show` 确认非敏感字段，再运行 `test`。检查云厂商出站 SMTP 限制、端口、STARTTLS/SMTPS 选择和应用专用密码。日志不会输出密码。

## 企业微信失败

使用 `sudo canton-fair-subscribers show ID` 检查掩码地址，通过 `test ID --channel wecom` 复现。确认机器人未删除、key 未轮换、服务器可访问 `qyapi.weixin.qq.com`。不要把完整 Webhook 粘贴到日志。

## timer 没有运行

```bash
systemctl status canton-fair-refresh.timer canton-fair-alert.timer
systemctl list-timers --all 'canton-fair-*'
journalctl -u canton-fair-refresh.service -u canton-fair-alert.service
```

两个 timer 都应为 enabled，且包含 `Persistent=true`。配置时间变化后重新运行部署脚本以重新生成 timer。

## SQLite 错误

停止两个 timer，保留损坏文件并复制最近备份进行人工检查。不要让脚本自动覆盖可疑数据库：

```bash
sudo systemctl stop canton-fair-refresh.timer canton-fair-alert.timer
sudo sqlite3 /var/lib/canton-fair-alert/app.db 'PRAGMA integrity_check;'
sudo ls -lt /var/lib/canton-fair-alert/backups
```
