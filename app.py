import json
import re
import time
from pathlib import Path

import altair as alt
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from curl_cffi import requests
import yfinance as yf

PERIOD_MAP = {
    "1개월": "1mo",
    "3개월": "3mo",
    "6개월": "6mo",
    "1년": "1y",
}
HISTORY_FETCH = {
    "1mo": "6mo",
    "3mo": "1y",
    "6mo": "2y",
    "1y": "2y",
}
DISPLAY_DAYS = {
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 370,
}

# Yahoo 검색은 한글 종목명을 거의 찾지 못해, 주요 종목은 별칭으로 연결한다.
NAME_ALIASES = {
    "삼성전자": "005930.KS",
    "삼성": "005930.KS",
    "sk하이닉스": "000660.KS",
    "하이닉스": "000660.KS",
    "lg에너지솔루션": "373220.KS",
    "삼성바이오로직스": "207940.KS",
    "현대차": "005380.KS",
    "현대자동차": "005380.KS",
    "기아": "000270.KS",
    "셀트리온": "068270.KS",
    "kb금융": "105560.KS",
    "신한지주": "055550.KS",
    "네이버": "035420.KS",
    "naver": "035420.KS",
    "포스코홀딩스": "005490.KS",
    "포스코": "005490.KS",
    "삼성물산": "028260.KS",
    "현대모비스": "012330.KS",
    "삼성생명": "032830.KS",
    "lg화학": "051910.KS",
    "카카오": "035720.KS",
    "삼성sdi": "006400.KS",
    "한국전력": "015760.KS",
    "한전": "015760.KS",
    "하나금융지주": "086790.KS",
    "하나금융": "086790.KS",
    "lg전자": "066570.KS",
    "삼성화재": "000810.KS",
    "sk텔레콤": "017670.KS",
    "삼성에스디에스": "018260.KS",
    "삼성sds": "018260.KS",
    "삼성전기": "009150.KS",
    "고려아연": "010130.KS",
    "한화에어로스페이스": "012450.KS",
    "한화오션": "042660.KS",
    "두산에너빌리티": "034020.KS",
    "hmm": "011200.KS",
    "크래프톤": "259960.KS",
    "카카오뱅크": "323410.KS",
    "sk이노베이션": "096770.KS",
    "kt": "030200.KS",
    "대한항공": "003490.KS",
    "엔씨소프트": "036570.KS",
    "아모레퍼시픽": "090430.KS",
    "하이브": "352820.KS",
    "lg이노텍": "011070.KS",
    "애플": "AAPL",
    "테슬라": "TSLA",
    "마이크로소프트": "MSFT",
    "마소": "MSFT",
    "구글": "GOOGL",
    "알파벳": "GOOGL",
    "엔비디아": "NVDA",
    "아마존": "AMZN",
    "메타": "META",
    "페이스북": "META",
    "넷플릭스": "NFLX",
}

PREFERRED_EXCHANGES = ("NASDAQ", "NYSE", "Korea", "KOSDAQ", "NYSEArca", "AMEX")
SAVED_TICKERS_PATH = Path(__file__).resolve().parent / "saved_tickers.json"
MAX_SAVED_TICKERS = 24
WATCHLIST_LIMIT = 10


RETRY_DELAYS = (3, 8, 15)


def create_session():
    return requests.Session(impersonate="chrome", verify=False)


def retry_on_rate_limit(func, *args, **kwargs):
    last_exc = None
    for attempt, delay in enumerate((0, *RETRY_DELAYS)):
        if delay:
            time.sleep(delay)
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            if "rate limit" in msg or "too many requests" in msg or "429" in msg:
                last_exc = exc
                continue
            raise
    raise last_exc


def normalize_query(query: str) -> str:
    return query.strip().lower().replace(" ", "")


def format_price(price: float, currency: str) -> str:
    if currency == "KRW":
        return f"{price:,.0f}원"
    if currency == "USD":
        return f"${price:,.2f}"
    return f"{price:,.2f} {currency}"


def parse_number_input(text: str) -> float | None:
    cleaned = (
        str(text)
        .replace(",", "")
        .replace(" ", "")
        .replace("원", "")
        .replace("$", "")
    )
    if cleaned == "":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def looks_like_ticker(query: str) -> bool:
    text = query.strip()
    if re.fullmatch(r"\d{6}(\.(KS|KQ))?", text, re.IGNORECASE):
        return True
    # 대문자 또는 소문자만 티커로 본다. Apple, Tesla 같은 종목명은 검색한다.
    if re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?(\.[A-Z]{1,3})?", text):
        return True
    if re.fullmatch(r"[a-z]{1,5}(-[a-z])?(\.[a-z]{1,3})?", text):
        return True
    return False


def normalize_ticker(query: str) -> str:
    symbol = query.strip().upper()
    if re.fullmatch(r"\d{6}", symbol):
        return f"{symbol}.KS"
    return symbol


def preferred_name(ticker: str, fallback: str) -> str:
    hangul_names = [
        name
        for name, symbol in NAME_ALIASES.items()
        if symbol.upper() == ticker.upper() and any("가" <= ch <= "힣" for ch in name)
    ]
    if not hangul_names:
        return fallback
    best = max(hangul_names, key=len)
    latin_len = 0
    while latin_len < len(best) and best[latin_len].isascii() and best[latin_len].isalpha():
        latin_len += 1
    if latin_len:
        return best[:latin_len].upper() + best[latin_len:]
    return best


def resolve_alias(query: str) -> str | None:
    normalized = normalize_query(query)
    if normalized in NAME_ALIASES:
        return NAME_ALIASES[normalized]

    matches = [
        (len(name), symbol)
        for name, symbol in NAME_ALIASES.items()
        if name in normalized or normalized in name
    ]
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def score_quote(quote: dict, query: str) -> int:
    symbol = quote.get("symbol") or ""
    quote_type = quote.get("quoteType") or ""
    short_name = (quote.get("shortname") or "").lower()
    long_name = (quote.get("longname") or "").lower()
    exchange = quote.get("exchDisp") or quote.get("exchange") or ""
    query_l = query.lower()

    score = 0
    if quote_type == "EQUITY":
        score += 50
    elif quote_type == "ETF":
        score += 10
    if symbol.upper() == query.strip().upper():
        score += 100
    if query_l in short_name or query_l in long_name:
        score += 30
    if query_l == short_name or query_l == long_name:
        score += 40
    if "." not in symbol:
        score += 20
    if symbol.endswith(".KS") or symbol.endswith(".KQ"):
        score += 15
    if any(name in exchange for name in PREFERRED_EXCHANGES):
        score += 15
    if any(symbol.endswith(suffix) for suffix in (".F", ".WA", ".BK", ".SA", ".TO", ".L")):
        score -= 20
    return score


def has_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


def naver_market_suffix(type_code: str) -> str:
    code = (type_code or "").upper()
    if code in {"KOSDAQ", "KSQ"}:
        return ".KQ"
    return ".KS"


def search_naver(query: str, session) -> str | None:
    url = "https://m.stock.naver.com/front-api/search/autoComplete"
    try:
        response = session.get(
            url,
            params={"query": query.strip(), "target": "stock"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None

    items = ((payload or {}).get("result") or {}).get("items") or []
    query_n = normalize_query(query)
    ranked = []
    for item in items:
        if item.get("category") not in (None, "stock"):
            continue
        code = str(item.get("code") or item.get("reutersCode") or "")
        if not re.fullmatch(r"\d{6}", code):
            continue
        name_n = normalize_query(item.get("name") or "")
        score = 0
        if name_n == query_n:
            score += 100
        elif query_n in name_n or name_n in query_n:
            score += 40
        ranked.append((score, f"{code}{naver_market_suffix(item.get('typeCode') or '')}"))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][1]


def search_yahoo(query: str, session) -> str | None:
    result = yf.Search(query, max_results=10, news_count=0, session=session)
    quotes = [item for item in result.quotes if item.get("symbol")]
    if not quotes:
        return None
    quotes.sort(key=lambda item: score_quote(item, query), reverse=True)
    return quotes[0]["symbol"]


def resolve_ticker(query: str, session) -> str:
    alias = resolve_alias(query)
    if alias:
        return alias

    if looks_like_ticker(query):
        return normalize_ticker(query)

    if has_hangul(query):
        symbol = search_naver(query, session)
        if symbol:
            return symbol

    symbol = search_yahoo(query, session)
    if symbol:
        return symbol

    raise ValueError(
        "종목을 찾지 못했습니다. 종목 코드(예: 005930.KS, AAPL) "
        "또는 종목명(예: 삼성전자, 한화오션, 애플, Tesla)을 입력해 주세요."
    )


def parse_symbol_queries(text: str) -> list[str]:
    normalized = str(text).replace("，", ",").replace("、", ",").replace("﹐", ",")
    parts = [part.strip() for part in normalized.split(",")]
    unique = []
    seen = set()
    for part in parts:
        if not part:
            continue
        key = normalize_query(part)
        if key in seen:
            continue
        seen.add(key)
        unique.append(part)
    return unique


def load_saved_tickers() -> list[dict]:
    if not SAVED_TICKERS_PATH.exists():
        return []
    try:
        data = json.loads(SAVED_TICKERS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(data, list):
        return []
    saved = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").strip()
        if not ticker:
            continue
        saved.append(
            {
                "ticker": ticker,
                "name": str(item.get("name") or ticker).strip() or ticker,
            }
        )
    return saved


def remember_tickers(items: list[dict]) -> None:
    incoming = []
    seen = set()
    for item in items:
        ticker = str(item.get("ticker") or "").strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        incoming.append(
            {
                "ticker": ticker,
                "name": str(item.get("company_name") or ticker).strip() or ticker,
            }
        )
    if not incoming:
        return
    incoming_tickers = {item["ticker"] for item in incoming}
    previous = [item for item in load_saved_tickers() if item["ticker"] not in incoming_tickers]
    merged = incoming + previous
    write_saved_tickers(merged)


def write_saved_tickers(items: list[dict]) -> None:
    SAVED_TICKERS_PATH.write_text(
        json.dumps(items[:MAX_SAVED_TICKERS], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def remove_saved_tickers(tickers: list[str]) -> None:
    remove = {str(ticker).strip() for ticker in tickers if ticker}
    remaining = [item for item in load_saved_tickers() if item["ticker"] not in remove]
    write_saved_tickers(remaining)


def apply_saved_ticker_selection() -> None:
    selected = st.session_state.get("saved_ticker_pills") or []
    if selected:
        st.session_state.stock_query = ", ".join(selected)
        st.session_state.pending_lookup = True


def _clear_saved_pills_state(remaining: list[dict]) -> None:
    if remaining:
        st.session_state.saved_ticker_pills = []
        return
    if "saved_ticker_pills" in st.session_state:
        del st.session_state["saved_ticker_pills"]


def delete_selected_saved_tickers() -> None:
    selected = list(st.session_state.get("saved_ticker_pills") or [])
    if not selected:
        st.session_state.delete_saved_warning = True
        return
    remove_saved_tickers(selected)
    st.session_state.delete_saved_warning = False
    remove = set(selected)
    st.session_state.watchlist_quotes = [
        item
        for item in (st.session_state.get("watchlist_quotes") or [])
        if item.get("ticker") not in remove
    ]
    _clear_saved_pills_state(load_saved_tickers())


def delete_all_saved_tickers() -> None:
    write_saved_tickers([])
    st.session_state.delete_saved_warning = False
    _clear_saved_pills_state([])
    st.session_state.watchlist_quotes = []


def delete_watchlist_ticker(ticker: str) -> None:
    remove_saved_tickers([ticker])
    quotes = [
        item
        for item in (st.session_state.get("watchlist_quotes") or [])
        if item.get("ticker") != ticker
    ]
    st.session_state.watchlist_quotes = quotes
    selected = [item for item in (st.session_state.get("saved_ticker_pills") or []) if item != ticker]
    if selected:
        st.session_state.saved_ticker_pills = selected
    elif "saved_ticker_pills" in st.session_state:
        del st.session_state["saved_ticker_pills"]
    if "watchlist_editor" in st.session_state:
        del st.session_state["watchlist_editor"]


def delete_selected_watchlist_tickers(tickers: list[str]) -> None:
    remove = {str(ticker).strip() for ticker in tickers if ticker}
    if not remove:
        return
    remove_saved_tickers(list(remove))
    st.session_state.watchlist_quotes = [
        item
        for item in (st.session_state.get("watchlist_quotes") or [])
        if item.get("ticker") not in remove
    ]
    selected = [item for item in (st.session_state.get("saved_ticker_pills") or []) if item not in remove]
    if selected:
        st.session_state.saved_ticker_pills = selected
    elif "saved_ticker_pills" in st.session_state:
        del st.session_state["saved_ticker_pills"]
    if "watchlist_editor" in st.session_state:
        del st.session_state["watchlist_editor"]


def infer_currency(ticker: str) -> str:
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return "KRW"
    return "USD"


def _fetch_quote_history(resolved: str, session):
    stock = yf.Ticker(resolved, session=session)
    history = stock.history(period="5d")
    return history


def derive_previous_close(current_price: float | None, change_pct: float | None) -> float | None:
    if current_price is None or change_pct is None:
        return None
    ratio = 1 + (change_pct / 100)
    if ratio <= 0:
        return None
    return current_price / ratio


def fetch_current_quote(ticker: str, name: str, session) -> dict:
    resolved = ticker
    history = retry_on_rate_limit(_fetch_quote_history, resolved, session)
    if history.empty and re.fullmatch(r"\d{6}\.KS", resolved):
        resolved = resolved[:-3] + ".KQ"
        history = retry_on_rate_limit(_fetch_quote_history, resolved, session)
    if history.empty:
        raise ValueError("현재가를 찾을 수 없습니다.")
    close = history["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    current_price = float(close.iloc[-1])
    previous_close = float(close.iloc[-2]) if len(close) >= 2 else current_price
    change_pct = 0.0
    if previous_close:
        change_pct = (current_price - previous_close) / previous_close * 100
    return {
        "ticker": resolved,
        "company_name": preferred_name(resolved, name),
        "previous_close": previous_close,
        "current_price": current_price,
        "change_pct": change_pct,
        "currency": infer_currency(resolved),
    }


def refresh_watchlist_quotes(session, fetched_items: list[dict], fetch_errors: list[str]) -> None:
    extra = {item["ticker"]: item for item in fetched_items}
    quotes = []
    for item in load_saved_tickers()[:WATCHLIST_LIMIT]:
        ticker = item["ticker"]
        if ticker in extra:
            src = extra[ticker]
            quotes.append(
                {
                    "ticker": ticker,
                    "company_name": src["company_name"],
                    "previous_close": src.get("previous_close")
                    or derive_previous_close(src.get("current_price"), src.get("change_pct")),
                    "current_price": src["current_price"],
                    "change_pct": src["change_pct"],
                    "currency": src["currency"],
                }
            )
            continue
        try:
            quotes.append(fetch_current_quote(ticker, item.get("name") or ticker, session))
        except Exception as exc:
            fetch_errors.append(f"{item.get('name') or ticker}: {exc}")
            quotes.append(
                {
                    "ticker": ticker,
                    "company_name": item.get("name") or ticker,
                    "previous_close": None,
                    "current_price": None,
                    "change_pct": None,
                    "currency": infer_currency(ticker),
                }
            )
    st.session_state.watchlist_quotes = quotes


def render_watchlist_tab(quotes: list[dict] | None) -> None:
    items = quotes or []
    if not items:
        st.info("코드 또는 종목에 관심 종목을 입력하고 조회하면, 최대 10개의 현재가가 여기에 표시됩니다.")
        return
    st.write("**관심 종목 현재가**")
    st.caption("코드 또는 종목으로 조회한 종목을 최대 10개까지 보여 줍니다. 삭제하면 관심 종목에서 제외됩니다.")
    table_rows = []
    for item in items:
        previous_close = (
            format_price(item["previous_close"], item["currency"])
            if item.get("previous_close") is not None
            else "-"
        )
        current_price = (
            format_price(item["current_price"], item["currency"])
            if item.get("current_price") is not None
            else "-"
        )
        change = f"{item['change_pct']:+.2f}%" if item.get("change_pct") is not None else "-"
        table_rows.append(
            {
                "종목": item["company_name"],
                "전일종가": previous_close,
                "현재가": current_price,
                "전일 대비": change,
                "삭제": False,
            }
        )
    edited = st.data_editor(
        pd.DataFrame(table_rows),
        hide_index=True,
        use_container_width=True,
        key="watchlist_editor",
        disabled=["종목", "전일종가", "현재가", "전일 대비"],
        column_config={
            "종목": st.column_config.TextColumn(width="medium"),
            "전일종가": st.column_config.TextColumn(width="small"),
            "현재가": st.column_config.TextColumn(width="small"),
            "전일 대비": st.column_config.TextColumn(width="small"),
            "삭제": st.column_config.CheckboxColumn(width="small"),
        },
    )
    delete_targets = [items[idx]["ticker"] for idx, row in edited.iterrows() if row["삭제"]]
    if delete_targets:
        st.button(
            "선택한 관심 종목 삭제",
            key="delete_watchlist_selected",
            on_click=delete_selected_watchlist_tickers,
            args=(delete_targets,),
            use_container_width=True,
        )


def fetch_one_stock(query: str, period: str, session):
    resolved = resolve_ticker(query, session)
    try:
        return fetch_stock_data(resolved, period, session)
    except ValueError:
        fallback = search_yahoo(query, session)
        if not fallback or fallback == resolved:
            raise
        return fetch_stock_data(fallback, period, session)


def collect_lookup_queries(text: str) -> list[str]:
    queries = parse_symbol_queries(text)
    seen = {normalize_query(item) for item in queries}
    for ticker in st.session_state.get("saved_ticker_pills") or []:
        key = normalize_query(str(ticker))
        if not key or key in seen:
            continue
        seen.add(key)
        queries.append(str(ticker))
    return queries


def perform_stock_lookup(symbol_or_name: str, period_label: str) -> None:
    if not symbol_or_name:
        st.error("코드 또는 종목을 입력해 주세요.")
        return
    try:
        session = create_session()
        queries = collect_lookup_queries(symbol_or_name)
        if len(queries) > WATCHLIST_LIMIT:
            fetch_errors = [f"관심 종목은 최대 {WATCHLIST_LIMIT}개까지 조회합니다."]
            queries = queries[:WATCHLIST_LIMIT]
        else:
            fetch_errors = []
        comparisons = []
        seen_tickers = set()
        for item_query in queries:
            try:
                ticker, company_name, current_price, change_pct, currency, history = fetch_one_stock(
                    item_query, PERIOD_MAP[period_label], session
                )
                if ticker in seen_tickers:
                    continue
                seen_tickers.add(ticker)
                comparisons.append(
                    {
                        "ticker": ticker,
                        "company_name": preferred_name(ticker, company_name),
                        "current_price": current_price,
                        "change_pct": change_pct,
                        "currency": currency,
                        "history": history,
                    }
                )
            except Exception as item_error:
                fetch_errors.append(f"{item_query}: {item_error}")
        if not comparisons:
            raise ValueError("조회된 종목이 없습니다. " + " ".join(fetch_errors))
        first = comparisons[0]
        remember_tickers(comparisons)
        refresh_watchlist_quotes(session, comparisons, fetch_errors)
        st.session_state.stock_result = {
            **first,
            "comparisons": comparisons,
            "fetch_errors": fetch_errors,
        }
    except Exception as exc:
        st.session_state.stock_result = None
        st.error(f"조회에 실패했습니다: {exc}")


def comparison_label(item: dict, used: set[str]) -> str:
    name = item["company_name"]
    if name in used:
        name = f"{name} ({item['ticker']})"
    used.add(name)
    return name


def comparison_frame(items: list[dict]) -> pd.DataFrame:
    used_names: set[str] = set()
    series_map = {}
    for item in items:
        name = comparison_label(item, used_names)
        series_map[name] = dated_close_series(item["history"]).dropna()

    frame = pd.DataFrame(series_map).sort_index()
    if frame.empty:
        return frame

    starts = [series.first_valid_index() for series in series_map.values()]
    starts = [start for start in starts if start is not None]
    if not starts:
        return pd.DataFrame()
    common_start = max(starts)
    frame = frame.loc[frame.index >= common_start].ffill()

    normalized = {}
    for column in frame.columns:
        series = frame[column].dropna()
        if series.empty:
            continue
        first = float(series.iloc[0])
        if first == 0:
            continue
        normalized[column] = (frame[column] / first - 1) * 100
    return pd.DataFrame(normalized)


def make_altair_comparison_chart(frame: pd.DataFrame):
    long_df = frame.reset_index()
    date_col = long_df.columns[0]
    long_df = long_df.rename(columns={date_col: "date"})
    long_df = long_df.melt(id_vars="date", var_name="name", value_name="ret")
    long_df["date"] = pd.to_datetime(long_df["date"])
    return (
        alt.Chart(long_df)
        .mark_line(strokeWidth=2.5)
        .encode(
            x=alt.X("date:T", title="날짜"),
            y=alt.Y("ret:Q", title="수익률(%)"),
            color=alt.Color(
                "name:N",
                title="종목",
                legend=alt.Legend(orient="top", symbolType="stroke"),
                scale=alt.Scale(scheme="category10"),
            ),
            tooltip=[
                alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
                alt.Tooltip("name:N", title="종목"),
                alt.Tooltip("ret:Q", title="수익률(%)", format=".2f"),
            ],
        )
        .properties(title="시작일 기준 수익률 비교", height=520, width="container")
        .configure(aria=False)
        .interactive()
    )


def _fetch_ticker_data(ticker: str, fetch_period: str, session):
    stock = yf.Ticker(ticker, session=session)
    info = stock.info or {}
    history = stock.history(period=fetch_period)
    return stock, info, history


def fetch_stock_data(ticker: str, period: str, session):
    fetch_period = HISTORY_FETCH.get(period, period)
    _stock, info, history = retry_on_rate_limit(_fetch_ticker_data, ticker, fetch_period, session)

    if history.empty and re.fullmatch(r"\d{6}\.KS", ticker):
        ticker = ticker[:-3] + ".KQ"
        _stock, info, history = retry_on_rate_limit(_fetch_ticker_data, ticker, fetch_period, session)

    company_name = info.get("longName") or info.get("shortName") or ticker
    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    previous_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
    currency = info.get("currency") or "USD"

    if current_price is None and not history.empty:
        current_price = float(history["Close"].iloc[-1])
    if previous_close is None and len(history) >= 2:
        previous_close = float(history["Close"].iloc[-2])

    if current_price is None or previous_close is None or history.empty:
        raise ValueError("종목 데이터를 찾을 수 없습니다. 종목 코드 또는 종목명을 확인해 주세요.")

    history = add_moving_averages(history)
    history = slice_display_window(history, period)
    change_pct = (current_price - previous_close) / previous_close * 100
    return ticker, company_name, current_price, change_pct, currency, history


def history_close(history) -> pd.Series:
    close = history["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return close.astype(float)


def dated_close_series(history) -> pd.Series:
    close = history_close(history).copy()
    index = close.index
    if isinstance(index, pd.DatetimeIndex):
        if index.tz is not None:
            index = index.tz_localize(None)
        close.index = pd.to_datetime(index.date)
    return close.sort_index()


def price_on_or_after(close: pd.Series, buy_date):
    buy_ts = pd.Timestamp(buy_date)
    later = close[close.index >= buy_ts]
    if not later.empty:
        return float(later.iloc[0]), later.index[0].date()
    earlier = close[close.index <= buy_ts]
    if not earlier.empty:
        return float(earlier.iloc[-1]), earlier.index[-1].date()
    return None, None


def render_return_calculator(result):
    with st.sidebar:
        st.markdown(
            '<div class="notranslate" translate="no" lang="ko" '
            'style="font-size:1.5rem; font-weight:700; margin: 0 0 0.75rem 0;">수익률 계산기</div>',
            unsafe_allow_html=True,
        )
        if not result:
            st.info("먼저 데이터를 조회해 주세요.")
            return

        comparisons = result.get("comparisons") or [result]
        if len(comparisons) > 1:
            labels = [f"{item['company_name']} ({item['ticker']})" for item in comparisons]
            selected = st.selectbox("종목", labels, key="calc_ticker")
            item = comparisons[labels.index(selected)]
        else:
            item = result
            st.caption(f"{item['company_name']} ({item['ticker']})")

        currency = item["currency"]
        close = dated_close_series(item["history"])
        min_date = close.index.min().date()
        max_date = close.index.max().date()
        buy_date = st.date_input(
            "매수 날짜",
            value=min_date,
            min_value=min_date,
            max_value=max_date,
            key=f"calc_buy_date_{item['ticker']}",
        )

        looked_up_buy, actual_date = price_on_or_after(close, buy_date)
        if looked_up_buy is None or looked_up_buy <= 0:
            st.error("선택한 날짜의 가격을 찾을 수 없습니다.")
            return
        if actual_date != buy_date:
            st.caption(f"해당일 시세 반영일: {actual_date}")

        price_key = f"calc_buy_price_text_{item['ticker']}_{buy_date}"
        if price_key not in st.session_state:
            if currency == "KRW":
                st.session_state[price_key] = f"{int(round(looked_up_buy)):,}"
            else:
                st.session_state[price_key] = f"{looked_up_buy:,.2f}"
        raw_price = st.text_input("매수 단가", key=price_key)
        buy_price = parse_number_input(raw_price)
        if buy_price is None or buy_price <= 0:
            st.warning("매수 단가를 숫자로 입력해 주세요.")
            return
        buy_qty = st.number_input(
            "매수 수량",
            min_value=0,
            value=1,
            step=1,
            format="%d",
            key=f"calc_buy_qty_int_{item['ticker']}",
        )
        buy_amount = buy_price * buy_qty
        amount_key = f"calc_buy_amount_{item['ticker']}"
        st.session_state[amount_key] = format_price(buy_amount, currency)
        st.text_input(
            "매수 금액",
            disabled=True,
            key=amount_key,
        )

        current_price = float(item["current_price"])
        st.write(f"**현재 단가:** {format_price(current_price, currency)}")

        same_qty = st.checkbox("현재 수량 = 매수 수량", value=True, key=f"calc_same_qty_{item['ticker']}")
        if same_qty:
            current_qty = buy_qty
            st.write(f"**현재 수량:** {current_qty:g}")
        else:
            current_qty = st.number_input(
                "현재 수량",
                min_value=0.0,
                value=float(buy_qty),
                step=1.0,
                key=f"calc_current_qty_{item['ticker']}",
            )

        current_amount = current_price * current_qty
        st.write(f"**현재 금액: {format_price(current_amount, currency)}**")

        if buy_amount <= 0:
            st.warning("매수 금액이 0이라 수익률을 계산할 수 없습니다.")
            return

        return_pct = (current_amount / buy_amount - 1) * 100
        profit_amount = current_amount - buy_amount
        color = "#e03131" if return_pct >= 0 else "#1c7ed6"
        profit_text = (
            f"+{format_price(profit_amount, currency)}"
            if profit_amount >= 0
            else format_price(profit_amount, currency)
        )
        st.markdown(
            f"<p style='color:{color}; font-size:1rem; margin-top:0.25rem; margin-bottom:0.25rem;'>"
            f"<strong>수익 금액:</strong> <strong>{profit_text}</strong></p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<p style='color:{color}; font-size:1rem; margin: 0;'>"
            f"<strong>수익률:</strong> <strong>{return_pct:+.2f}%</strong></p>",
            unsafe_allow_html=True,
        )


def add_moving_averages(history):
    result = history.copy()
    close = history_close(result)
    result["MA20"] = close.rolling(window=20, min_periods=20).mean()
    result["MA60"] = close.rolling(window=60, min_periods=60).mean()
    return result


def slice_display_window(history, period: str):
    days = DISPLAY_DAYS.get(period)
    if not days:
        return history
    start = history.index.max() - pd.Timedelta(days=days)
    return history[history.index >= start]


def ohlc_frame(history) -> pd.DataFrame:
    data = {}
    for name in ("Open", "High", "Low", "Close", "Volume", "MA20", "MA60"):
        if name not in history.columns:
            continue
        column = history[name]
        if isinstance(column, pd.DataFrame):
            column = column.iloc[:, 0]
        data[name] = column.astype(float).to_numpy()
    index = history.index
    if isinstance(index, pd.DatetimeIndex):
        index = pd.to_datetime(index.date)
    return pd.DataFrame(data, index=index)


def close_series(history) -> pd.Series:
    return ohlc_frame(history)["Close"].rename("종가")


def make_price_chart(history, company_name: str, currency: str, show_ma20: bool, show_ma60: bool):
    series = close_series(history)
    x_values = [idx.strftime("%Y-%m-%d") for idx in series.index]
    y_values = [float(value) for value in series.tolist()]
    frame = ohlc_frame(history)

    if currency == "KRW":
        hovertemplate = "날짜: %{x}<br>가격: %{y:,.0f}원<extra></extra>"
        tickformat = ",.0f"
        ma_hover = "날짜: %{x}<br>%{fullData.name}: %{y:,.0f}원<extra></extra>"
    elif currency == "USD":
        hovertemplate = "날짜: %{x}<br>가격: $%{y:,.2f}<extra></extra>"
        tickformat = ",.2f"
        ma_hover = "날짜: %{x}<br>%{fullData.name}: $%{y:,.2f}<extra></extra>"
    else:
        hovertemplate = "날짜: %{x}<br>가격: %{y:,.2f}<extra></extra>"
        tickformat = ",.2f"
        ma_hover = "날짜: %{x}<br>%{fullData.name}: %{y:,.2f}<extra></extra>"

    traces = [
        go.Scatter(
            x=x_values,
            y=y_values,
            mode="lines",
            name="종가",
            line={"color": "#1f77b4", "width": 2},
            hovertemplate=hovertemplate,
        )
    ]
    if show_ma20 and "MA20" in frame.columns:
        traces.append(
            go.Scatter(
                x=x_values,
                y=frame["MA20"].astype(float).tolist(),
                mode="lines",
                name="MA20",
                line={"color": "orange", "width": 2},
                hovertemplate=ma_hover,
            )
        )
    if show_ma60 and "MA60" in frame.columns:
        traces.append(
            go.Scatter(
                x=x_values,
                y=frame["MA60"].astype(float).tolist(),
                mode="lines",
                name="MA60",
                line={"color": "green", "width": 2},
                hovertemplate=ma_hover,
            )
        )

    fig = go.Figure(data=traces)
    fig.update_layout(
        title=f"{company_name} 주가 추이",
        xaxis_title="날짜",
        yaxis_title="가격",
        yaxis_tickformat=tickformat,
        height=520,
        autosize=True,
        margin=dict(l=40, r=20, t=60, b=40),
        hovermode="x unified",
        template="plotly_white",
    )
    fig.update_xaxes(showgrid=True, type="date")
    fig.update_yaxes(showgrid=True)
    return fig


def make_altair_candlestick(history, company_name: str, show_ma20: bool, show_ma60: bool):
    frame = ohlc_frame(history).reset_index()
    date_col = frame.columns[0]
    frame = frame.rename(
        columns={
            date_col: "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "MA20": "ma20",
            "MA60": "ma60",
        }
    )
    frame["date"] = pd.to_datetime(frame["date"])
    frame["body_low"] = frame[["open", "close"]].min(axis=1)
    frame["body_high"] = frame[["open", "close"]].max(axis=1)
    volume_threshold = float(frame["volume"].quantile(0.75))
    frame["high_volume"] = frame["volume"] >= volume_threshold
    frame["volume_flag"] = ["예" if flag else "아니오" for flag in frame["high_volume"]]
    frame["bar_color"] = [
        ("#e03131" if up else "#1c7ed6") if high_vol else ("#ffa8a8" if up else "#a5d8ff")
        for up, high_vol in zip(frame["close"] >= frame["open"], frame["high_volume"])
    ]

    color = alt.condition(
        alt.datum.close >= alt.datum.open,
        alt.value("red"),
        alt.value("blue"),
    )
    wick_size = max(1, min(2, int(80 / max(len(frame), 1))))
    body_size = max(4, min(16, int(360 / max(len(frame), 1))))
    zoom = alt.selection_interval(bind="scales", encodings=["x"])
    hover = alt.selection_point(fields=["date"], nearest=True, on="mouseover", empty=True)
    opacity = alt.condition(hover, alt.value(1), alt.value(0.35))
    candle_tooltips = [
        alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
        alt.Tooltip("open:Q", title="시가", format=",.2f"),
        alt.Tooltip("high:Q", title="고가", format=",.2f"),
        alt.Tooltip("low:Q", title="저가", format=",.2f"),
        alt.Tooltip("close:Q", title="종가", format=",.2f"),
        alt.Tooltip("volume:Q", title="거래량", format=","),
    ]
    volume_tooltips = [
        alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
        alt.Tooltip("volume:Q", title="거래량", format=","),
        alt.Tooltip("volume_flag:N", title="거래량 많음"),
    ]
    base = alt.Chart(frame)
    wicks = base.mark_rule(strokeWidth=wick_size).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(labels=False, ticks=False)),
        y=alt.Y("low:Q", title="가격", scale=alt.Scale(zero=False)),
        y2="high:Q",
        color=color,
        opacity=opacity,
        tooltip=candle_tooltips,
    )
    bodies = base.mark_bar(size=body_size).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(labels=False, ticks=False)),
        y="body_low:Q",
        y2="body_high:Q",
        color=color,
        opacity=opacity,
        tooltip=candle_tooltips,
    )
    layers = [wicks, bodies]
    if show_ma20 and "ma20" in frame.columns:
        layers.append(
            base.mark_line(color="orange", strokeWidth=2).encode(
                x=alt.X("date:T", title=None, axis=alt.Axis(labels=False, ticks=False)),
                y=alt.Y("ma20:Q", title="가격"),
                tooltip=[
                    alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
                    alt.Tooltip("ma20:Q", title="MA20", format=",.2f"),
                ],
            )
        )
    if show_ma60 and "ma60" in frame.columns:
        layers.append(
            base.mark_line(color="green", strokeWidth=2).encode(
                x=alt.X("date:T", title=None, axis=alt.Axis(labels=False, ticks=False)),
                y=alt.Y("ma60:Q", title="가격"),
                tooltip=[
                    alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
                    alt.Tooltip("ma60:Q", title="MA60", format=",.2f"),
                ],
            )
        )
    candle = alt.layer(*layers).properties(
        title=f"{company_name} 캔들스틱",
        height=380,
        width="container",
    )
    volume = base.mark_bar(size=body_size).encode(
        x=alt.X("date:T", title="날짜"),
        y=alt.Y("volume:Q", title="거래량"),
        color=alt.Color("bar_color:N", scale=None, legend=None),
        opacity=opacity,
        tooltip=volume_tooltips,
    ).properties(height=160, width="container")
    return (
        alt.vconcat(candle, volume, spacing=8)
        .resolve_scale(x="shared")
        .add_params(zoom, hover)
        .configure(aria=False)
    )


def render_price_chart(
    history,
    company_name: str,
    currency: str,
    comparisons: list[dict] | None = None,
    watchlist_quotes: list[dict] | None = None,
):
    ma_col1, ma_col2 = st.columns(2)
    with ma_col1:
        show_ma20 = st.checkbox("20일 이동평균선 (주황)", value=True, key="show_ma20")
    with ma_col2:
        show_ma60 = st.checkbox("60일 이동평균선 (초록)", value=True, key="show_ma60")

    tabs = ["관심 종목", "종목 비교", "선 차트", "캔들스틱"]
    tab_objects = st.tabs(tabs)
    tab_map = dict(zip(tabs, tab_objects))

    with tab_map["관심 종목"]:
        render_watchlist_tab(watchlist_quotes)

    with tab_map["종목 비교"]:
        items = comparisons or []
        if len(items) >= 2:
            frame = comparison_frame(items)
            if frame.empty:
                st.warning("비교할 수익률 데이터가 없습니다.")
            else:
                st.altair_chart(
                    make_altair_comparison_chart(frame),
                    width="stretch",
                    theme=None,
                    key="comparison_chart",
                )
        else:
            st.info("종목을 2개 이상 조회하면 시작일 기준 수익률 비교 차트가 여기에 표시됩니다. 예: MSFT, AAPL")

    with tab_map["선 차트"]:
        chart_data = pd.DataFrame({"종가": close_series(history)})
        frame = ohlc_frame(history)
        if show_ma20 and "MA20" in frame.columns:
            chart_data["MA20"] = frame["MA20"].to_numpy()
        if show_ma60 and "MA60" in frame.columns:
            chart_data["MA60"] = frame["MA60"].to_numpy()
        st.line_chart(chart_data, x_label="날짜", y_label="가격", height=520, width="stretch")
        st.plotly_chart(
            make_price_chart(history, company_name, currency, show_ma20, show_ma60),
            width="stretch",
            height=520,
            theme=None,
            on_select="ignore",
            key="line_plotly_chart",
        )
    with tab_map["캔들스틱"]:
        st.altair_chart(
            make_altair_candlestick(history, company_name, show_ma20, show_ma60),
            width="stretch",
            theme=None,
        )


st.set_page_config(page_title="주식 데이터 분석기", layout="wide")
components.html(
    """
    <script>
    const doc = window.parent.document;
    doc.documentElement.setAttribute("lang", "ko");
    doc.documentElement.setAttribute("translate", "no");
    doc.documentElement.classList.add("notranslate");
    if (doc.body) {
      doc.body.setAttribute("translate", "no");
      doc.body.classList.add("notranslate");
    }
    </script>
    """,
    height=0,
)
st.title("주식 데이터 분석기")
st.markdown(
    """
    <style>
    div[data-testid="stFormSubmitButton"] button,
    .stFormSubmitButton button {
        background-color: #1c7ed6 !important;
        color: #ffffff !important;
        border: 1px solid #1c7ed6 !important;
    }
    div[data-testid="stFormSubmitButton"] button:hover,
    .stFormSubmitButton button:hover {
        background-color: #1864ab !important;
        color: #ffffff !important;
        border-color: #1864ab !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stTextInput"]:has(input:disabled) label p {
        font-weight: 700 !important;
        color: #111111 !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stTextInput"] input:disabled {
        font-weight: 700 !important;
        color: #111111 !important;
        -webkit-text-fill-color: #111111 !important;
        opacity: 1 !important;
    }
    [data-testid="stCheckbox"] label[data-selected] div:has(> svg) {
        background-color: #31333F !important;
        border-color: #31333F !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<p class="notranslate" translate="no" lang="ko" '
    'style="color: rgba(49, 51, 63, 0.6); font-size: 0.875rem; margin: 0 0 0.75rem 0;">'
    "종목 비교를 원하면 , 로 구분해서 입력하셔요.</p>",
    unsafe_allow_html=True,
)

if "stock_query" not in st.session_state:
    st.session_state.stock_query = "005930.KS"

with st.form("stock_lookup", border=False, enter_to_submit=True):
    query = st.text_input(
        "코드 또는 종목",
        key="stock_query",
        placeholder="예: 005930.KS, 000660.KS",
        help="종목 코드와 종목명을 입력할 수 있습니다. 여러 종목은 쉼표로 구분해 비교합니다. Enter로 조회합니다.",
    )
    period_label = st.selectbox("기간", list(PERIOD_MAP.keys()), key="period_label")
    submitted = st.form_submit_button("데이터 조회", type="primary")

saved_tickers = load_saved_tickers()
if saved_tickers:
    name_by_ticker = {item["ticker"]: item["name"] for item in saved_tickers}
    st.pills(
        "저장된 종목",
        options=[item["ticker"] for item in saved_tickers],
        selection_mode="multi",
        format_func=lambda ticker: name_by_ticker.get(ticker, ticker),
        key="saved_ticker_pills",
        on_change=apply_saved_ticker_selection,
        help="이전에 조회한 종목입니다. 클릭하면 입력란에 채워지고, 고른 뒤 삭제도 할 수 있습니다.",
    )
    delete_col, clear_col = st.columns(2, gap="small")
    with delete_col:
        st.button(
            "삭제",
            key="delete_saved_selected",
            on_click=delete_selected_saved_tickers,
            use_container_width=True,
        )
    with clear_col:
        st.button(
            "전체 삭제",
            key="delete_saved_all",
            on_click=delete_all_saved_tickers,
            use_container_width=True,
        )
    if st.session_state.pop("delete_saved_warning", False):
        st.warning("삭제할 종목을 먼저 선택해 주세요.")

if submitted or st.session_state.pop("pending_lookup", False):
    perform_stock_lookup(
        (st.session_state.get("stock_query") or query or "").strip(),
        period_label,
    )

result = st.session_state.get("stock_result")
render_return_calculator(result)
if result:
    st.success("데이터 조회가 완료되었습니다.")
    comparisons = result.get("comparisons") or [result]
    if len(comparisons) >= 2:
        frame = comparison_frame(comparisons)
        last_row = frame.iloc[-1] if not frame.empty else None
        rows = []
        used_names: set[str] = set()
        for item in comparisons:
            name = comparison_label(item, used_names)
            ret = None
            if last_row is not None and name in last_row.index and pd.notna(last_row[name]):
                ret = float(last_row[name])
            rows.append(
                {
                    "종목": name,
                    "코드": item["ticker"],
                    "현재가": format_price(item["current_price"], item["currency"]),
                    "전일 대비": f"{item['change_pct']:+.2f}%",
                    "기간 수익률": f"{ret:+.2f}%" if ret is not None else "-",
                }
            )
        st.write("**비교 종목**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.write(f"**회사명:** {result['company_name']}")
        st.write(f"**종목 코드:** {result['ticker']}")
        st.write(f"**현재가:** {format_price(result['current_price'], result['currency'])}")
        st.write(f"**전일 대비 등락률:** {result['change_pct']:+.2f}%")
    for warning in result.get("fetch_errors") or []:
        st.warning(warning)
    render_price_chart(
        result["history"],
        result["company_name"],
        result["currency"],
        comparisons,
        st.session_state.get("watchlist_quotes") or [],
    )
    st.subheader("역사적 데이터")
    history_table = result["history"].drop(columns=["MA20", "MA60"], errors="ignore")
    st.dataframe(history_table, width="stretch")
