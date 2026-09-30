# DeepSeek Flash 基线迁移验收报告

## 模型与运行
- model: `deepseek-flash`
- full ISO run: 3960 rows
- report: `/data/wangzeyu/ElderBench/eval/results/report_deepseek_flash_iso_20260930_013314.json`

## 运行结果
| rows | errors | stop | length | empty | truncation % | empty % | gates |
|---|---|---|---|---|---|---|---|
| 3960 | 0 | 3855 | 105 | 70 | 2.6515 | 1.7677 | {'overall': 'FAIL', 'coverage': 'OK', 'secondary_response_crisis': 'OK'} |

## 12 条失败任务阶梯
| max_tokens | ok | stop | length | nonempty | empty |
|---|---|---|---|---|---|
| 6144 | 12 | 7 | 5 | 8 | 4 |
| 8192 | 12 | 11 | 1 | 11 | 1 |
| 9216 | 12 | 11 | 1 | 11 | 1 |

## 结论
- DeepSeek full ISO run completed with zero API errors under max_tokens=8192 and max_model_len is not exposed by /models.
- Truncated and empty responses are retained and counted as failures.
- The gateway accepted max_tokens values from 256 through 6144 in the simple ladder probe; full target-task validation used 8192.
- The old GLM results remain historical only and do not enter the new DeepSeek ranking.