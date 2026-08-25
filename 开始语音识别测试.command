#!/bin/zsh

project_dir="${0:A:h}"
cd "$project_dir" || exit 1

clear
echo "Mac 本地语音识别测试"
echo "========================"
echo "程序将录音 10 秒，然后在本机离线转写。"
echo "如果 macOS 询问麦克风权限，请选择“允许”。"
echo

./run.sh 10
status=$?

echo
if [[ $status -eq 0 ]]; then
  echo "测试完成。"
else
  echo "测试失败，退出码: $status"
fi
echo "按任意键关闭窗口…"
read -k 1
exit $status
