import sys

import pandas as pd
from curl_cffi import requests
import yfinance as yf

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 120)

sys.stdout.reconfigure(encoding="utf-8")

TICKER = "AAPL"

# Windows에서 Yahoo SSL 인증서 검증이 실패하는 경우가 있어 세션을 직접 지정한다.
session = requests.Session(impersonate="chrome", verify=False)
stock = yf.Ticker(TICKER, session=session)
info = stock.info

company_name = info.get("longName") or info.get("shortName") or TICKER
current_price = info.get("currentPrice") or info.get("regularMarketPrice")
previous_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
change_pct = (current_price - previous_close) / previous_close * 100

print(f"회사명: {company_name}")
print(f"현재가: ${current_price:,.2f}")
print(f"전일 대비 등락률: {change_pct:+.2f}%")

print("\n최근 1개월 역사적 데이터 (처음 5개 행):")
history = stock.history(period="1mo")
print(history.head())
