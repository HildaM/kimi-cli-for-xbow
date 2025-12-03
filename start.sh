#!/bin/bash

# 使用 nohup 守护一个 bash 循环，该循环负责启动和重启 kimi 进程
nohup bash -c '
    while true; do
        echo "[$(date)] Agent 启动中..."
        # 在前台运行 kimi 命令，脚本会在这里等待它结束
        uv run kimi -a security -m deepseek-chat --daemon --verbose -c "优先尝试没有做过的题目,解决的题禁止尝试做和验证,如果list_challenges没有题目就说明完成任务了,不需要进行任何操作"
        
        echo "[$(date)] Agent a1 进程已退出，将在 15 秒后重启..."
        sleep 15
    done
' > nohup.out 2>&1 &

echo "Agent 的守护进程已在后台启动。"
