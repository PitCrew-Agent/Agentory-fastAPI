"""장비별 알람 이력 조회 인덱스 성능 벤치 (NEW_ALARM01_HISTORY01/02)

별도 벤치 테이블(bench_alarm_telemetry)을 스케일업 적재해 운영 시드 테이블을 건드리지
않고, 인덱스 구성 4종 x 알람 조회 쿼리 4종 조합별로 지연을 실측
각 조합마다 EXPLAIN(ANALYZE,BUFFERS)로 플래너 실행시간·스캔 방식을, 클라이언트 반복으로
중앙값·p95를 측정하고 인덱스 크기까지 기록

실행: uv run python scripts/bench/alarm_query_index_bench.py --rows 2000000 --reps 50
전제: docker compose up -d db (또는 로컬 postgres 기동), asyncpg 설치
결과: scripts/bench/alarm_query_index_result.json 에 기록
"""

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from agentory.core.config import get_settings

TABLE = "bench_alarm_telemetry"
RESULT_PATH = Path(__file__).with_name("alarm_query_index_result.json")

# 측정 대상 파라미터 (적재 규칙과 일치해야 실제 행이 걸림)
TARGET_EQUIPMENT = "EQP-007"
TARGET_ALARM = "ERR-301"

# 인덱스 구성 4종, name->생성 DDL (none은 보조 인덱스 없이 PK만)
INDEX_CONFIGS: dict[str, str | None] = {
    "none": None,
    "equip_time": f"CREATE INDEX bench_ix_equip_time ON {TABLE} (equipment_id, timestamp)",
    "equip_time_partial": (
        f"CREATE INDEX bench_ix_equip_time_partial ON {TABLE} (equipment_id, timestamp) "
        "WHERE alarm_code IS NOT NULL"
    ),
    "equip_code_time": (
        f"CREATE INDEX bench_ix_equip_code_time ON {TABLE} (equipment_id, alarm_code, timestamp)"
    ),
}

# 알람 조회 쿼리 4종, 엔드포인트 SQL과 동형 (타임라인 3종 + 요약 1종)
QUERIES: dict[str, tuple[str, list[Any]]] = {
    "timeline_first": (
        f"SELECT * FROM {TABLE} WHERE equipment_id = $1 AND alarm_code IS NOT NULL "
        "ORDER BY timestamp DESC, log_id DESC LIMIT 11",
        [TARGET_EQUIPMENT],
    ),
    "timeline_range": (
        f"SELECT * FROM {TABLE} WHERE equipment_id = $1 AND alarm_code IS NOT NULL "
        "AND timestamp >= $2 AND timestamp <= $3 "
        "ORDER BY timestamp DESC, log_id DESC LIMIT 11",
        [
            TARGET_EQUIPMENT,
            datetime(2026, 2, 1, tzinfo=UTC),
            datetime(2026, 2, 15, tzinfo=UTC),
        ],
    ),
    "timeline_code": (
        f"SELECT * FROM {TABLE} WHERE equipment_id = $1 AND alarm_code IS NOT NULL "
        "AND alarm_code = $2 ORDER BY timestamp DESC, log_id DESC LIMIT 11",
        [TARGET_EQUIPMENT, TARGET_ALARM],
    ),
    "summary_aggregate": (
        f"SELECT alarm_code, count(*), min(timestamp), max(timestamp) FROM {TABLE} "
        "WHERE equipment_id = $1 AND alarm_code IS NOT NULL GROUP BY alarm_code "
        "ORDER BY count(*) DESC",
        [TARGET_EQUIPMENT],
    ),
}


def _dsn() -> str:
    # SQLAlchemy asyncpg URL을 asyncpg 순정 DSN으로 변환
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _rebuild_table(conn: asyncpg.Connection, rows: int) -> None:
    # 벤치 테이블 재생성 후 스케일업 적재 (generate_series로 서버측 대량 생성)
    # 20개 설비에 5초 간격 tick을 분산, 약 0.6% 행에만 알람 코드 부여 (실측 알람 희소성 반영)
    await conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    await conn.execute(
        f"""
        CREATE TABLE {TABLE} (
            log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            equipment_id varchar(50) NOT NULL,
            timestamp timestamptz NOT NULL,
            temperature numeric(5,2),
            pressure numeric(5,2),
            rf_power numeric(6,2),
            gas_flow numeric(7,2),
            alarm_code varchar(20)
        )
        """
    )
    await conn.execute(
        f"""
        INSERT INTO {TABLE}
            (equipment_id, timestamp, temperature, pressure, rf_power, gas_flow, alarm_code)
        SELECT
            'EQP-' || lpad(((g % 20) + 1)::text, 3, '0'),
            TIMESTAMPTZ '2026-01-01 00:00:00+00' + (g * interval '5 seconds'),
            60 + (random() * 10)::numeric(5,2),
            40 + (random() * 5)::numeric(5,2),
            2 + (random() * 2)::numeric(6,2),
            600 + (random() * 40)::numeric(7,2),
            CASE WHEN random() < 0.006
                THEN (ARRAY['ERR-401','ERR-301','ERR-201','ERR-402',
                            'ERR-901','WRN-501','WRN-701','WRN-801'])[1 + floor(random() * 8)::int]
                ELSE NULL END
        FROM generate_series(0, $1::bigint - 1) AS g
        """,
        rows,
    )
    await conn.execute(f"ANALYZE {TABLE}")


async def _drop_secondary_indexes(conn: asyncpg.Connection) -> None:
    # 보조 인덱스 전부 제거 (PK 제외), 구성 전환 시 초기화
    names = await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE tablename = $1 AND indexname LIKE 'bench_ix_%'",
        TABLE,
    )
    for row in names:
        await conn.execute(f"DROP INDEX IF EXISTS {row['indexname']}")


async def _index_size_bytes(conn: asyncpg.Connection) -> int:
    # 현재 보조 인덱스 총 크기 (partial 인덱스가 얼마나 작은지 평가용)
    val = await conn.fetchval(
        """
        SELECT coalesce(sum(pg_relation_size(indexrelid)), 0)
        FROM pg_stat_user_indexes WHERE relname = $1 AND indexrelname LIKE 'bench_ix_%'
        """,
        TABLE,
    )
    return int(val)


def _scan_type(plan: dict[str, Any]) -> str:
    # 플랜 트리에서 벤치 테이블을 읽는 노드의 스캔 방식 추출 (Seq/Index/Index Only/Bitmap)
    found: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        if node.get("Relation Name") == TABLE or "Scan" in node.get("Node Type", ""):
            if node.get("Relation Name") == TABLE or node.get("Index Name", "").startswith(
                "bench_ix_"
            ):
                found.append(node["Node Type"])
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    return found[0] if found else "unknown"


async def _explain_exec_ms(
    conn: asyncpg.Connection, sql: str, params: list[Any]
) -> tuple[float, str]:
    # EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) 실행시간(ms)·스캔 방식 반환
    rows = await conn.fetch(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}", *params)
    plan = json.loads(rows[0]["QUERY PLAN"])[0]
    return float(plan["Execution Time"]), _scan_type(plan["Plan"])


async def _client_latency_ms(
    conn: asyncpg.Connection, sql: str, params: list[Any], reps: int
) -> tuple[float, float]:
    # 클라이언트 왕복 반복 측정, 중앙값·p95(ms), 워밍업 3회 선행
    for _ in range(3):
        await conn.fetch(sql, *params)
    samples: list[float] = []
    for _ in range(reps):
        t0 = time.perf_counter()
        await conn.fetch(sql, *params)
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    median = statistics.median(samples)
    p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
    return median, p95


async def run(rows: int, reps: int) -> dict[str, Any]:
    conn = await asyncpg.connect(_dsn())
    try:
        print(f"[적재] {TABLE} {rows:,}행 생성 중...")
        await _rebuild_table(conn, rows)
        total = await conn.fetchval(f"SELECT count(*) FROM {TABLE}")
        alarms = await conn.fetchval(f"SELECT count(alarm_code) FROM {TABLE}")
        tgt = await conn.fetchval(
            f"SELECT count(alarm_code) FROM {TABLE} WHERE equipment_id = $1", TARGET_EQUIPMENT
        )
        print(f"[적재] 완료: 총 {total:,}행, 알람 {alarms:,}행, 대상설비 알람 {tgt:,}행")

        results: list[dict[str, Any]] = []
        for cfg_name, ddl in INDEX_CONFIGS.items():
            await _drop_secondary_indexes(conn)
            if ddl:
                await conn.execute(ddl)
            await conn.execute(f"ANALYZE {TABLE}")
            idx_kb = await _index_size_bytes(conn) // 1024
            print(f"\n[구성] {cfg_name} (보조인덱스 {idx_kb:,} KB)")
            for q_name, (sql, params) in QUERIES.items():
                exec_ms, scan = await _explain_exec_ms(conn, sql, params)
                median, p95 = await _client_latency_ms(conn, sql, params, reps)
                print(
                    f"  {q_name:<18} scan={scan:<16} "
                    f"exec={exec_ms:8.2f}ms  median={median:7.2f}ms  p95={p95:7.2f}ms"
                )
                results.append(
                    {
                        "index_config": cfg_name,
                        "index_kb": idx_kb,
                        "query": q_name,
                        "scan_type": scan,
                        "planner_exec_ms": round(exec_ms, 3),
                        "client_median_ms": round(median, 3),
                        "client_p95_ms": round(p95, 3),
                    }
                )
        return {
            "rows": total,
            "alarm_rows": alarms,
            "target_equipment": TARGET_EQUIPMENT,
            "target_equipment_alarm_rows": tgt,
            "reps": reps,
            "results": results,
        }
    finally:
        # 벤치 테이블 정리, dev DB 오염·autogenerate 드리프트 방지
        await conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=2_000_000, help="벤치 테이블 총 행수")
    parser.add_argument("--reps", type=int, default=50, help="쿼리별 클라이언트 반복 횟수")
    args = parser.parse_args()

    report = asyncio.run(run(args.rows, args.reps))
    RESULT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[기록] {RESULT_PATH}")


if __name__ == "__main__":
    main()
