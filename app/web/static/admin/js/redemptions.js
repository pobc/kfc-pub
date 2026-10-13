import { adminApi } from './api.js';
import {
  OTHER_GROUP,
  dateTime,
  escapeHtml,
  handleError,
  productGroupName,
  renderPagination,
  state,
} from './common.js';

let requestSequence = 0;
const AUTO_REFRESH_INTERVAL_MS = 3000;

function statusLabel(status) {
  return ({ UNREDEEMED: '未核销', REDEEMED: '已核销', INVALID: '已失效' })[status] || status;
}
function statusClass(status) {
  if (status === 'REDEEMED') return 'badge--success';
  if (status === 'INVALID') return 'badge--danger';
  return 'badge--warning';
}
function listPath() {
  const query = new URLSearchParams({ page: String(state.redemptionPage), pageSize: String(state.redemptionPageSize) });
  if (state.redemptionProductId) query.set('productId', state.redemptionProductId);
  if (state.redemptionStatus) query.set('status', state.redemptionStatus);
  return `redemptions?${query.toString()}`;
}

export function initRedemptions() {
  document.getElementById('redemption-status-tabs').querySelectorAll('[data-status]').forEach((button) => {
    button.addEventListener('click', () => {
      state.redemptionStatus = button.dataset.status;
      state.redemptionPage = 1;
      void loadRedemptions();
    });
  });

  // 核销任务在另一个页面异步执行，管理页可见时需要主动同步最新状态。
  window.setInterval(() => {
    if (document.hidden || state.activeSection !== 'redemptions') return;
    void loadRedemptions({ silent: true });
  }, AUTO_REFRESH_INTERVAL_MS);

  window.addEventListener('focus', () => {
    if (state.activeSection === 'redemptions') void loadRedemptions({ silent: true });
  });
}

export async function loadRedemptions({ silent = false } = {}) {
  const seq = ++requestSequence;
  try {
    const data = await adminApi.get(listPath());
    if (seq !== requestSequence) return;
    state.redemptions = data.items || [];
    state.redemptionPage = data.page || 1;
    state.redemptionPageSize = data.pageSize || 20;
    state.redemptionTotal = data.total || 0;
    state.redemptionTotalPages = data.totalPages || 1;
    state.redemptionAllTotal = data.allTotal || 0;
    state.redemptionProductCounts = data.productCounts || {};
    renderRedemptions();
  } catch (error) {
    if (seq === requestSequence && !silent) handleError(error);
  }
}

function productCount(productId) {
  return state.redemptionProductCounts[productId] || 0;
}

export function renderRedemptions() {
  document.getElementById('redemption-all-total').textContent = String(state.redemptionAllTotal || 0);
  document.getElementById('redemption-total').textContent = String(state.redemptionTotal || 0);

  const tabs = document.getElementById('redemption-product-tabs');
  tabs.innerHTML = [
    `<button type="button" data-product="" class="${state.redemptionProductId === '' ? 'active' : ''}"><span>全部</span><small>${state.redemptionAllTotal || 0}</small></button>`,
    ...state.products.map((product) => `<button type="button" data-product="${escapeHtml(product.id)}" class="${state.redemptionProductId === product.id ? 'active' : ''}"><span>${escapeHtml(product.name)}</span><small>${productCount(product.id)}</small></button>`),
    `<button type="button" data-product="${OTHER_GROUP}" class="${state.redemptionProductId === OTHER_GROUP ? 'active' : ''}"><span>其他</span><small>${productCount(OTHER_GROUP)}</small></button>`,
  ].join('');
  tabs.querySelectorAll('[data-product]').forEach((button) => button.addEventListener('click', () => {
    state.redemptionProductId = button.dataset.product;
    state.redemptionPage = 1;
    void loadRedemptions();
  }));
  document.getElementById('redemption-status-tabs').querySelectorAll('[data-status]').forEach((button) => button.classList.toggle('active', button.dataset.status === state.redemptionStatus));

  const rows = document.getElementById('redemption-rows');
  document.getElementById('redemption-empty').hidden = state.redemptions.length > 0;
  rows.innerHTML = state.redemptions.map((item) => `<div class="data-row redemption-admin-row">
    <code>${escapeHtml(item.uuid)}</code><code>${escapeHtml(item.couponCode || '—')}</code>
    <span class="redemption-cell"><strong>${escapeHtml(item.productName || productGroupName(item.productId))}</strong><small>${escapeHtml(productGroupName(item.productId))}</small></span>
    <span class="redemption-cell"><strong>${escapeHtml(item.platformName || item.platformId || '—')}</strong><small>${escapeHtml(item.accountName || item.accountId || '—')}</small></span>
    <b class="badge ${statusClass(item.status)}">${escapeHtml(statusLabel(item.status))}</b>
    <span>${escapeHtml(dateTime(item.orderedAt))}</span><span>${escapeHtml(dateTime(item.redeemedAt))}</span>
    <span class="redemption-cell"><strong>${escapeHtml(item.storeName || '—')}</strong><small>${escapeHtml(item.storeAddress || item.storeCode || '')}</small></span>
  </div>`).join('');

  renderPagination(document.getElementById('redemption-pagination'), {
    page: state.redemptionPage,
    pageSize: state.redemptionPageSize,
    total: state.redemptionTotal,
    totalPages: state.redemptionTotalPages,
    onPage: (page) => { state.redemptionPage = page; void loadRedemptions(); },
    onPageSize: (size) => { state.redemptionPageSize = size; state.redemptionPage = 1; void loadRedemptions(); },
  });
}
