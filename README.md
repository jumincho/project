# Forward-Looking Guidance RAG 코드 최적화

석사 연구에서 사용 중인 멀티홉 QA 코드(**Forward-Looking Guidance**, 계획 기반 반복 검색 RAG)의
**호스트 측 공통 유틸리티**를 대상으로, 수업에서 배운 자료구조 · 반복자/제너레이터 · 클래스 설계 ·
데코레이터 기법을 적용해 구조와 성능을 개선한 과제 저장소입니다.

모델(vLLM/Qwen3-8B, OpenAI)과 검색(Contriever+FAISS) 자체는 건드리지 않았기 때문에 평가 점수(EM/F1)는
변하지 않으며, 최적화 전후 함수의 출력이 동일함을 무작위 입력 20,000건(총 60,500회 비교, 불일치 0건)으로
확인했습니다(`results/equivalence.json`, `benchmark/run_benchmark.py`의 `exp_equivalence`). 모든 벤치마크는
**CPU에서만** 돌아가고 GPU·네트워크·모델 가중치가 필요 없습니다.

## 무엇을 바꿨나

| 분류 | 대상 | 변경 | 핵심 결과 (CPU 실측) |
|------|------|------|------|
| **A. 자료구조/복잡도** | `normalize_answer`의 `remove_punc` | `set()`+제너레이터 → `str.translate` + 정규식 사전 컴파일 | 입력 크기 무관 **약 1.6배** |
| **B. 제너레이터/지연평가** | `read_jsonl` | 한 행씩 내보내는 `iter_jsonl` 분리, 스트리밍 fold | 80k행 코퍼스 통계에서 **피크 메모리 약 3.3배 절감** (+시간 ~30% 단축) |
| **C. 클래스 설계 (SRP)** | `LLMEngine` | 설정 `dataclass` + `Backend` Protocol + 레지스트리/팩토리로 분리 | `is_openai` 런타임 분기 **4→1**, 새 백엔드 추가 시 수정 메서드 **3→0** |
| **D. 데코레이터** | `normalize_answer`, API 호출 | `lru_cache` + `timed`/`retry`(`functools.wraps`) | 캐시: 반복도에 따라 **2.7배~20배** |

> 부가로 `docs_to_str`의 `s +=`를 `join`으로 바꿨으나, CPython의 in-place `+=` 최적화 때문에
> **속도 이득은 없었습니다**(보고서 6.4에 정직하게 분석). 안전한 관용구라 유지했습니다.

## 저장소 구조

```
.
├─ README.md
├─ requirements.txt          # 벤치/보고서 도구 (numpy, matplotlib, reportlab) — CPU 전용
├─ CHANGES_common_diff.txt   # before/after common.py 통합 diff (전후 비교 근거)
├─ src/
│  ├─ before/                # 원본 코드 (그대로)
│  └─ after/                 # 최적화 코드 (공개 API 동일)
├─ benchmark/
│  ├─ run_benchmark.py       # 측정 → results/benchmark_results.csv, env.json
│  └─ make_figures.py        # CSV → results/figures/*.png
├─ results/
│  ├─ benchmark_results.csv
│  ├─ env.json
│  ├─ decorator_semantics.json
│  └─ figures/
└─ report/
   ├─ build_report.py        # 보고서 PDF 생성기 (한글, reportlab)
   └─ report.pdf             # 제출 보고서
```

## 재현 방법

```bash
pip install -r requirements.txt

python benchmark/run_benchmark.py     # 측정 (CPU, 수 분) → results/*.csv
python benchmark/make_figures.py      # 그래프 생성 → results/figures/
python report/build_report.py         # 보고서 PDF 재생성 (선택)
```

최적화 **전후 비교**는 다음 중 아무거나로 확인할 수 있습니다.

```bash
# 1) 코드 차이
diff -u src/before/common.py src/after/common.py     # (CHANGES_common_diff.txt 와 동일)

# 2) 동작 동일성: 같은 입력에 대해 평가 점수가 완전히 같음
python src/before/evaluate.py <preds.jsonl> --aliases --by_hop
python src/after/evaluate.py  <preds.jsonl> --aliases --by_hop
```

## 측정 환경

`results/env.json`에 자동 기록됩니다(OS, Python, CPU, 라이브러리 버전, 반복 횟수, seed, GPU 미사용 등).
보고서 표지와 7절에도 동일하게 적혀 있습니다.

## 원본 연구 코드

`src/before/README.md`에 원본 파이프라인(데이터 준비 → 인덱스 빌드 → 방법×데이터셋 행렬 실행 → 평가)
설명이 그대로 들어 있습니다. 본 과제는 그 위에서 평가/입출력/엔진 유틸리티만 개선한 것입니다.
