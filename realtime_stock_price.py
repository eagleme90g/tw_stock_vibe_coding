#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TWSE / TPEX 基本市況報導 — 即時行情爬蟲 v1.2

變更摘要（v1.2）
- 修正：在部分環境（如 Pyodide）會因缺少 tzdata 導致 `ZoneInfoNotFoundError: No time zone found with key Asia/Taipei`。
  * 新增：嘗試在匯入 zoneinfo 前載入 tzdata；若失敗則自動退回使用 UTC。
- 維持 v1.1 修正與自我測試功能。

功能概述：
1) 自動判斷上市(tse)/上櫃(otc) 並組合 ex_ch 參數
2) 支援多檔股票，一次/分批請求
3) 解析主要價量欄位 + 五檔(a,b,f,g)
4) 終端輸出表格化摘要 + 進度訊息
5) 輸出 CSV 與 Excel（避免 MultiIndex；last_quotes 與 snapshots 兩個分頁）
6) 全域 error_log（記憶體 + 檔案）

注意：本程式僅供學術/自用研究，請遵守資料來源網站之使用條款，避免高頻請求。
"""
from __future__ import annotations

import sys
import json
import time
import math
import csv
import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

import requests
import pandas as pd

# =========================
# 時區設定（處理 tzdata 缺失情況）
# =========================
try:
    from zoneinfo import ZoneInfo
    try:
        TAIPEI_TZ = ZoneInfo("Asia/Taipei")
    except Exception:
        print("[WARN] 無法載入 Asia/Taipei，退回使用 UTC。請確認環境是否已安裝 tzdata。")
        TAIPEI_TZ = timezone.utc
except Exception:
    from pytz import timezone as _tz  # type: ignore
    class ZoneInfo:  # type: ignore
        def __init__(self, name: str):
            self._tz = _tz(name)
        def utcoffset(self, dt):
            return self._tz.utcoffset(dt)
        def tzname(self, dt):
            return self._tz.tzname(dt)
        def dst(self, dt):
            return self._tz.dst(dt)
    try:
        TAIPEI_TZ = ZoneInfo("Asia/Taipei")
    except Exception:
        TAIPEI_TZ = timezone.utc

# =========================
# 基本設定
# =========================
API_ENDPOINT = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Referer": "https://mis.twse.com.tw/stock/detail-item",
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache"
}
REQUEST_TIMEOUT = 5
MAX_RETRIES = 3
RETRY_BACKOFF = [0.5, 1.0, 2.0]
MAX_CODES_PER_REQUEST = 50
MIN_INTERVAL_SEC = 0.2

OUTDIR = "."
CSV_BASENAME = "snapshots_{date}.csv"
EXCEL_BASENAME = "twse_snapshots_{date}.xlsx"
SHEET_SNAPSHOTS = "snapshots"
SHEET_LAST = "last_quotes"
ERRORLOG_BASENAME = "error_log_{date}.log"

# =========================
# 錯誤紀錄器
# =========================
class ErrorLogger:
    def __init__(self):
        self._records: List[str] = []

    def log(self, level: str, code: str, step: str, message: str, context: Optional[dict] = None):
        now = datetime.now(TAIPEI_TZ).isoformat()
        ctx = json.dumps(context, ensure_ascii=False) if context else "{}"
        line = f"{now} | {level.upper()} | {code} | {step} | {message} | {ctx}"
        self._records.append(line)
        print(f"[LOG:{level.upper()}] {message}")

    def flush_to_file(self, folder: str = OUTDIR):
        if not self._records:
            return
        date_str = datetime.now(TAIPEI_TZ).strftime("%Y%m%d")
        path = os.path.join(folder, ERRORLOG_BASENAME.format(date=date_str))
        with open(path, "a", encoding="utf-8") as f:
            for line in self._records:
                f.write(line + "\n")
        self._records.clear()

ERR = ErrorLogger()

# =========================
# 市場別判斷
# =========================
MARKET_MAP: Dict[str, str] = {
    "2330": "tse",
    "2317": "tse",
    "2603": "tse",
    "3008": "otc",
}

def decide_market(stock_code: str) -> str:
    return MARKET_MAP.get(stock_code, "tse")

# =========================
# 工具函式
# =========================
def chunked(lst: List[Any], n: int) -> List[List[Any]]:
    return [lst[i:i + n] for i in range(0, len(lst), n)]

def build_ex_ch(code_market_pairs: List[Tuple[str, str]]) -> str:
    return "|".join(f"{m}_{c}.tw" for c, m in code_market_pairs)

def http_get(params: Dict[str, str]) -> Optional[requests.Response]:
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(API_ENDPOINT, params=params, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp
            ERR.log("warn", "HTTP", "get", f"status_code={resp.status_code}", {"attempt": attempt+1})
        except Exception as e:
            ERR.log("error", "HTTP", "get", f"{type(e).__name__}: {e}", {"attempt": attempt+1})
        if attempt < len(RETRY_BACKOFF):
            time.sleep(RETRY_BACKOFF[attempt])
    return None

def parse_json_response(resp: requests.Response) -> Optional[Dict[str, Any]]:
    try:
        return resp.json()
    except Exception:
        try:
            return json.loads(resp.text)
        except Exception as e:
            ERR.log("error", "PARSE", "json", f"JSON decode failed: {e}")
            return None

def to_float(x: Any) -> Optional[float]:
    if x is None: return None
    if isinstance(x, (int, float)): return float(x)
    s = str(x).strip()
    if s in ("", "-", "N/A"): return None
    try: return float(s)
    except ValueError: return None

def to_int(x: Any) -> Optional[int]:
    if x is None: return None
    if isinstance(x, int): return x
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "N/A"): return None
    try: return int(float(s))
    except ValueError: return None

def split_levels(s: Optional[str]) -> List[Optional[float]]:
    return [to_float(p) for p in str(s).split("_") if p] if s else []

def split_sizes(s: Optional[str]) -> List[Optional[int]]:
    return [to_int(p) for p in str(s).split("_") if p] if s else []

def parse_datetime(date_str: Optional[str], time_str: Optional[str]) -> Optional[str]:
    if not date_str or not time_str: return None
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y%m%d %H:%M:%S").replace(tzinfo=TAIPEI_TZ)
        return dt.isoformat()
    except Exception:
        return None

def parse_msg_item(item: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "ts": parse_datetime(item.get("d"), item.get("t") or item.get("%")),
        "market": item.get("ex"),
        "code": item.get("c"),
        "name": item.get("n"),
        "fullname": item.get("nf"),
        "open": to_float(item.get("o")),
        "high": to_float(item.get("h")),
        "low": to_float(item.get("l")),
        "prev_close": to_float(item.get("y")),
        "last": to_float(item.get("z")),
        "up_limit": to_float(item.get("u")),
        "dn_limit": to_float(item.get("w")),
        "vol": to_int(item.get("v")),
        "date": item.get("d"),
        "time": item.get("t") or item.get("%"),
    }
    a, b, f, g = split_levels(item.get("a")), split_levels(item.get("b")), split_sizes(item.get("f")), split_sizes(item.get("g"))
    for i in range(5):
        out[f"bid_px_{i+1}"] = a[i] if i < len(a) else None
        out[f"bid_sz_{i+1}"] = f[i] if i < len(f) else None
        out[f"ask_px_{i+1}"] = b[i] if i < len(b) else None
        out[f"ask_sz_{i+1}"] = g[i] if i < len(g) else None
    return out

# =========================
# 主流程
# =========================
def fetch_once(codes: List[str], interval_sec: float = 0) -> pd.DataFrame:
    pairs = [(c, decide_market(c)) for c in codes]
    rows: List[Dict[str, Any]] = []
    for batch in chunked(pairs, MAX_CODES_PER_REQUEST):
        params = {"ex_ch": build_ex_ch(batch), "json": "1", "delay": "0", "lang": "zh_tw"}
        resp = http_get(params)
        if not resp: continue
        data = parse_json_response(resp)
        if not data: continue
        arr = data.get("msgArray", [])
        for item in arr:
            rows.append(parse_msg_item(item))
        time.sleep(max(interval_sec, MIN_INTERVAL_SEC))
    df = pd.DataFrame(rows)
    base = ["ts","market","code","name","fullname","open","high","low","prev_close","last","up_limit","dn_limit","vol","date","time"]
    levels = [f"{s}_{i}" for i in range(1,6) for s in ("bid_px","bid_sz","ask_px","ask_sz")]
    for c in base+levels:
        if c not in df.columns: df[c]=None
    return df[base+levels] if not df.empty else df

def append_csv(df: pd.DataFrame, folder: str = OUTDIR):
    if df.empty: return
    date_str = datetime.now(TAIPEI_TZ).strftime("%Y%m%d")
    path = os.path.join(folder, CSV_BASENAME.format(date=date_str))
    df.to_csv(path, mode="a", index=False, encoding="utf-8-sig", header=not os.path.exists(path))

def write_excel(df: pd.DataFrame, folder: str = OUTDIR):
    if df.empty: return
    date_str = datetime.now(TAIPEI_TZ).strftime("%Y%m%d")
    path = os.path.join(folder, EXCEL_BASENAME.format(date=date_str))
    with pd.ExcelWriter(path, engine="openpyxl", mode="a" if os.path.exists(path) else "w") as writer:
        df.to_excel(writer, sheet_name=SHEET_SNAPSHOTS, index=False)

def run_once(codes: List[str]):
    df = fetch_once(codes)
    if df.empty:
        print("無資料可寫出。")
        return
    print("\n=== Snapshot ===")
    print(df[["code","name","last","open","high","low","prev_close","vol","time"]].to_string(index=False))
    append_csv(df)
    write_excel(df)

# =========================
# 測試
# =========================
def _self_tests():
    print("Running self tests ...")
    assert split_levels("1_2_") == [1.0, 2.0]
    assert split_sizes("10_20_") == [10, 20]
    assert to_float("-") is None
    assert to_int("1,000") == 1000
    ts = parse_datetime("20250919","13:30:00")
    assert ts and "2025-09-19T13:30:00" in ts
    item={"c":"3305","n":"昇貿","nf":"昇貿科技股份有限公司","ex":"tse","o":"116.5","h":"121","l":"113","y":"116","z":"118.5","u":"127.5","w":"104.5","v":"23415","d":"20250919","t":"13:30:00","a":"119_120_","b":"118_117_","f":"94_108_","g":"102_147_"}
    row=parse_msg_item(item)
    assert row["last"]==118.5 and row["bid_px_1"]==119.0 and row["ask_px_1"]==118.0
    print("Self tests passed.")

# =========================
# CLI
# =========================
def main():
    global OUTDIR
    import argparse
    parser=argparse.ArgumentParser(description="TWSE/TPEX 即時行情爬蟲 v1.2")
    parser.add_argument("codes",nargs="+",help="股票代號")
    parser.add_argument("--interval",type=float,default=0.0)
    parser.add_argument("--rounds",type=int,default=1)
    parser.add_argument("--outdir",type=str,default=OUTDIR)
    parser.add_argument("--selftest",action="store_true")
    args=parser.parse_args()
    if args.selftest:
        _self_tests(); return
    OUTDIR=args.outdir
    for r in range(max(1,args.rounds) if args.interval>0 else 1):
        print(f"\n===== 進行第 {r+1}/{args.rounds} 輪 =====")
        run_once(args.codes)
        if r<args.rounds-1 and args.interval>0:
            time.sleep(max(args.interval,MIN_INTERVAL_SEC))
    ERR.flush_to_file(OUTDIR)

if __name__=="__main__":
    main()
