from fastapi.testclient import TestClient


def _parse_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    if not raw:
        return labels

    for pair in raw.split(","):
        key, raw_value = pair.split("=", 1)
        labels[key] = raw_value.strip().strip('"')
    return labels


def get_metric_value(
    client: TestClient, metric_name: str, labels: dict[str, str] | None = None
) -> float:
    response = client.get("/metrics")
    assert response.status_code == 200

    for line in response.text.splitlines():
        if line.startswith("#"):
            continue

        if labels:
            prefix = f"{metric_name}" + "{"
            if not line.startswith(prefix):
                continue
            label_block, value = line.split("} ", 1)
            parsed = _parse_labels(label_block[len(prefix) :])
            if all(parsed.get(k) == v for k, v in labels.items()):
                return float(value)
            continue

        if line.startswith(f"{metric_name} "):
            return float(line.split(" ", 1)[1])

    label_text = f" with labels {labels}" if labels else ""
    raise AssertionError(f"Metric {metric_name}{label_text} not found")
