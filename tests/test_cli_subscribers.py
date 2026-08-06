from canton_fair_alert.cli import main


def configure(monkeypatch, settings):
    monkeypatch.setenv("DATABASE_PATH", str(settings.database_path))
    monkeypatch.setenv("SNAPSHOT_DIR", str(settings.snapshot_dir))
    monkeypatch.setenv("SOURCES_FILE", str(settings.sources_file))


def test_cli_subscriber_crud(monkeypatch, settings, capsys):
    configure(monkeypatch, settings)
    assert main(["migrate"]) == 0
    assert (
        main(
            [
                "subscribers",
                "add",
                "--name",
                "Alice",
                "--email",
                "alice@example.com",
                "--enable-email",
            ]
        )
        == 0
    )
    assert main(["subscribers", "list"]) == 0
    output = capsys.readouterr().out
    assert "Alice" in output
    assert main(["subscribers", "update", "1", "--email", "new@example.com"]) == 0
    assert main(["subscribers", "disable", "1"]) == 0
    assert main(["subscribers", "enable", "1"]) == 0
    assert main(["subscribers", "delete", "1", "--yes"]) == 0


def test_cli_rejects_missing_channel(monkeypatch, settings):
    configure(monkeypatch, settings)
    assert main(["subscribers", "add", "--name", "No channel"]) == 2


def test_cli_masks_webhook(monkeypatch, settings, capsys):
    configure(monkeypatch, settings)
    webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret-abcd"
    assert (
        main(["subscribers", "add", "--name", "Bot", "--wecom-webhook", webhook, "--enable-wecom"])
        == 0
    )
    assert main(["subscribers", "show", "1"]) == 0
    output = capsys.readouterr().out
    assert "secret" not in output
    assert "abcd" in output
