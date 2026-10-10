"""Measure API latency for disruption queries against a running Reroute backend.

Usage: python scripts/benchmark_api.py --url http://localhost:8000 --runs 40

Targets are drawn with a fixed seed from the loaded network (stops and lines), each run
alternating closure and slowdown, so repeated runs compare like with like.
"""

import argparse
import random
import statistics
import time

import httpx


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


def report(label: str, values: list[float]) -> None:
    print(
        f"{label:<34} n={len(values):<3} p50={statistics.median(values):7.1f} ms  "
        f"p95={percentile(values, 0.95):7.1f} ms  max={max(values):7.1f} ms"
    )


def timed(client: httpx.Client, method: str, path: str, **kwargs: object) -> float:
    started = time.perf_counter()
    response = client.request(method, path, **kwargs)
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--runs", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    with httpx.Client(base_url=args.url, timeout=120) as client:
        features = client.get("/api/network").json()["features"]
        stops = sorted(f["id"] for f in features if f["properties"]["entity_type"] == "stop")
        routes = sorted(f["id"] for f in features if f["properties"]["entity_type"] == "route")
        rng = random.Random(args.seed)
        print(f"Network: {len(routes)} lines, {len(stops)} stops")

        report("GET /api/network", [timed(client, "GET", "/api/network") for _ in range(5)])
        results: dict[str, list[float]] = {"stop": [], "route": []}
        first = None
        for index in range(args.runs):
            target_type = "stop" if index % 2 == 0 else "route"
            body = {
                "target_type": target_type,
                "target_id": rng.choice(stops if target_type == "stop" else routes),
                "disruption_type": "closure" if index % 4 < 2 else "slowdown",
                "duration_minutes": 30,
            }
            try:
                elapsed = timed(client, "POST", "/api/simulations", json=body)
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 422:  # stop without service
                    continue
                raise
            if first is None:
                first = elapsed
            results[target_type].append(elapsed)
        print(f"{'first simulation (cold cache)':<34} {first:.1f} ms")
        report("POST /api/simulations (stop)", results["stop"])
        report("POST /api/simulations (line)", results["route"])
        report("POST /api/simulations (all)", results["stop"] + results["route"])


if __name__ == "__main__":
    main()
