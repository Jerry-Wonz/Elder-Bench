---
dataset_info:
  features:
  - name: id
    dtype: int64
  - name: topic
    dtype: string
  - name: messages
    list:
    - name: content
      dtype: string
    - name: role
      dtype: string
  splits:
  - name: train
    num_bytes: 623153158
    num_examples: 248253
  - name: test
    num_bytes: 25110856
    num_examples: 10000
  - name: human_validation
    num_bytes: 252690
    num_examples: 100
  download_size: 365633556
  dataset_size: 648516704
configs:
- config_name: default
  data_files:
  - split: train
    path: data/train-*
  - split: test
    path: data/test-*
  - split: human_validation
    path: data/human_validation-*
---
