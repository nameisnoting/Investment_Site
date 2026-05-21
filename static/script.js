(() => {
  const form = document.getElementById("asset-form");
  const submitBtn = document.getElementById("submit-btn");
  const resultEl = document.getElementById("result");
  const kebabBtn = document.getElementById("kebab-btn");
  const kebabMenu = document.getElementById("kebab-menu");
  const kebabList = document.getElementById("kebab-list");
  const kebabClearWrap = document.getElementById("kebab-clear-wrap");
  const kebabClear = document.getElementById("kebab-clear");

  const ASSET_FIELDS = ["cash", "deposits", "savings", "retirement", "current_invest"];

  const STORAGE_KEY = "asset_portfolios_v1";
  const MAX_SAVED = 5;
  const LABEL_CHARS = ["A", "B", "C", "D", "E"];

  const RISK_LABELS = {
    1: "안정적",
    2: "적당히 안정적",
    3: "중간",
    4: "적당히 공격적",
    5: "공격적",
  };

  const riskInput = document.getElementById("risk_level");
  const riskLabel = document.getElementById("risk-label");
  function syncRiskLabel() {
    const v = parseInt(riskInput.value, 10);
    riskLabel.textContent = RISK_LABELS[v] || "—";
  }
  riskInput.addEventListener("input", syncRiskLabel);
  syncRiskLabel();

  // ── 숫자 입력: 자동 콤마 ─────────────────────────────────
  function parseNum(v) {
    if (!v) return 0;
    const n = parseFloat(String(v).replace(/,/g, "").trim());
    return Number.isFinite(n) ? n : 0;
  }
  function formatNum(n) {
    if (!Number.isFinite(n) || n === 0) return "";
    return Math.round(n).toLocaleString("ko-KR");
  }

  function attachCommaFormatter(input) {
    input.addEventListener("input", () => {
      const caret = input.selectionStart;
      const before = input.value;
      const n = parseNum(before);
      const formatted = n === 0 ? before.replace(/[^\d,]/g, "") : formatNum(n);
      if (formatted !== before) {
        input.value = formatted;
        // 캐럿 위치 대략 복원
        const diff = formatted.length - before.length;
        try { input.setSelectionRange(caret + diff, caret + diff); } catch {}
      }
      updateTotalAssets();
    });
  }

  document.querySelectorAll('input[type="text"][inputmode="numeric"]').forEach(attachCommaFormatter);

  function updateTotalAssets() {
    const total = ASSET_FIELDS.reduce(
      (sum, id) => sum + parseNum(document.getElementById(id).value), 0
    );
    document.getElementById("total_assets").value = total > 0 ? formatNum(total) : "";
  }

  // ── 라디오 토글 → 월수입 입력 활성/비활성 ────────────────
  const incomeRadios = document.querySelectorAll('input[name="has_income"]');
  const monthlyIncomeInput = document.getElementById("monthly_income");
  function syncIncomeInput() {
    const yes = document.querySelector('input[name="has_income"]:checked').value === "yes";
    monthlyIncomeInput.disabled = !yes;
    if (!yes) monthlyIncomeInput.value = "";
  }
  incomeRadios.forEach(r => r.addEventListener("change", syncIncomeInput));
  syncIncomeInput();

  // ── 제출 ─────────────────────────────────────────────────
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      cash:               parseNum(document.getElementById("cash").value),
      deposits:           parseNum(document.getElementById("deposits").value),
      savings:            parseNum(document.getElementById("savings").value),
      monthly_savings:    parseNum(document.getElementById("monthly_savings").value),
      retirement:         parseNum(document.getElementById("retirement").value),
      monthly_retirement: parseNum(document.getElementById("monthly_retirement").value),
      current_invest:     parseNum(document.getElementById("current_invest").value),
      has_income:         document.querySelector('input[name="has_income"]:checked').value,
      monthly_income:     parseNum(monthlyIncomeInput.value),
      risk_level:         parseInt(riskInput.value, 10) || 3,
    };

    submitBtn.disabled = true;
    submitBtn.textContent = "분석 중...";
    resultEl.innerHTML = `
      <div class="loading">
        <div class="spinner"></div>
        <span>시장 분석 + 종목 스크리닝 중입니다. 처음엔 1~3분 정도 걸릴 수 있어요.</span>
      </div>`;

    try {
      const res = await fetch("/api/advise", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.ok) {
        renderError(data.error || "알 수 없는 오류");
      } else {
        renderResult(data);
        savePortfolio(payload, data);
      }
    } catch (err) {
      renderError(err.message);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "입력 완료";
    }
  });

  // ── 저장/복원 (localStorage) ─────────────────────────────
  function loadSaved() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function writeSaved(arr) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(arr));
    } catch (e) {
      console.warn("localStorage 쓰기 실패:", e);
    }
  }

  function savePortfolio(input, result) {
    const arr = loadSaved();
    arr.push({ savedAt: Date.now(), input, result });
    while (arr.length > MAX_SAVED) arr.shift();   // 오래된 것부터 제거 (FIFO)
    writeSaved(arr);
    renderKebabList();
  }

  function fmtDate(ts) {
    const d = new Date(ts);
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${mm}/${dd} ${hh}:${mi}`;
  }

  function renderKebabList() {
    const arr = loadSaved();
    if (arr.length === 0) {
      kebabList.innerHTML = `<div class="kebab-empty">저장된 항목 없음</div>`;
      kebabClearWrap.hidden = true;
      return;
    }
    kebabList.innerHTML = arr.map((entry, idx) => {
      const label = LABEL_CHARS[idx] || `?${idx}`;
      return `
        <div class="kebab-item" data-idx="${idx}">
          <span class="label">포트폴리오 ${label}</span>
          <span class="meta">${fmtDate(entry.savedAt)}</span>
        </div>`;
    }).join("");
    kebabClearWrap.hidden = false;
  }

  function populateForm(input) {
    const setField = (id, val) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = (val && val !== 0) ? formatNum(val) : "";
    };
    setField("cash",               input.cash);
    setField("deposits",           input.deposits);
    setField("savings",            input.savings);
    setField("monthly_savings",    input.monthly_savings);
    setField("retirement",         input.retirement);
    setField("monthly_retirement", input.monthly_retirement);
    setField("current_invest",     input.current_invest);
    setField("monthly_income",     input.monthly_income);

    const incomeRadio = document.querySelector(
      `input[name="has_income"][value="${input.has_income || "yes"}"]`
    );
    if (incomeRadio) incomeRadio.checked = true;
    syncIncomeInput();
    updateTotalAssets();

    if (input.risk_level) {
      riskInput.value = String(input.risk_level);
      syncRiskLabel();
    }
  }

  function restoreEntry(idx) {
    const arr = loadSaved();
    const entry = arr[idx];
    if (!entry) return;
    populateForm(entry.input);
    renderResult(entry.result);
    closeKebab();
    resultEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ── 케밥 메뉴 이벤트 ────────────────────────────────────
  function openKebab() {
    kebabMenu.hidden = false;
    kebabBtn.setAttribute("aria-expanded", "true");
  }
  function closeKebab() {
    kebabMenu.hidden = true;
    kebabBtn.setAttribute("aria-expanded", "false");
  }

  kebabBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (kebabMenu.hidden) { renderKebabList(); openKebab(); }
    else closeKebab();
  });

  kebabList.addEventListener("click", (e) => {
    const item = e.target.closest(".kebab-item");
    if (!item) return;
    const idx = parseInt(item.dataset.idx, 10);
    if (Number.isFinite(idx)) restoreEntry(idx);
  });

  kebabClear.addEventListener("click", () => {
    if (!confirm("저장된 포트폴리오를 모두 삭제할까요?")) return;
    localStorage.removeItem(STORAGE_KEY);
    renderKebabList();
  });

  document.addEventListener("click", (e) => {
    if (!kebabMenu.hidden && !kebabMenu.contains(e.target) && e.target !== kebabBtn) {
      closeKebab();
    }
  });

  // 초기 1회 렌더 (개수 확인용)
  renderKebabList();

  // ── 결과 렌더링 ──────────────────────────────────────────
  function renderError(msg) {
    resultEl.innerHTML = `<div class="error">오류: ${escape(msg)}</div>`;
  }

  function escape(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  const TIER_LABEL = {
    starter: "초기단계",
    small:   "소형",
    mid:     "중형",
    large:   "대형",
  };

  const STATUS_LABEL = {
    employed:   "안정 (재직)",
    transition: "이직/저소득",
    unemployed: "백수 (소득 없음)",
  };

  const RISK_CLASS = {
    LOW: "good", MEDIUM: "good", HIGH: "warn",
    VERY_HIGH: "bad", EXTREME: "bad", UNKNOWN: "bad",
  };

  const ACTION_TAG = {
    IMMEDIATE: "🟢", SPLIT_BUY: "🟡", WAIT_PULLBACK: "🟠", AVOID: "🔴",
    DCA: "💰",
    AGGRESSIVE_BUY: "🔥",
  };

  function won(n) {
    if (!Number.isFinite(n) || n === 0) return "₩0";
    return "₩" + Math.round(n).toLocaleString("ko-KR");
  }
  function usd(n) {
    if (!Number.isFinite(n)) return "$0";
    return "$" + Math.round(n).toLocaleString("en-US");
  }
  function pct(n, digits = 1) {
    if (!Number.isFinite(n)) return "—";
    return n.toFixed(digits) + "%";
  }

  function renderResult(d) {
    const parts = [];
    parts.push(renderProfile(d.profile));
    parts.push(renderRegime(d.us_regime, d.kr_regime));
    if (d.message) {
      parts.push(`<div class="notice">${escape(d.message)}</div>`);
    }
    if (d.portfolio) {
      parts.push(renderPortfolio(d.portfolio, d.profile));
    }
    parts.push(`
      <p class="footnote">
        ⚠️ 본 결과는 참고용이며 투자 책임은 투자자 본인에게 있습니다.<br />
        🟢 즉시 진입 · 🟡 분할 매수 · 🟠 눌림목 대기 · 🔴 진입 보류<br />
        PBR <code>*</code> = 재무제표 직접 계산 · PER <code>ᶠ</code> = forward PE
      </p>
    `);
    resultEl.innerHTML = parts.join("");
  }

  function renderProfile(p) {
    const tier = TIER_LABEL[p.tier] || p.tier;
    const status = STATUS_LABEL[p.employment_status] || p.employment_status;
    const riskLabel = RISK_LABELS[p.risk_level] || "—";
    const lines = [
      ["총 자산 (자산+투자금)", won(p.grand_total)],
      ["  - 현금성 자산",         won(p.liquid_pool)],
      ["  - 기존 투자금",         won(p.current_invest)],
      ["월 생활비 추정",          won(p.monthly_expense)],
      ["비상자금 (필수 확보)",    won(p.emergency_reserve)],
    ];
    if (p.monthly_income > 0) lines.splice(3, 0, ["월 수입", won(p.monthly_income)]);

    const depositKeepBlock = p.deposit_keep_amount > 0 ? `
      <h3>🏦 예금 보존 (성향 반영)</h3>
      <div class="kv">
        <div class="k">투자에 동원 안 함</div>
        <div class="v" style="color:var(--good);font-weight:600;">${won(p.deposit_keep_amount)}</div>
      </div>` : "";

    return `
      <div class="result-block">
        <h2>👤 투자자 프로필
          <span class="badge info">${escape(status)}</span>
          <span class="tier-pill">${escape(tier)}</span>
          <span class="risk-pill">${escape(riskLabel)}</span>
        </h2>
        <div class="kv">
          ${lines.map(([k, v]) =>
            `<div class="k">${escape(k)}</div><div class="v">${escape(v)}</div>`
          ).join("")}
        </div>
        ${depositKeepBlock}
        <h3>💼 자유 투자 가능 자금</h3>
        <div class="kv">
          <div class="k">원화 기준</div><div class="v">${won(p.investable_capital)}</div>
          <div class="k">달러 환산 (@${Math.round(p.usd_krw_rate)})</div>
          <div class="v">${usd(p.investable_usd)}</div>
        </div>
      </div>
    `;
  }

  function renderRegime(us, kr) {
    function compositeClass(c) {
      if (c >= 0.65) return "good";
      if (c >= 0.45) return "info";
      if (c >= 0.30) return "warn";
      return "bad";
    }
    function meter(value, label) {
      const pct = Math.round(Math.max(0, Math.min(value, 1)) * 100);
      const cls = value >= 0.65 ? "meter-good"
                : value >= 0.45 ? "meter-info"
                : value >= 0.30 ? "meter-warn"
                : "meter-bad";
      return `
        <div class="meter-row">
          <span class="meter-label">${escape(label)}</span>
          <div class="meter-bar"><div class="${cls}" style="width:${pct}%"></div></div>
          <span class="meter-val">${value.toFixed(2)}</span>
        </div>`;
    }

    function block(label, r, isDeep) {
      const status = r.is_investable
        ? `<span class="badge good">투자 가능</span>`
        : `<span class="badge bad">관망</span>`;
      const cls = compositeClass(r.composite_score);
      const fearBadge = r.fear_score >= 0.5
        ? `<span class="badge bad">🔥 공포 ${r.fear_score.toFixed(2)} · 역발상 매수 구간</span>`
        : (r.fear_score >= 0.3 ? `<span class="badge warn">⚠️ 공포 ${r.fear_score.toFixed(2)}</span>` : "");

      const signals = isDeep ? `
        <div class="signals-grid">
          ${meter(r.trend_score,       "추세")}
          ${meter(r.vix_signal,         `VIX ${r.vix.toFixed(1)}`)}
          ${meter(r.breadth_pct,        `breadth ${Math.round(r.breadth_pct*100)}%`)}
          ${meter(r.yield_curve_signal, `금리차 ${r.yield_curve_spread > 0 ? "+" : ""}${r.yield_curve_spread.toFixed(2)}%p`)}
          ${meter(r.sector_rotation,    "섹터 RS")}
        </div>` : `
        <div style="font-size:11px;color:var(--text-dim);">
          기본 신호만 (한국 시장은 sector/yield 데이터 미적용)
        </div>`;

      return `
        <div style="margin-bottom: 14px;">
          <div style="font-weight:600;font-size:14px;">${label} ${status}
            <span class="badge ${cls}">종합 ${r.composite_score.toFixed(2)}</span>
            ${fearBadge}
          </div>
          ${signals}
          <div style="font-size:11px;color:var(--text-dim);margin-top:6px;">
            ${escape(r.detail)}
          </div>
        </div>
      `;
    }
    return `
      <div class="result-block">
        <h2>📊 시장 국면 (5개 신호 종합)</h2>
        ${block("🇺🇸 미국 (S&P 500)", us, true)}
        ${block("🇰🇷 한국 (KOSPI)",   kr, false)}
      </div>
    `;
  }

  function renderPortfolio(p, prof) {
    const corePct = p.core_budget || 0;
    const usPct = p.us_budget;
    const krPct = p.kr_budget;
    const cashPct = p.cash_pct;

    return `
      <div class="result-block">
        <h2>💰 자산 배분</h2>
        <div class="bar">
          <div class="b-core" style="width:${corePct}%" title="코어 ETF ${corePct}%"></div>
          <div class="b-us"   style="width:${usPct}%"   title="미국 위성 ${usPct}%"></div>
          <div class="b-kr"   style="width:${krPct}%"   title="한국 ${krPct}%"></div>
          <div class="b-cash" style="width:${cashPct}%" title="현금 ${cashPct}%"></div>
        </div>
        <div class="kv" style="margin-top:8px;">
          <div class="k">🛡️ 코어 ETF (안전자산)</div><div class="v">${pct(corePct)}</div>
          <div class="k">🇺🇸 미국 개별주 (위성)</div><div class="v">${pct(usPct)}</div>
          <div class="k">🇰🇷 한국 개별주 (위성)</div><div class="v">${pct(krPct)}</div>
          <div class="k">💵 현금 보유</div><div class="v">${pct(cashPct)}</div>
        </div>
        <div style="font-size:11px;color:var(--text-dim);margin-top:6px;">
          스크리닝: ETF ${p.screened.core_passed}/${p.screened.core_total},
          미국 ${p.screened.us_passed}/${p.screened.us_total},
          한국 ${p.screened.kr_passed}/${p.screened.kr_total}
        </div>

        ${renderCoreETFs(p.core_etfs, p.dca_months || 12)}
        ${renderStocks("🇺🇸 미국 추천 (위성)", p.us_stocks)}
        ${renderStocks("🇰🇷 한국 추천 (위성)", p.kr_stocks)}
      </div>
    `;
  }

  function renderCoreETFs(etfs, dcaMonths) {
    if (!etfs || etfs.length === 0) return "";
    return `
      <h3>🛡️ 코어 ETF (월 적립 · ${dcaMonths}개월)</h3>
      ${etfs.map(e => renderETFCard(e, dcaMonths)).join("")}
    `;
  }

  function renderETFCard(s, dcaMonths) {
    const plan = s.entry_plan;
    const tag = plan ? (ACTION_TAG[plan.action] || "⚪") : "⚪";
    const planLabel = plan ? plan.label : "—";
    const totalAmount = s.invest_amount > 0 ? usd(s.invest_amount) : "";
    const monthlyUsd = (s.invest_amount > 0 && dcaMonths > 0)
      ? s.invest_amount / dcaMonths
      : 0;

    return `
      <div class="stock-card etf-card">
        <div class="stock-head">
          <div>
            ${tag}
            <span class="tag-${plan ? plan.action : "DCA"}">[${escape(planLabel)}]</span>
            ${escape(s.name)}
            <span class="ticker">(${escape(s.ticker)})</span>
          </div>
          <div style="font-variant-numeric:tabular-nums;color:var(--accent-soft);">
            ${pct(s.weight_pct)}
          </div>
        </div>
        <div class="stock-meta">
          ETF · 목표 비중 ${pct(s.weight_pct)}
          ${totalAmount ? `· 목표 ${totalAmount}` : ""}
        </div>
        ${monthlyUsd > 0 ? `
          <div class="dca-monthly">
            매월 ≈ <b>${usd(monthlyUsd)}</b> 적립 × ${dcaMonths}개월
          </div>` : ""}
        ${plan ? `
          <div class="stock-plan">
            <span class="reason">${escape(plan.rationale)}</span>
          </div>` : ""}
      </div>
    `;
  }

  function renderStocks(label, stocks) {
    if (!stocks || stocks.length === 0) return "";
    return `
      <h3>${label}</h3>
      ${stocks.map(renderStockCard).join("")}
    `;
  }

  function renderStockCard(s) {
    const plan = s.entry_plan;
    const tag = plan ? (ACTION_TAG[plan.action] || "⚪") : "⚪";
    const planLabel = plan ? plan.label : "—";
    const amountStr = s.invest_amount > 0
      ? (s.country === "KR" ? won(s.invest_amount) : usd(s.invest_amount))
      : "";

    const pbrStr = s.pbr == null
      ? "N/A"
      : s.pbr.toFixed(2) + (s.pbr_source === "computed" ? "*" : "");
    const perStr = s.per == null
      ? "N/A"
      : s.per.toFixed(1) + (s.per_source === "forward" ? "ᶠ" : "");
    const roeStr = s.roe == null ? "N/A" : s.roe.toFixed(1) + "%";

    return `
      <div class="stock-card">
        <div class="stock-head">
          <div>
            ${tag}
            <span class="tag-${plan ? plan.action : "AVOID"}">[${escape(planLabel)}]</span>
            ${escape(s.name)}
            <span class="ticker">(${escape(s.ticker)})</span>
          </div>
          <div style="font-variant-numeric:tabular-nums;color:var(--accent-soft);">
            ${pct(s.weight_pct)}
          </div>
        </div>
        <div class="stock-meta">
          ${escape(s.sector)}
          ${amountStr ? `· ≈ ${amountStr}` : ""}
          · 종합점수 ${s.composite_score.toFixed(1)}
        </div>
        <div class="stock-metrics">
          <div><span>ROE</span> <b>${roeStr}</b></div>
          <div><span>RSI</span> <b>${s.rsi.toFixed(1)}</b></div>
          <div><span>PBR</span> <b>${pbrStr}</b></div>
          <div><span>모멘텀</span> <b>${(s.momentum_pct >= 0 ? "+" : "") + s.momentum_pct.toFixed(1)}%</b></div>
          <div><span>PER</span> <b>${perStr}</b></div>
          <div><span>부채</span> <b>${s.debt_to_equity == null ? "N/A" : s.debt_to_equity.toFixed(0)}</b></div>
          <div><span>FCF마진</span> <b>${s.fcf_margin == null ? "N/A" : (s.fcf_margin >= 0 ? "+" : "") + s.fcf_margin.toFixed(1) + "%"}</b></div>
          <div><span>영업이익률</span> <b>${s.operating_margin == null ? "N/A" : s.operating_margin.toFixed(1) + "%"}</b></div>
          <div><span>PEG</span> <b>${s.peg == null ? "N/A" : s.peg.toFixed(2)}</b></div>
        </div>
        ${plan ? renderPlan(plan, s.country) : ""}
      </div>
    `;
  }

  function renderPlan(plan, country) {
    const priceFmt = (p) => country === "KR" ? won(p) : "$" + p.toFixed(2);

    if (plan.action === "AGGRESSIVE_BUY") {
      const items = plan.target_levels.map(([price, p], i) => {
        const diff = i === 0 ? "현재가" : ((price / plan.current_price - 1) * 100).toFixed(1) + "%";
        return `<li>${i+1}차 ${priceFmt(price)} (${p.toFixed(1)}%) — ${diff}</li>`;
      }).join("");
      return `
        <div class="stock-plan aggr-plan">
          ▸ <b>역발상 공격 매수</b> (5분할, 첫 매수 50%):
          <ul class="split-list">${items}</ul>
          <span class="reason">사유: ${escape(plan.rationale)}</span>
        </div>`;
    }
    if (plan.action === "IMMEDIATE") {
      return `
        <div class="stock-plan">
          ▸ 시장가 매수 (현재가 ${priceFmt(plan.current_price)})
          <span class="reason">사유: ${escape(plan.rationale)}</span>
        </div>`;
    }
    if (plan.action === "SPLIT_BUY") {
      const items = plan.target_levels.map(([price, p], i) => {
        const diff = i === 0 ? "현재가" : ((price / plan.current_price - 1) * 100).toFixed(1) + "%";
        return `<li>${i+1}차 ${priceFmt(price)} (${p.toFixed(1)}%) — ${diff}</li>`;
      }).join("");
      return `
        <div class="stock-plan">
          ▸ 3분할 진입
          <ul class="split-list">${items}</ul>
          <span class="reason">사유: ${escape(plan.rationale)}</span>
        </div>`;
    }
    if (plan.action === "WAIT_PULLBACK") {
      const t = plan.target_levels[0][0];
      const pullback = ((t / plan.current_price - 1) * 100).toFixed(1);
      return `
        <div class="stock-plan">
          ▸ 진입 대기: 현재 ${priceFmt(plan.current_price)} → 목표 ${priceFmt(t)} (${pullback}%)
          <span class="reason">사유: ${escape(plan.rationale)}</span>
        </div>`;
    }
    return `
      <div class="stock-plan">
        ▸ 진입 보류
        <span class="reason">사유: ${escape(plan.rationale)}</span>
      </div>`;
  }
})();
