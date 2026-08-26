#!/usr/bin/env python3
"""Small, network-free tests for pluggable ASR model identifiers."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asr_adapters import (  # noqa: E402
    ParaformerAdapter,
    _merge_stream_text,
    paraformer_identifier,
    resolve_punctuation_source,
    resolve_paraformer_source,
    split_model_identifier,
    write_hotword_file,
)
from glossary import GlossaryEntry  # noqa: E402


def main() -> int:
    assert split_model_identifier("mlx-community/whisper-small-mlx") == (
        "whisper",
        "mlx-community/whisper-small-mlx",
    )
    assert split_model_identifier("paraformer:funasr/paraformer-zh") == (
        "paraformer",
        "funasr/paraformer-zh",
    )
    assert paraformer_identifier("funasr/paraformer-zh") == "paraformer:funasr/paraformer-zh"
    assert _merge_stream_text("今天天气", "天气不错") == "今天天气不错"
    assert _merge_stream_text("hello", "world") == "hello world"

    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory)
        source = project / "models/paraformer/funasr--paraformer-zh"
        source.mkdir(parents=True)
        (source / "config.yaml").touch()
        (source / "model.pt").touch()
        assert resolve_paraformer_source(project, "funasr/paraformer-zh") == source.resolve()

        punctuation = (
            project
            / "models/punctuation/iic--punc_ct-transformer_zh-cn-common-vocab272727-pytorch"
        )
        punctuation.mkdir(parents=True)
        (punctuation / "config.yaml").touch()
        (punctuation / "model.pt").touch()
        assert resolve_punctuation_source(project) == punctuation.resolve()

        hotword_file = write_hotword_file(
            project / "runtime/seaco-hotwords-final.txt",
            ["Acme CLI", "GPU 驱动", "Acme CLI"],
        )
        assert hotword_file.read_text(encoding="utf-8").splitlines() == [
            "Acme CLI",
            "GPU 驱动",
        ]

        seaco = project / "models/paraformer/iic--seaco-test"
        seaco.mkdir(parents=True)
        (seaco / "config.yaml").write_text("model: SeacoParaformer\n", encoding="utf-8")
        (seaco / "model.pt").touch()
        (seaco / "seg_dict").write_text("main ma@@ in\n", encoding="utf-8")
        adapter = ParaformerAdapter(project, "iic/seaco-test", role="final")
        assert adapter.catalog_report_path.name == "hotword-catalog-report.json"
        assert adapter.active_report_path.name == "hotword-active-report-final.json"
        catalog = adapter.refresh_hotword_catalog([
            GlossaryEntry("main"),
            GlossaryEntry("NovaKit"),
        ])
        assert catalog["entries"] == 2
        adapter.prepare_hotwords([GlossaryEntry("main")], max_terms=1)
        assert json.loads(adapter.catalog_report_path.read_text(encoding="utf-8"))["summary"]["entries"] == 2
        assert json.loads(adapter.active_report_path.read_text(encoding="utf-8"))["summary"]["entries"] == 1

    print("ASR adapter tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
