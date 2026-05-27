from report.charts import build_chart_scripts


def test_build_chart_scripts_includes_canvas_ids():
    topics = [
        {
            "period_key": "2026-05-20",
            "tag": "balance",
            "title": "밸런스",
            "mentions": 10,
            "distinct_nicks": 5,
        },
        {
            "period_key": "2026-05-21",
            "tag": "balance",
            "title": "밸런스2",
            "mentions": 3,
            "distinct_nicks": 2,
        },
    ]
    patches = [
        {"period_key": "2026-05-20", "stance": "negative", "mentions": 4},
        {"period_key": "2026-05-20", "stance": "positive", "mentions": 2},
    ]
    script = build_chart_scripts(topics, patches)
    assert "chartTagTrend" in script
    assert "chartTopTopics" in script
    assert "chartPatchStance" not in script
    assert "chart.js" in script.lower()
