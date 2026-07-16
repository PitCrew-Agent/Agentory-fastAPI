"""anomaly.eval_set 단위 테스트, 소형 config로 생성·적재·결정론 검증"""

from anomaly.eval_set import (
    build_eval_set,
    load_eval_frame,
    load_events,
    load_manifest,
    load_train_frame,
)

TINY_CONFIG = {
    "version": "test",
    "seed": 42,
    "tick_seconds": 5.0,
    "train": {
        "seed": 43,
        "n_ticks": 30,
        "equipments": [{"equipment_id": "TRN-T01", "process_type": "Etching"}],
    },
    "eval": {
        "n_ticks": 50,
        "drift_start_tick": 10,
        "segments": [
            {"equipment_id": "EVL-T01", "process_type": "Etching", "scenario": "normal"},
            {
                "equipment_id": "EVL-T02",
                "process_type": "Etching",
                "scenario": "temp_acute_pressure_drift",
            },
        ],
    },
}


def test_build_eval_set_outputs_and_labels(tmp_path):
    manifest = build_eval_set(TINY_CONFIG, tmp_path)

    frame = load_eval_frame(tmp_path)
    assert len(frame) == 100  # 세그먼트 2개 x 50 tick
    expected_cols = {
        "equipment_id",
        "tick",
        "scenario",
        "alarm_code",
        "temperature",
        "pressure",
        "rf_power",
        "gas_flow",
        "alarm_temperature",
        "alarm_pressure",
        "alarm_rf_power",
        "alarm_gas_flow",
    }
    assert expected_cols <= set(frame.columns)

    # 복합 시나리오 → acute(temperature) + drift(pressure) 이벤트 2건, normal은 0건
    events = load_events(tmp_path)
    assert len(events) == 2
    kinds = {(e.kind, e.variables) for e in events}
    assert kinds == {("acute", ("temperature",)), ("drift", ("pressure",))}
    assert all(e.equipment_id == "EVL-T02" for e in events)
    assert all(e.start_tick == 10 and e.end_tick == 49 for e in events)

    train = load_train_frame(tmp_path)
    assert len(train) == 30
    assert (train["scenario"] == "normal").all()

    assert load_manifest(tmp_path)["events"] == manifest["events"] == 2


def test_build_eval_set_is_deterministic(tmp_path):
    build_eval_set(TINY_CONFIG, tmp_path / "a")
    build_eval_set(TINY_CONFIG, tmp_path / "b")
    frame_a = load_eval_frame(tmp_path / "a")
    frame_b = load_eval_frame(tmp_path / "b")
    assert frame_a.equals(frame_b)


def test_acute_injection_visible_after_drift_start(tmp_path):
    build_eval_set(TINY_CONFIG, tmp_path)
    frame = load_eval_frame(tmp_path)
    target = frame[frame["equipment_id"] == "EVL-T02"]
    # 급성 주입 후 온도가 밴드 밖 계단 이탈, 주입 전 대비 뚜렷한 상승
    before = target[target["tick"] < 10]["temperature"].mean()
    after = target[target["tick"] >= 10]["temperature"].mean()
    assert after > before + 1.0
