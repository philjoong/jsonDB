"""Chart.js dataset builders for HTML reports."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


def build_chart_scripts(
    topic_rows: list[dict[str, Any]],
    patch_rows: list[dict[str, Any]] | None = None,
) -> str:
    """Return inline script tags configuring Chart.js canvases."""
    _ = patch_rows
    tag_by_period: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in topic_rows:
        pk = str(r.get("period_key") or "")
        tag = str(r.get("tag") or "general")
        tag_by_period[pk][tag] += int(r.get("mentions") or 0)

    period_labels = sorted(tag_by_period.keys())
    all_tags: set[str] = set()
    for tags in tag_by_period.values():
        all_tags.update(tags.keys())
    tag_list = sorted(all_tags)[:8]

    line_datasets = []
    palette = [
        "#1976d2",
        "#388e3c",
        "#f57c00",
        "#7b1fa2",
        "#c62828",
        "#00838f",
        "#5d4037",
        "#455a64",
    ]
    for i, tag in enumerate(tag_list):
        line_datasets.append(
            {
                "label": tag,
                "data": [tag_by_period[pk].get(tag, 0) for pk in period_labels],
                "borderColor": palette[i % len(palette)],
                "backgroundColor": palette[i % len(palette)] + "33",
                "tension": 0.2,
            }
        )

    top_sorted = sorted(
        topic_rows,
        key=lambda r: (
            -int(r.get("mentions") or 0),
            -int(r.get("distinct_nicks") or 0),
        ),
    )[:12]
    bar_labels = [
        str(r.get("title") or r.get("topic_key") or "?")[:40] for r in top_sorted
    ]
    bar_mentions = [int(r.get("mentions") or 0) for r in top_sorted]
    bar_nicks = [int(r.get("distinct_nicks") or 0) for r in top_sorted]

    line_cfg = {
        "type": "line",
        "data": {"labels": period_labels, "datasets": line_datasets},
        "options": {
            "responsive": True,
            "plugins": {"title": {"display": True, "text": "태그별 언급 추이"}},
            "scales": {"y": {"beginAtZero": True}},
        },
    }
    bar_cfg = {
        "type": "bar",
        "data": {
            "labels": bar_labels,
            "datasets": [
                {
                    "label": "언급",
                    "data": bar_mentions,
                    "backgroundColor": "#1976d299",
                },
                {
                    "label": "참여 닉",
                    "data": bar_nicks,
                    "backgroundColor": "#388e3c99",
                },
            ],
        },
        "options": {
            "responsive": True,
            "plugins": {"title": {"display": True, "text": "상위 주제"}},
            "scales": {"y": {"beginAtZero": True}},
        },
    }

    if not period_labels:
        line_cfg["data"]["labels"] = ["(데이터 없음)"]
        line_cfg["data"]["datasets"] = []

    return f"""
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
(function() {{
  const lineCfg = {json.dumps(line_cfg, ensure_ascii=False)};
  const barCfg = {json.dumps(bar_cfg, ensure_ascii=False)};
  if (document.getElementById('chartTagTrend')) {{
    new Chart(document.getElementById('chartTagTrend'), lineCfg);
  }}
  if (document.getElementById('chartTopTopics')) {{
    new Chart(document.getElementById('chartTopTopics'), barCfg);
  }}
}})();
</script>
"""
