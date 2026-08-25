#!/usr/bin/env python3
"""Small, network-free tests for pluggable ASR model identifiers."""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asr_adapters import (  # noqa: E402
    _merge_stream_text,
    paraformer_identifier,
    resolve_punctuation_source,
    resolve_paraformer_source,
    split_model_identifier,
    write_hotword_file,
)


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

    print("ASR adapter tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
