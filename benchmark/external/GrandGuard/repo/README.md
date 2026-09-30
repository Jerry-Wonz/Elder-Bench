# GrandGuard

Code for *GrandGuard: Taxonomy, Benchmark, and Safeguards for Elderly-Chatbot Interaction Safety*.

## Structure

```
├── config.yaml                          # API keys, models, training hyperparams
├── data/
│   └── elderlysafe_final.parquet         # Benchmark dataset (3,249 × 2 prompts + 1,953 × 2 responses)
├── src/
│   ├── config.py                        # Config loader
│   ├── taxonomy.py                      # 50 risk types, 3-level hierarchy
│   ├── llm/                             # LLM clients (OpenAI, Anthropic, Google, DeepSeek, Qwen, xAI)
│   ├── data/                            # Dataset loading, train/eval splits, external data
│   ├── generation/                      # Prompt generation (B3), judge filter (B4), safe alternatives (B2)
│   ├── evaluation/                      # Response judge (B5), hybrid labeling, knowledge-action gap
│   └── safeguards/
│       ├── llamaguard/                  # Fine-tuned Llama-Guard-3 (LoRA)
│       ├── policy_enhanced/             # Elderly-sensitive policy + routing
│       └── agent/                       # GrandGuard Agent (3-stage pipeline)
├── scripts/                             # 01–12 numbered pipeline scripts
└── outputs/
    ├── results/                         # JSON/CSV results
    ├── models/elderly-guard/            # LoRA checkpoint
    └── figures/                         # Generated plots
```

## Setup

```bash
pip install -r requirements.txt
```

Set API keys as environment variables (see `config.yaml` for required keys).

## Scripts

| # | Script | What it does |
|---|--------|-------------|
| 01 | `analyze_dataset` | Dataset stats |
| 02 | `generate_prompts` | Unsafe prompt generation (Box B3, Grok-4) |
| 03 | `filter_prompts` | LLM-judge filtering (Box B4, GPT-5.1) |
| 04 | `generate_safe_alternatives` | Safe rewriting (Box B2) |
| 05 | `collect_responses` | Query 10 target LLMs |
| 06 | `evaluate_responses` | Dual-judge evaluation (Box B5) |
| 07 | `knowledge_action_gap` | PA / RS / RC / Gap |
| 08 | `evaluate_baselines` | Existing safeguard baselines |
| 09 | `train_llamaguard` | LoRA fine-tune Llama-Guard-3 |
| 10 | `evaluate_safeguards` | Evaluate fine-tuned + policy-enhanced |
| 11 | `run_grandguard_agent` | 3-stage agent pipeline |
| 12 | `ablation_study` | Ablation experiments |

## Citation

```bibtex
@inproceedings{fan2026grandguard,
  title={GrandGuard: Taxonomy, Benchmark, and Safeguards for Elderly-Chatbot Interaction Safety},
  author={Fan, Changxuan and Yang, Xi and Zheng, Yueyuan and Zhou, Bin and Wang, Yuanping and Hu, Wenbin and Jing, Huihao and Hung, Ki Sen and Du, Dazhao and Li, Haoran and Hsiao, Janet Hui-wen and Song, Yangqiu},
  booktitle={Findings of the Association for Computational Linguistics: ACL 2026},
  year={2026}
}
```
