# 이상 감지 스코어러 서빙

실험(EXP-000~007)으로 확정한 이상 감지 아키텍처를 watcher 모듈에 통합한 서빙 구성입니다.
규칙 임계가 못 잡는 임계 안쪽 이상(센서 간 상관 붕괴·완만 드리프트)을 주기적으로 감지해
기존 알람·알림 파이프라인으로 발령합니다.

## 구성 요소

| 요소 | 위치 | 역할 |
| --- | --- | --- |
| 스코어러 | `watcher/detector.py` | PCAX + EWMA 경로 판정 (순수 로직) |
| 모델 저장 | `equipment_anomaly_models` 테이블 | 공정 유형별 PCAX 파라미터 (JSON) |
| 학습 CLI | `anomaly-fit` | 정상 telemetry로 적합·저장 |
| 데이터 접근 | `watcher/anomaly_repository.py` | 모델 적재·시계열 조회·알람 전이 |
| 주기 워처 | `watcher/anomaly_worker.py` | 주기(=stride) 스코어링·발령 |

## 동작 흐름

```
[anomaly-worker, 30초 주기]
   → 설비별 최근 300행 조회 → 스코어러 판정 (윈도우 60초·stride 30초·EWMA·K2)
   → 발령 시 EquipmentAlarm(WRN-901, metric=최대기여 채널) 기록
   → (기존) sync_from_alarms → 30분 dedup → notifications → SSE
```

발령 코드는 `WRN-901`(통계 이상, 심각도 주의)이며 규칙 코드(ERR/WRN-50x~80x)와 구분됩니다.
`metric`에는 T²/SPE 최대 기여 센서가 기록되어 알림·대응 계획의 설명 근거가 됩니다.

## 섀도우 모드 (BE_ANOM01_SHADOW01)

실발령 전 검증 단계입니다. `ANOMALY_SHADOW_MODE=true`(기본)면 스코어러가 실데이터에
동작하되 WRN-901을 알림 파이프라인에 쏘지 않고 `equipment_anomaly_shadow_events`
관찰 저널에만 기록합니다. 운영자 노출 없이 규칙 레이어와 비교 관찰할 수 있습니다.

```bash
uv run anomaly-shadow-report --days 7   # 섀도우 vs 규칙 비교
```

리포트는 감지를 세 가지로 분류합니다.

| 분류 | 의미 |
| --- | --- |
| detector-only | 섀도우만 발생, 규칙이 놓친 임계 안쪽 이상 후보 |
| overlapping | 섀도우·규칙 시간 겹침, 동일 이상 양쪽 감지 |
| rule-only | 규칙만 발생, 섀도우 미감지 (급성 등, 규칙 레이어 담당) |

detector-only가 실제 이상인지 오탐인지 운영자가 리뷰해 실발령 전환·파라미터 조정을
결정합니다. 이 라벨이 이후 레시피 마스킹·설비별 캘리브레이션·반지도 전환의 근거입니다.

## 배포 절차

```bash
uv run alembic upgrade head          # 이상 감지 테이블 생성
uv run anomaly-fit                   # 공정 유형별 모델 적합·저장 (정상 데이터 필요)
# 1단계 섀도우: ANOMALY_DETECTION_ENABLED=true, ANOMALY_SHADOW_MODE=true 후 재기동
#   → N주 관찰, anomaly-shadow-report로 규칙 비교
# 2단계 실발령: 검증 후 ANOMALY_SHADOW_MODE=false 후 재기동 → WRN-901 실발령
```

모델 미적재 상태에서는 워처가 즉시 반환하므로 활성화해도 무해합니다. 서빙 파라미터는
`core/config.py`의 `anomaly_*` 필드로 조정하며 기본값은 EXP-006·007 확정값입니다.

## 아키텍처 정합

- 모델 파라미터는 RDS에 저장(확정 아키텍처), pickle 대신 JSON 배열 직렬화로 버전 견고
- baseline·임계가 RDS에 있어 재적합(`anomaly-fit` 재실행)만으로 drift 갱신 가능
- 규칙 레이어는 유지되며 본 스코어러는 보완 레이어 (급성은 규칙이 즉시 담당)

## 남은 과제 (후속)

- 레시피 컨텍스트 마스킹: 점화·램프업 모드 전이 오탐 억제 (EXP-007 지목 최대 오탐원)
- 설비별 캘리브레이션: 동일 공정 내 개체 차 반영 (실측 데이터 확보 후)
- baseline drift 자동 갱신: 정비 이벤트 트리거 재적합
- 섀도우 모드: 실알람 미발령·로깅만으로 운영자 피드백 수집 후 반지도 전환
