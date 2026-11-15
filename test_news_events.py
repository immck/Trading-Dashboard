import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import calendar_feed, news


class NewsCalendarTests(unittest.TestCase):
    def test_fed_calendar_uses_end_date_and_handles_cross_month(self):
        html = '''<a id="1">2025 FOMC Meetings</a>
        <div class="fomc-meeting--shaded fomc-meeting__month col-xs-5"><strong>March</strong></div>
        <div class="fomc-meeting__date col-xs-4">18-19*</div>
        <div class="fomc-meeting__month"><strong>Apr/May</strong></div>
        <div class="fomc-meeting__date">30-1</div>
        <div class="fomc-meeting__month"><strong>August</strong></div>
        <div class="fomc-meeting__date">22 (notation vote)</div>'''
        records = calendar_feed.parse_fed_calendar(html)
        self.assertEqual([r["date"] for r in records], ["2025-03-19", "2025-05-01"])
        self.assertTrue(all(r["type"] == "fed" for r in records))

    def test_panews_structured_calendar(self):
        table = [{"id": 1, "startAt": 2, "translations": 3, "articleId": 4,
                  "ignoreTime": 5, "url": 6}, "one", "2026-11-14T12:30:00.000Z",
                 [7], "article", False, None, {"lang": 8, "title": 9}, "zh", "美国CPI公布"]
        html = '<script id="__NUXT_DATA__" type="application/json">' + json.dumps(table) + '</script>'
        item = calendar_feed.parse_panews(html)[0]
        self.assertEqual(item["type"], "cpi")
        self.assertEqual(item["date"], "2026-11-14")
        self.assertIn("article", item["url"])
        self.assertTrue(item["scheduled"])

    def test_block_page_is_not_an_empty_success(self):
        with self.assertRaises(ValueError):
            calendar_feed.parse_panews("<html>Access Restricted</html>")

    def test_archive_deduplicates_without_losing_history(self):
        # 用工程内的临时目录，而不是 tempfile：部分受限环境会禁止写系统临时目录
        folder = Path(__file__).resolve().parent / "data" / "_test_tmp"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "archive.json"
        try:
            with patch.object(calendar_feed, "FILE", str(target)):
                calendar_feed.archive([{"id": "1", "time": 1, "title": "old"}])
                calendar_feed.archive([{"id": "1", "time": 1, "title": "updated"}, {"id": "2", "time": 2}])
                rows = calendar_feed.read()
                self.assertEqual(len(rows), 2)
                self.assertEqual([r["title"] for r in rows if r["id"] == "1"], ["updated"])
        finally:
            if target.exists():
                target.unlink()
            try:
                folder.rmdir()
            except OSError:
                pass

    def test_news_timestamp_seconds_and_milliseconds(self):
        self.assertEqual(news._news_timestamp({"publishTimestamp": 1700000000}), 1700000000000)
        self.assertEqual(news._news_timestamp({"publishTimestamp": 1700000000000}), 1700000000000)

    def test_chinese_detection_and_translation_applies_to_all_rows(self):
        text = "Microsoft apologizes for massive outage"
        with patch.object(news, "_translation_cache", {text: "微软就大规模服务中断致歉"}):
            rows = news._translate_items([{"title": text, "body": "中文正文"} for _ in range(30)])
        self.assertEqual(len(rows), 30)
        self.assertTrue(all(row["title"] == "微软就大规模服务中断致歉" for row in rows))
        self.assertEqual(rows[-1]["original_title"], text)

    def test_bls_archive_extracts_release_dates_from_links(self):
        html = ('<a href="https://www.bls.gov/news.release/archives/cpi_01152025.htm">Jan</a>'
                '<a href="https://www.bls.gov/news.release/archives/cpi_12112024.htm">Dec</a>'
                '<a href="https://www.bls.gov/news.release/archives/cpi_12112024.pdf">pdf</a>')
        rows = calendar_feed.parse_bls_archive(html, "cpi", "美国 CPI 公布")
        self.assertEqual([r["date"] for r in rows], ["2025-01-15", "2024-12-11"])
        self.assertTrue(all(r["type"] == "cpi" for r in rows))
        self.assertTrue(all(r["id"].startswith("bls-cpi-") for r in rows))

    def test_bls_archive_maps_employment_situation_to_payroll(self):
        html = '<a href="https://www.bls.gov/news.release/archives/empsit_01052024.htm">NFP</a>'
        rows = calendar_feed.parse_bls_archive(html, "payroll", "美国非农就业公布")
        self.assertEqual(rows[0]["date"], "2024-01-05")
        self.assertEqual(rows[0]["type"], "payroll")

    def test_bls_ics_keeps_only_key_indicators(self):
        ics = (
            "BEGIN:VEVENT\nDTSTART;TZID=US-Eastern:20250115T083000\n"
            "SUMMARY:Consumer Price Index\nEND:VEVENT\n"
            "BEGIN:VEVENT\nDTSTART;TZID=US-Eastern:20250103T100000\n"
            "SUMMARY:Metropolitan Area Employment and Unemployment (Monthly)\nEND:VEVENT\n"
            "BEGIN:VEVENT\nDTSTART;TZID=US-Eastern:20250110T083000\n"
            "SUMMARY:Employment Situation\nEND:VEVENT\n"
        )
        rows = calendar_feed.parse_bls_ics(ics)
        self.assertEqual([r["date"] for r in rows], ["2025-01-15", "2025-01-10"])
        self.assertEqual([r["type"] for r in rows], ["cpi", "payroll"])


if __name__ == "__main__":
    unittest.main()
