#!/usr/bin/env python3
"""Network-free checks for Meya's code-switch benchmark metrics."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.final_model_compare import (  # noqa: E402
    cer,
    english_term_hit,
    english_terms,
    mer,
    mixed_tokens,
)


def main() -> int:
    assert mixed_tokens("把 Nydus 部署到 K8s 的 manifest。") == [
        "把", "nydus", "部", "署", "到", "k8s", "的", "manifest"
    ]
    assert mer("把 Nydus 部署到 K8s", "把奈达斯部署到K8s") == 3 / 6
    assert mer("merge 到 main 分支", "merge到main分支") == 0.0
    assert cer("manifest", "manifast") == 1 / 8
    assert mer("manifest", "manifast") == 1.0
    assert english_terms("CLI. CI/CD、C++ ") == ["CLI", "CI/CD", "C++"]
    assert english_term_hit("提交到 main 分支", "main")
    assert not english_term_hit("maintenance", "main")
    assert english_term_hit("用 C++ 开发", "C++")
    print("final model comparison metric tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
