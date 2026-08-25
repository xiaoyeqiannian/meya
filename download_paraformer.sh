#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h}"
PYTHON="$PROJECT_DIR/.venv/bin/python"
STREAMING_TARGET="$PROJECT_DIR/models/paraformer/iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
SEACO_TARGET="$PROJECT_DIR/models/paraformer/iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
PUNCTUATION_TARGET="$PROJECT_DIR/models/punctuation/iic--punc_ct-transformer_zh-cn-common-vocab272727-pytorch"

if [[ ! -x "$PYTHON" ]]; then
  print -u2 "未找到麦芽 Python 环境，请先运行 ./bootstrap.sh"
  exit 1
fi

"$PYTHON" -c 'from modelscope import snapshot_download; import sys; snapshot_download("iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online", local_dir=sys.argv[1])' "$STREAMING_TARGET"
"$PYTHON" -c 'from modelscope import snapshot_download; import sys; snapshot_download("iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch", local_dir=sys.argv[1])' "$SEACO_TARGET"
"$PYTHON" -c 'from modelscope import snapshot_download; import sys; snapshot_download("iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch", local_dir=sys.argv[1])' "$PUNCTUATION_TARGET"
print "Paraformer 推荐流水线已安装："
print "  实时：$STREAMING_TARGET"
print "  定稿：$SEACO_TARGET"
print "  标点：$PUNCTUATION_TARGET"
