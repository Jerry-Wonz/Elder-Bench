---
dataset_info:
- config_name: pqa_artificial
  features:
  - name: Question
    dtype: string
  - name: Knowledge
    sequence: string
  - name: Ground Truth
    dtype: string
  - name: Difficulty Level
    dtype: string
  - name: Hallucinated Answer
    dtype: string
  - name: Category of Hallucination
    dtype: string
  splits:
  - name: train
    num_bytes: 18206610
    num_examples: 9000
  download_size: 10283429
  dataset_size: 18206610
- config_name: pqa_labeled
  features:
  - name: Question
    dtype: string
  - name: Knowledge
    sequence: string
  - name: Ground Truth
    dtype: string
  - name: Difficulty Level
    dtype: string
  - name: Hallucinated Answer
    dtype: string
  - name: Category of Hallucination
    dtype: string
  splits:
  - name: train
    num_bytes: 1979216
    num_examples: 1000
  download_size: 1116475
  dataset_size: 1979216
configs:
- config_name: pqa_artificial
  data_files:
  - split: train
    path: pqa_artificial/train-*
- config_name: pqa_labeled
  data_files:
  - split: train
    path: pqa_labeled/train-*
---

# Dataset Card for MedHallu

MedHallu is a comprehensive benchmark dataset designed to evaluate the ability of large language models to detect hallucinations in medical question-answering tasks. 

## Dataset Details

### Dataset Description

MedHallu is intended to assess the reliability of large language models in a critical domain—medical question-answering—by measuring their capacity to detect hallucinated outputs. The dataset includes two distinct splits:

- **pqa_labeled:** 1,000 high-quality samples derived from PubMedQA pqa_labeled split.
- **pqa_artificial:** 9,000 samples generated from PubMedQA pqa_artificial split.

- **Curated by:** UTAustin-AIHealth  
- **Language(s) (NLP):** English  
- **License:** [MIT License](https://opensource.org/license/mit/)


### Dataset Sources

- **Paper:** *MedHallu: A Comprehensive Benchmark for Detecting Medical Hallucinations in Large Language Models*  
- **Original Dataset:** Derived from PubMedQA ["qiaojin/PubMedQA"](https://huggingface.co/datasets/qiaojin/PubMedQA)

## Setup Environment

To work with the MedHallu dataset, please install the Hugging Face `datasets` library using pip:

```bash
pip install datasets
```

## How to Use MedHallu

**Downloading the Dataset:**  
```python
from datasets import load_dataset

# Load the 'pqa_labeled' split: 1,000 high-quality, human-annotated samples.
medhallu_labeled = load_dataset("UTAustin-AIHealth/MedHallu", "pqa_labeled")

# Load the 'pqa_artificial' split: 9,000 samples generated via an automated pipeline.
medhallu_artificial = load_dataset("UTAustin-AIHealth/MedHallu", "pqa_artificial")
```

## Dataset Structure

MedHallu is organized into two main splits:

- **pqa_labeled:** 1,000 high-quality samples derived from PubMedQA pqa_labeled split.
- **pqa_artificial:** Contains 9,000 samples generated from PubMedQA pqa_artificial split.

Each sample includes a medical question along with its corresponding ground truth answer, hallucinated answer, difficulty level, and the category of hallucination.

## Source Data

The MedHallu dataset is built upon data sourced from the PubMedQA dataset.

## Citation

If you use the MedHallu dataset in your research, please consider citing our work:

```
@misc{pandit2025medhallucomprehensivebenchmarkdetecting,
      title={MedHallu: A Comprehensive Benchmark for Detecting Medical Hallucinations in Large Language Models}, 
      author={Shrey Pandit and Jiawei Xu and Junyuan Hong and Zhangyang Wang and Tianlong Chen and Kaidi Xu and Ying Ding},
      year={2025},
      eprint={2502.14302},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2502.14302}, 
}
```
