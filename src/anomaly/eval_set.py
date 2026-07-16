"""E0 고정 평가 세트 생성·적재 (EXP-000)

simulator의 순수 생성 로직(generate_reading)을 DB 없이 재사용해 정답 이벤트가
라벨링된 평가 세트와 정상 전용 학습 세트를 생성

설계 원칙
- 학습 세트는 평가 세트와 seed·설비 ID를 격리해 정보 누출 차단
- 정답 이벤트는 (설비, 이상 유형, 대상 변수 집합) 단위, 시작 tick은 drift_start_tick
- 같은 config면 항상 동일 산출물, 설비별 독립 rng로 세그먼트 추가·삭제에도 재현 유지
- 규칙 레이어 판정(alarm_code·변수별 코드)을 함께 저장, E1 베이스라인 채점에 사용
"""

import json
import random
import subprocess
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from anomaly import synthetic
from anomaly.evaluation import AnomalyEvent
from simulator.generator import VARS, generate_reading
from simulator.scenarios import SCENARIOS

# WRN-801 이동창 판정용 최근값 길이, simulator main의 deque(maxlen=8)과 동일
HISTORY_LEN = 8

EVAL_FILE = "eval.parquet"
TRAIN_FILE = "train_normal.parquet"
EVENTS_FILE = "events.json"
MANIFEST_FILE = "manifest.json"


@dataclass(frozen=True)
class SegmentSpec:
    """세그먼트 1개 = 설비 1대 x 시나리오 1개의 연속 관측 구간

    generator: simulator(독립 채널, 규칙 가시 이상) | synthetic(결합 채널, 임계 안쪽 이상)
    """

    equipment_id: str
    process_type: str
    scenario: str
    generator: str = "simulator"


def _generate_segment(
    spec: SegmentSpec, n_ticks: int, drift_start_tick: int, seed: int
) -> list[dict]:
    if spec.generator == "synthetic":
        return synthetic.generate_rows(
            spec.equipment_id, spec.scenario, n_ticks, drift_start_tick, seed
        )
    # 설비별 독립 rng, 다른 세그먼트 추가·삭제가 이 세그먼트 값에 영향 주지 않음
    rng = random.Random(f"{seed}:{spec.equipment_id}")
    history: deque = deque(maxlen=HISTORY_LEN)
    scenario = SCENARIOS[spec.scenario]
    rows: list[dict] = []
    for tick in range(n_ticks):
        reading = generate_reading(
            spec.equipment_id,
            spec.process_type,
            scenario=scenario,
            tick=tick,
            drift_start_tick=drift_start_tick,
            history=list(history),
            rng=rng,
        )
        history.append(reading)
        row: dict = {
            "equipment_id": spec.equipment_id,
            "tick": tick,
            "scenario": spec.scenario,
            "alarm_code": reading.alarm_code,
        }
        for var in VARS:
            row[var] = float(getattr(reading, var))
            row[f"alarm_{var}"] = reading.alarm_codes.get(var)
        rows.append(row)
    return rows


def _segment_events(spec: SegmentSpec, n_ticks: int, drift_start_tick: int) -> list[AnomalyEvent]:
    # 시나리오 정의에서 정답 이벤트 도출, normal·ignition(정상 라벨)은 빈 목록
    if spec.generator == "synthetic":
        if spec.scenario not in synthetic.EVENT_KINDS:
            return []
        kind, variables = synthetic.EVENT_KINDS[spec.scenario]
        return [
            AnomalyEvent(
                equipment_id=spec.equipment_id,
                kind=kind,
                variables=variables,
                start_tick=drift_start_tick,
                end_tick=n_ticks - 1,
            )
        ]
    scenario = SCENARIOS[spec.scenario]
    events: list[AnomalyEvent] = []
    for kind, variables in (
        ("acute", scenario.acute_vars),
        ("drift", scenario.drift_vars),
        ("variance", scenario.variance_vars),
    ):
        if variables:
            events.append(
                AnomalyEvent(
                    equipment_id=spec.equipment_id,
                    kind=kind,
                    variables=tuple(variables),
                    start_tick=drift_start_tick,
                    end_tick=n_ticks - 1,
                )
            )
    return events


def _git_commit() -> str | None:
    # 재현 추적용 커밋 해시, git 미사용 환경이면 None
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def build_eval_set(config: dict, out_dir: Path) -> dict:
    """config 기준 평가·학습 세트 생성 후 out_dir 저장, manifest 반환

    산출물: eval.parquet, train_normal.parquet, events.json, manifest.json
    """
    eval_cfg = config["eval"]
    train_cfg = config["train"]

    eval_rows: list[dict] = []
    events: list[AnomalyEvent] = []
    n_ticks, drift_start = eval_cfg["n_ticks"], eval_cfg["drift_start_tick"]
    for raw in eval_cfg["segments"]:
        spec = SegmentSpec(**raw)
        eval_rows.extend(_generate_segment(spec, n_ticks, drift_start, config["seed"]))
        events.extend(_segment_events(spec, n_ticks, drift_start))

    train_rows: list[dict] = []
    for raw in train_cfg["equipments"]:
        # 학습 세트는 전 구간 normal, drift_start 무의미하므로 0 고정
        spec = SegmentSpec(scenario="normal", **raw)
        train_rows.extend(_generate_segment(spec, train_cfg["n_ticks"], 0, train_cfg["seed"]))

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(eval_rows).to_parquet(out_dir / EVAL_FILE, index=False)
    pd.DataFrame(train_rows).to_parquet(out_dir / TRAIN_FILE, index=False)
    (out_dir / EVENTS_FILE).write_text(
        json.dumps([asdict(e) for e in events], ensure_ascii=False, indent=2)
    )
    manifest = {
        "version": config["version"],
        "git_commit": _git_commit(),
        "config": config,
        "eval_rows": len(eval_rows),
        "train_rows": len(train_rows),
        "events": len(events),
    }
    (out_dir / MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def load_eval_frame(root: Path) -> pd.DataFrame:
    """평가 세트 본체 적재"""
    return pd.read_parquet(root / EVAL_FILE)


def load_train_frame(root: Path) -> pd.DataFrame:
    """정상 전용 학습 세트 적재"""
    return pd.read_parquet(root / TRAIN_FILE)


def load_events(root: Path) -> list[AnomalyEvent]:
    """정답 이벤트 목록 적재"""
    raw = json.loads((root / EVENTS_FILE).read_text())
    return [
        AnomalyEvent(
            equipment_id=e["equipment_id"],
            kind=e["kind"],
            variables=tuple(e["variables"]),
            start_tick=e["start_tick"],
            end_tick=e["end_tick"],
        )
        for e in raw
    ]


def load_manifest(root: Path) -> dict:
    """생성 manifest 적재 (config 스냅샷·커밋 해시·행 수)"""
    return json.loads((root / MANIFEST_FILE).read_text())
