from __future__ import annotations

from report.openchat_email import build_openchat_report_email_html


def test_build_openchat_report_email_html_contains_summary():
    html = build_openchat_report_email_html(
        project_label="리니지M",
        snapshot={
            "executive_summary": "패치 반응이 큼",
            "highlights": ["하이라이트 1"],
            "topics": [
                {
                    "tag": "balance",
                    "title": "밸런스 논의",
                    "mentions": 12,
                    "distinct_nicks": 5,
                    "summary": "너프에 대한 의견",
                }
            ],
            "scope_label": "최근 7일",
            "bucket_count": 3,
        },
        report_view_url="http://example.com/reports/1/file",
    )
    assert "리니지M" in html or "&#" in html
    assert "전체 리포트 보기" in html
    assert "http://example.com/reports/1/file" in html
    assert "밸런스" in html or "&#" in html
