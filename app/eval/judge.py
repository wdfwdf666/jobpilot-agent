"""LLM-as-judge 评测（第 2 周填充）。

计划：
- eval/dataset.jsonl：20-30 组 {jd, resume, expected_points} 标注数据
- eval/judge.py：judge prompt + 评分聚合
- eval/testset_retrieval.jsonl：20 组检索测试对，量化 V1 向量 vs V2 混合检索
"""
