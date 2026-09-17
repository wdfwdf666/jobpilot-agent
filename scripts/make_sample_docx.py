"""生成测试用 docx 简历：把样例 md 转成 docx，用于验证 docx 解析路径。"""
import sys
from pathlib import Path

import docx

ROOT = Path(__file__).resolve().parent.parent
src = ROOT / "data" / "samples" / "sample_resume.md"
dst = ROOT / "data" / "samples" / "sample_resume.docx"

lines = src.read_text(encoding="utf-8").splitlines()
document = docx.Document()
for ln in lines:
    document.add_paragraph(ln)
document.save(str(dst))
print(f"已生成 {dst}（{len(lines)} 段）")
sys.exit(0)
