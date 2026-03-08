# Load Testing with Locust

## How to Run

From the `api/` directory:

```bash
cd api && uv run locust -f tests/load/locustfile.py --host http://localhost:8000
```

Then open the Locust web UI at <http://localhost:8089> to configure and start a test run.

### Headless Mode

Run without the web UI by specifying users and spawn rate directly:

```bash
cd api && uv run locust -f tests/load/locustfile.py \
  --host http://localhost:8000 \
  --headless \
  -u 10 \
  -r 2 \
  --run-time 60s
```

## Recommended Configurations

| Test Type    | Users (`-u`) | Spawn Rate (`-r`) | Duration (`--run-time`) |
|-------------|-------------:|-----------------:|------------------------:|
| Smoke test  |           10 |                1 |                     60s |
| Load test   |           50 |                5 |                    300s |
| Stress test |          100 |               10 |                    300s |

## Task Scenarios

The load test defines the following weighted task scenarios:

| Task                       | Weight | Description                                      |
|---------------------------|-------:|-------------------------------------------------|
| `send_hr_query`           |      3 | Sends an HR-related chat message                 |
| `send_it_query`           |      2 | Sends an IT support chat message                 |
| `send_general_query`      |      1 | Sends a general organisation query                    |
| `multi_turn_conversation` |      1 | Starts a conversation then sends a follow-up     |
| `health_check`            |      1 | Hits the health check endpoint                   |

## Key Metrics to Monitor

- **p50 / p95 / p99 response time** -- Median, 95th, and 99th percentile latencies indicate how consistently the API responds under load.
- **Error rate** -- Percentage of requests that return non-2xx status codes. Should stay below 1% under normal load.
- **Throughput (RPS)** -- Requests per second the API can sustain. Watch for throughput plateaus which indicate a bottleneck.
- **Failure distribution** -- Which endpoints fail and why (timeouts, 5xx errors, etc.).

## Exporting Results

Locust can export CSV reports automatically:

```bash
cd api && uv run locust -f tests/load/locustfile.py \
  --host http://localhost:8000 \
  --headless \
  -u 50 -r 5 --run-time 300s \
  --csv results/load_test
```

This generates `load_test_stats.csv`, `load_test_failures.csv`, and `load_test_stats_history.csv` in the `results/` directory.
