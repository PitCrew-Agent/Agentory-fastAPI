"""주요 조회 경로 인덱스·방식 조합 벤치

알람/트윈 상태 외 나머지 조회 경로를 인덱스 구성 x 쿼리 방식 조합으로 실측
운영 시드 테이블은 건드리지 않고 케이스별 벤치 테이블을 스케일업 적재
각 조합마다 EXPLAIN(ANALYZE,BUFFERS)로 실행시간·스캔 방식·디스크 스필을, 클라이언트 반복으로
중앙값·p95를 측정

케이스
- sensor_by_line: 라인별 센서 로그, IN 서브쿼리 vs JOIN vs 비정규화 line_name 컬럼
- notification_page: 알림 키셋 페이지, 인덱스 없음/단일/복합/미읽음 부분 인덱스
- repair_page: 수리 이력 전체 페이지, 전역 정렬 인덱스 유무

실행: uv run python scripts/bench/read_path_index_bench.py --reps 30
전제: docker compose up -d db (또는 로컬 postgres 기동), asyncpg 설치
결과: scripts/bench/read_path_index_result.json 에 기록
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

RESULT_PATH = Path(__file__).with_name("read_path_index_result.json")

# 공통 파라미터 (적재 규칙과 일치해야 실제 행이 걸림)
TARGET_LINE = "LINE-1"
# 넓은 범위로 최신 top-N을 뽑는 실제 사용 패턴 (라인 최근 로그 조회)
RANGE_LO = datetime(2025, 1, 1, tzinfo=UTC)
RANGE_HI = datetime(2030, 1, 1, tzinfo=UTC)


def _dsn() -> str:
    # SQLAlchemy asyncpg URL을 asyncpg 순정 DSN으로 변환
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


def _scan_summary(plan: dict[str, Any], table: str) -> tuple[str, int]:
    # 플랜 트리에서 대상 테이블 스캔 방식과 디스크 스필(KB) 추출
    scans: list[str] = []
    disk_kb = 0

    def walk(node: dict[str, Any]) -> None:
        nonlocal disk_kb
        if node.get("Relation Name") == table:
            scans.append(node["Node Type"])
        if node.get("Sort Space Type") == "Disk":
            disk_kb += int(node.get("Sort Space Used", 0))
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    return (scans[0] if scans else "unknown"), disk_kb


async def _explain(
    conn: asyncpg.Connection, sql: str, params: list[Any], table: str
) -> tuple[float, str, int]:
    # EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) 실행시간(ms)·스캔 방식·디스크 스필(KB)
    res = await conn.fetch(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}", *params)
    plan = json.loads(res[0]["QUERY PLAN"])[0]
    scan, disk_kb = _scan_summary(plan["Plan"], table)
    return float(plan["Execution Time"]), scan, disk_kb


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


async def _drop_bench_indexes(conn: asyncpg.Connection, table: str) -> None:
    # 대상 테이블의 보조 인덱스 전부 제거 (PK 제외), 구성 전환 시 초기화
    names = await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE tablename = $1 AND indexname LIKE 'bench_ix_%'",
        table,
    )
    for row in names:
        await conn.execute(f"DROP INDEX IF EXISTS {row['indexname']}")


async def _index_kb(conn: asyncpg.Connection, table: str) -> int:
    val = await conn.fetchval(
        "SELECT coalesce(sum(pg_relation_size(indexrelid)), 0) "
        "FROM pg_stat_user_indexes WHERE relname = $1 AND indexrelname LIKE 'bench_ix_%'",
        table,
    )
    return int(val) // 1024


# ---------------------------------------------------------------------------
# 케이스 A: 라인별 센서 로그
# ---------------------------------------------------------------------------
A_TEL = "bench_line_telemetry"
A_MASTER = "bench_line_master"
A_EQUIP = 20
A_LINES = 3  # 라인 3개에 설비 분산 (설비 하나는 정확히 한 라인 소속)

A_INDEX_CONFIGS: dict[str, list[str]] = {
    "equip_time": [f"CREATE INDEX bench_ix_a_equip_time ON {A_TEL} (equipment_id, timestamp)"],
    "equip_time__line_time": [
        f"CREATE INDEX bench_ix_a_equip_time ON {A_TEL} (equipment_id, timestamp)",
        f"CREATE INDEX bench_ix_a_line_time ON {A_TEL} (line_name, timestamp)",
    ],
}
A_QUERIES: dict[str, tuple[str, list[Any]]] = {
    "in_subquery": (
        f"SELECT * FROM {A_TEL} WHERE timestamp >= $2 AND timestamp <= $3 "
        f"AND equipment_id IN (SELECT equipment_id FROM {A_MASTER} WHERE line_name = $1) "
        "ORDER BY timestamp DESC LIMIT 500",
        [TARGET_LINE, RANGE_LO, RANGE_HI],
    ),
    "join": (
        f"SELECT t.* FROM {A_TEL} t JOIN {A_MASTER} m ON m.equipment_id = t.equipment_id "
        "WHERE m.line_name = $1 AND t.timestamp >= $2 AND t.timestamp <= $3 "
        "ORDER BY t.timestamp DESC LIMIT 500",
        [TARGET_LINE, RANGE_LO, RANGE_HI],
    ),
    "denorm_line_col": (
        f"SELECT * FROM {A_TEL} WHERE line_name = $1 AND timestamp >= $2 AND timestamp <= $3 "
        "ORDER BY timestamp DESC LIMIT 500",
        [TARGET_LINE, RANGE_LO, RANGE_HI],
    ),
    # 스키마 변경 없이 기존 (equipment_id, timestamp) 인덱스만으로 설비별 top-N 후 병합
    "lateral_merge": (
        f"SELECT sub.* FROM (SELECT t.* FROM {A_MASTER} m CROSS JOIN LATERAL ("
        f"  SELECT * FROM {A_TEL} t WHERE t.equipment_id = m.equipment_id "
        "  AND t.timestamp >= $2 AND t.timestamp <= $3 ORDER BY t.timestamp DESC LIMIT 500"
        ") t WHERE m.line_name = $1) sub ORDER BY sub.timestamp DESC LIMIT 500",
        [TARGET_LINE, RANGE_LO, RANGE_HI],
    ),
}


async def _build_a(conn: asyncpg.Connection, rows: int) -> None:
    await conn.execute(f"DROP TABLE IF EXISTS {A_TEL}")
    await conn.execute(f"DROP TABLE IF EXISTS {A_MASTER}")
    await conn.execute(
        f"""
        CREATE TABLE {A_TEL} (
            log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            equipment_id varchar(50) NOT NULL,
            line_name varchar(50) NOT NULL,
            timestamp timestamptz NOT NULL,
            temperature numeric(5,2),
            alarm_code varchar(20)
        )
        """
    )
    # equip개 설비를 라운드로빈, 라인은 설비번호 % A_LINES로 결정 (비정규화 line_name 동봉)
    await conn.execute(
        f"""
        INSERT INTO {A_TEL} (equipment_id, line_name, timestamp, temperature)
        SELECT
            'EQP-' || lpad(((g % {A_EQUIP}) + 1)::text, 3, '0'),
            'LINE-' || (((g % {A_EQUIP}) % {A_LINES}) + 1)::text,
            TIMESTAMPTZ '2026-01-01 00:00:00+00' + (g * interval '5 seconds'),
            60 + (random() * 10)::numeric(5,2)
        FROM generate_series(0, $1::bigint - 1) AS g
        """,
        rows,
    )
    await conn.execute(
        f"""
        CREATE TABLE {A_MASTER} AS
        SELECT 'EQP-' || lpad(g::text, 3, '0') AS equipment_id,
               'LINE-' || (((g - 1) % {A_LINES}) + 1)::text AS line_name
        FROM generate_series(1, {A_EQUIP}) AS g
        """
    )
    await conn.execute(f"ALTER TABLE {A_MASTER} ADD PRIMARY KEY (equipment_id)")
    await conn.execute(f"ANALYZE {A_TEL}")
    await conn.execute(f"ANALYZE {A_MASTER}")


# ---------------------------------------------------------------------------
# 케이스 B: 알림 키셋 페이지네이션
# ---------------------------------------------------------------------------
B_TABLE = "bench_notifications"
B_LIMIT = 50

B_INDEX_CONFIGS: dict[str, list[str]] = {
    "none": [],
    "occurred_at": [f"CREATE INDEX bench_ix_b_occurred ON {B_TABLE} (occurred_at)"],
    "occurred_id": [f"CREATE INDEX bench_ix_b_occurred_id ON {B_TABLE} (occurred_at, id)"],
    "occurred_id__unread_partial": [
        f"CREATE INDEX bench_ix_b_occurred_id ON {B_TABLE} (occurred_at, id)",
        f"CREATE INDEX bench_ix_b_unread ON {B_TABLE} (occurred_at, id) WHERE is_read = false",
    ],
}
# 커서는 적재 시각 중간 지점, 페이지네이션 2페이지째를 모사
B_CURSOR_TS = datetime(2026, 6, 1, tzinfo=UTC)


def _b_queries() -> dict[str, tuple[str, list[Any]]]:
    return {
        # 현재 코드 방식: OR로 펼친 커서 술어 (row-value 비교 대비 sargable 하지 않음)
        "page_or_form": (
            f"SELECT * FROM {B_TABLE} WHERE (occurred_at < $1 OR (occurred_at = $1 AND id < $2)) "
            "ORDER BY occurred_at DESC, id DESC LIMIT 50",
            [B_CURSOR_TS, 10_000_000],
        ),
        # 제안 방식: row-value 튜플 비교, 복합 인덱스에서 커서 위치로 직접 seek
        "page_rowvalue": (
            f"SELECT * FROM {B_TABLE} WHERE (occurred_at, id) < ($1, $2) "
            "ORDER BY occurred_at DESC, id DESC LIMIT 50",
            [B_CURSOR_TS, 10_000_000],
        ),
        "page_unread_or_form": (
            f"SELECT * FROM {B_TABLE} WHERE is_read = false "
            "AND (occurred_at < $1 OR (occurred_at = $1 AND id < $2)) "
            "ORDER BY occurred_at DESC, id DESC LIMIT 50",
            [B_CURSOR_TS, 10_000_000],
        ),
        "page_unread_rowvalue": (
            f"SELECT * FROM {B_TABLE} WHERE is_read = false AND (occurred_at, id) < ($1, $2) "
            "ORDER BY occurred_at DESC, id DESC LIMIT 50",
            [B_CURSOR_TS, 10_000_000],
        ),
    }


async def _build_b(conn: asyncpg.Connection, rows: int) -> None:
    await conn.execute(f"DROP TABLE IF EXISTS {B_TABLE}")
    await conn.execute(
        f"""
        CREATE TABLE {B_TABLE} (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            occurred_at timestamptz NOT NULL,
            equipment_id varchar(50) NOT NULL,
            alarm_code varchar(20) NOT NULL,
            is_read boolean NOT NULL,
            message text NOT NULL
        )
        """
    )
    # 5분 간격 알림, 약 15%만 미읽음 (미읽음 부분 인덱스 선택도 평가)
    await conn.execute(
        f"""
        INSERT INTO {B_TABLE} (occurred_at, equipment_id, alarm_code, is_read, message)
        SELECT
            TIMESTAMPTZ '2026-01-01 00:00:00+00' + (g * interval '5 minutes'),
            'EQP-' || lpad(((g % 20) + 1)::text, 3, '0'),
            'ERR-' || (100 + (g % 5))::text,
            random() > 0.15,
            'msg ' || g
        FROM generate_series(0, $1::bigint - 1) AS g
        """,
        rows,
    )
    await conn.execute(f"ANALYZE {B_TABLE}")


# ---------------------------------------------------------------------------
# 케이스 C: 수리 이력 전체 페이지
# ---------------------------------------------------------------------------
C_TABLE = "bench_repairs"

C_INDEX_CONFIGS: dict[str, list[str]] = {
    # 운영 현재: (equipment_id, repaired_at)와 (repaired_by)만 있어 전역 정렬용 인덱스 부재
    "equip_time_only": [
        f"CREATE INDEX bench_ix_c_equip_time ON {C_TABLE} (equipment_id, repaired_at)"
    ],
    "add_repaired_at_id": [
        f"CREATE INDEX bench_ix_c_equip_time ON {C_TABLE} (equipment_id, repaired_at)",
        f"CREATE INDEX bench_ix_c_repaired_id ON {C_TABLE} (repaired_at, id)",
    ],
}
C_CURSOR_TS = datetime(2026, 6, 1, tzinfo=UTC)
C_QUERIES: dict[str, tuple[str, list[Any]]] = {
    # 현재 코드 방식: OR로 펼친 커서 술어
    "global_page_or_form": (
        f"SELECT * FROM {C_TABLE} WHERE (repaired_at < $1 OR (repaired_at = $1 AND id < $2)) "
        "ORDER BY repaired_at DESC, id DESC LIMIT 50",
        [C_CURSOR_TS, 10_000_000],
    ),
    # 제안 방식: row-value 튜플 비교
    "global_page_rowvalue": (
        f"SELECT * FROM {C_TABLE} WHERE (repaired_at, id) < ($1, $2) "
        "ORDER BY repaired_at DESC, id DESC LIMIT 50",
        [C_CURSOR_TS, 10_000_000],
    ),
}


async def _build_c(conn: asyncpg.Connection, rows: int) -> None:
    await conn.execute(f"DROP TABLE IF EXISTS {C_TABLE}")
    await conn.execute(
        f"""
        CREATE TABLE {C_TABLE} (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            equipment_id varchar(50) NOT NULL,
            repaired_by bigint,
            repaired_at timestamptz NOT NULL,
            note text
        )
        """
    )
    await conn.execute(
        f"""
        INSERT INTO {C_TABLE} (equipment_id, repaired_by, repaired_at, note)
        SELECT
            'EQP-' || lpad(((g % 20) + 1)::text, 3, '0'),
            (g % 5) + 1,
            TIMESTAMPTZ '2026-01-01 00:00:00+00' + (g * interval '30 minutes'),
            'note ' || g
        FROM generate_series(0, $1::bigint - 1) AS g
        """,
        rows,
    )
    await conn.execute(f"ANALYZE {C_TABLE}")


# ---------------------------------------------------------------------------
# 케이스 실행 공통
# ---------------------------------------------------------------------------
async def _run_case(
    conn: asyncpg.Connection,
    *,
    name: str,
    table: str,
    rows: int,
    build,
    index_configs: dict[str, list[str]],
    queries: dict[str, tuple[str, list[Any]]],
    reps: int,
) -> list[dict[str, Any]]:
    print(f"\n[적재] {name}: {table} {rows:,}행 생성 중...")
    await build(conn, rows)
    total = await conn.fetchval(f"SELECT count(*) FROM {table}")
    print(f"[적재] 완료: 총 {total:,}행")
    out: list[dict[str, Any]] = []
    for cfg_name, ddls in index_configs.items():
        await _drop_bench_indexes(conn, table)
        for ddl in ddls:
            await conn.execute(ddl)
        await conn.execute(f"ANALYZE {table}")
        idx_kb = await _index_kb(conn, table)
        print(f"  [구성] {cfg_name} (보조인덱스 {idx_kb:,} KB)")
        for q_name, (sql, params) in queries.items():
            exec_ms, scan, disk_kb = await _explain(conn, sql, params, table)
            median, p95 = await _client_latency_ms(conn, sql, params, reps)
            print(
                f"    {q_name:<16} scan={scan:<14} disk={disk_kb:>6}KB  "
                f"exec={exec_ms:8.2f}ms  median={median:7.2f}ms  p95={p95:7.2f}ms"
            )
            out.append(
                {
                    "case": name,
                    "rows": total,
                    "index_config": cfg_name,
                    "index_kb": idx_kb,
                    "query": q_name,
                    "scan_type": scan,
                    "sort_disk_kb": disk_kb,
                    "planner_exec_ms": round(exec_ms, 3),
                    "client_median_ms": round(median, 3),
                    "client_p95_ms": round(p95, 3),
                }
            )
    await conn.execute(f"DROP TABLE IF EXISTS {table}")
    return out


async def run(reps: int, scale: dict[str, int]) -> dict[str, Any]:
    conn = await asyncpg.connect(_dsn())
    try:
        results: list[dict[str, Any]] = []
        results += await _run_case(
            conn,
            name="sensor_by_line",
            table=A_TEL,
            rows=scale["sensor"],
            build=_build_a,
            index_configs=A_INDEX_CONFIGS,
            queries=A_QUERIES,
            reps=reps,
        )
        await conn.execute(f"DROP TABLE IF EXISTS {A_MASTER}")
        results += await _run_case(
            conn,
            name="notification_page",
            table=B_TABLE,
            rows=scale["notification"],
            build=_build_b,
            index_configs=B_INDEX_CONFIGS,
            queries=_b_queries(),
            reps=reps,
        )
        results += await _run_case(
            conn,
            name="repair_page",
            table=C_TABLE,
            rows=scale["repair"],
            build=_build_c,
            index_configs=C_INDEX_CONFIGS,
            queries=C_QUERIES,
            reps=reps,
        )
        return {"reps": reps, "scale": scale, "results": results}
    finally:
        for tbl in (A_TEL, A_MASTER, B_TABLE, C_TABLE):
            await conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=30, help="쿼리별 클라이언트 반복 횟수")
    parser.add_argument("--sensor-rows", type=int, default=2_000_000)
    parser.add_argument("--notification-rows", type=int, default=1_000_000)
    parser.add_argument("--repair-rows", type=int, default=500_000)
    args = parser.parse_args()

    scale = {
        "sensor": args.sensor_rows,
        "notification": args.notification_rows,
        "repair": args.repair_rows,
    }
    report = asyncio.run(run(args.reps, scale))
    RESULT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[기록] {RESULT_PATH}")


if __name__ == "__main__":
    main()
