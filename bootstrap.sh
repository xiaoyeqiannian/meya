#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h}"
python_bin="/opt/homebrew/opt/python@3.12/bin/python3.12"

if [[ ! -x "$python_bin" ]]; then
  echo "未找到 Homebrew Python 3.12: $python_bin" >&2
  exit 1
fi

if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
  "$python_bin" -m venv "$project_dir/.venv"
fi

"$project_dir/.venv/bin/pip" install -r "$project_dir/requirements.txt"

echo "环境已安装。首次模型下载："
echo "  $project_dir/.venv/bin/python $project_dir/transcribe.py <16-bit WAV>"
