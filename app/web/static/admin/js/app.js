import { adminApi } from './api.js';
import {
  $$,
  adminBasePath,
  adminLoginPath,
  bindDialogClosers,
  clearError,
  handleError,
  state,
  syncError,
} from './common.js';
import { renderDashboard } from './dashboard.js';
import { initTickets, renderTickets } from './tickets.js';
import { initProducts, renderProducts } from './products.js';
import { initAccessLinks, loadAccessLinks, renderAccessLinks } from './access-links.js';
import { initCoupons, renderBatches, renderCoupons } from './coupons.js';
import { initRedemptions, loadRedemptions, renderRedemptions } from './redemptions.js';
import { initTaobaoPrices, loadTaobaoPrices, renderTaobaoPrices } from './taobao-prices.js';
import { initTaobaoKeywords, loadTaobaoKeywords } from './taobao-keywords.js';

const sectionTitles = {
  dashboard: '总览',
  'kfc-tickets': 'KFC Ticket',
  products: '餐品管理',
  'taobao-prices': '淘宝价格',
  'taobao-keywords': '淘宝搜索关键词',
  'access-links': '访问链接',
  import: '导入卡券',
  coupons: '卡券管理',
  batches: '导入批次',
  redemptions: '核销管理',
};


async function loadPageFragments() {
  const host = document.getElementById('page-panels');
  const sections = Object.keys(sectionTitles);
  const html = await Promise.all(sections.map(async (section) => {
    const response = await fetch(`/frontend/admin/${section}/index.html`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`后台页面加载失败：${section}`);
    return response.text();
  }));
  host.innerHTML = html.join('');
}

function sectionFromPath() {
  const base = adminBasePath();
  const relative = base ? window.location.pathname.slice(base.length).replace(/^\//, '') : '';
  const segment = relative.split('/')[0];
  return sectionTitles[segment] ? segment : 'dashboard';
}

function showSection(section, { updateUrl = true } = {}) {
  state.activeSection = sectionTitles[section] ? section : 'dashboard';
  syncError();
  document.getElementById('page-title').textContent = sectionTitles[state.activeSection];
  document.getElementById('admin-content').classList.toggle('admin-content--compact', ['coupons', 'access-links', 'redemptions', 'taobao-prices'].includes(state.activeSection));
  $$('[data-panel]').forEach((panel) => { panel.hidden = panel.dataset.panel !== state.activeSection; });
  $$('#admin-nav [data-section]').forEach((button) => button.classList.toggle('active', button.dataset.section === state.activeSection));
  if (updateUrl) {
    const base = adminBasePath();
    const path = state.activeSection === 'dashboard' ? base : `${base}/${state.activeSection}`;
    if (window.location.pathname !== path) history.pushState({ section: state.activeSection }, '', path);
  }
}

async function loadCore() {
  const [stats, ticketData, productData, couponData, batchData] = await Promise.all([
    adminApi.get('dashboard'),
    adminApi.get('kfc-tickets'),
    adminApi.get('products'),
    adminApi.get('coupons'),
    adminApi.get('coupon-batches'),
  ]);
  state.stats = stats;
  state.tickets = ticketData.items || [];
  state.products = productData.items || [];
  state.coupons = couponData.items || [];
  state.batches = batchData.items || [];
}

export async function reloadAll() {
  clearError();
  try {
    await loadCore();
    await Promise.all([loadAccessLinks(), loadRedemptions()]);
    renderAll();
  } catch (error) {
    handleError(error);
  }
}

function renderAll() {
  renderDashboard();
  renderTickets();
  renderProducts();
  renderAccessLinks();
  renderCoupons();
  renderBatches();
  renderRedemptions();
  renderTaobaoPrices();
}

async function logout() {
  try {
    await adminApi.post('auth/logout', {});
  } catch {
    // 即使后端会话已经失效，也要清理本地 token。
  } finally {
    localStorage.removeItem('kfc-admin-token');
    window.location.replace(adminLoginPath());
  }
}

async function boot() {
  await loadPageFragments();
  bindDialogClosers();
  const context = { reloadAll };
  initTickets(context);
  initProducts(context);
  initAccessLinks(context);
  initCoupons(context);
  initRedemptions(context);
  initTaobaoPrices();
  initTaobaoKeywords();

  document.getElementById('admin-nav').addEventListener('click', (event) => {
    const button = event.target.closest('[data-section]');
    if (button) showSection(button.dataset.section);
  });
  document.getElementById('logout-button').addEventListener('click', logout);
  window.addEventListener('popstate', () => showSection(sectionFromPath(), { updateUrl: false }));
  showSection(sectionFromPath(), { updateUrl: false });

  try {
    await Promise.all([reloadAll(), loadTaobaoPrices(), loadTaobaoKeywords()]);
  } catch (error) {
    handleError(error);
  } finally {
    document.getElementById('global-loading').hidden = true;
  }
}

void boot();
