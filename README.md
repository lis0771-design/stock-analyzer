# 주식 데이터 분석기

Streamlit과 yfinance를 사용한 주식 조회·비교 앱입니다. 종목 코드나 종목명으로 시세를 찾고, 차트·관심 종목·수익률 계산을 한 화면에서 볼 수 있습니다.

## 기능

- 종목 코드 또는 한글/영문 종목명으로 조회 (예: `005930.KS`, `삼성전자`, `AAPL`)
- 여러 종목을 쉼표로 구분해 시작일 기준 수익률 비교
- 선 차트, 캔들스틱, 20일/60일 이동평균선
- 관심 종목 현재가 (최대 10개)
- 저장된 종목 클릭 선택 및 삭제
- 사이드바 수익률 계산기

## 실행 방법

Python 3.10 이상을 권장합니다.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

macOS / Linux에서는 가상환경 활성화가 다릅니다.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

브라우저에서 안내된 주소(보통 `http://localhost:8501`)로 접속합니다.

## 사용 방법

1. **코드 또는 종목**에 종목을 입력합니다.
2. 비교하려면 쉼표로 구분합니다. 예: `005930.KS, 000660.KS` 또는 `삼성전자, 애플`
3. 기간을 고른 뒤 **데이터 조회**를 누르거나 Enter를 누릅니다.
4. **관심 종목**, **종목 비교**, **선 차트**, **캔들스틱** 탭에서 결과를 확인합니다.
5. 왼쪽 사이드바에서 매수 날짜·수량으로 수익률을 계산할 수 있습니다.

한국 종목 6자리 코드만 입력하면 `.KS`(코스피)를 붙이고, 데이터가 없으면 `.KQ`(코스닥)로 다시 조회합니다.

## 프로젝트 구성

| 파일 | 설명 |
| --- | --- |
| `app.py` | Streamlit 앱 |
| `requirements.txt` | 필요한 패키지 |
| `.streamlit/config.toml` | Streamlit 서버 설정 |
| `test_yfinance.py` | yfinance 조회 테스트 스크립트 |

관심 종목 목록은 로컬 `saved_tickers.json`에 저장되며 Git에는 올리지 않습니다.
