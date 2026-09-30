# H6 消融与部署组合汇总

| 指标 | base 无人格 | base + persona v2 | SFT 无人格 | SFT + persona v2 部署态 |
|---|---|---|---|---|
| medical health_ref ROUGE-L | 17.54 | 20.25 | 19.75 | 16.02 |
| medical health_calc accuracy | 50.5 | 53.0 | 63.0 | 55.5 |
| medical health_hallu F1 | 81.46 | 80.68 | 85.71 | 86.82 |
| risk_crisis recall | 84.7 | 89.64 | 87.99 | 79.28 |
| risk_crisis FPR | 9.0 | 11.25 | 9.25 | 7.0 |
| risk_response dual-missing rate | 5.43 | 20.89 | 32.73 | 65.3 |
| elder_safety omit rate | 57.6 | 77.2 | 83.0 | 93.2 |
| elder_refusal over-refusal rate | 0.4 | 0.6 | 0.2 | 0.0 |
| finish_reason=length rate | 1.9444 | 0.5303 | 0.5051 | 0.3535 |
| empty content rate | 0.0 | 0.0 | 0.0 | 0.0 |
- base_no_persona, base_persona_v2 and sft_no_persona use temperature=0, max_tokens=8192.
- sft_persona_v2_deploy_r1 uses the actual deployment setting temperature=0.15, max_tokens=3072, so the difference includes both persona and decoding changes.
- The controlled comparison for persona v2 is base_no_persona vs base_persona_v2.
- The controlled comparison for LoRA weight is base_no_persona vs sft_no_persona.
- The SFT plus persona arm is an actual deployment configuration, not a pure prompt ablation.