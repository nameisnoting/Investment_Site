# 자산 포트폴리오 & 종목 추천 시스템

개인 자산 현황을 입력하면 **시장 국면 분석 + 종목 스크리닝 + 위험 성향 반영**을 통해
맞춤형 포트폴리오와 매수 타이밍을 추천하는 웹 애플리케이션.

> 🔗 **Live Demo**: _배포 URL 추가 예정_

---

## 📌 핵심 기능

1. **자산 입력 폼** — 현금/예금/적금/IRP/투자금/월수입 입력
2. **투자 성향 슬라이더** (5단계) — 안정적 ↔ 공격적 선택에 따라 자동 비중 조정
3. **실시간 시장 국면 분석** — 5개 신호 종합으로 강세/약세 평가
4. **종목 추천** — 미국(S&P500 풀) + 한국(KOSPI 풀) + 코어 ETF 분산
5. **진입 플랜** — 종목별로 즉시매수 / 분할매수 / 눌림목 대기 / 역발상 매수 자동 판정
6. **결과 자동 저장** — 최근 5개 시나리오 브라우저(localStorage)에 보존

---

## 🧠 분석 로직 (요약)

### 1단계: 프로필 구성
입력값을 받아 **고용 상태 / 자산 tier / 성향**을 자동 판정.
자산 tier에 따라 코어 ETF 비중과 위성(개별주) 비중이 차등 적용됨.

### 2단계: 시장 국면 분석 (5개 신호 종합)
| 신호 | 가중치 | 데이터 |
|------|------|------|
| 추세 (MA200 + 모멘텀) | 25% | S&P 500 |
| VIX 역발상 매핑 | 20% | ^VIX |
| 시장 breadth | 20% | us_pool 45종목 |
| 장단기 금리차 | 15% | ^TNX − ^IRX |
| 섹터 로테이션 (공격주 vs 방어주) | 20% | XLK/XLY/XLF vs XLP/XLU/XLV |

가중합으로 `composite_score (0~1)` 산출 → 주식/현금 비중 결정의 기반.

추가로 **fear_score** (VIX 30↑ AND 시장 -15%↓) 발동 시 → 종목 진입을 "역발상 공격 매수"로 격상.

### 3단계: 종목 스크리닝
**퀄리티 게이트 통과** → **펀더멘털 60% + 기술적 40%** 가중합으로 종합 점수.

펀더멘털 항목: ROE, PBR, PER, 매출성장, 순이익률, FCF 마진, 영업이익률, PEG (총 100점)
기술적 항목: RSI, MA 추세, 20일 모멘텀 (총 100점)

### 4단계: 진입 플랜 (5가지 액션)
🔥 **AGGRESSIVE_BUY** (역발상) → 🟢 IMMEDIATE → 🟡 SPLIT_BUY → 🟠 WAIT_PULLBACK → 🔴 AVOID
+ 코어 ETF는 항상 💰 **DCA (월 적립)** 으로 분류

### 5단계: 비중 배분
- 주식 vs 현금: `composite_score` 기반
- 코어 vs 위성: 자산 tier + 성향 슬라이더
- 섹터 집중도 제한 (한 섹터 최대 2종목)
- 비중 → 실제 금액 (KRW/USD) 환산

---

## 🛠 기술 스택

| 영역 | 사용 기술 |
|------|----------|
| 백엔드 | Python 3.12 · Flask · yfinance · pandas · numpy |
| 프론트엔드 | Vanilla JS · CSS3 (Dark theme) |
| 데이터 캐시 | 디스크 기반 pickle (6시간 TTL) |
| 배포 | Gunicorn · Render |
| 저장 | 브라우저 localStorage (서버 DB 없음 — 프라이버시 보존) |

---

## 🚀 로컬 실행 방법

```powershell
# 1. 가상환경 생성
python -m venv .venv
.\.venv\Scripts\activate

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 실행
python app.py
```

브라우저에서 http://127.0.0.1:5000 접속.

> 💡 Windows 사용자는 `start.bat` 더블클릭으로 Flask + ngrok 동시 실행 가능.

---

## 📁 디렉토리 구조

```
Investment_Site/
├── app.py               # Flask 백엔드 (HTTP 라우팅, 입력→프로필 변환)
├── advisor.py           # 핵심 분석 엔진 (MacroFilter, Screener, Portfolio)
├── config.py            # 모든 파라미터 (티커 풀, 신호 가중치, 임계값)
├── models.py            # 데이터 클래스 (InvestorProfile, MarketRegime, StockScore)
├── cache.py             # 디스크 캐시 (yfinance 호출 절약)
├── backtest.py          # 워크포워드 백테스트 엔진
├── main.py              # CLI 실행 진입점
├── templates/index.html # UI 마크업
├── static/style.css     # 다크 테마 스타일
├── static/script.js     # 폼/저장/렌더링 로직
├── requirements.txt     # Python 의존성
├── Procfile             # 배포용 gunicorn 명령
└── runtime.txt          # Python 버전 지정
```

---

## ⚠️ 면책 조항

본 시스템은 **참고용 자료**이며, 실제 투자 의사결정에 따른 책임은 투자자 본인에게 있습니다.
yfinance 데이터의 정확성/지연/누락은 보장되지 않으며, 시장 상황은 분석 시점 이후 급변할 수 있습니다.
