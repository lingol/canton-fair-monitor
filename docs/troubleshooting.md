# 故障排查

## 官网抓取或解析失败

```bash
journalctl -u canton-fair-refresh.service -n 200 --no-pager
sudo ls -l /var/lib/canton-fair-alert/snapshots
sudo canton-fair-schedule show
```

服务保留最后有效日程；不要删除数据库或快照。若日程已过期，提醒任务会停止用户投递。可核对官方公告后导入严格校验的人工 JSON。格式变化应通过新增确定性 selector/正则和脱敏 fixture 修复，不得接入 LLM 解析。

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
