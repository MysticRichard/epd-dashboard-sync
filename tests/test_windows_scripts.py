from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"


def test_windows_runner_refreshes_local_usage_before_combined_sync() -> None:
    script = (SCRIPTS / "windows-sync.ps1").read_text(encoding="utf-8")

    snapshot_index = script.index('"codex-snapshot"')
    sync_index = script.index('"sync-once"')
    assert snapshot_index < sync_index
    assert '-AllowFailure' in script
    assert "sync-once" in script
    assert '"--only", "codex", "--combined"' in script
    assert "config.toml" in script
    assert "Copy-SnapshotToNas" not in script


def test_windows_task_installer_requires_a_successful_manual_sync() -> None:
    script = (SCRIPTS / "install-windows-task.ps1").read_text(encoding="utf-8")

    assert "lastResult" in script
    assert "success" in script
    assert "Register-ScheduledTask" in script
    assert "Unregister-ScheduledTask" in script


def test_windows_task_runs_without_showing_a_shell_window() -> None:
    installer = (SCRIPTS / "install-windows-task.ps1").read_text(encoding="utf-8")
    wrapper = (SCRIPTS / "windows-sync-hidden.vbs").read_text(encoding="utf-8")

    assert "wscript.exe" in installer
    assert "-NonInteractive" in wrapper
    assert "-WindowStyle Hidden" in wrapper
    assert "New-ScheduledTaskSettingsSet -Hidden" in installer


def test_windows_task_repeats_every_30_minutes() -> None:
    script = (SCRIPTS / "install-windows-task.ps1").read_text(encoding="utf-8")

    assert "-RepetitionInterval (New-TimeSpan -Minutes 30)" in script
    assert "every 30 minutes" in script


def test_windows_runner_uses_the_NAS_dashboard_as_the_device_feed() -> None:
    script = (SCRIPTS / "windows-sync.ps1").read_text(encoding="utf-8")

    assert "0x56E2" not in script
    assert "-epd-dashboard-state" not in script
    assert "dashboard.json" not in script
    assert '"sync-once"' in script


def test_windows_runner_logs_failures_for_silent_task_diagnosis() -> None:
    script = (SCRIPTS / "windows-sync.ps1").read_text(encoding="utf-8")

    assert "catch {" in script
    assert "error:" in script


def test_windows_task_uses_a_windowless_vbs_entrypoint() -> None:
    installer = (SCRIPTS / "install-windows-task.ps1").read_text(encoding="utf-8")
    wrapper = (SCRIPTS / "windows-sync-hidden.vbs").read_text(encoding="utf-8")

    assert "windows-sync-hidden.vbs" in installer
    assert '"System32\\wscript.exe"' in installer
    assert "shell.Run commandLine, 0, True" in wrapper
    assert "-WindowStyle Hidden" in wrapper


def test_windows_runner_serializes_manual_and_scheduled_runs() -> None:
    script = (SCRIPTS / "windows-sync.ps1").read_text(encoding="utf-8")

    assert "[Threading.Mutex]" in script
    assert "Global\\EPD-Dashboard-Sync" in script
    assert "WaitOne(0)" in script
    assert "ReleaseMutex" in script
