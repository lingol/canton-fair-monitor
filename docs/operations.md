# 运维手册

## 日常检查

```bash
sudo canton-fair-doctor
canton-fair-alert status
systemctl list-timers 'canton-fair-*'
journalctl --since today -u canton-fair-refresh.service -u canton-fair-alert.service
```

正式修改前先导出日程并备份数据库：

```bash
sudo canton-fair-schedule export-json /var/lib/canton-fair-alert/backups/schedule.json
sudo cp -a /var/lib/canton-fair-alert/app.db /var/lib/canton-fair-alert/backups/app.db.manual
```

## 人工日程格式

```json
{
  "edition": 140,
  "season": "autumn",
  "source_name": "manual_admin_override",
  "source_url": "manual",
  "windows": [
    {"phase": 1, "start_date": "2026-10-15", "end_date": "2026-10-19", "event_type": "exhibition"},
    {"phase": 2, "start_date": "2026-10-23", "end_date": "2026-10-27", "event_type": "exhibition"},
    {"phase": 3, "start_date": "2026-10-31", "end_date": "2026-11-04", "event_type": "exhibition"}
  ]
}
```

先运行 `validate-json`，再运行 `import-json`。官网恢复后对比日程，最后显式使用 `refresh --accept-official` 切回。

## 管理员告警

首次异常立即邮件通知；同一规范化指纹在默认 24 小时内只累计次数。冷却期后仍失败会再次发送，恢复后发送一次恢复邮件。若 SMTP 本身失败，命令返回非零并由 systemd 标记失败，避免递归告警。

## 干净 Ubuntu 20.04 验收记录模板

该验证必须在实际 Ubuntu 20.04 服务器上执行，本仓库的 macOS 开发主机不能替代该证据。部署人员应记录日期、Ubuntu 镜像、commit、SMTP/Webhook 测试账号和以下命令的脱敏输出：

```bash
sudo ./scripts/deploy.sh
sudo canton-fair-doctor
systemctl is-enabled canton-fair-refresh.timer canton-fair-alert.timer
systemctl list-timers 'canton-fair-*'
sudo canton-fair-admin-email test
sudo canton-fair-subscribers test 1 --channel all
sudo ./scripts/deploy.sh   # 第二次执行，验证幂等和备份
```

确认 `/etc/canton-fair-alert/app.env` 为 `0640 root:cantonfair`，数据库非 world-readable，二次部署没有删除订阅者或日程。
