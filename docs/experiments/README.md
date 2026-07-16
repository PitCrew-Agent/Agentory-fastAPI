# 이상 감지 실험 규약

자기지도학습 기반 이상 감지 구조 도입을 위한 실험 관리 규약입니다. 모든 실험은 이 문서의
규약을 따르며, 실험 간 비교가 성립하도록 E0에서 얼린 고정 평가 세트로만 채점합니다.

## 배경과 목표

현재 위험·경고 감지는 고정 임계값·알람 코드·LLM 판단에 의존합니다. 설비별 정상 패턴 차이와
복합 이상을 반영하기 위해, 정상 운전 데이터만으로 학습하는 one-class 이상 감지 파이프라인을
실험으로 검증하고 도입합니다. 하드 안전 임계 레이어는 유지하며, 새 파이프라인은 임계가 잡지
못하는 미묘·복합 이상을 보완하는 레이어로 추가합니다. LLM은 판정에서 설명 역할로 전환합니다.

## 실험 로드맵

| ID | 실험 | 산출물 |
| --- | --- | --- |
| EXP-000 | 고정 평가 세트 구축 (simulator 시나리오 기반 이상 주입) | data/eval/v1 (완료) |
| EXP-001 | 현행 규칙 레이어 단독 채점 (베이스라인) | 베이스라인 지표 (완료) |
| EXP-002 | PCA MSPC (T² + SPE) | v0 후보 (완료) |
| EXP-003 | ablation (윈도우 길이·오버랩, 임계 퍼센타일) | v0 확정 config (완료) |
| EXP-004 | 평가 세트 v2 (임계 안쪽 이상) 구축·재채점 | data/eval/v2 + 사각 정량화 (완료) |
| EXP-005 | TS2Vec + kNN 거리 | v1 후보, 판정 기준은 corr_break 유예 내 감지 |
| EXP-006 | v0 vs v1 A/B + 설비별 캘리브레이션 | 채택 결정 |

## 실험 1건의 규약

- 실험 ID는 EXP-NNN, config 파일명·문서명·MLflow run 이름에 동일하게 사용합니다
- 실행 조건은 config yaml 1개로 완결하며, 코드 수정 없이 config만으로 재현 가능해야 합니다
- 재현 키는 config + git commit + seed 세 가지입니다
- 평가는 반드시 `anomaly.evaluation.summarize`로 채점합니다 (지표 정의 단일 소스)
- 지표·아티팩트는 MLflow 로컬(mlruns/)에 자동 기록하고, 해석은 실험 문서에 수동 기록합니다
- 설계 결정·시행착오·선택 과정은 [decisions.md](decisions.md)에 정량 비교와 함께 누적
  기록하며, 실험 결과로 결정이 뒤집히면 상태를 갱신합니다

## 평가 지표

| 지표 | 정의 | 용도 |
| --- | --- | --- |
| event_recall | 정답 이벤트 중 구간 내 판정 1건 이상 발생 비율 | 사건 감지 능력 |
| false_alarms_per_equipment_day | 이벤트 밖 판정 수 / (설비 x 관측일) | 운영자 신뢰 |
| detection_delay_mean/p90_ticks | 이벤트 시작부터 첫 판정까지 tick | 조기 감지 가치 |
| auc_pr | 포인트 단위 점수 순위 품질 | 임계 무관 모델 비교 |

## 실험 문서 템플릿

실험마다 `docs/experiments/EXP-NNN-<슬러그>.md`를 작성합니다.

```markdown
# EXP-NNN 실험명

## 목적·가설
(무엇을 확인하려는지, 기대 결과를 격식체로 서술)

## 설정
- config: experiments/configs/exp_NNN_*.yaml
- 데이터: data/eval/<version>
- git commit: <hash>

## 결과
(지표 표, 이전 실험 대비 정량 비교)

| 지표 | 이번 실험 | 비교 대상 (EXP-MMM) |
| --- | --- | --- |

## 결론
(채택 / 기각 / 후속 실험, 근거를 격식체로 서술)
```

## 명령어

```bash
uv sync --group experiment                              # 실험 의존성 설치 (MLflow 포함)
uv run python experiments/build_eval_set.py             # EXP-000 평가 세트 생성
uv run python experiments/run.py --config experiments/configs/exp_001_rule_baseline.yaml
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db  # 실험 기록 UI (localhost:5000)
```

## 디렉토리

```
src/anomaly/            파이프라인 라이브러리 (windowing·평가기·모델, 서빙과 공용)
experiments/            실험 실행 스크립트·config (배포 대상 아님)
experiments/results/    실험별 확정 지표 JSON (git 커밋, 문서 비교표의 단일 소스)
docs/experiments/       실험 문서 (본 규약 + decisions.md + EXP-NNN 문서)
data/eval/<version>/    얼린 평가 세트 (manifest.json으로 재현 조건 추적)
mlflow.db, mlruns/      MLflow 로컬 스토어 (전체 원본, git 추적 제외)
```
