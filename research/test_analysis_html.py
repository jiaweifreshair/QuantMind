#!/usr/bin/env python3
"""确认 analysis.html 能被解析，且价格来自本次 results.json，不是占位样例。"""

from __future__ import annotations

import json
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "research" / "output" / "analysis.html"
RESULTS = ROOT / "research" / "output" / "results.json"
MANIFEST = ROOT / "research" / "output" / "run_manifest.json"


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        self.parts.append(data)


def main() -> int:
    html = HTML.read_text(encoding="utf-8")
    parser = TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    assert parser.title.startswith("QuantMind 只读研究记录")
    assert "整套平台没有运行" in text
    assert "不是上涨概率" in text
    assert "上涨概率为" not in text
    assert "建议买入" not in text
    assert "净胜率为" not in text
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert payload["run"]["platform_fully_ran"] is False
    assert payload["run"]["upstream_sha"] == "1ba0b4d818ab8aa4910b3439f3842a5ba86a0247"
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["upstream"]["sha"] == payload["run"]["upstream_sha"]
    calculated = [s for s in payload["symbols"] if s["status"] == "calculated"]
    if not calculated:
        print("HTML 可解析，但没有任何标的完成计算；这不是成功的行情研究。")
        return 2
    checked = 0
    for item in calculated:
        series = item["layers"][item["primary_layer"]]["series"]
        close = series["last_bar"]["close"]
        rendered = f"{close:.2f}"
        if rendered not in text:
            raise SystemExit(f"{item['symbol']} 收盘价 {rendered} 不在 HTML 中")
        if rendered in {"0.00", "1.00", "10.00", "100.00"}:
            raise SystemExit(f"{item['symbol']} 收盘价看起来像占位数字")
        checked += 1
    print(f"ok html_bytes={HTML.stat().st_size} calculated={checked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
