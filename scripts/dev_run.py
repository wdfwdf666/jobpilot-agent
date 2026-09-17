"""诊断用包装器：完整捕获子进程 stdout/stderr 到文件（本机 shell shim 不完整）。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
args = sys.argv[1:]  # python 解释器之后的参数：脚本路径 + 其参数
cmd = [sys.executable] + args
proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
out = f"exit: {proc.returncode}\n--- STDOUT ---\n{proc.stdout}\n--- STDERR ---\n{proc.stderr}"
(ROOT / "_check.txt").write_text(out, encoding="utf-8")
print(out)
