#!/usr/bin/env python3
"""Static regression checks for the compact status-bar menu."""

from pathlib import Path


def main() -> int:
    source = (Path(__file__).resolve().parents[1] / "app/LocalVoiceInput.swift").read_text(
        encoding="utf-8"
    )
    menu = source[source.index("private func setupStatusItem()") : source.index("func menuWillOpen")]
    forbidden = ["我已授权，重启麦芽", "重新打开 Fn 权限设置…", "restartForFunctionKeyPermissions"]
    assert not any(value in source for value in forbidden)
    assert 'NSMenuItem(title: "更多"' in menu
    assert 'title: "权限与诊断…"' in menu
    assert menu.index("learnLastCorrectionItem =") < menu.index("管理个人词库…")
    assert menu.index("管理个人词库…") < menu.index("管理识别模型…")
    assert "moreMenu.addItem(reviewLearningItem)" in menu
    assert "moreMenu.addItem(folderItem)" in menu
    assert "learnLastCorrectionItem?.isEnabled = canLearn" in source
    assert "showPermissionDiagnostics" in source
    assert "麦克风：" in source and "输入监控：" in source and "辅助功能：" in source
    assert "\\(mark(hasMicrophone))" in source and "\\(tapEnabled ?" in source
    assert "授权后麦芽会自动检测并恢复，无需重启" in source
    print("status menu tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
