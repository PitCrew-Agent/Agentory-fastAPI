"""사람 스팟체크 시트 생성·채점 (AI_AGENT01_QUALITY01)

LLM judge(pairwise)의 신뢰도를 사람 판단과 대조 검증하기 위한 20건 블라인드 시트를 만든다.
generate: 벤치 원자료에서 카테고리 층화 샘플 20건을 뽑아, 두 답변을 무작위 슬롯(1·2)으로 섞어
  spotcheck_sheet.csv(사람 기입용)와 spotcheck_key.json(슬롯→config 매핑)을 생성한다.
score: 사람이 human_pick(1·2·tie)을 채운 CSV와 judge 결과를 받아, 사람 vs judge 일치율과
  각자의 after(현재 구조) 선호 승률을 계산해 judge 신뢰도를 보고한다.

블라인드: 시트에는 어느 슬롯이 어느 구조인지 표기하지 않는다, 매핑은 key 파일에만 있다.

실행(생성): uv run python scripts/bench/quality_spotcheck_sheet.py generate --n 20
실행(채점): uv run python scripts/bench/quality_spotcheck_sheet.py score \
    --filled scripts/bench/spotcheck_sheet.csv
"""

import argparse
import csv
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BENCH = ROOT / "scripts" / "bench" / "agent_answer_quality_result.json"
DEFAULT_JUDGE = ROOT / "scripts" / "bench" / "agent_quality_judge_result.json"
DEFAULT_EVAL_DIR = ROOT / "data" / "eval"
SHEET = ROOT / "scripts" / "bench" / "spotcheck_sheet.csv"
KEY = ROOT / "scripts" / "bench" / "spotcheck_key.json"

BEFORE = "supervisor_before"
AFTER = "orchestrator_after"


def load_cases(eval_dir: Path) -> dict[str, dict]:
    cases: dict[str, dict] = {}
    for path in sorted(eval_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for c in data if isinstance(data, list) else [data]:
            cases[c["id"]] = c
    return cases


def rep0_answers(rows: list[dict]) -> dict[str, dict[str, str]]:
    by: dict[str, dict[str, str]] = {BEFORE: {}, AFTER: {}}
    for r in rows:
        if r.get("rep") == 0:
            by[r["config"]][r["id"]] = r.get("answer", "")
    return by


def stratified_sample(cases: dict[str, dict], ids: list[str], n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    by_cat: dict[str, list[str]] = defaultdict(list)
    for cid in ids:
        by_cat[cases.get(cid, {}).get("category", "?")].append(cid)
    for v in by_cat.values():
        rng.shuffle(v)
    # 카테고리 라운드로빈으로 균형 있게 n건 확보
    picked: list[str] = []
    cats = sorted(by_cat)
    while len(picked) < n and any(by_cat.values()):
        for cat in cats:
            if by_cat[cat]:
                picked.append(by_cat[cat].pop())
                if len(picked) >= n:
                    break
    return picked


def generate(args: argparse.Namespace) -> None:
    payload = json.loads(Path(args.bench).read_text(encoding="utf-8"))
    cases = load_cases(Path(args.eval_dir))
    ans = rep0_answers(payload["rows"])
    common = [cid for cid in cases if cid in ans[BEFORE] and cid in ans[AFTER]]
    picked = stratified_sample(cases, common, args.n, args.seed)

    rng = random.Random(args.seed + 1)
    key = {}
    rows_out = []
    for cid in picked:
        case = cases[cid]
        swap = rng.random() < 0.5  # 슬롯1·2에 무작위 배치
        slot1, slot2 = (AFTER, BEFORE) if swap else (BEFORE, AFTER)
        key[cid] = {"slot1": slot1, "slot2": slot2}
        rows_out.append(
            {
                "id": cid,
                "category": case.get("category", "?"),
                "query": case.get("query", ""),
                "answer_slot1": ans[slot1][cid],
                "answer_slot2": ans[slot2][cid],
                "human_pick(1/2/tie)": "",
                "note": "",
            }
        )

    with open(SHEET, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "category",
                "query",
                "answer_slot1",
                "answer_slot2",
                "human_pick(1/2/tie)",
                "note",
            ],
        )
        writer.writeheader()
        writer.writerows(rows_out)
    KEY.write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"스팟체크 시트 {len(rows_out)}건 생성: {SHEET}")
    print(f"블라인드 키 저장(채점 전 열람 금지): {KEY}")
    print("사람이 human_pick 열에 1·2·tie를 기입한 뒤 score 서브커맨드로 채점")


def _human_pick_to_config(pick: str, mapping: dict) -> str | None:
    pick = (pick or "").strip().lower()
    if pick == "1":
        return mapping["slot1"]
    if pick == "2":
        return mapping["slot2"]
    if pick == "tie":
        return "tie"
    return None


def score(args: argparse.Namespace) -> None:
    key = json.loads(KEY.read_text(encoding="utf-8"))
    with open(args.filled, encoding="utf-8") as f:
        filled = list(csv.DictReader(f))

    judge_pick: dict[str, str] = {}
    if Path(args.judge).exists():
        jdata = json.loads(Path(args.judge).read_text(encoding="utf-8"))
        for p in jdata.get("pairwise", []):
            s = p["after_score"]
            judge_pick[p["id"]] = "after" if s > 0.5 else "before" if s < 0.5 else "tie"

    human, both, agree = [], [], 0
    human_after_wins = 0
    for row in filled:
        cid = row["id"]
        hp = _human_pick_to_config(row.get("human_pick(1/2/tie)", ""), key.get(cid, {}))
        if hp is None:
            continue
        human.append(hp)
        if hp == "after":
            human_after_wins += 1
        jp = judge_pick.get(cid)
        if jp is not None:
            both.append(cid)
            if jp == hp:
                agree += 1

    n_human = len(human)
    if n_human == 0:
        print("채점 가능한 사람 응답 없음, human_pick 열을 먼저 채우세요")
        return

    print(f"사람 응답 {n_human}건")
    print(f"사람 기준 after(현재) 선호 승률: {round(human_after_wins / n_human, 3)}")
    if both:
        # tie를 0.5로 본 3값 일치율 + 완전 일치율
        exact = round(agree / len(both), 3)
        print(f"judge 대조 {len(both)}건, 사람-judge 완전 일치율: {exact}")
    else:
        print("judge 결과가 없어 대조 생략 (agent_pairwise_judge.py 먼저 실행)")

    # 카테고리별 사람 after 승률
    by_cat = defaultdict(list)
    keyed = {row["id"]: row for row in filled}
    for cid, hp in zip([r["id"] for r in filled if r["id"] in key], human, strict=False):
        cat = keyed[cid].get("category", "?")
        by_cat[cat].append(1 if hp == "after" else 0.5 if hp == "tie" else 0)
    print("카테고리별 사람 after 승률:")
    for cat in sorted(by_cat):
        print(f"  {cat}: {round(statistics.mean(by_cat[cat]), 3)} (n={len(by_cat[cat])})")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="블라인드 스팟체크 시트 생성")
    g.add_argument("--bench", default=str(DEFAULT_BENCH))
    g.add_argument("--eval-dir", default=str(DEFAULT_EVAL_DIR))
    g.add_argument("--n", type=int, default=20)
    g.add_argument("--seed", type=int, default=42)
    g.set_defaults(func=generate)

    s = sub.add_parser("score", help="사람 기입 CSV 채점·judge 대조")
    s.add_argument("--filled", default=str(SHEET))
    s.add_argument("--judge", default=str(DEFAULT_JUDGE))
    s.set_defaults(func=score)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
