"""3D 트윈 설비 상태 조회 쿼리 방식 벤치 (NEW_TWIN01_SYNC01)

GET /equipment/status는 설비별 최신 tick 1건을 모아 3D 뷰 색상에 매핑하는 폴링 경로
설비별 최신 행 추출을 DISTINCT ON(전체 정렬)과 LATERAL(설비당 인덱스 1건)로 각각 실측해
행수 증가에 따른 확장성 차이를 정량 비교
별도 벤치 테이블(bench_twin_telemetry)로 운영 시드 테이블은 건드리지 않음
각 방식마다 EXPLAIN(ANALYZE,BUFFERS)로 실행시간·스캔 방식·디스크 스필을, 클라이언트 반복으로
중앙값·p95를 측정

실행: uv run python scripts/bench/twin_status_query_bench.py --rows 330000 2000000 --reps 50
전제: docker compose up -d db (또는 로컬 postgres 기동), asyncpg 설치
결과: scripts/bench/twin_status_query_result.json 에 기록
"""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

import asyncpg

from agentory.core.config import get_settings

TELEMETRY = "bench_twin_telemetry"
MASTER = "bench_twin_master"
RESULT_PATH = Path(__file__).with_name("twin_status_query_result.json")

# 설비별 최신 상태 추출 방식 2종, 엔드포인트가 반환하는 (equipment_id, alarm_code) 형태와 동형
# 실제 조회는 마스터 배치값도 조인하나 최신 tick 추출 비용이 지배적이라 그 부분만 격리 측정
QUERIES: dict[str, str] = {
    "distinct_on": (
        f"SELECT m.equipment_id, latest.alarm_code "
        f"FROM {MASTER} m LEFT JOIN ("
        f"  SELECT DISTINCT ON (equipment_id) equipment_id, alarm_code "
        f"  FROM {TELEMETRY} ORDER BY equipment_id, timestamp DESC"
        f") latest ON m.equipment_id = latest.equipment_id "
        f"ORDER BY m.equipment_id"
    ),
    "lateral": (
        f"SELECT m.equipment_id, latest.alarm_code "
        f"FROM {MASTER} m LEFT JOIN LATERAL ("
        f"  SELECT t.alarm_code FROM {TELEMETRY} t "
        f"  WHERE t.equipment_id = m.equipment_id ORDER BY t.timestamp DESC LIMIT 1"
        f") latest ON true "
        f"ORDER BY m.equipment_id"
    ),
}


def _dsn() -> str:
    # SQLAlchemy asyncpg URL을 asyncpg 순정 DSN으로 변환
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _rebuild_tables(conn: asyncpg.Connection, rows: int, equip: int) -> None:
    # 벤치 테이블 재생성 후 스케일업 적재 (generate_series로 서버측 대량 생성)
    # equip개 설비에 5초 간격 tick을 라운드로빈 분산, 약 0.6% 행에만 알람 코드 부여
    # 운영과 동일하게 (equipment_id, timestamp) 인덱스만 두어 실제 플랜을 재현
    await conn.execute(f"DROP TABLE IF EXISTS {TELEMETRY}")
    await conn.execute(f"DROP TABLE IF EXISTS {MASTER}")
    await conn.execute(
        f"""
        CREATE TABLE {TELEMETRY} (
            log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            equipment_id varchar(50) NOT NULL,
            timestamp timestamptz NOT NULL,
            alarm_code varchar(20)
        )
        """
    )
    await conn.execute(
        f"""
        INSERT INTO {TELEMETRY} (equipment_id, timestamp, alarm_code)
        SELECT
            'EQP-' || lpad(((g % $2::int) + 1)::text, 3, '0'),
            TIMESTAMPTZ '2026-01-01 00:00:00+00' + (g * interval '5 seconds'),
            CASE WHEN random() < 0.006
                THEN (ARRAY['ERR-401','ERR-301','ERR-201','WRN-501'])[1 + floor(random() * 4)::int]
                ELSE NULL END
        FROM generate_series(0, $1::bigint - 1) AS g
        """,
        rows,
        equip,
    )
    await conn.execute(
        f"CREATE INDEX bench_twin_ix_equip_time ON {TELEMETRY} (equipment_id, timestamp)"
    )
    # 마스터는 설비 1행씩 (운영 equipment_masters와 동일 카디널리티)
    await conn.execute(
        f"""
        CREATE TABLE {MASTER} AS
        SELECT 'EQP-' || lpad(g::text, 3, '0') AS equipment_id
        FROM generate_series(1, $1::int) AS g
        """,
        equip,
    )
    await conn.execute(f"ALTER TABLE {MASTER} ADD PRIMARY KEY (equipment_id)")
    await conn.execute(f"ANALYZE {TELEMETRY}")
    await conn.execute(f"ANALYZE {MASTER}")


def _scan_summary(plan: dict[str, Any]) -> tuple[str, int]:
    # 플랜 트리에서 텔레메트리 테이블 스캔 방식과 디스크 스필(KB) 추출
    scans: list[str] = []
    disk_kb = 0

    def walk(node: dict[str, Any]) -> None:
        nonlocal disk_kb
        if node.get("Relation Name") == TELEMETRY:
            scans.append(node["Node Type"])
        # 외부 정렬 시 디스크 사용량(Sort Space Used, KB) 누적
        if node.get("Sort Space Type") == "Disk":
            disk_kb += int(node.get("Sort Space Used", 0))
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    return (scans[0] if scans else "unknown"), disk_kb


async def _explain(conn: asyncpg.Connection, sql: str) -> tuple[float, str, int]:
    # EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) 실행시간(ms)·스캔 방식·디스크 스필(KB)
    res = await conn.fetch(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
    plan = json.loads(res[0]["QUERY PLAN"])[0]
    scan, disk_kb = _scan_summary(plan["Plan"])
    return float(plan["Execution Time"]), scan, disk_kb


async def _client_latency_ms(conn: asyncpg.Connection, sql: str, reps: int) -> tuple[float, float]:
    # 클라이언트 왕복 반복 측정, 중앙값·p95(ms), 워밍업 3회 선행
    for _ in range(3):
        await conn.fetch(sql)
    samples: list[float] = []
    for _ in range(reps):
        t0 = time.perf_counter()
        await conn.fetch(sql)
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    median = statistics.median(samples)
    p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
    return median, p95


async def run(row_scales: list[int], equip: int, reps: int) -> dict[str, Any]:
    conn = await asyncpg.connect(_dsn())
    try:
        results: list[dict[str, Any]] = []
        for rows in row_scales:
            print(f"\n[적재] {TELEMETRY} {rows:,}행 / 설비 {equip}대 생성 중...")
            await _rebuild_tables(conn, rows, equip)
            total = await conn.fetchval(f"SELECT count(*) FROM {TELEMETRY}")
            print(f"[적재] 완료: 총 {total:,}행")
            for q_name, sql in QUERIES.items():
                exec_ms, scan, disk_kb = await _explain(conn, sql)
                median, p95 = await _client_latency_ms(conn, sql, reps)
                print(
                    f"  {q_name:<12} scan={scan:<14} disk={disk_kb:>6}KB  "
                    f"exec={exec_ms:8.2f}ms  median={median:7.2f}ms  p95={p95:7.2f}ms"
                )
                results.append(
                    {
                        "rows": total,
                        "equipment": equip,
                        "query": q_name,
                        "scan_type": scan,
                        "sort_disk_kb": disk_kb,
                        "planner_exec_ms": round(exec_ms, 3),
                        "client_median_ms": round(median, 3),
                        "client_p95_ms": round(p95, 3),
                    }
                )
        return {"row_scales": row_scales, "equipment": equip, "reps": reps, "results": results}
    finally:
        # 벤치 테이블 정리, dev DB 오염·autogenerate 드리프트 방지
        await conn.execute(f"DROP TABLE IF EXISTS {TELEMETRY}")
        await conn.execute(f"DROP TABLE IF EXISTS {MASTER}")
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rows",
        type=int,
        nargs="+",
        default=[330_000, 2_000_000],
        help="벤치 텔레메트리 행수(복수)",
    )
    parser.add_argument("--equip", type=int, default=20, help="설비 수")
    parser.add_argument("--reps", type=int, default=50, help="쿼리별 클라이언트 반복 횟수")
    args = parser.parse_args()

    report = asyncio.run(run(args.rows, args.equip, args.reps))
    RESULT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[기록] {RESULT_PATH}")


if __name__ == "__main__":
    main()
