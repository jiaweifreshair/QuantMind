#!/usr/bin/env python3
"""只读运行 QuantMind 已审阅的行情/资料函数，并写出 analysis.html。

不启动 API、Engine、Trade、Stream，不调用券商、QMT、通达信下单，
不读取或写入交易凭据，不执行 deploy/full-deploy.sh。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "output"
BARS = OUT / "bars"
UPSTREAM = Path(os.environ.get("QUANTMIND_ROOT", "/tmp/quantmind-upstream"))
PINNED_SHA = "1ba0b4d818ab8aa4910b3439f3842a5ba86a0247"
SH = ZoneInfo("Asia/Shanghai")

UNIVERSE = [
    {"symbol": "002415.SZ", "expect": "海康威视", "market": "A", "ccy": "CNY", "priority": True},
    {"symbol": "09988.HK", "expect": "阿里", "market": "HK", "ccy": "HKD", "priority": True},
    {"symbol": "002230.SZ", "expect": "科大讯飞", "market": "A", "ccy": "CNY", "priority": False},
    {"symbol": "09660.HK", "expect": "地平线", "market": "HK", "ccy": "HKD", "priority": False},
    {"symbol": "01810.HK", "expect": "小米", "market": "HK", "ccy": "HKD", "priority": False},
    {"symbol": "000591.SZ", "expect": "太阳能", "market": "A", "ccy": "CNY", "priority": False},
    {"symbol": "600276.SH", "expect": "恒瑞", "market": "A", "ccy": "CNY", "priority": False},
    {"symbol": "00981.HK", "expect": "中芯", "market": "HK", "ccy": "HKD", "priority": False},
    {"symbol": "601899.SH", "expect": "紫金", "market": "A", "ccy": "CNY", "priority": False},
]


def now_sh() -> datetime:
    return datetime.now(SH)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha(repo: Path) -> str:
    out = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True)
    return out.strip()


def run_cmd(cmd: list[str], cwd: Path | None, env: dict, timeout: int) -> dict:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "cmd": cmd,
            "cwd": str(cwd) if cwd else None,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-4000:],
            "seconds": round(time.perf_counter() - t0, 3),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "cmd": cmd,
            "cwd": str(cwd) if cwd else None,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "seconds": round(time.perf_counter() - t0, 3),
        }


def suffix_ok(symbol: str, market: str) -> tuple[bool, str]:
    code, _, ex = symbol.partition(".")
    if market == "A":
        if ex == "SH" and code.startswith(("60", "68", "9")):
            return True, "后缀与代码段一致"
        if ex == "SZ" and code.startswith(("00", "30", "20")):
            return True, "后缀与代码段一致"
        if ex == "BJ" and code.startswith(("4", "8", "92")):
            return True, "后缀与代码段一致"
        return False, f"A股代码段与交易所后缀不一致: {symbol}"
    if market == "HK":
        if ex == "HK" and code.isdigit() and len(code) == 5:
            return True, "港股使用 5 位代码加 .HK"
        return False, f"港股代码格式不符合 5 位+.HK: {symbol}"
    return False, "未知市场"


def frame_records(df, limit: int = 8) -> list[dict]:
    if df is None or getattr(df, "empty", True):
        return []
    view = df.tail(limit)
    rows = []
    for rec in view.to_dict(orient="records"):
        clean = {}
        for k, v in rec.items():
            if hasattr(v, "isoformat"):
                clean[str(k)] = v.isoformat()
            elif v is None or (isinstance(v, float) and v != v):
                clean[str(k)] = None
            elif hasattr(v, "item"):
                clean[str(k)] = v.item()
            else:
                clean[str(k)] = v if isinstance(v, (str, int, float, bool)) else str(v)
        rows.append(clean)
    return rows


def ohlc_problems(rows: list[dict]) -> list[str]:
    problems = []
    dates = []
    for i, row in enumerate(rows):
        o, h, l, c = (row.get(k) for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c):
            problems.append(f"row {i} 价格缺失")
            continue
        tol = max(0.02, abs(float(c)) * 1e-4)
        if float(h) + tol < max(float(o), float(c)) or float(l) - tol > min(float(o), float(c)):
            problems.append(f"{row.get('trade_date')} OHLC 不自洽")
        if float(h) + tol < float(l):
            problems.append(f"{row.get('trade_date')} high < low")
        vol = row.get("volume")
        if vol is not None and float(vol) < 0:
            problems.append(f"{row.get('trade_date')} volume < 0")
        dates.append(str(row.get("trade_date")))
    if dates != sorted(dates):
        problems.append("交易日未按升序")
    if len(dates) != len(set(dates)):
        problems.append("交易日重复")
    return problems[:12]


def standardize(df, date_col: str) -> list[dict]:
    import pandas as pd

    if df is None or df.empty:
        return []
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out[date_col]).dt.strftime("%Y-%m-%d")
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        else:
            out[col] = None
    out = out.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    out = out.drop_duplicates("trade_date", keep="last")
    rows = []
    for rec in out[["trade_date", "open", "high", "low", "close", "volume", "amount"]].to_dict(orient="records"):
        rows.append(
            {
                "trade_date": rec["trade_date"],
                "open": None if rec["open"] != rec["open"] else float(rec["open"]),
                "high": None if rec["high"] != rec["high"] else float(rec["high"]),
                "low": None if rec["low"] != rec["low"] else float(rec["low"]),
                "close": None if rec["close"] != rec["close"] else float(rec["close"]),
                "volume": None if rec["volume"] != rec["volume"] else float(rec["volume"]),
                "amount": None if rec["amount"] != rec["amount"] else float(rec["amount"]),
            }
        )
    return rows


def window_stats(rows: list[dict], sessions: int) -> dict | None:
    if len(rows) <= sessions:
        return None
    last = rows[-1]
    base = rows[-1 - sessions]
    c0 = float(base["close"])
    c1 = float(last["close"])
    if c0 <= 0 or c1 <= 0:
        return None
    segment = rows[-sessions - 1 :]
    closes = [float(r["close"]) for r in segment]
    peak = closes[0]
    max_dd = 0.0
    for px in closes:
        peak = max(peak, px)
        if peak > 0:
            max_dd = min(max_dd, px / peak - 1.0)
    rets = []
    for a, b in zip(closes, closes[1:]):
        if a > 0:
            rets.append(b / a - 1.0)
    vol = None
    if len(rets) >= 5:
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        vol = (var ** 0.5) * (252 ** 0.5)
    hi = max(float(r["high"] or r["close"]) for r in segment)
    lo = min(float(r["low"] or r["close"]) for r in segment)
    return {
        "sessions": sessions,
        "start_date": base["trade_date"],
        "end_date": last["trade_date"],
        "start_close": round(c0, 4),
        "end_close": round(c1, 4),
        "close_to_close_return": round(c1 / c0 - 1.0, 6),
        "max_drawdown_close": round(max_dd, 6),
        "realized_vol_ann": None if vol is None else round(vol, 6),
        "window_high": round(hi, 4),
        "window_low": round(lo, 4),
        "label": "计算",
    }


def save_bars(symbol: str, source: str, adjust: str, rows: list[dict]) -> str | None:
    if not rows:
        return None
    BARS.mkdir(parents=True, exist_ok=True)
    path = BARS / f"{symbol}_{source}_{adjust}.csv"
    with path.open("w", encoding="utf-8") as f:
        f.write("trade_date,open,high,low,close,volume,amount\n")
        for r in rows:
            f.write(
                "{trade_date},{open},{high},{low},{close},{volume},{amount}\n".format(**r)
            )
    return str(path.relative_to(ROOT))


def rel_diff(a: float, b: float) -> float | None:
    if b == 0:
        return None
    return abs(a - b) / abs(b)


def name_from_f10(df) -> tuple[str, list[dict]]:
    rows = frame_records(df, limit=30)
    name = ""
    for row in rows:
        values = [str(v) for v in row.values() if v is not None]
        blob = " ".join(values)
        if any(key in blob for key in ("股票简称", "证券简称", "名称")):
            # key/value layout: the other cell is the name
            for v in values:
                if v and "简称" not in v and "名称" not in v and len(v) <= 20:
                    name = v
        if "item" in row and "value" in row and "简称" in str(row["item"]):
            name = str(row["value"])
    return name, rows


def name_from_profile_rows(rows: list[dict]) -> str:
    if not rows:
        return ""
    row = rows[0]
    for key in ("公司名称", "证券简称", "股票名称"):
        if row.get(key):
            return str(row[key])
    return ""


def name_from_profile(df) -> tuple[str, list[dict]]:
    rows = frame_records(df, limit=20)
    return name_from_profile_rows(rows), rows


def dividend_dates(df) -> list[str]:
    rows = frame_records(df, limit=30)
    found = []
    for row in rows:
        for key, val in row.items():
            if val and any(token in str(key).lower() for token in ("ex_date", "除净", "除权", "除息")):
                found.append(str(val)[:10])
    return found


def series_block(source: str, adjust: str, rows: list[dict], fetched_at: str) -> dict:
    problems = ohlc_problems(rows)
    last = rows[-1] if rows else None
    return {
        "source": source,
        "adjust": adjust,
        "rows": len(rows),
        "first_date": rows[0]["trade_date"] if rows else None,
        "last_date": last["trade_date"] if last else None,
        "last_bar": last,
        "recent": rows[-5:],
        "ohlc_problems": problems,
        "windows": {
            str(n): window_stats(rows, n) if not problems else None for n in (20, 60, 120)
        },
        "fetched_at": fetched_at,
        "bars_csv": save_bars(
            source.split(":")[0] if False else "pending",
            source,
            adjust,
            [],
        ),
    }


def attach_bars(symbol: str, block: dict, rows: list[dict]) -> dict:
    block["bars_csv"] = save_bars(symbol, block["source"], block["adjust"], rows)
    return block


def call_timed(fn):
    t0 = time.perf_counter()
    try:
        value = fn()
        return {"ok": True, "value": value, "error": None, "seconds": round(time.perf_counter() - t0, 3)}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "value": None,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=4),
            "seconds": round(time.perf_counter() - t0, 3),
        }


def series_from_rows(symbol: str, source: str, adjust: str, rows: list[dict], fetched_at: str, note: str = "") -> dict:
    problems = ohlc_problems(rows)
    block = {
        "source": source,
        "adjust": adjust,
        "rows": len(rows),
        "first_date": rows[0]["trade_date"] if rows else None,
        "last_date": rows[-1]["trade_date"] if rows else None,
        "last_bar": rows[-1] if rows else None,
        "recent": rows[-5:],
        "ohlc_problems": problems,
        "windows": {str(n): window_stats(rows, n) if rows and not problems else None for n in (20, 60, 120)},
        "fetched_at": fetched_at,
    }
    if note:
        block["adjustment_note"] = note
    return attach_bars(symbol, block, rows)


def build_a(symbol: str, expect: str, fetched_at: str, ctx: dict) -> dict:
    from datetime import date as date_cls

    bs = ctx["baostock"]
    start = date_cls(2024, 1, 1)
    end = now_sh().date()
    layers = {
        "akshare_provider_qfq": {
            "ok": False,
            "error": ctx["push2_block"],
            "seconds": 0,
            "series": None,
        },
        "eastmoney_provider_qfq": {
            "ok": False,
            "error": "与 AkShare 东财接口同一供应商。前一次调用已返回限流，按该提示不再调用。",
            "seconds": 0,
            "series": None,
        },
    }
    for adjust in ("qfq", "none"):
        result = call_timed(lambda adjust=adjust: standardize(bs.fetch_daily(symbol, start, end, adjust=adjust), "trade_date"))
        rows = result["value"] or []
        layers[f"baostock_adapter_{adjust}"] = {
            "ok": result["ok"] and bool(rows),
            "error": result["error"] or (None if rows else "empty"),
            "seconds": result["seconds"],
            "series": series_from_rows(symbol, "BaostockAdapter.fetch_daily", adjust, rows, fetched_at) if rows else None,
        }
    meta = ctx.get("a_meta")
    observed = ""
    if meta is not None and not meta.empty:
        hit = meta[meta["symbol"] == symbol]
        if not hit.empty:
            observed = str(hit.iloc[0]["name"])
    return {
        "identity": {
            "expect": expect,
            "observed_name": observed,
            "name_ok": bool(observed) and expect in observed,
            "source": "BaostockAdapter.fetch_meta",
            "f10_ok": False,
            "f10_error": "未调用。AkShare F10 走东方财富，同一次准备中已出现 push2 限流。",
        },
        "layers": layers,
    }


def build_hk(symbol: str, expect: str, fetched_at: str, ctx: dict) -> dict:
    from backend.scripts.akshare_hk_fundamental import _fetch_dividend, _fetch_profile
    from backend.scripts.akshare_kline import _fetch_stock

    code5 = symbol.split(".")[0]
    k = call_timed(lambda: _fetch_stock("HK", code5, "20240101"))
    rows = []
    if k["ok"] and k["value"] is not None and not k["value"].empty:
        rows = standardize(k["value"], "time")
    problems = ohlc_problems(rows)
    if ctx.get("stop_eastmoney_extra"):
        profile = {"ok": False, "value": None, "error": "前序东方财富资料接口已失败，停止后续调用", "seconds": 0}
    else:
        profile = call_timed(lambda: _fetch_profile(code5))
        if not profile["ok"]:
            ctx["stop_eastmoney_extra"] = True
    observed, preview = ("", [])
    if profile["ok"] and profile["value"] is not None:
        observed, preview = name_from_profile(profile["value"])
    if ctx.get("stop_eastmoney_extra"):
        dividend = {"ok": False, "value": None, "error": "未调用分红接口", "seconds": 0}
    else:
        dividend = call_timed(lambda: _fetch_dividend(code5))
        if not dividend["ok"]:
            ctx["stop_eastmoney_extra"] = True
    ex_dates = dividend_dates(dividend["value"]) if dividend["ok"] else []
    return {
        "identity": {
            "expect": expect,
            "observed_name": observed,
            "name_ok": bool(observed) and expect in observed,
            "profile_ok": profile["ok"] and bool(observed),
            "profile_error": profile["error"],
            "profile_seconds": profile["seconds"],
            "profile_preview": preview[:8],
            "normalized_symbol": None if not rows else None,
            "note": "akshare_kline._to_symbol 会把 5 位代码去掉一个前导 0，例如 09988 → 9988.HK。本次请求仍使用用户给出的 5 位代码。",
        },
        "dividend": {
            "ok": dividend["ok"] and bool(ex_dates or (dividend["value"] is not None and not getattr(dividend["value"], "empty", True))),
            "error": dividend["error"],
            "seconds": dividend["seconds"],
            "ex_dates": ex_dates,
            "preview": frame_records(dividend["value"], 6) if dividend["ok"] else [],
        },
        "layers": {
            "akshare_kline_none": {
                "ok": k["ok"] and bool(rows),
                "error": k["error"] or (None if rows else "empty"),
                "seconds": k["seconds"],
                "series": attach_bars(symbol, {
                    "source": "akshare_kline._fetch_stock",
                    "adjust": "none",
                    "rows": len(rows),
                    "first_date": rows[0]["trade_date"] if rows else None,
                    "last_date": rows[-1]["trade_date"] if rows else None,
                    "last_bar": rows[-1] if rows else None,
                    "recent": rows[-5:],
                    "ohlc_problems": problems,
                    "windows": {str(n): window_stats(rows, n) if rows and not problems else None for n in (20, 60, 120)},
                    "fetched_at": fetched_at,
                    "adjustment_note": "该函数调用 ak.stock_hk_daily，上游注释标明不复权。后复权脚本依赖付费 CSV，本次没有运行。",
                }, rows) if rows else None,
            }
        },
    }


def choose_primary(market: str, layers: dict) -> tuple[str | None, str]:
    if market == "A":
        order = ["baostock_adapter_qfq", "baostock_adapter_none"]
    else:
        order = ["akshare_kline_none"]
    for key in order:
        layer = layers.get(key) or {}
        series = layer.get("series") or {}
        if layer.get("ok") and series and not series.get("ohlc_problems"):
            return key, "该层 OHLC 约束通过，作为本次计算所用序列"
    for key, layer in layers.items():
        series = (layer or {}).get("series") or {}
        if layer.get("ok") and series:
            return key, "只有带 OHLC 问题的序列可用，相应收益计算停止"
    return None, "没有可用日线"


def cross_check(layers: dict) -> dict:
    def last_close(key: str):
        series = ((layers.get(key) or {}).get("series") or {})
        bar = series.get("last_bar") or {}
        if not bar or bar.get("close") is None or not series.get("last_date"):
            return None
        return series.get("last_date"), float(bar["close"])

    notes = []
    pairs = [
        ("akshare_provider_qfq", "akshare_provider_none"),
        ("baostock_adapter_qfq", "baostock_adapter_none"),
        ("akshare_provider_qfq", "baostock_adapter_qfq"),
        ("eastmoney_provider_qfq", "baostock_adapter_qfq"),
        ("akshare_provider_none", "baostock_adapter_none"),
    ]
    details = []
    mismatch = False
    for a, b in pairs:
        la, lb = last_close(a), last_close(b)
        if not la or not lb:
            continue
        if la[0] != lb[0]:
            details.append({"left": a, "right": b, "status": "日期不同", "left_date": la[0], "right_date": lb[0]})
            continue
        diff = rel_diff(la[1], lb[1])
        flag = diff is not None and diff > 0.01
        mismatch = mismatch or flag
        details.append({
            "left": a,
            "right": b,
            "date": la[0],
            "left_close": round(la[1], 4),
            "right_close": round(lb[1], 4),
            "rel_diff": None if diff is None else round(diff, 6),
            "over_1pct": flag,
        })
    if mismatch:
        notes.append("同一交易日收盘价相对差超过 1%，不把任一来源写成唯一成交价。")
    return {"comparisons": details, "material_mismatch": mismatch, "notes": notes}


def stale(last_date: str | None, today: date) -> bool:
    if not last_date:
        return True
    try:
        d = date.fromisoformat(last_date)
    except ValueError:
        return True
    return d < today - timedelta(days=7)


def analyze_symbol(spec: dict, fetched_at: str, today: date, ctx: dict) -> dict:
    ok, reason = suffix_ok(spec["symbol"], spec["market"])
    item = {
        "symbol": spec["symbol"],
        "market": spec["market"],
        "currency": spec["ccy"],
        "priority": spec["priority"],
        "code_check": {"ok": ok, "detail": reason},
        "status": "pending",
    }
    if not ok:
        item["status"] = "stopped_code"
        item["coverage"] = "代码/市场校验未通过，停止该标的分析"
        return item
    try:
        if spec["market"] == "A":
            built = build_a(spec["symbol"], spec["expect"], fetched_at, ctx)
        else:
            built = build_hk(spec["symbol"], spec["expect"], fetched_at, ctx)
    except Exception as exc:  # noqa: BLE001
        item["status"] = "failed_exception"
        item["error"] = f"{type(exc).__name__}: {exc}"
        item["trace"] = traceback.format_exc(limit=6)
        item["coverage"] = "原生模块调用失败，该标的没有分析结论"
        return item
    item.update(built)
    return conclude(item, today)


def conclude(item: dict, today: date) -> dict:
    market = item["market"]
    if not item.get("identity", {}).get("name_ok"):
        item["status"] = "stopped_identity"
        item["coverage"] = "没有核到与研究对象一致的证券简称，停止收益与结论"
        return item
    key, why = choose_primary(market, item.get("layers") or {})
    item["primary_layer"] = key
    item["primary_reason"] = why
    series = (((item.get("layers") or {}).get(key) or {}).get("series") if key else None) or {}
    item["cross_check"] = cross_check(item.get("layers") or {}) if market == "A" else {"comparisons": [], "material_mismatch": False, "notes": ["港股本次只有不复权一条原生日线，没有第二来源交叉。"]}
    if not series:
        item["status"] = "stopped_no_bars"
        item["coverage"] = "身份通过但没有可用日线"
        return item
    if series.get("ohlc_problems"):
        item["status"] = "stopped_ohlc"
        item["coverage"] = "OHLC 约束未通过，停止收益计算"
        return item
    if item["cross_check"].get("material_mismatch"):
        item["status"] = "stopped_source_conflict"
        item["coverage"] = "来源之间最新收盘价不一致，停止单一结论"
        return item
    if stale(series.get("last_date"), today):
        item["status"] = "stopped_stale"
        item["coverage"] = f"最新K线日期 {series.get('last_date')} 距抓取日超过 7 天，不写成当前结论"
        return item
    windows = series.get("windows") or {}
    if not any(windows.values()):
        item["status"] = "stopped_short_sample"
        item["coverage"] = "可用样本短于 20 个交易日，停止区间收益计算"
        return item
    item["status"] = "calculated"
    item["coverage"] = "身份、OHLC 与时效通过；区间数字是收盘价计算，不是上涨概率"
    if market == "HK":
        ex = set((item.get("dividend") or {}).get("ex_dates") or [])
        for w in windows.values():
            if not w:
                continue
            w["dividend_in_window"] = any(w["start_date"] < d <= w["end_date"] for d in ex if len(d) == 10)
        item["coverage"] += "。港股价格为不复权，窗口内若有除净，收益含价格缺口。"
    return item


def module_matrix(symbols: list[dict], probes: dict) -> list[dict]:
    rows = [
        {"module": "deploy/full-deploy.sh", "result": "未执行", "detail": "审阅到 curl|sudo bash，按约束不运行"},
        {"module": "scripts/setup/init.sh", "result": "未执行", "detail": "依赖容器内路径与数据库，不在本次只读范围"},
        {"module": "backend/main_oss.py", "result": "未执行", "detail": "会拉起 API/Engine/Trade/Stream"},
        {"module": "券商 / QMT / 通达信下单 / 模拟撮合", "result": "未执行", "detail": "本次明确禁用"},
        {"module": "QuantBot / 原生 LLM 研报", "result": "未运行", "detail": probes["llm"]["detail"]},
        {"module": "Qlib 回测", "result": probes["qlib"]["result"], "detail": probes["qlib"]["detail"]},
        {"module": "scripts/stock_9layer_fetch.py", "result": probes["nine"]["result"], "detail": probes["nine"]["detail"]},
        {"module": "QuantDB / 付费港股 CSV", "result": "缺失", "detail": "仓库与本机都没有 data/quantdb 或 data/quanthk/_paid_raw"},
    ]
    a_ok = sum(1 for s in symbols if s["market"] == "A" and s["status"] == "calculated")
    h_ok = sum(1 for s in symbols if s["market"] == "HK" and s["status"] == "calculated")
    rows.append({"module": "A股 AkShareProvider / EastMoneyProvider", "result": "未取得日线", "detail": probes["push2"]["detail"]})
    rows.append({"module": "A股 BaostockAdapter.fetch_daily / fetch_meta", "result": f"{a_ok}/5 只完成区间计算", "detail": "匿名登录后的历史日线与证券简称，前复权与不复权各一次"})
    rows.append({"module": "港股 akshare_kline._fetch_stock + 公司资料/分红", "result": f"{h_ok}/4 只完成区间计算", "detail": "不复权；未跑复权因子脚本"})
    return rows


def render_html(payload: dict) -> str:
    def n(x, digits=2):
        if x is None:
            return "—"
        if isinstance(x, float):
            return f"{x:.{digits}f}"
        return escape(str(x))

    def pct(x):
        if x is None:
            return "—"
        return f"{x * 100:.2f}%"

    status_zh = {
        "calculated": "已计算区间收益",
        "stopped_code": "因代码校验停止",
        "stopped_identity": "因简称未核验停止",
        "stopped_no_bars": "无日线",
        "stopped_ohlc": "因 OHLC 约束停止",
        "stopped_source_conflict": "因来源冲突停止",
        "stopped_stale": "因行情日期过旧停止",
        "stopped_short_sample": "因样本过短停止",
        "failed_exception": "模块异常",
    }

    def adjust_note(s: dict) -> str:
        layers = s.get("layers") or {}
        q = (((layers.get("baostock_adapter_qfq") or {}).get("series") or {}).get("windows") or {}).get("120")
        raw = (((layers.get("baostock_adapter_none") or {}).get("series") or {}).get("windows") or {}).get("120")
        if not q or not raw:
            return ""
        diff = abs(q["close_to_close_return"] - raw["close_to_close_return"])
        if diff <= 0.005:
            return "120 个交易日的前复权与不复权涨跌接近，最新收盘也对齐。"
        return (
            f"120 个交易日前复权涨跌 {pct(q['close_to_close_return'])}，不复权 {pct(raw['close_to_close_return'])}。"
            "表内使用前复权。两者不同说明区间内有除权，不复权涨跌不是投资者总回报。"
        )

    def card(s: dict) -> str:
        ident = s.get("identity") or {}
        key = s.get("primary_layer")
        series = (((s.get("layers") or {}).get(key) or {}).get("series") if key else None) or {}
        bar = series.get("last_bar") or {}
        windows = series.get("windows") or {}
        rows = []
        for nwin in (20, 60, 120):
            w = windows.get(str(nwin))
            if not w:
                rows.append(f"<tr><td>{nwin} 个交易日</td><td colspan='5'>样本不足或已停止</td></tr>")
                continue
            div = ""
            if w.get("dividend_in_window"):
                div = "；窗口内有除净记录，不复权收益含价格缺口"
            rows.append(
                "<tr>"
                f"<td>{nwin} 个交易日<br><span class='muted'>{escape(w['start_date'])} → {escape(w['end_date'])}</span></td>"
                f"<td>{n(w['start_close'])} → {n(w['end_close'])} {escape(s['currency'])}</td>"
                f"<td>{pct(w['close_to_close_return'])}</td>"
                f"<td>{pct(w['max_drawdown_close'])}</td>"
                f"<td>{pct(w['realized_vol_ann'])}</td>"
                f"<td>计算{div}</td>"
                "</tr>"
            )
        recent = ""
        for r in series.get("recent") or []:
            recent += (
                "<tr>"
                f"<td>{escape(str(r['trade_date']))}</td><td>{n(r['open'])}</td><td>{n(r['high'])}</td>"
                f"<td>{n(r['low'])}</td><td>{n(r['close'])}</td><td>{n(r.get('volume'), 0)}</td>"
                "</tr>"
            )
        checks = ""
        for comp in (s.get("cross_check") or {}).get("comparisons") or []:
            checks += f"<li>{escape(json.dumps(comp, ensure_ascii=False))}</li>"
        problems = ""
        for layer_name, layer in (s.get("layers") or {}).items():
            err = layer.get("error")
            flag = "成功" if layer.get("ok") else "失败"
            problems += f"<li><b>{escape(layer_name)}</b> {flag}，{layer.get('seconds')} 秒"
            if err:
                problems += f"，{escape(str(err))}"
            series_l = layer.get("series") or {}
            if series_l.get("ohlc_problems"):
                problems += "，OHLC：" + escape("；".join(series_l["ohlc_problems"]))
            problems += "</li>"
        pri = "优先" if s.get("priority") else "覆盖"
        return f"""
        <article class="card {'priority' if s.get('priority') else ''}">
          <header>
            <p class="kicker">{pri} · {escape(s['market'])} · {escape(s['currency'])}</p>
            <h3>{escape(s['symbol'])} {escape(ident.get('observed_name') or ident.get('expect') or '')}</h3>
            <p class="status">{escape(status_zh.get(s['status'], s['status']))}</p>
          </header>
          <p>{escape(s.get('coverage') or '')}</p>
          <dl>
            <dt>简称核验</dt><dd>期望包含「{escape(ident.get('expect') or '')}」，观察值「{escape(ident.get('observed_name') or '无')}」{('；英文名称「' + escape(ident.get('english_name')) + '」') if ident.get('english_name') else ''}</dd>
            <dt>计算序列</dt><dd>{escape(key or '无')} · {escape(series.get('source') or '')} · 复权 {escape(series.get('adjust') or '无')}</dd>
            <dt>样本</dt><dd>{series.get('rows') or 0} 根日线，{escape(str(series.get('first_date') or '—'))} 至 {escape(str(series.get('last_date') or '—'))}</dd>
            <dt>最新日线收盘</dt><dd>{n(bar.get('close'))} {escape(s['currency'])}，交易日 {escape(str(bar.get('trade_date') or '—'))}。这是日线，不是抓取时刻的逐笔价。</dd>
          </dl>
          <table>
            <thead><tr><th>窗口</th><th>收盘到收盘</th><th>区间涨跌</th><th>收盘最大回撤</th><th>年化实现波动</th><th>标签</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
          <p>{adjust_note(s)}</p>
          <h4>近 5 根日线</h4>
          <table>
            <thead><tr><th>交易日</th><th>开</th><th>高</th><th>低</th><th>收</th><th>量</th></tr></thead>
            <tbody>{recent or '<tr><td colspan="6">无</td></tr>'}</tbody>
          </table>
          <h4>各层</h4>
          <ul>{problems or '<li>无</li>'}</ul>
          <h4>交叉校验</h4>
          <ul>{checks or '<li>无第二来源或未形成可比收盘</li>'}</ul>
        </article>
        """

    mods = ""
    for m in payload["modules"]:
        mods += f"<tr><td>{escape(m['module'])}</td><td>{escape(m['result'])}</td><td>{escape(m['detail'])}</td></tr>"
    cards = "".join(card(s) for s in payload["symbols"])
    run = payload["run"]
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>QuantMind 只读研究记录 · {escape(run['fetched_at'])}</title>
<style>
  :root {{ color-scheme: light; --ink:#1c1915; --paper:#f6f3ec; --line:#d9d1c3; --alert:#8c2f2f; --ok:#1f4d38; }}
  body {{ margin:0; font:16px/1.55 "Source Han Sans SC","Noto Sans SC","PingFang SC",sans-serif; color:var(--ink); background:var(--paper); }}
  main {{ max-width:1080px; margin:0 auto; padding:32px 20px 80px; }}
  h1 {{ font-size:32px; line-height:1.25; margin:0 0 8px; }}
  h2 {{ margin-top:36px; font-size:22px; }}
  h3 {{ margin:0; font-size:22px; }}
  h4 {{ margin:18px 0 6px; font-size:15px; }}
  .banner {{ background:#3d1f1f; color:#fff8f2; padding:18px 20px; border-radius:14px; }}
  .banner strong {{ color:#ffd3c2; }}
  .muted {{ color:#655e54; font-size:13px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; }}
  th, td {{ border-bottom:1px solid var(--line); text-align:left; padding:8px 10px; vertical-align:top; font-size:14px; }}
  th {{ background:#efe8dc; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:16px; padding:18px; margin:16px 0; }}
  .card.priority {{ border-color:#8c2f2f; }}
  .kicker {{ letter-spacing:.08em; font-size:12px; margin:0; color:#8c2f2f; }}
  .status {{ font-weight:700; }}
  dl {{ display:grid; grid-template-columns:140px 1fr; gap:6px 12px; }}
  dt {{ color:#655e54; }}
  dd {{ margin:0; }}
  code {{ font-family:ui-monospace,monospace; font-size:13px; }}
</style>
</head>
<body>
<main>
  <p class="muted">抓取与生成时间（Asia/Shanghai）{escape(run['fetched_at'])}</p>
  <h1>九只股票的只读数据记录</h1>
  <section class="banner">
    <p><strong>QuantMind 整套平台没有运行。</strong>没有启动 Web、引擎、交易、行情推送，没有部署，没有下单，也没有原生 LLM 研报。</p>
    <p>实际跑通的是上游源码里的只读日线与公司资料函数。下面的价格和区间涨跌只来自这些函数的返回值。没有上涨概率、净胜率或买卖承诺。</p>
  </section>
  <h2>上游与本次边界</h2>
  <ul>
    <li>上游 <a href="https://github.com/qusong0627/QuantMind">qusong0627/QuantMind</a>，master HEAD <code>{escape(run['upstream_sha'])}</code>，pyproject 版本 {escape(run['version'])}，许可证 {escape(run['license'])}。</li>
    <li>GitHub Releases 列表为空，没有可用的发布版本号，因此版本以 pyproject 的 1.0.0 与提交 SHA 为准。</li>
    <li>个人仓库原来只有 README。本次没有改上游，没有改可见性，没有强推。</li>
    <li>持有周期按数周到数月查看 20/60/120 个交易日，只做股票，不涉及加密资产。</li>
  </ul>
  <h2>模块结果</h2>
  <table>
    <thead><tr><th>模块</th><th>结果</th><th>说明</th></tr></thead>
    <tbody>{mods}</tbody>
  </table>
  <h2>怎么读这些数字</h2>
  <ul>
    <li><b>事实</b>：函数返回的简称、交易日、OHLC、成交量。成交额只表示成交金额，不是资金净流入。</li>
    <li><b>计算</b>：用上述收盘价做的区间涨跌、收盘最大回撤、年化实现波动。波动用日收益样本标准差乘以 252 的平方根。</li>
    <li><b>推断</b>：本次没有。缺少预注册的样本外统计和交易成本，不能把区间涨跌写成概率或胜率。</li>
    <li><b>缺口</b>：QuantDB 300 维因子、Qlib 回测、LLM 研报、港股后复权、宏观与资金流叙事都没有数据支持。</li>
  </ul>
  <h2>标的</h2>
  <p>优先查看海康威视与阿里巴巴。其余标的使用同一套函数，覆盖状态写在每张卡片上。</p>
  {cards}
  <h2>来源与风险</h2>
  <ul>
    <li>A股日线：<code>BaostockAdapter.fetch_daily</code>，前复权与不复权。简称来自 <code>BaostockAdapter.fetch_meta</code>。<code>AkShareProvider</code> 与 <code>EastMoneyProvider</code> 没有产出日线：akshare 1.20.45 的 <code>stock_zh_a_hist</code> 在本次准备中对 002415 返回东方财富 push2 限流，随后按该错误提示停止同类请求。</li>
    <li>港股日线：<code>backend/scripts/akshare_kline.py</code> 的 <code>_fetch_stock</code>，接口 <code>ak.stock_hk_daily</code>，不复权。没有使用会改写 high/low 的 <code>quanthk_akshare_kline._fetch_stock</code>。公司资料与分红来自 <code>akshare_hk_fundamental.py</code>。</li>
    <li>公开页面：<a href="https://github.com/qusong0627/QuantMind/blob/{escape(run['upstream_sha'])}/backend/services/engine/data_gateway/providers/akshare_provider.py">AkShareProvider</a>，<a href="https://github.com/qusong0627/QuantMind/blob/{escape(run['upstream_sha'])}/backend/scripts/akshare_kline.py">akshare_kline.py</a>。</li>
    <li>数据供应商、接口和复权口径会变化。日线收盘不是投资建议。港股未复权，除净日前后的区间涨跌不能当成投资者总回报。</li>
  </ul>
</main>
</body>
</html>
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    fetched_at = now_sh().isoformat(timespec="seconds")
    today = now_sh().date()
    t_all = time.perf_counter()
    sha = git_sha(UPSTREAM)
    if sha != PINNED_SHA:
        raise SystemExit(f"上游 SHA 不符: {sha}")
    license_path = UPSTREAM / "LICENSE"
    pyproject = (UPSTREAM / "pyproject.toml").read_text(encoding="utf-8")
    version = "unknown"
    for line in pyproject.splitlines():
        if line.startswith("version = "):
            version = line.split("=", 1)[1].strip().strip('"')
            break
    env = os.environ.copy()
    env["PYTHONPATH"] = "/tmp/qm-py" + os.pathsep + env.get("PYTHONPATH", "")
    env["TZ"] = "Asia/Shanghai"
    env["QUANTMIND_ROOT"] = str(UPSTREAM)
    nine = run_cmd(
        [sys.executable, "scripts/stock_9layer_fetch.py", "002415.SZ"],
        UPSTREAM,
        env,
        90,
    )
    nine_text = (nine["stdout"] or "") + "\n" + (nine["stderr"] or "")
    if "无该标的" in nine_text or "缺失" in nine_text or "QuantDB" in nine_text or nine["returncode"] not in (0,):
        nine_result = "失败"
    else:
        nine_result = "返回了输出，需人工对照"
    qlib = run_cmd([sys.executable, "-c", "import qlib"], None, env, 30)
    qlib_result = "可导入" if qlib["returncode"] == 0 else "未安装/不可导入"
    llm_keys = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY",
        "QUANTDB_API_KEY",
        "QUANTMIND_LLM_API_KEY",
    ]
    present = [k for k in llm_keys if os.environ.get(k)]
    probes = {
        "nine": {
            "result": nine_result,
            "detail": "优先标的 002415.SZ 直接运行。本机没有 QuantDB parquet。" + nine_text[-500:].replace("\n", " "),
            "command": nine,
        },
        "qlib": {
            "result": qlib_result,
            "detail": (qlib["stderr"] or qlib["stdout"] or "无输出")[-300:],
            "command": qlib,
        },
        "llm": {
            "detail": "未配置 " + ", ".join(llm_keys) if not present else "环境里存在密钥名 " + ",".join(present) + "，本次仍未调用 LLM",
        },
        "push2": {
            "result": "限流后停止",
            "detail": "同一次准备里，akshare 1.20.45 的 stock_zh_a_hist(002415, 前复权) 抛出 Push2RateLimitError，提示东方财富 push2 断开且不要继续请求。因此没有调用 AkShareProvider 与 EastMoneyProvider。",
        },
    }
    from backend.services.engine.data_platform.adapters.baostock_adapter import BaostockAdapter

    ba = BaostockAdapter()
    meta_res = call_timed(lambda: ba.fetch_meta("A"))
    ctx = {
        "baostock": ba,
        "a_meta": meta_res["value"] if meta_res["ok"] else None,
        "push2_block": probes["push2"]["detail"],
        "stop_eastmoney_extra": False,
        "meta_seconds": meta_res["seconds"],
        "meta_error": meta_res["error"],
    }
    symbols = []
    for spec in UNIVERSE:
        print(f"[run] {spec['symbol']}", flush=True)
        symbols.append(analyze_symbol(spec, fetched_at, today, ctx))
    try:
        import baostock as bs

        bs.logout()
    except Exception:
        pass
    import akshare
    import baostock
    import pandas

    payload = {
        "run": {
            "fetched_at": fetched_at,
            "timezone": "Asia/Shanghai",
            "upstream_url": "https://github.com/qusong0627/QuantMind",
            "upstream_sha": sha,
            "version": version,
            "license": "AGPL-3.0",
            "license_sha256": sha256_file(license_path),
            "releases": [],
            "target_repo": "https://github.com/jiaweifreshair/QuantMind",
            "target_was_empty_except_readme": True,
            "platform_fully_ran": False,
            "trading_disabled": True,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "packages": {
                "akshare": akshare.__version__,
                "baostock": getattr(baostock, "__version__", "unknown"),
                "pandas": pandas.__version__,
            },
            "seconds": round(time.perf_counter() - t_all, 3),
        },
        "probes": probes,
        "modules": module_matrix(symbols, probes),
        "symbols": symbols,
    }
    # drop bulky command blobs from html source by keeping them only in json
    results_path = OUT / "results.json"
    html_path = OUT / "analysis.html"
    results_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html_path.write_text(render_html(payload), encoding="utf-8")
    manifest = {
        "upstream": {
            "url": "https://github.com/qusong0627/QuantMind",
            "sha": sha,
            "branch": "master",
            "version_pyproject": version,
            "license": "AGPL-3.0-only",
            "license_sha256": payload["run"]["license_sha256"],
            "github_releases": [],
        },
        "target": {
            "url": "https://github.com/jiaweifreshair/QuantMind",
            "preexisting": "README.md only, commit 1937c0623a4208d049c34e67815ecefb5653b715",
        },
        "disabled": [
            "deploy/full-deploy.sh",
            "sudo",
            "broker",
            "QMT",
            "tongdaxin order",
            "simulation matching",
            "new cron",
            "LLM report",
        ],
        "commands": [
            {"purpose": "verify upstream", "cmd": "git -C /tmp/quantmind-upstream rev-parse HEAD"},
            nine,
            qlib,
            {"purpose": "research runner", "cmd": "PYTHONPATH=/tmp/qm-py:/tmp/quantmind-upstream TZ=Asia/Shanghai python3 research/run_readonly_research.py"},
        ],
        "inputs": UNIVERSE,
        "modules": payload["modules"],
        "symbol_status": {s["symbol"]: s["status"] for s in symbols},
        "started_note": fetched_at,
        "elapsed_seconds": payload["run"]["seconds"],
        "packages": payload["run"]["packages"],
        "artifacts": {},
    }
    manifest_path = OUT / "run_manifest.json"
    for path in (results_path, html_path):
        manifest["artifacts"][str(path.relative_to(ROOT))] = {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
    for path in sorted(BARS.glob("*.csv")) if BARS.exists() else []:
        manifest["artifacts"][str(path.relative_to(ROOT))] = {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["artifacts"][str(manifest_path.relative_to(ROOT))] = {
        "sha256": sha256_file(manifest_path),
        "bytes": manifest_path.stat().st_size,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["symbol_status"], ensure_ascii=False, indent=2))
    print(f"html {html_path}")
    return 0


def relabel_saved() -> int:
    """用已落盘的公司资料纠正简称字段，不重新请求行情。"""
    payload = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    today = date.fromisoformat(payload["run"]["fetched_at"][:10])
    for item in payload["symbols"]:
        if item.get("market") != "HK":
            continue
        preview = (item.get("identity") or {}).get("profile_preview") or []
        name = name_from_profile_rows(preview)
        item["identity"]["observed_name"] = name
        item["identity"]["name_ok"] = bool(name) and item["identity"].get("expect", "") in name
        if preview and preview[0].get("英文名称"):
            item["identity"]["english_name"] = preview[0]["英文名称"]
        conclude(item, today)
    payload["modules"] = module_matrix(payload["symbols"], payload["probes"])
    payload["run"]["identity_parser"] = "公司名称优先；英文名称只作并列事实"
    results_path = OUT / "results.json"
    html_path = OUT / "analysis.html"
    manifest_path = OUT / "run_manifest.json"
    results_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html_path.write_text(render_html(payload), encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["symbol_status"] = {s["symbol"]: s["status"] for s in payload["symbols"]}
    manifest["modules"] = payload["modules"]
    manifest["identity_parser_fix"] = payload["run"]["identity_parser"]
    manifest["artifacts"] = {}
    for path in (results_path, html_path):
        manifest["artifacts"][str(path.relative_to(ROOT))] = {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
    for path in sorted(BARS.glob("*.csv")) if BARS.exists() else []:
        manifest["artifacts"][str(path.relative_to(ROOT))] = {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["artifacts"][str(manifest_path.relative_to(ROOT))] = {
        "sha256": sha256_file(manifest_path),
        "bytes": manifest_path.stat().st_size,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["symbol_status"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if os.environ.get("QM_RELABEL") == "1":
        raise SystemExit(relabel_saved())
    raise SystemExit(main())
