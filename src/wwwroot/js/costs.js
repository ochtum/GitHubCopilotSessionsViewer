(() => {
const LANGUAGE_STORAGE_KEY = 'github_copilot_sessions_viewer_language_v1';
const COST_CURRENCY_STORAGE_KEY = 'github_copilot_sessions_viewer_cost_currency_v1';
const COST_SUMMARY_CACHE_KEY = 'github_copilot_sessions_viewer_cost_summary_cache_v1';
const COST_SUMMARY_CACHE_MAX_AGE_MS = 5 * 60 * 1000;
const SUPPORTED_LANGUAGES = ['ja', 'en', 'zh-Hans', 'zh-Hant'];
const SUPPORTED_COST_CURRENCIES = ['USD', 'JPY', 'CNY', 'TWD', 'HKD'];
const I18N = {
  ja: {
    'language.selector': '言語',
    'currency.selector': '通貨',
    'page.title': 'コスト表示 | GitHub Copilot Sessions Viewer',
    'page.badge': 'GitHub Copilot Sessions Viewer',
    'page.heroTitle': 'コスト表示',
    'page.heroCopy': '月別・週別・日別に、session 単位の request / premium request / total cost をまとめて確認できます。進行中セッションは premium request と total cost に反映されません。',
    'page.refresh': 'Refresh',
    'meta.generatedAt': '更新日時',
    'meta.timeZone': 'タイムゾーン',
    'meta.unitPrice': 'Premium 単価',
    'status.loading': 'コスト集計を読み込み中...',
    'status.error': 'コスト集計の取得に失敗しました。',
    'group.month': '月別',
    'group.week': '週別',
    'group.day': '日別',
    'period.two_months_ago': '先々月',
    'period.last_month': '先月',
    'period.this_month': '今月',
    'period.two_weeks_ago': '先々週',
    'period.last_week': '先週',
    'period.this_week': '今週',
    'period.two_days_ago': '一昨日',
    'period.yesterday': '昨日',
    'period.today': '今日',
    'column.period': '期間',
    'column.request': 'REQUEST',
    'column.premiumRequest': 'PREMIUM REQUEST',
    'column.totalCost': 'TOTAL COST',
  },
  en: {
    'language.selector': 'Language',
    'currency.selector': 'Currency',
    'page.title': 'Cost Summary | GitHub Copilot Sessions Viewer',
    'page.badge': 'GitHub Copilot Sessions Viewer',
    'page.heroTitle': 'Cost Summary',
    'page.heroCopy': 'Review session-level request, premium request, and total cost totals by month, week, and day. Active sessions are excluded from premium request and total cost totals.',
    'page.refresh': 'Refresh',
    'meta.generatedAt': 'Updated',
    'meta.timeZone': 'Time zone',
    'meta.unitPrice': 'Premium unit price',
    'status.loading': 'Loading cost summary...',
    'status.error': 'Failed to load the cost summary.',
    'group.month': 'Monthly',
    'group.week': 'Weekly',
    'group.day': 'Daily',
    'period.two_months_ago': '2 months ago',
    'period.last_month': 'Last month',
    'period.this_month': 'This month',
    'period.two_weeks_ago': '2 weeks ago',
    'period.last_week': 'Last week',
    'period.this_week': 'This week',
    'period.two_days_ago': '2 days ago',
    'period.yesterday': 'Yesterday',
    'period.today': 'Today',
    'column.period': 'Period',
    'column.request': 'REQUEST',
    'column.premiumRequest': 'PREMIUM REQUEST',
    'column.totalCost': 'TOTAL COST',
  },
  'zh-Hans': {
    'language.selector': '语言',
    'currency.selector': '货币',
    'page.title': '成本汇总 | GitHub Copilot Sessions Viewer',
    'page.badge': 'GitHub Copilot Sessions Viewer',
    'page.heroTitle': '成本汇总',
    'page.heroCopy': '可按月、周、日查看按 session 聚合的 request、premium request 和 total cost。进行中的 session 不计入 premium request 和 total cost。',
    'page.refresh': 'Refresh',
    'meta.generatedAt': '更新时间',
    'meta.timeZone': '时区',
    'meta.unitPrice': 'Premium 单价',
    'status.loading': '正在加载成本汇总...',
    'status.error': '获取成本汇总失败。',
    'group.month': '按月',
    'group.week': '按周',
    'group.day': '按日',
    'period.two_months_ago': '前前月',
    'period.last_month': '上月',
    'period.this_month': '本月',
    'period.two_weeks_ago': '前前周',
    'period.last_week': '上周',
    'period.this_week': '本周',
    'period.two_days_ago': '前天',
    'period.yesterday': '昨天',
    'period.today': '今天',
    'column.period': '期间',
    'column.request': 'REQUEST',
    'column.premiumRequest': 'PREMIUM REQUEST',
    'column.totalCost': 'TOTAL COST',
  },
};
I18N['zh-Hant'] = {
  ...I18N['zh-Hans'],
  'language.selector': '語言',
  'currency.selector': '幣別',
  'page.title': '成本彙總 | GitHub Copilot Sessions Viewer',
  'page.heroTitle': '成本彙總',
  'page.heroCopy': '可按月、週、日查看按 session 彙總的 request、premium request 和 total cost。進行中的 session 不會計入 premium request 和 total cost。',
  'meta.generatedAt': '更新時間',
  'meta.timeZone': '時區',
  'meta.unitPrice': 'Premium 單價',
  'status.loading': '正在載入成本彙總...',
  'status.error': '取得成本彙總失敗。',
  'group.month': '按月',
  'group.week': '按週',
  'group.day': '按日',
  'period.two_months_ago': '前前月',
  'period.last_month': '上月',
  'period.this_month': '本月',
  'period.two_weeks_ago': '前前週',
  'period.last_week': '上週',
  'period.this_week': '本週',
  'period.two_days_ago': '前天',
  'period.yesterday': '昨天',
  'period.today': '今天',
  'column.period': '期間',
};

let uiLanguage = 'ja';
let selectedCostCurrency = 'USD';
let costSummaryData = null;
let isLoading = false;

function normalizeLanguage(value){
  const raw = (value || '').trim();
  if(raw === 'zh' || raw === 'zh-CN' || raw === 'zh-SG'){
    return 'zh-Hans';
  }
  if(raw === 'zh-TW' || raw === 'zh-HK' || raw === 'zh-MO'){
    return 'zh-Hant';
  }
  return SUPPORTED_LANGUAGES.includes(raw) ? raw : 'ja';
}

function normalizeCostCurrency(value){
  const raw = (value || '').trim().toUpperCase();
  return SUPPORTED_COST_CURRENCIES.includes(raw) ? raw : '';
}

function getDefaultCostCurrencyForLanguage(language){
  const normalized = normalizeLanguage(language);
  if(normalized === 'ja'){
    return 'JPY';
  }
  if(normalized === 'zh-Hans'){
    return 'CNY';
  }
  if(normalized === 'zh-Hant'){
    return 'TWD';
  }
  return 'USD';
}

function t(key){
  return (I18N[uiLanguage] && I18N[uiLanguage][key])
    || I18N.ja[key]
    || key;
}

function esc(value){
  return (value ?? '').toString().replace(/[&<>\"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', '\'': '&#39;' }[ch]));
}

function isCostsPage(){
  return !!document.getElementById('costs_groups') && !!document.getElementById('refresh_costs');
}

function getUiLocale(){
  if(uiLanguage === 'zh-Hans') return 'zh-CN';
  if(uiLanguage === 'zh-Hant') return 'zh-TW';
  return uiLanguage || 'ja';
}

function formatNumber(value){
  const numeric = Number(value);
  if(!Number.isFinite(numeric)){
    return '-';
  }
  return numeric.toLocaleString(getUiLocale());
}

function formatUsd(value){
  const numeric = Number(value);
  if(!Number.isFinite(numeric)){
    return '-';
  }
  const digits = numeric >= 10 ? 2 : (numeric >= 1 ? 3 : 4);
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(numeric);
}

function formatJpy(value){
  const numeric = Number(value);
  if(!Number.isFinite(numeric)){
    return '';
  }
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: 'JPY',
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(numeric);
}

function formatCny(value){
  const numeric = Number(value);
  if(!Number.isFinite(numeric)){
    return '';
  }
  const digits = numeric >= 10 ? 2 : (numeric >= 1 ? 3 : 4);
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(numeric);
}

function formatLocalCurrency(value, currencyCode){
  const numeric = Number(value);
  if(!Number.isFinite(numeric) || !currencyCode || currencyCode === 'USD'){
    return '';
  }
  if(currencyCode === 'JPY'){
    return formatJpy(numeric);
  }
  if(currencyCode === 'CNY'){
    return formatCny(numeric);
  }
  const digits = numeric >= 10 ? 2 : (numeric >= 1 ? 3 : 4);
  return new Intl.NumberFormat(getUiLocale(), {
    style: 'currency',
    currency: currencyCode,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(numeric);
}

function getPreferredCostCurrencyCode(){
  return normalizeCostCurrency(selectedCostCurrency) || 'USD';
}

function getExchangeRateValue(exchangeRate, currencyCode){
  if(!exchangeRate || currencyCode === 'USD'){
    return null;
  }
  const rate = currencyCode === 'JPY'
    ? Number(exchangeRate.jpy_rate)
    : currencyCode === 'CNY'
      ? Number(exchangeRate.cny_rate)
      : currencyCode === 'TWD'
        ? Number(exchangeRate.twd_rate)
        : currencyCode === 'HKD'
          ? Number(exchangeRate.hkd_rate)
          : NaN;
  return Number.isFinite(rate) && rate > 0 ? rate : null;
}

function convertUsdToLocalCurrency(value, exchangeRate, currencyCode){
  const usd = Number(value);
  const rate = getExchangeRateValue(exchangeRate, currencyCode);
  if(!Number.isFinite(usd) || !Number.isFinite(rate) || rate <= 0){
    return null;
  }
  return usd * rate;
}

function formatCostDisplay(value, exchangeRate){
  const usd = Number(value);
  if(!Number.isFinite(usd)){
    return formatUsd(NaN);
  }
  const currencyCode = getPreferredCostCurrencyCode();
  if(currencyCode === 'USD'){
    return formatUsd(usd);
  }
  const localValue = convertUsdToLocalCurrency(usd, exchangeRate, currencyCode);
  if(localValue == null){
    return formatUsd(usd);
  }
  const formattedLocal = formatLocalCurrency(localValue, currencyCode);
  return formattedLocal ? `${formatUsd(usd)} / ${formattedLocal}` : formatUsd(usd);
}

function formatTimestamp(value){
  if(!value){
    return '-';
  }
  const timestamp = new Date(value);
  if(Number.isNaN(timestamp.getTime())){
    return value;
  }
  return timestamp.toLocaleString(getUiLocale());
}

function setStatus(text, tone){
  const status = document.getElementById('costs_status');
  if(!status){
    return;
  }
  status.textContent = text || '';
  status.classList.toggle('error', tone === 'error');
}

function readCostSummaryCache(){
  try {
    const raw = localStorage.getItem(COST_SUMMARY_CACHE_KEY) || '';
    if(!raw){
      return null;
    }
    const parsed = JSON.parse(raw);
    if(!parsed || typeof parsed !== 'object' || !parsed.data){
      return null;
    }
    const savedAt = Number(parsed.saved_at);
    return {
      data: parsed.data,
      savedAt: Number.isFinite(savedAt) ? savedAt : 0,
    };
  } catch (error) {
    return null;
  }
}

function writeCostSummaryCache(data){
  if(!data){
    return;
  }
  try {
    localStorage.setItem(COST_SUMMARY_CACHE_KEY, JSON.stringify({
      saved_at: Date.now(),
      data,
    }));
  } catch (error) {
    // Ignore storage quota errors and keep the page state only.
  }
}

function isCostSummaryCacheFresh(entry){
  return !!entry
    && Number.isFinite(entry.savedAt)
    && entry.savedAt > 0
    && (Date.now() - entry.savedAt) <= COST_SUMMARY_CACHE_MAX_AGE_MS;
}

function applyCostSummaryData(data){
  costSummaryData = data || null;
  renderMeta();
  renderGroups();
}

function renderMeta(){
  const meta = document.getElementById('costs_meta');
  if(!meta){
    return;
  }
  if(!costSummaryData){
    meta.innerHTML = '';
    return;
  }
  meta.innerHTML = [
    `<div class="costs-meta-item"><span class="costs-meta-label">${esc(t('meta.generatedAt'))}</span><span>${esc(formatTimestamp(costSummaryData.generated_at))}</span></div>`,
    `<div class="costs-meta-item"><span class="costs-meta-label">${esc(t('meta.timeZone'))}</span><span>${esc(costSummaryData.time_zone_id || '-')}</span></div>`,
    `<div class="costs-meta-item"><span class="costs-meta-label">${esc(t('meta.unitPrice'))}</span><span>${esc(formatUsd(costSummaryData.unit_price_usd))}</span></div>`,
  ].join('');
}

function renderTable(periods){
  return `<div class="costs-table-wrap"><table class="costs-table"><thead><tr>
    <th>${esc(t('column.period'))}</th>
    <th>${esc(t('column.request'))}</th>
    <th>${esc(t('column.premiumRequest'))}</th>
    <th>${esc(t('column.totalCost'))}</th>
  </tr></thead><tbody>${periods.map(period => {
    return `<tr>
      <td class="costs-period-label">${esc(t(`period.${period.key}`))}</td>
      <td>${esc(formatNumber(period.request_count || 0))}</td>
      <td>${esc(formatNumber(period.premium_request_count || 0))}</td>
      <td>${esc(formatCostDisplay(period.total_cost_usd || 0, costSummaryData && costSummaryData.exchange_rate))}</td>
    </tr>`;
  }).join('')}</tbody></table></div>`;
}

function renderGroups(){
  const groups = document.getElementById('costs_groups');
  if(!groups){
    return;
  }
  if(!costSummaryData || !Array.isArray(costSummaryData.groups)){
    groups.innerHTML = '';
    return;
  }

  groups.innerHTML = costSummaryData.groups.map(group => {
    return `<section class="costs-group">
      <div class="costs-group-header">
        <div class="costs-group-kicker">Usage Summary</div>
        <div class="costs-group-title">${esc(t(`group.${group.key}`))}</div>
      </div>
      ${renderTable(Array.isArray(group.periods) ? group.periods : [])}
    </section>`;
  }).join('');
}

function applyLanguage(){
  document.documentElement.lang = uiLanguage;
  document.title = t('page.title');
  const languageSelect = document.getElementById('language_select');
  if(languageSelect){
    languageSelect.value = uiLanguage;
    languageSelect.setAttribute('aria-label', t('language.selector'));
  }
  const currencySelect = document.getElementById('currency_select');
  if(currencySelect){
    currencySelect.value = getPreferredCostCurrencyCode();
    currencySelect.setAttribute('aria-label', t('currency.selector'));
  }
  const refresh = document.getElementById('refresh_costs');
  if(refresh){
    refresh.textContent = t('page.refresh');
  }
  const badge = document.getElementById('page_badge');
  if(badge){
    badge.textContent = t('page.badge');
  }
  const title = document.getElementById('page_title');
  if(title){
    title.textContent = t('page.heroTitle');
  }
  const copy = document.getElementById('page_copy');
  if(copy){
    copy.textContent = t('page.heroCopy');
  }
  renderMeta();
  renderGroups();
}

async function loadCostSummary(options){
  const opts = options || {};
  isLoading = true;
  const refresh = document.getElementById('refresh_costs');
  if(refresh){
    refresh.disabled = true;
  }
  if(!opts.silent){
    setStatus(t('status.loading'));
  }
  try {
    const params = new URLSearchParams();
    params.set('ts', Date.now().toString());
    if(opts.forceRefresh){
      params.set('force', '1');
    }
    const response = await fetch(`/api/cost-summary?${params.toString()}`, { cache: 'no-store' });
    if(!response.ok){
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    writeCostSummaryCache(data);
    applyCostSummaryData(data);
    setStatus('');
  } catch (error) {
    if(!costSummaryData || opts.clearOnError){
      applyCostSummaryData(null);
    }
    setStatus(t('status.error'), 'error');
  } finally {
    isLoading = false;
    if(refresh){
      refresh.disabled = false;
    }
  }
}

function loadInitialLanguage(){
  const params = new URLSearchParams(window.location.search);
  const fromQuery = normalizeLanguage(params.get('lang'));
  const stored = normalizeLanguage(localStorage.getItem(LANGUAGE_STORAGE_KEY));
  uiLanguage = fromQuery || stored || normalizeLanguage(navigator.language);
  localStorage.setItem(LANGUAGE_STORAGE_KEY, uiLanguage);
}

function loadInitialCostCurrency(){
  const params = new URLSearchParams(window.location.search);
  const fromQuery = normalizeCostCurrency(params.get('currency'));
  const stored = normalizeCostCurrency(localStorage.getItem(COST_CURRENCY_STORAGE_KEY));
  selectedCostCurrency = fromQuery || stored || getDefaultCostCurrencyForLanguage(uiLanguage);
  localStorage.setItem(COST_CURRENCY_STORAGE_KEY, selectedCostCurrency);
}

function setCostCurrency(nextCurrency, persist){
  selectedCostCurrency = normalizeCostCurrency(nextCurrency) || getDefaultCostCurrencyForLanguage(uiLanguage);
  if(persist !== false){
    localStorage.setItem(COST_CURRENCY_STORAGE_KEY, selectedCostCurrency);
  }
  applyLanguage();
}

function initCostsPage(){
  if(!isCostsPage() || window.__githubCopilotCostsPageInitialized){
    return;
  }

  window.__githubCopilotCostsPageInitialized = true;
  loadInitialLanguage();
  loadInitialCostCurrency();
  applyLanguage();

  const languageSelect = document.getElementById('language_select');
  if(languageSelect){
    languageSelect.addEventListener('change', event => {
      uiLanguage = normalizeLanguage(event.target.value);
      localStorage.setItem(LANGUAGE_STORAGE_KEY, uiLanguage);
      applyLanguage();
    });
  }

  const currencySelect = document.getElementById('currency_select');
  if(currencySelect){
    currencySelect.addEventListener('change', event => {
      setCostCurrency(event.target.value);
    });
  }

  const refresh = document.getElementById('refresh_costs');
  if(refresh){
    refresh.addEventListener('click', () => {
      void loadCostSummary({ forceRefresh: true });
    });
  }

  window.addEventListener('storage', event => {
    if(event.key === LANGUAGE_STORAGE_KEY){
      const nextLanguage = normalizeLanguage(event.newValue || 'ja');
      if(nextLanguage !== uiLanguage){
        uiLanguage = nextLanguage;
        applyLanguage();
      }
      return;
    }
    if(event.key === COST_CURRENCY_STORAGE_KEY){
      const nextCurrency = normalizeCostCurrency(event.newValue || '') || getDefaultCostCurrencyForLanguage(uiLanguage);
      if(nextCurrency !== getPreferredCostCurrencyCode()){
        setCostCurrency(nextCurrency, false);
      }
      return;
    }
    if(event.key === COST_SUMMARY_CACHE_KEY){
      const cached = readCostSummaryCache();
      if(cached && cached.data){
        applyCostSummaryData(cached.data);
        setStatus('');
      }
    }
  });

  const cached = readCostSummaryCache();
  if(cached && cached.data){
    applyCostSummaryData(cached.data);
    setStatus('');
    if(!isCostSummaryCacheFresh(cached)){
      void loadCostSummary({ silent: true });
    }
  } else {
    void loadCostSummary();
  }
}

if(document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', initCostsPage, { once: true });
} else {
  initCostsPage();
}
})();
