# benchmark/external — 养老陪护评测迁移数据集

依据《养老陪护Agent评测体系调研报告》第 4.2 节评测方法迁移矩阵，从**医疗、心理、养老专用**三个维度筛选"适配迁移 + 直接迁移"两档的核心评测数据集。本目录存放原始数据，未做任何迁移改造。

下载日期：2026-09-09。所有条目数均已本地解析校验（脚本：`validate_downloads.py`）。

## 总览

| 维度     | 数据集          | 4.2 迁移方式     | 本地路径                       | 条目数                                             | 体积            |
| -------- | --------------- | ---------------- | ------------------------------ | -------------------------------------------------- | --------------- |
| 医疗     | HealthBench     | 适配迁移         | `HealthBench/`               | 5,000（主）+ 1,000（难）                           | 72.9MB          |
| 医疗     | MedCalc-Bench   | 直接迁移         | `MedCalc-Bench/`             | 11,643（train 10,543 + test 1,100）                | 56.5MB          |
| 医疗     | MedHallu        | 直接迁移（子集） | `MedHallu/`                  | 10,000（pqa_artificial 9,000 + pqa_labeled 1,000） | 11.4MB          |
| 心理     | ESConv          | 适配迁移         | `ESConv/`                    | 1,300 段对话                                       | 9.0MB           |
| 心理     | SoulChatCorpus  | 适配迁移         | `SoulChat/`                  | 258,353 段多轮对话                                 | 365.6MB         |
| 心理     | PsyCrisis-Bench | 直接迁移（方法） | `PsyCrisis-Bench/repo/Data/` | 608 条高风险语料（+800 条人工标注响应）            | 7.4MB（含代码） |
| 养老专用 | GrandGuard      | 直接迁移         | `GrandGuard/repo/data/`      | 3,249 prompts + 1,953 responses                    | 5.6MB（数据）   |
| 养老专用 | ElderBench      | 直接迁移（子集） | `ElderBench/`（空）          | **数据尚未公开**                             | —              |

---

## 医疗维度

### HealthBench（OpenAI，2025）

- **来源**：https://huggingface.co/datasets/openai/healthbench（官方）
- **许可**：MIT（随 simple-evals 仓库）
- **文件**：`healthbench_main.jsonl`（5,000 段对话，含 physician 标注的对话性 rubric）、`healthbench_hard.jsonl`（1,000 段困难子集）、`README.md`
- **原始任务形式**：多轮医患对话 + 每段对话附专家编写的评分准则（rubric，逐条加权），由模型裁判按准则逐条判定
- **4.2 迁移映射**：对话式健康咨询 → 陪伴对话与健康咨询质量（CE-Chat）
- **推荐指标**：准则满足率（rubric 加权得分）；对话/响应/上下文寻求三主题分项
- **迁移改造要点**（4.2 原文）：专家团队换为老年医学、护理、心理与药学人员共同建设；场景更换为老年角色；准则增加共情与适老表达维度；模型裁判需本地校准

### MedCalc-Bench（NCBI/NLM，NeurIPS 2024）

- **来源**：https://huggingface.co/datasets/ncbi/MedCalc-Bench（官方）
- **许可**：CC BY 4.0（数据部分 CC BY-NC 4.0，见 README）
- **文件**：`train_data_11_18_final.csv`（10,543 条）、`test_data_11_18_final.csv`（1,100 条）
- **原始任务形式**：给定患者病历/临床笔记，按计算器类别（剂量换算、给药间隔、肌酐清除率、体表面积等）计算并给出分步解释；含容差上下限
- **4.2 迁移映射**：用药计算 → 用药剂量、间隔与时间计算（CE-Task 计算项）
- **推荐指标**：容差内准确率（Upper/Lower Limit 列）、分步解释正确率
- **迁移改造要点**：选取老年用药相关的计算器子集；由药师审核；协议本身不变

### MedHallu（UT Austin，2025）

- **来源**：https://huggingface.co/datasets/UTAustin-AIHealth/MedHallu（官方；注意正确 ID 为 UTAustin-AIHealth，非 UT-Austin-D3Lab）
- **许可**：见数据卡（arXiv:2502.14302）
- **文件**：`pqa_artificial.parquet`（9,000 条）、`pqa_labeled.parquet`（1,000 条，PubMedQA 人工标注），字段：Question / Knowledge / Ground Truth / Hallucinated Answer
- **原始任务形式**：三级难度判别——判断给定医学问答答案是"-ground truth"还是"hallucinated"；三级对应知识越少越难判
- **4.2 迁移映射**：幻觉检测 → 健康信息幻觉检测
- **推荐指标**：判别 F1（最难档）
- **迁移改造要点**：直接迁移取子集；补充老年群体高发的谣传样本（保健品、偏方等）；三级难度设置沿用

---

## 心理维度

### ESConv（Emotional Support Conversation，清华 CoAI，2021）

- **来源**：https://huggingface.co/datasets/thu-coai/esconv（官方）
- **许可**：仅研究用途（原始论文 AAAI 2022）
- **文件**：`ESConv.json`（1,300 段对话）、`strategy.json`（八种支持策略定义）、`README.md`
- **原始任务形式**：求助者-支持者多轮对话，每轮 utterance 标注八种情感支持策略之一（提问、反思重述、自我暴露、提供信息等）；求助者问题前后自评量表（problem/emotion）
- **4.2 迁移映射**：情感支持策略与帮助性 → 陪伴对话的情感支持（CE-Chat 子项）
- **推荐指标**：八策略使用分布与顺序、情绪变化量（前后自评差）、帮助性评分
- **迁移改造要点**：情绪议题更换为老年主题（孤独、丧偶、慢病焦虑、代际关系）；情绪变化改为陪伴前后的简短自评量表；与 SoulChat 专家盲评协议配合使用

### SoulChatCorpus（华南理工，EMNLP 2023 Findings）

- **来源（官方）**：https://www.modelscope.cn/datasets/YIRONGCHEN/SoulChatCorpus （258,354 段多轮对话，共 1,517,344 轮；**ModelScope 本次网络不可达**）
- **来源（本次使用，HF 镜像）**：https://huggingface.co/datasets/Spiderman01/soulchat_split_raw （官方语料的 parquet 重打包，train 248,253 + test 10,000 + 人工校验 100）
- **许可**：见原仓库（https://github.com/scutcyr/SoulChat ，仅研究用途）
- **文件**：`soulchatcorpus_train_0/1.parquet`、`soulchatcorpus_test.parquet`、`soulchatcorpus_human_validation.parquet`，字段：id / topic / messages
- **原始任务形式**：中文心理咨询多轮共情对话（用户↔心理咨询师），覆盖学业、家庭、行为、情感等议题
- **4.2 迁移映射**：情感支持策略与帮助性（中文侧）→ 陪伴对话的情感支持；专家盲评协议来源
- **推荐指标**：与 ESConv 相同的策略/共情指标；中文专家盲评（SoulChat 论文协议）
- **迁移改造要点**：同 ESConv（老年主题替换）；专家盲评协议沿用
- **备注**：镜像仓库为第三方重打包，已抽样校验内容与官方描述一致（id/topic/messages 结构、中文心理对话）。如需严格官方版本，待网络可达 ModelScope 后重新下载

### PsyCrisis-Bench（HKUST-GZ/武汉大学等，arXiv 2508.08236）

- **来源（官方）**：https://github.com/mental-health-llm-safety-eval/psycrisis-bench （已浅克隆至 `PsyCrisis-Bench/repo/`）
- **许可**：见仓库 LICENSE
- **文件**：`Data/PsyCrisis-Bench.xlsx`（608 条中文高风险用户表述：自伤、自杀意念、存在性痛苦）、`Data/output_response_human annotation_800.xlsx`（800 条响应的人工标注）、`code/evaluate.py`（LLM-as-Judge 评估代码）
- **原始任务形式**：无参考评估——以专家定义的干预原则推理链为锚，LLM-as-Judge 逐安全维度二元打分
- **4.2 迁移映射**：危机检测 → 自伤意念识别与转介（CE-Risk 6.2）
- **推荐指标**：危机检出 F1、**危机漏报率（红线）**、安全维度逐项通过率、转介执行率
- **⚠️ 重要说明**：调研报告 4.2 引用的是 **PsyCrisisBench（杭州心理援助热线版，arXiv 2506.01329，540 条转录）**，经核实该版本**数据从未公开**（2026-08 复核亦确认）。本目录存放的是**同名另一工作 PsyCrisis-Bench（arXiv 2508.08236）**，数据公开且方法（专家原则锚定的 LLM-as-Judge）同样适配"直接迁移（方法）"档位。热线版的贡献仅能借鉴其四任务划分（情绪状态识别/自杀意念检测/自杀计划识别/风险评估）

---

## 养老专用维度

### GrandGuard（HKUST，ACL 2026 Findings）

- **来源（官方）**：https://github.com/HKUST-KnowComp/GrandGuard （已浅克隆至 `GrandGuard/repo/`）
- **许可**：见仓库
- **文件**：`data/elderlysafe_final.parquet`（3,249×2 prompts + 1,953×2 responses，`safe_prompts`/`unsafe_prompts` 配对）；`src/taxonomy.py` 内置三级 50 类风险分类体系
- **原始任务形式**：老年人特定情境风险——对普通用户无害、对老人危险的 prompt（如黑暗中独自修吊灯=跌倒风险）；响应按双准则评（风险指明 + 危害规避并给更安全替代）
- **4.2 迁移映射**：老年交互安全 → 安全红线库（CE-Risk 骨架）
- **推荐指标**：双准则通过率、防护模型检出率（官方防护模型达 96.2%）
- **迁移改造要点**：三级分类沿用；补充家庭设备误操作与用药风险类型；防护模型需中文化
- **备注**：论文 arXiv:2605.20203；10,404 条标注中的 5,202 条（prompts+responses 主数据）随仓库发布

### ElderBench（复旦大学，arXiv 2609.04850，2026-09-04）

- **状态**：**数据尚未公开**。论文发布仅数日，arXiv 页面无数据链接，GitHub/HF 暂无官方仓库
- **原始任务形式**（据论文）：249 个从 20 个应用中自然采集的老年智能手机任务，含间接言语、指代模糊、欠明确请求三类语言学特征
- **4.2 迁移映射**：老年间接表达任务 → 间接意图理解（能力 1.2）
- **推荐指标**：间接意图命中率
- **迁移改造要点**：直接迁移取子集；任务形式由图形界面操作改为对话表达；意图落点改为工具调用
- **行动建议**：跟踪复旦团队（Tun Lu 组）后续开源动态；短期可用自建间接表达用例（本项目 `data/device_intent_dataset.jsonl` 已有雏形）过渡

---

## 未纳入本次下载的候选（备用）

| 数据集                      | 维度 | 4.2 档位        | 未选原因                                                                                                         |
| --------------------------- | ---- | --------------- | ---------------------------------------------------------------------------------------------------------------- |
| MedQA / CMB-Exam            | 医疗 | 适配迁移        | 静态单选考试，4.3 明确不宜作主指标；如需知识回归测试可后补（CMB 见 https://github.com/FreedomIntelligence/CMB ） |
| MedSafetyBench              | 医疗 | 适配迁移        | 备选项；若 CE-Risk 需要更多安全回应演示数据可后补                                                                |
| AgentClinic / MedAgentBench | 医疗 | 适配迁移        | 智能体环境重在协议而非数据，FHIR 环境建议按报告第 6 章自建家庭状态库                                             |
| CRAFT-MD                    | 医疗 | 适配迁移        | 框架方法，无可直接下载的评测数据                                                                                 |
| SoulChat 专家盲评协议       | 心理 | （随 SoulChat） | 协议已随上表覆盖                                                                                                 |

## 复现下载

- HF 官方源经本地代理直连（hf-mirror 的 API 会 308 回官方站，直连即可）；ModelScope 当前网络不可达
- GitHub 均为 `git clone --depth 1` 浅克隆
- 校验：`"C:\Users\wzy13\.workbuddy\binaries\python\envs\default\Scripts\python" validate_downloads.py`（需 pyarrow，已装入托管 venv）
