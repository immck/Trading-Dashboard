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


if __name__ == "__main__":
    unittest.main()
