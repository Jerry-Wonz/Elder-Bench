# 当前三维度模型对比

ISO subjects use temperature=0, top_p=1, max_tokens=8192, seed=20260910, no persona.
Ablation uses base + persona v2, temperature=0, top_p=1, max_tokens=8192, seed=20260910.
Deployment uses SFT + persona v2, temperature=0.15, max_tokens=3072, seed=20260910.
Truncation and empty response are counted as failures, not excluded.

| subject | medical health_ref ROUGE-L | medical health_calc accuracy | medical health_hallu F1 | risk_crisis recall | risk_crisis FPR | risk_response dual-missing rate | elder_safety omit rate | elder_refusal over-refusal rate | finish_reason=length rate | empty content rate |
|---|---|---|---|---|---|---|---|---|---|---|
| deepseek_flash_iso | 17.69 | 77.5 | 84.12 | 85.86 | 10.25 | 0.99 | 42.4 | 0.0 | 2.6515 | 1.7677 |
| qwen35_4b_base_iso | 17.54 | 50.5 | 81.46 | 84.7 | 9.0 | 5.43 | 57.6 | 0.4 | 1.9444 | 0.0 |
| qwen35_4b_sft_iso | 19.75 | 63.0 | 85.71 | 87.99 | 9.25 | 32.73 | 83.0 | 0.2 | 0.5051 | 0.0 |
| qwen35_4b_ablation_v2 | 20.25 | 53.0 | 80.68 | 89.64 | 11.25 | 20.89 | 77.2 | 0.6 | 0.5303 | 0.0 |
| qwen35_4b_deploy_v2_r1 | 16.02 | 55.5 | 86.82 | 79.28 | 7.0 | 65.3 | 93.2 | 0.0 | 0.3535 | 0.0 |
