"""EXP-000 고정 평가 세트 생성 CLI

실행: uv run python experiments/build_eval_set.py
      [--config experiments/configs/exp_000_eval_set.yaml] [--out data/eval/v1]
생성 로직은 anomaly.eval_set에 위치, 여기는 얇은 진입점만 유지
"""

import argparse
from pathlib import Path

import yaml

from anomaly.eval_set import build_eval_set


def main() -> None:
    parser = argparse.ArgumentParser(description="EXP-000 고정 평가 세트 생성")
    parser.add_argument("--config", default="experiments/configs/exp_000_eval_set.yaml")
    parser.add_argument("--out", default=None, help="산출 디렉토리, 기본 data/eval/<version>")
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.config).read_text())
    out_dir = Path(args.out) if args.out else Path("data/eval") / config["version"]
    manifest = build_eval_set(config, out_dir)

    print(f"평가 세트 생성 완료: {out_dir}")
    print(f"  eval rows   : {manifest['eval_rows']:,}")
    print(f"  train rows  : {manifest['train_rows']:,}")
    print(f"  events      : {manifest['events']}")
    print(f"  git commit  : {manifest['git_commit']}")


if __name__ == "__main__":
    main()
