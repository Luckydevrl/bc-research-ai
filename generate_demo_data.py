"""Generate the SYNTHETIC demo dataset (clearly NOT BC.GAME data).

Crash-style result multipliers drawn from P(M >= m) = RTP / m with an ASSUMED RTP of 0.99,
seeded for reproducibility. Run:  python sample_data/generate_demo_data.py
"""
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

OUT = Path(__file__).with_name("demo_synthetic_crash_rounds.csv")


def main(n: int = 3000, seed: int = 2024, rtp: float = 0.99) -> None:
    rng = np.random.default_rng(seed)
    u = rng.random(n)
    mult = np.maximum(np.floor(100 * rtp / (1 - u)) / 100, 1.0)  # values <1 (about 1% of rounds) are instant busts -> 1.00
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["game", "round_id", "timestamp", "multiplier"])
        for i, m in enumerate(mult):
            w.writerow(["Crash", f"SYNTH-{i + 1:06d}", (t0 + timedelta(seconds=20 * i)).isoformat(), f"{m:.2f}"])
    print(f"wrote {OUT} ({n} synthetic rows)")


if __name__ == "__main__":
    main()
