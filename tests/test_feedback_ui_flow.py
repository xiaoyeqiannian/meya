#!/usr/bin/env python3
"""Static regression checks for the macOS last-correction learning flow."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    source = (ROOT / "app/LocalVoiceInput.swift").read_text(encoding="utf-8")
    final_flow = source[source.index("private func completeFinal(") : source.index("private func showPermissionError")]
    assert final_flow.index("prepareFeedback(finalText: finalText)") < final_flow.index("commit(finalText)")
    assert "manualFeedbackSubmission" in final_flow
    assert "showManualLearningDialog" in final_flow
    assert "captureFeedbackBaseline" in final_flow
    assert "captureCurrentFeedbackEdit" in source
    assert "feedbackDialogTexts" in source
    assert "func menuWillOpen(_ menu: NSMenu)" in source
    assert "focusedElement(applicationPID:" in source
    assert "kAXStringForRangeParameterizedAttribute" in source
    assert "识别原文（只读）" in source
    assert "正确文本（请修改）" in source
    assert "clearPendingFeedback" in final_flow
    assert "if !explicit || foundMapping" in final_flow
    assert "consumePendingFeedback" not in source
    assert 'runtime/hotword-catalog-report.json' in source
    assert 'command": "refresh_hotword_catalog' in source
    assert '"--refresh-catalog-only"' in source
    assert '"建议发音（点击采纳）"' in source
    assert "acceptSuggestion" in source
    assert 'saveButton.title = "正在检测…"' in source
    assert 'case pronunciationSuggestions = "pronunciation_suggestions"' in source
    assert 'title: "管理已学规则…"' in source
    assert 'sendLearningCommand("list_learning_rules"' in source
    assert 'sendLearningCommand("rollback_learning_rule"' in source
    assert "rule.hitCount" in source
    assert "rule.evidence" in source
    print("feedback UI flow tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
