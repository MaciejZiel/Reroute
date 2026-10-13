"""Measure the latency of the live-map endpoints against a running backend.

Usage: python scripts/benchmark_live.py --url http://localhost:8000 --runs 100

Stops are drawn with a fixed seed from the loaded network: random stops, plus the busiest
interchanges (most lines) as the worst case for the stop panel.
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
        f"{label:<38} n={len(values):<4} p50={statistics.median(values):7.1f} ms  "
        f"p95={percentile(values, 0.95):7.1f} ms  max={max(values):7.1f} ms"
    )


def timed(client: httpx.Client, path: str, **kwargs: object) -> tuple[float, httpx.Response]:
    started = time.perf_counter()
    response = client.get(path, **kwargs)
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return elapsed, response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    with httpx.Client(base_url=args.url, timeout=30) as client:
        network = client.get("/api/network").json()
        stops = [f["id"] for f in network["features"] if f["properties"]["entity_type"] == "stop"]
        lines = sorted(
            {
                f["properties"]["short_name"]
                for f in network["features"]
                if "short_name" in f["properties"]
            }
        )
        # Warm-up: first requests build caches and open connections.
        timed(client, f"/api/stops/{stops[0]}/departures")
        samples = rng.sample(stops, min(args.runs, len(stops)))
        report(
            "GET /stops/{id}/departures (random)",
            [timed(client, f"/api/stops/{s}/departures")[0] for s in samples],
        )
        busiest = []
        for stop_id in rng.sample(stops, min(300, len(stops))):
            _, response = timed(client, f"/api/stops/{stop_id}/departures")
            body = response.json()
            busiest.append((len(body["lines"]), stop_id))
        top = [stop_id for _, stop_id in sorted(busiest, reverse=True)[:20]]
        report(
            "GET /stops/{id}/departures (busiest 20)",
            [timed(client, f"/api/stops/{s}/departures")[0] for s in top for _ in range(3)],
        )
        report(
            "GET /stops/{id}/departures?scope=platform",
            [
                timed(client, f"/api/stops/{s}/departures", params={"scope": "platform"})[0]
                for s in samples
            ],
        )
        report(
            "GET /stops?q= (search)",
            [
                timed(client, "/api/stops", params={"q": q})[0]
                for q in ["cen", "dw", "ła", "plac", "metro", "ron", "ul", "kr"] * 5
            ],
        )
        line_sample = rng.sample(lines, min(args.runs, len(lines)))
        report("GET /lines/{line}", [timed(client, f"/api/lines/{n}")[0] for n in line_sample])
        report(
            "GET /lines/{line}/vehicles",
            [timed(client, f"/api/lines/{n}/vehicles")[0] for n in line_sample],
        )
        report("GET /vehicles (all)", [timed(client, "/api/vehicles")[0] for _ in range(20)])
        vehicles = client.get("/api/vehicles").json()["features"]
        vehicle_ids = [v["id"] for v in rng.sample(vehicles, min(args.runs, len(vehicles)))]
        report("GET /vehicles/{id}", [timed(client, f"/api/vehicles/{v}")[0] for v in vehicle_ids])
        report(
            "GET /predictions?stop_id=",
            [timed(client, "/api/predictions", params={"stop_id": s})[0] for s in samples[:30]],
        )


if __name__ == "__main__":
    main()
