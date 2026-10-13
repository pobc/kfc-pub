import { adminApi } from './api.js';
import { clearError, dateTime, escapeHtml, handleError, renderPagination, setBusy, showMessage, state } from './common.js';

let pollTimer;
let sortBy = 'price';
let sortOrder = 'asc';
let savedKeywords = [];
let priceRequestId = 0;
let keywordFilterInitialized = false;
let pricePage = 1;
const PRICE_PAGE_SIZE = 100;
let pricePagination = { page: 1, pageSize: PRICE_PAGE_SIZE, total: 0, totalPages: 1 };

const DEFAULT_SORT_ORDERS = {
  shopName: 'asc',
  productUrl: 'asc',
  itemName: 'asc',
  price: 'desc',
  scrapedAt: 'desc',
};

export function initTaobaoPrices() {
  document.addEventListener('taobao-keywords-changed', (event) => {
    savedKeywords = event.detail;
    renderKeywordOptions();
  });
  document.getElementById('taobao-scrape-keyword').addEventListener('change', renderTaobaoLoginStatus);
  document.getElementById('taobao-login-start').addEventListener('click', startLogin);
  document.getElementById('taobao-login-save').addEventListener('click', saveLogin);
  document.getElementById('taobao-login-cancel').addEventListener('click', cancelLogin);
  document.getElementById('taobao-scrape-target').addEventListener('submit', startScrape);
  document.getElementById('taobao-price-filter').addEventListener('submit', async (event) => {
    event.preventDefault();
    await queryPrices();
  });
  document.getElementById('taobao-price-keyword-options').addEventListener('change', async (event) => {
    if (!event.target.closest('[data-taobao-keyword]')) return;
    await queryPrices();
  });
  document.getElementById('taobao-price-reset').addEventListener('click', async () => {
    document.getElementById('taobao-price-search').value = '';
    document.getElementById('taobao-price-keywords').value = '';
    document.querySelectorAll('[data-taobao-keyword]').forEach((input) => { input.checked = false; });
    await queryPrices();
  });
  document.querySelectorAll('[data-taobao-sort]').forEach((button) => {
    button.addEventListener('click', async () => {
      const nextSortBy = button.dataset.taobaoSort;
      if (nextSortBy === sortBy) {
        sortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
      } else {
        sortBy = nextSortBy;
        sortOrder = DEFAULT_SORT_ORDERS[sortBy] || 'asc';
      }
      await queryPrices();
    });
  });
}

function renderKeywordOptions() {
  const select = document.getElementById('taobao-scrape-keyword');
  const selected = select.value;
  const placeholder = savedKeywords.length ? '请选择搜索关键词' : '暂无关键词，请先在“淘宝搜索关键词”中新增';
  select.innerHTML = `<option value="">${placeholder}</option>` + savedKeywords.map((item) =>
    `<option value="${item.id}">${escapeHtml(item.keyword)}</option>`).join('');
  const selectedItem = savedKeywords.find((item) => String(item.id) === selected);
  if (selectedItem) {
    select.value = selected;
  }
  const checked = new Set(Array.from(document.querySelectorAll('[data-taobao-keyword]:checked'), (input) => input.value));
  const keywords = [...new Set(savedKeywords.map((item) => item.keyword))];
  const selectFirst = !keywordFilterInitialized && checked.size === 0 && keywords.length > 0;
  document.getElementById('taobao-price-keyword-options').innerHTML = keywords.map((keyword, index) =>
    `<label><input type="checkbox" data-taobao-keyword value="${escapeHtml(keyword)}" ${checked.has(keyword) || (selectFirst && index === 0) ? 'checked' : ''} />${escapeHtml(keyword)}</label>`).join('');
  if (keywords.length > 0) keywordFilterInitialized = true;
  renderTaobaoLoginStatus();
  if (selectFirst) void queryPrices();
}

async function queryPrices({ resetPage = true } = {}) {
  if (resetPage) pricePage = 1;
  clearError();
  try { await loadTaobaoPrices(); } catch (error) { handleError(error); }
}

export async function loadTaobaoPrices() {
  const requestId = ++priceRequestId;
  const search = document.getElementById('taobao-price-search')?.value.trim() || '';
  const query = new URLSearchParams({ search, sortBy, sortOrder, page: String(pricePage) });
  const manual = document.getElementById('taobao-price-keywords').value.split(/[\s,，;；]+/);
  const selected = Array.from(document.querySelectorAll('[data-taobao-keyword]:checked'), (input) => input.value);
  // 商品项文字和来源复选框分别传参，避免来源命中后把文字筛选短路。
  const keywords = [...new Set(manual.map((value) => value.trim()).filter(Boolean))];
  const sourceKeywords = [...new Set(selected)];
  if (keywords.length > 20 || keywords.some((value) => value.length > 200)) {
    throw new Error('最多选择或输入 20 个关键词，每个关键词不超过 200 个字符');
  }
  if (sourceKeywords.length > 20) throw new Error('最多选择 20 个来源关键词');
  keywords.forEach((keyword) => query.append('keywords', keyword));
  sourceKeywords.forEach((keyword) => query.append('sourceKeywords', keyword));
  const [data, login] = await Promise.all([
    adminApi.get(`taobao-prices?${query.toString()}`),
    adminApi.get('taobao-login/status'),
  ]);
  // 连续查询时只展示最后一次筛选，避免较慢的旧请求覆盖当前结果。
  if (requestId !== priceRequestId) return;
  state.taobaoPrices = data.items || [];
  state.taobaoPriceSummary = data.summary || {};
  pricePagination = data.pagination || { page: 1, pageSize: PRICE_PAGE_SIZE, total: 0, totalPages: 1 };
  pricePage = Number(pricePagination.page || 1);
  state.taobaoScrape = data.scrape || {};
  state.taobaoLogin = login || {};
  renderTaobaoPrices();
}

async function startLogin() {
  clearError();
  const button = document.getElementById('taobao-login-start');
  setBusy(button, true, '正在打开…');
  try {
    const capture = await adminApi.post('taobao-login/start', {});
    state.taobaoLogin = { ...state.taobaoLogin, ...capture };
    renderTaobaoPrices();
    showMessage('淘宝登录窗口已打开，请登录后保存登录信息');
  } catch (error) {
    handleError(error);
  } finally {
    setBusy(button, false);
    renderTaobaoLoginStatus();
  }
}

async function saveLogin() {
  const captureId = state.taobaoLogin?.captureId;
  if (!captureId) return;
  clearError();
  const button = document.getElementById('taobao-login-save');
  setBusy(button, true, '正在保存…');
  try {
    state.taobaoLogin = await adminApi.post('taobao-login/save', { captureId });
    renderTaobaoPrices();
    showMessage('淘宝登录信息已保存，现在可以开始抓取');
  } catch (error) {
    handleError(error);
  } finally {
    setBusy(button, false);
    renderTaobaoLoginStatus();
  }
}

async function cancelLogin() {
  const captureId = state.taobaoLogin?.captureId;
  if (!captureId) return;
  clearError();
  try {
    await adminApi.delete(`taobao-login/${encodeURIComponent(captureId)}`);
    state.taobaoLogin = await adminApi.get('taobao-login/status');
    renderTaobaoPrices();
  } catch (error) {
    handleError(error);
  }
}

async function startScrape(event) {
  event.preventDefault();
  clearError();
  const button = document.getElementById('taobao-price-scrape');
  setBusy(button, true, '正在启动…');
  try {
    const selected = document.getElementById('taobao-scrape-keyword').value;
    const keyword = savedKeywords.find((item) => String(item.id) === selected);
    if (!keyword) throw new Error('请先选择已保存的搜索关键词');
    const url = keyword.url;
    state.taobaoScrape = await adminApi.post('taobao-prices/scrape', { url });
    renderTaobaoPrices();
    ensurePolling();
    showMessage('淘宝价格抓取已启动');
  } catch (error) {
    handleError(error);
  } finally {
    if (state.taobaoScrape?.state !== 'running') setBusy(button, false);
    renderTaobaoLoginStatus();
  }
}

function ensurePolling() {
  if (pollTimer || state.taobaoScrape?.state !== 'running') return;
  pollTimer = window.setInterval(pollScrape, 2000);
}

async function pollScrape() {
  try {
    state.taobaoScrape = await adminApi.get('taobao-prices/scrape/status');
    renderTaobaoPrices();
    if (state.taobaoScrape.state !== 'running') {
      window.clearInterval(pollTimer);
      pollTimer = undefined;
      await loadTaobaoPrices();
      if (state.taobaoScrape.state === 'succeeded') showMessage(state.taobaoScrape.message);
    }
  } catch (error) {
    window.clearInterval(pollTimer);
    pollTimer = undefined;
    handleError(error);
  }
}

function renderTaobaoLoginStatus() {
  const login = state.taobaoLogin || {};
  const active = Boolean(login.active && login.captureId);
  const saved = Boolean(login.saved);
  const badge = document.getElementById('taobao-login-state');
  badge.textContent = active ? '等待保存' : saved ? '已保存' : '未登录';
  badge.className = `badge ${active ? 'badge--warning' : saved ? 'badge--success' : 'badge--muted'}`;
  document.getElementById('taobao-login-message').textContent = active
    ? '请在浏览器中完成淘宝登录，然后保存登录信息'
    : saved ? '淘宝登录信息可用' : '请先打开淘宝登录窗口';
  document.getElementById('taobao-login-detail').textContent = active
    ? `登录窗口有效期至 ${dateTime(login.expiresAt)}`
    : saved
      ? `更新于 ${dateTime(login.capturedAt)}`
      : '登录完成后点击“保存登录信息”，再开始抓取。';

  const startButton = document.getElementById('taobao-login-start');
  const saveButton = document.getElementById('taobao-login-save');
  const cancelButton = document.getElementById('taobao-login-cancel');
  startButton.disabled = active || state.taobaoScrape?.state === 'running';
  startButton.textContent = saved ? '重新登录' : '登录淘宝';
  saveButton.hidden = !active;
  saveButton.disabled = !active;
  cancelButton.hidden = !active;
  const scraping = state.taobaoScrape?.state === 'running';
  const select = document.getElementById('taobao-scrape-keyword');
  select.disabled = active || scraping;
  const selected = savedKeywords.some((item) => String(item.id) === select.value);
  document.getElementById('taobao-price-scrape').disabled = !saved || !selected || active || scraping;
}

function renderScrapeStatus() {
  const scrape = state.taobaoScrape || {};
  const running = scrape.state === 'running';
  const waitingVerification = running && scrape.phase === 'waiting_verification';
  const badge = document.getElementById('taobao-price-state');
  const labels = { idle: '未运行', running: '抓取中', succeeded: '已完成', failed: '失败' };
  badge.textContent = waitingVerification ? '待验证' : labels[scrape.state] || '未运行';
  badge.className = `badge ${scrape.state === 'succeeded' ? 'badge--success' : scrape.state === 'failed' ? 'badge--danger' : running ? 'badge--warning' : 'badge--muted'}`;
  document.getElementById('taobao-price-message').textContent = scrape.message || '尚未开始抓取';
  const productCount = Number(scrape.productCount || 0);
  const processedCount = Number(scrape.processedCount || 0);
  const itemCount = Number(scrape.itemCount || 0);
  const failedCount = Number(scrape.failedCount || 0);
  const progress = productCount
    ? `${processedCount}/${productCount} 个商品 · ${itemCount} 个价格项 · 失败 ${failedCount}`
    : '';
  const cleanup = scrape.deletedCount ? `已清理本次关键词的 ${scrape.deletedCount} 条历史价格` : '';
  document.getElementById('taobao-price-progress').textContent = [progress, cleanup].filter(Boolean).join(' · ');
  setBusy(document.getElementById('taobao-price-scrape'), running, waitingVerification ? '等待验证…' : '抓取中…');
  if (running) ensurePolling();
}

function renderSortHeaders() {
  document.querySelectorAll('[data-taobao-sort]').forEach((button) => {
    const active = button.dataset.taobaoSort === sortBy;
    const label = button.dataset.sortLabel || button.textContent.trim();
    const indicator = button.querySelector('[data-sort-indicator]');
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
    button.setAttribute(
      'aria-label',
      active ? `${label}，当前${sortOrder === 'asc' ? '升序' : '降序'}，点击切换` : `按${label}排序`,
    );
    if (indicator) indicator.textContent = active ? (sortOrder === 'asc' ? '↑' : '↓') : '↕';
  });
}

function renderScrapedTime(value, today) {
  const date = new Date(value);
  // 按页面显示时间的本地日期判断，避免 UTC 日期与用户看到的日期不一致。
  const otherDay = Boolean(value) && !Number.isNaN(date.getTime()) && date.toDateString() !== today;
  const attributes = otherDay ? ' class="taobao-scraped-time--other-day" title="非今日抓取"' : '';
  return `<span${attributes}>${escapeHtml(dateTime(value))}</span>`;
}

export function renderTaobaoPrices() {
  renderScrapeStatus();
  renderTaobaoLoginStatus();
  renderSortHeaders();
  const today = new Date().toDateString();
  const summary = state.taobaoPriceSummary || {};
  document.getElementById('taobao-price-product-count').textContent = String(summary.productCount || 0);
  document.getElementById('taobao-price-item-count').textContent = String(summary.itemCount || 0);
  document.getElementById('taobao-price-latest').innerHTML = renderScrapedTime(summary.latestScrapedAt, today);

  const rows = document.getElementById('taobao-price-rows');
  const empty = document.getElementById('taobao-price-empty');
  empty.hidden = state.taobaoPrices.length > 0;
  empty.textContent = '暂无符合条件的价格，可调整筛选条件或执行抓取';
  rows.innerHTML = state.taobaoPrices.map((item) => `
    <div class="data-row taobao-price-row">
      <span><b>${escapeHtml(item.shopName)}</b></span>
      <span class="taobao-price-link"><a href="${escapeHtml(item.productUrl)}" target="_blank" rel="noopener noreferrer">查看商品</a></span>
      <span>${escapeHtml(item.itemName)}</span>
      <strong class="taobao-price-value">¥${escapeHtml(item.price)}</strong>
      ${renderScrapedTime(item.scrapedAt, today)}
    </div>`).join('');

  renderPagination(document.getElementById('taobao-price-pagination'), {
    page: pricePage,
    pageSize: PRICE_PAGE_SIZE,
    total: Number(pricePagination.total || 0),
    totalPages: Number(pricePagination.totalPages || 1),
    onPage: async (nextPage) => {
      pricePage = nextPage;
      await queryPrices({ resetPage: false });
    },
  });
}
