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
    assert "feedbackDialogTexts" in source
    assert "识别原文（只读）" in source
    assert "正确文本（请修改）" in source
    assert "clearPendingFeedback" in final_flow
    assert "if !explicit || foundMapping" in final_flow
    assert "consumePendingFeedback" not in source
    print("feedback UI flow tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
