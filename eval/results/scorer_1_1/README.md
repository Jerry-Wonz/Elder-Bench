# scorer 1.1 修正产物

生成日期：2026-09-25。

本目录是 `docs/评测修正与优化.md` 中 P0-1/P0-2 的正式修正集合：

- 读正文模式先剥离 `</think>` 之前的推理轨迹；格式解析模式保留全文。
- 每行结果含 `scorer_version=1.1`、`rule_version=1.1`。
- 长度 `n_chars` 与判分正文同源。
- 报告含覆盖率、危机 FPR、阈值来源、养老适用性、health_ref 分层与降权指标。

## 规范报告

| subject | 报告 | 覆盖 | 门禁 | 现状 |
|---|---|---|---|---|
| glm53_flash | `report_glm53_flash_scorer1.1_20260925_013512.md` | 10 卷，正例覆盖 97.86% | INSUFFICIENT | 历史对象，2026-10-10 移至 `eval/_archive/` |
| qwen35_4b_base | `report_qwen35_4b_base_scorer1.1_20260925_015042.md` | 10 卷，100% | FAIL（FPR） | 历史对象，2026-10-10 移至 `eval/_archive/` |
| qwen35_4b_deploy_v2 | `report_qwen35_4b_deploy_v2_scorer1.1_20260925_013735.md` | 10 卷，100% | FAIL（漏报） | 活动对象，仍在本目录 |

并排对比：现行 `compare_20260930_105544.md`；`compare_20260928_112705.md` 已随历史对象一并移至 `eval/_archive/`。

## 历史口径

2026-09-25 之前的 `report_*.json/md` 使用 scorer 1.0。qwen 列的 `response` 混有推理轨迹，
旧 NLG、规则命中与长度分箱不得继续作为对外结论；文件保留用于追溯和历史回归。
v1 deploy/ablation 的 prompt、结果、报告和 manifest 已于 2026-09-28 按 H8 删除。

2026-10-10 清理：scorer 1.0 时代报告、历史对象（`glm53_flash`、`qwen35_4b_base`）的
结果与报告已移至 `eval/_archive/`（未入库）；本目录保留活动对象的 scorer 1.1 产物。

## 发布限制

报告中的 `publication_status` 依据生成时 Git 工作树计算。若为
`NOT_RELEASABLE_DIRTY_WORKTREE`，须在代码提交后的干净工作树重新运行 `rescore` 才能正式发布。
