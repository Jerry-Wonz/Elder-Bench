---
license: cc-by-sa-4.0
task_categories:
- question-answering
language:
- en
tags:
- medical
pretty_name: MedCalc-Bench-v1.2
size_categories:
- 1K<n<10K
configs:
- config_name: default
  default: true
  data_files:
  - split: train
    path: train_data_11_18_final.csv
  - split: test
    path: test_data_11_18_final.csv
dataset_info:
  features:
  - name: Row Number
    dtype: string
  - name: Calculator ID
    dtype: string
  - name: Calculator Name
    dtype: string
  - name: Category
    dtype: string
  - name: Output Type
    dtype: string
  - name: Note ID
    dtype: string
  - name: Note Type
    dtype: string
  - name: Patient Note
    dtype: string
  - name: Question
    dtype: string
  - name: Relevant Entities
    dtype: string
  - name: Ground Truth Answer
    dtype: string
  - name: Lower Limit
    dtype: string
  - name: Upper Limit
    dtype: string
  - name: Ground Truth Explanation
    dtype: string
  splits:
  - name: train
    num_examples: 10543
  - name: test
    num_examples: 1100
---

> [!Note]
> Please visit MedCalc-Bench Verified at this url: https://github.com/nikhilk7153/MedCalc-Bench-Verified for the latest changes. Here is the HuggingFace link: https://huggingface.co/datasets/nsk7153/MedCalc-Bench-Verified.
> The first version of MedCalc-Bench Verified is an update from v1.2 on this repository.

<br>
MedCalc-Bench is the first medical calculation dataset used to benchmark LLMs ability to serve as clinical calculators. Each instance in the dataset consists of a patient note, a question asking to compute a specific clinical value, a final answer value, and a step-by-step solution explaining how the final answer was obtained. Our dataset covers 55 different calculation tasks which are either rule-based calculations or are equation-based calculations. This dataset contains a training dataset of 10,543 instances and a testing dataset of 1,100 instances.
In all, we hope that our dataset and benchmark serves as a call to improve the computational reasoning skills of LLMs in medical settings. 

The codebase for this repository can be found at: https://github.com/ncbi-nlp/MedCalc-Bench
<br>

## MedCalc-Bench Dataset


Each Instance in the dataset contains the following information: 

- **Row Number**: Specifies the index of the instance.
- **Calculator ID**: Specifies the integer ID of the calculator.
- **Calculator Name**: Specifies the name of the clinical calculation task.
- **Category**: Specifies the sub-category of the calculator. For equation-based calculators, the options are lab test, dosage, date, or physical and for rule-based calculators, the options are risk, severity, and diagnosis.
- **Output Type**: Specifies the format type that the calculator will return. The options are decimal, integer, date (MM/DD/YY), or time in terms of weeks and days (i.e. (17 weeks, 4 days)).
- **Note ID**: Specifies the ID of the patient note. The ID of the note will either be the ID given by Open-Patients or it will be an integer value if the patient note was handwritten by clinicians or synthesized by a template.
- **Note Type**: Specifies whether the patient note was synthesized by a clinician (LLM generated and then edited/approvied by a clinician), produced from a template (Template), or was extracted from PMC-Patients (extracted).
- **Patient Note**: Specifies the patient note which provides the information needed to compute the final answer.
- **Question**: Specifies the question that is asked to the model to compute a specific medical value based on a particular calculator.
- **Relevant Entities**: Provides a dictionary of the parameters and their extracted values based on the patient note.
- **Ground Truth Answer**: Specifies the ground truth value without any units for the medical value that needs to be calculated.
- **Lower Limit**: For equation-based calculators whose output is a decimal, this value is 95% of the ground truth answer value. For all other cases, the lower limit is the same as the ground-truth value.
- **Upper Limit**: For equation-based calculators whose output is a decimal, this value is 105% of the ground truth answer value. For all other cases, the upper limit is the same as the ground-truth value.
- **Ground Truth Explanation**: The ground truth explanation for the data instance providing a step-by-step explanation for how the final answer was obtained.

## License 

Depending on the calculator, our dataset consists of notes that were either designed from templated-based functions implemented in Python, handwritten by clinicians, or taken from our dataset, Open-Patients. 

Open-Patients is an aggregated dataset of 180k patient notes coming from three different sources. We have authorization to use the dataset from all three sources. The first source is the USMLE questions from MedQA which is released under the MIT License. 
The second source of our dataset are the Trec Clinical Decision Support and Trec Clinical Trial which are available for redistribution because they are both government-owned datasets released to the public. Lastly, PMC-Patients is released under the CC-BY-SA 4.0 license and so we have permission to incorporate PMC-Patients inside Open-Patients and MedCalc-Bench, but the dataset must be released under the same lisense. Hence, our source of notes, Open-Patients, and the dataset curated from it, MedCalc-Bench, are both released under the CC-BY-SA 4.0 license. 

Based on the justification of license rules, both Open-Patients and MedCalc-Bench comply with the CC-BY-SA 4.0 license, but the authors of this paper will bear all responsibility in case of violation of rights. 
