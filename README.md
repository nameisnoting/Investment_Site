# 자산 포트폴리오 & 종목 추천 시스템

> **개인 투자자가 자기 자산에 맞는 분산 포트폴리오를 1분 안에 받을 수 있는 웹 서비스**
> 시장 데이터 + 5개 거시 신호 + 사용자 위험 성향을 종합해, 코어 ETF부터 개별주까지 비중과 매수 타이밍을 추천합니다.

🔗 **Live Demo**: [investment-site.onrender.com](https://investment-site-XXXX.onrender.com) _(첫 접속 시 15분 절전 깨우기에 30초 소요)_
📂 **Repo**: [github.com/nameisnoting/Investment_Site](https://github.com/nameisnoting/Investment_Site)

---

## 📌 문제 정의

개인 투자자가 분산 포트폴리오를 짤 때 마주치는 3가지 벽:

1. **천편일률적 추천** — 시중 로보어드바이저는 자산 규모/소득/위험성향에 거의 반응하지 않음
2. **블랙박스** — "왜 이렇게 추천했나"가 안 보임 → 신뢰 ↓
3. **개별 종목까지 보려면 별도 노력** — ETF만 추천하거나, 개별주 분석은 따로 해야 함

이 시스템은 **위 3개를 동시에 해결**합니다:
- 자산/소득/성향에 따라 코어:위성 비중과 종목 풀 자체가 동적 변경
- 모든 점수와 신호를 화면에 노출 (5개 시장 신호, 8개 펀더멘털 지표, 진입 플랜 사유)
- 코어 ETF + 개별주를 같은 화면에서 추천

---

## 👥 타겟 사용자

| 페르소나 | 상황 | 이 도구의 가치 |
|---------|------|--------------|
| **20대 사회 초년생** | 자산 ~5천만, 투자 경험 적음 | 자동으로 보수적 분배 (ETF 80%+) |
| **30대 직장인** | 자산 1~3억, 본업 바빠서 종목 리서치 시간 부족 | 매주 한 번 결과 받고 매월 적립 |
| **40대 자영업/은퇴 준비** | 자산 5억+, 자산 보존 중요 | 위험 슬라이더로 보수↔공격 즉시 토글 |

---

## 🎯 핵심 가치 제안

### 1. 4가지 포트폴리오 전략 모드 (사용자 선택)
| 모드 | 평가 핵심 | 종목 풀 | 적합 사용자 |
|------|---------|---------|----------|
| **장기투자** (기본) | 펀더멘털 60% + 기술 40% | 우량주 19개 + ETF 5개 | 5~10년 장기 보유 |
| **폭등시그널** | 매출성장 + FCF전환 + 볼린저 스퀴즈 | 소형 그로스주 24개 + 레버리지 ETF (TQQQ/SOXL/UPRO) | 중단기 알파 추구 |
| **모멘텀 추종** | 20일 모멘텀 + 거래량 + RSI 강도 | 동적 (30일 +15%↑ 통과 종목만) | 단기 트레이딩 (현금 비중 강제 ↑) |
| **혼합** | 위 세 결과를 슬라이더 비중으로 합성 | 모든 풀 | 균형 선호 |

### 2. 개인화 (Tier × 성향 × 소득)
- **자산 tier 4단계** (starter/small/mid/large)에 따라 코어/위성 비중 차등
- **5단계 위험 슬라이더** (안정적↔공격적)로 예금 보존율 + 코어 비중 + 레버리지 비중 + 모드 합성 가중치 즉시 조정
- **소득 유무 + 월수입**으로 비상자금 자동 산정 + 보수 모드 자동 발동

### 3. 투명한 분석 로직
- **시장 5신호 가중합** (추세/VIX/breadth/금리차/섹터로테이션)을 모두 화면 노출
- 종목별 **8개 펀더멘털 지표** + **3개 기술 지표** + 진입 플랜 사유 표시
- "왜 보류" "왜 즉시매수"가 명확

### 4. 백테스팅 (buy-and-hold + S&P 500 비교)
- 추천 받은 포트폴리오를 1년 / 3년 / 5년 전에 같은 비중으로 샀을 때의 시뮬레이션
- CAGR, MDD, Sharpe, Alpha, Beta 6개 지표 + SVG 라인 차트
- 요청 시에만 실행 (자동 X)

### 5. 데이터 기반 객관성
- 시장: yfinance 실시간 (S&P 500, KOSPI, VIX, 섹터 ETF, 국채)
- 펀더멘털: Finnhub /stock/metric (ROE, PBR, PER, 부채/자본 등)
- 캐시 6시간 TTL로 안정성 + 응답 속도 균형
- NaN/Inf 자동 sanitize로 클라이언트 호환성 보장

---

## 🧠 핵심 기능 흐름

```
사용자 입력 → 프로필 구성 → 시장 진단 → 종목 평가 → 진입 타이밍 → 비중 배분
   ↓                                                                    ↓
[자산/소득/슬라이더]                                              [결과 표시 + 저장]
```

### 단계별 요약

| 단계 | 무엇을 하나 | 핵심 알고리즘 |
|------|-----------|--------------|
| 1. 프로필 | 고용 상태 / 자산 tier / 성향 판정 | 분기 로직 + 슬라이더 가중치 |
| 2. 시장 진단 | 5개 신호 종합 점수 산출 | 가중합 (trend 25% + VIX 20% + breadth 20% + 금리 15% + 섹터 20%) |
| 3. 종목 평가 | 퀄리티 게이트 → 종합 점수 | 펀더멘털 60% + 기술 40% |
| 4. 진입 플랜 | 5가지 액션 자동 결정 | RSI/추세/fear_score 기반 분기 |
| 5. 비중 배분 | 시장 점수 + 자산 + 성향 종합 | composite × tier 매트릭스 |

자세한 분석 로직은 [docs/decisions.md](docs/decisions.md) 참조.

---

## 🔄 제품 진화 과정 (Changelog)

이 프로젝트는 사용자 피드백을 받으며 **10번의 메이저 업데이트**를 거쳤습니다.

| 버전 | 시점 | 추가 기능 / 변경 | 트리거 |
|------|------|----------------|-------|
| **v1.0** | 초기 | 자산 입력 폼 + 단순 자산 배분 추천 | 본인용 도구로 시작 |
| **v2.0** | +UX | 케밥 메뉴 + 최근 5개 결과 자동 저장 (localStorage) | "매번 입력하기 귀찮음" |
| **v3.0** | +자산 클래스 | 코어 ETF / 위성 개별주 분리, ETF는 DCA(월 적립) | "개별주뿐만 아니라 안전자산도 필요" |
| **v4.0** | +개인화 | 5단계 투자 성향 슬라이더 (예금 유지율 + 코어 비중 배율) | "예금까지 다 동원되는 게 부담스러움" |
| **v5.0** | +분석 고도화 | 시장 분석을 5신호 종합 + VIX 역발상 매핑 + 역발상 매수 트리거 | "VIX 30↑ 무조건 관망은 잘못" |
| **v6.0** | +운영 안정성 | 펀더멘털 데이터 소스를 yfinance → Twelve Data → Finnhub으로 2단계 피벗 | "배포 후 IP 차단 발견" |
| **v7.0** | +다중 전략 (Phase A) | 포트폴리오 성향 드롭다운 4종 + 저장 5→20개 + 페이지네이션 + 이름 + 개별삭제 | "장기투자만으론 단기 폭등 욕망 못 채움" |
| **v8.0** | +폭등시그널 (Phase B) | SurgeScorer (매출성장+FCF전환+볼린저 스퀴즈+거래량 폭발) + 레버리지 ETF (TQQQ/SOXL/UPRO) | 같은 피드백 |
| **v8.1** | +모멘텀 (Phase C) | MomentumScorer (RSI 70+ 종목 추출) + 동적 종목 필터링 + 단기용 현금 비중 강제 ↑ | 같은 피드백 |
| **v8.2** | +혼합 (Phase D) | 3개 전략 결과를 risk_level별 가중 합성 (예: risk=3 → 장기50% + 폭등30% + 모멘텀20%) | 같은 피드백 |
| **v9.0** | +백테스팅 (Phase E) | buy-and-hold 시뮬레이션 + S&P 500 비교 + SVG 차트 + 6개 성과 지표 | "결과 신뢰할 만한가 검증 필요" |
| **v9.1** | bug fix | NaN/Inf → null sanitize (numpy.float64 numbers.Real 처리) | "JSON.parse Unexpected token N" |

각 버전의 **왜?** 와 **어떻게 결정했나?** 는 [docs/decisions.md](docs/decisions.md)에 ADR(Architecture Decision Record) 형식으로 17개 기록.

---

## 🛣 로드맵 (v2 계획)

### 우선순위 1: 한국 시장 종목 부활 ⭐ (여전히 미완)
**현재 상태**:
- Render 배포: 한국 개별주 비활성, 한국 노출은 코어 ETF의 EWY(iShares MSCI Korea)로 제공
- 로컬/ngrok 시연: 환경변수 분기로 한국 개별주 활성화 (yfinance 가정 IP라 통과)

**왜 이렇게 됐나**:
- yfinance가 Render 무료 IP에서 차단당함 (rate limit)
- Finnhub 무료 플랜은 한국 종목 미지원
- Twelve Data 무료는 사실상 AAPL 1개만 작동

**원래 의도**: 사용자가 은행/증권사 방문 없이도 한국 장기 투자용 우량주를 자동 추천받는 것. **이게 이 도구의 궁극적 차별점이었음**.

**v2 해결 방안 검토 중**:
- a) Financial Modeling Prep (FMP) — 한국 종목 지원 + 250회/일 무료
- b) Polygon.io — 일부 한국 주식 지원
- c) DART Open API (한국 금융감독원) — 한국 펀더멘털 무료, 별도 통합 필요
- d) 자체 스크래퍼 (네이버 증권 등) — 가장 robust, 가장 작업 큼

**의사결정 기준**: 데이터 신뢰성 + 무료 한도 + 한국 종목 커버리지 비교 후 선정 예정.

### 우선순위 2: 백테스팅 고도화
v1에서 buy-and-hold + 1/3/5년만 구현. 다음 추가 검토:
- **정기 리밸런싱 시뮬레이션** (월/분기마다 비중 재조정)
- **워크포워드 백테스팅** (각 시점마다 추천 다시 받음 — 이미 [backtest.py](backtest.py) 엔진 있음, UI 연결만)
- **전략 모드별 백테스팅 비교** (장기 vs 폭등 vs 모멘텀 동시 차트)

### 우선순위 3: 진입 시그널 알림
사용자가 관심 종목 등록 → 진입 플랜이 SPLIT_BUY → IMMEDIATE로 격상되거나 fear_score 발동 시 이메일 알림.

### 우선순위 4: 모바일 PWA
현재 반응형이지만 모바일 앱처럼 홈 화면에 설치 가능한 PWA(Progressive Web App)로 전환.

### 우선순위 5: 자체 데이터 fallback
yfinance/Finnhub 동시 차단 대비 추가 데이터 소스 (FMP/Polygon) 통합 + 자동 fallback 체인.

---

## 🛠 기술 스택

| 영역 | 사용 기술 | 선택 이유 |
|------|----------|----------|
| 백엔드 | Python 3.12 · Flask · Gunicorn | 분석 로직(pandas/numpy) 활용에 최적, Flask는 단일 페이지 앱에 충분 |
| 데이터 | yfinance (시장지수/ETF) + Finnhub (펀더멘털) | 무료, 다중화로 단일 소스 장애 회피 |
| 프론트 | Vanilla HTML/CSS/JS | 페이지 1개라 React/Next.js는 오버킬, 빌드 단계 없이 단순 배포 |
| 캐시 | 디스크 기반 pickle (6시간 TTL) | 같은 종목 반복 호출 방지, Redis 같은 별도 인프라 불필요 |
| 저장 | 브라우저 localStorage (서버 DB 없음) | 자산 정보 외부 전송 안 함 → 프라이버시 보존 |
| 배포 | Render (무료 플랜) | Vercel은 serverless라 long-running 분석 부적합 |

기술 선택의 **trade-off**는 [docs/decisions.md](docs/decisions.md)에 정리.

---

## 🚀 로컬 실행

```powershell
# 1. 가상환경
python -m venv .venv
.\.venv\Scripts\activate

# 2. 의존성
pip install -r requirements.txt

# 3. (선택) Finnhub 키 설정 — 없으면 yfinance fallback
$env:FINNHUB_API_KEY = "your_key_here"

# 4. 실행
python app.py
```

브라우저 → http://127.0.0.1:5000

Windows에서 더블클릭으로 한 번에 실행: `start.bat`

---

## 📁 디렉토리 구조

```
Investment_Site/
├── app.py                    # Flask 백엔드 (HTTP 라우팅, 입력→프로필 변환, 결과 직렬화)
├── advisor.py                # 핵심 분석 엔진 (MacroFilter / Screener / PortfolioConstructor)
├── config.py                 # 모든 파라미터 (종목 풀, 신호 가중치, 임계값, API 키)
├── models.py                 # 데이터 클래스 (InvestorProfile, MarketRegime, StockScore, EntryPlan)
├── cache.py                  # 디스크 캐시 (6시간 TTL)
├── backtest.py               # 워크포워드 백테스트 엔진 (v2에서 UI 연결 예정)
├── templates/index.html      # UI 마크업
├── static/style.css          # 다크 테마 스타일
├── static/script.js          # 폼 입력 / 저장-복원 / 결과 렌더링
├── docs/
│   ├── decisions.md          # 11개 주요 의사결정 기록 (ADR)
│   ├── scenarios.md          # 페르소나 3개 시나리오 결과 비교
│   └── interview-stories.md  # 면접 예상 질문 답변
├── requirements.txt
├── Procfile                  # gunicorn 시작 명령 (Render용)
├── runtime.txt               # Python 3.12.9
└── start.bat                 # Windows 더블클릭 실행 (Flask + ngrok)
```

---

## ⚠️ 면책 조항

본 시스템은 **참고용 자료**이며, 실제 투자 의사결정에 따른 책임은 투자자 본인에게 있습니다.
시장 데이터의 정확성/지연/누락은 보장되지 않으며, 시장 상황은 분석 시점 이후 급변할 수 있습니다.

---

## 🤝 기여 / 문의

이 프로젝트는 PM 직군 포트폴리오용으로 시작되었으며, 위 v2 로드맵에 대한 의견/제안 환영합니다.
GitHub Issues 또는 PR로 자유롭게 의견 주세요.
