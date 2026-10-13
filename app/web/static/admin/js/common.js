import { ApiError } from './api.js';

export const OTHER_GROUP = '__OTHER__';
export const AUTO_PRODUCT_MATCH = '__AUTO_MATCH_BY_NAME__';


export function adminBasePath() {
  const match = window.location.pathname.match(/^\/([^/]+)\/admin(?:\/|$)/);
  return match ? `/${match[1]}/admin` : '';
}

export function adminLoginPath() {
  const base = adminBasePath();
  return base ? `${base}/login` : '/__not-found__';
}

export const state = {
  stats: null,
  tickets: [],
  products: [],
  coupons: [],
  batches: [],
  accessLinks: [],
  redemptions: [],
  taobaoPrices: [],
  taobaoPriceSummary: {},
  taobaoScrape: {},
  taobaoLogin: {},
  accessPage: 1,
  accessPageSize: 20,
  accessTotal: 0,
  accessTotalPages: 1,
  accessAllTotal: 0,
  accessSearch: '',
  accessStatus: '',
  redemptionPage: 1,
  redemptionPageSize: 20,
  redemptionTotal: 0,
  redemptionTotalPages: 1,
  redemptionAllTotal: 0,
  redemptionProductCounts: {},
  redemptionProductId: '',
  redemptionStatus: '',
  couponProductId: '',
  activeSection: 'dashboard',
  sectionErrors: {},
};

let successTimer;

export function $(selector, root = document) {
  return root.querySelector(selector);
}

export function $$(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

export function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

export function dateTime(value) {
  return value ? new Date(value).toLocaleString() : '—';
}

export function productGroupName(productId) {
  if (!productId) return '其他';
  return state.products.find((item) => item.id === productId)?.name || '其他';
}

export function matchesProductGroup(productId, groupId) {
  return groupId === OTHER_GROUP ? !productId : productId === groupId;
}

export function allCouponCount(productId) {
  return state.coupons.filter((item) => matchesProductGroup(item.productId, productId)).length;
}

export function enabledTickets() {
  return state.tickets.filter((item) => item.enabled);
}

function renderActiveError() {
  const box = $('#global-error');
  if (!box) return;
  const message = state.sectionErrors[state.activeSection] || '';
  box.textContent = message;
  box.hidden = !message;
}

export function syncError() {
  renderActiveError();
}

export function showError(message, section = state.activeSection) {
  state.sectionErrors[section] = message || '';
  if (section === state.activeSection) renderActiveError();
}

export function clearError(section = state.activeSection) {
  showError('', section);
}

export function showMessage(message) {
  const box = $('#global-success');
  const text = $('#global-success-text');
  if (!box || !text) return;
  if (successTimer) window.clearTimeout(successTimer);
  text.textContent = message;
  box.hidden = false;
  successTimer = window.setTimeout(() => {
    box.hidden = true;
    successTimer = undefined;
  }, 5000);
}

export function handleError(error) {
  if (error instanceof ApiError && error.status === 401) {
    localStorage.removeItem('kfc-admin-token');
    window.location.replace(adminLoginPath());
    return;
  }
  showError(error instanceof Error ? error.message : '操作失败，请稍后重试');
}

export function setBusy(element, busy, busyText) {
  if (!element) return;
  if (!element.dataset.defaultText) element.dataset.defaultText = element.textContent || '';
  element.disabled = Boolean(busy);
  element.textContent = busy ? busyText : element.dataset.defaultText;
}

export function replaceOptions(select, items, { includeEmpty = true, emptyLabel = '请选择', selected = '' } = {}) {
  if (!select) return;
  const options = [];
  if (includeEmpty) options.push(`<option value="">${escapeHtml(emptyLabel)}</option>`);
  for (const item of items) {
    options.push(`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`);
  }
  select.innerHTML = options.join('');
  if (selected && items.some((item) => item.id === selected)) select.value = selected;
}

export function pageNumbers(page, totalPages) {
  const start = Math.max(1, Math.min(page - 2, totalPages - 4));
  const end = Math.min(totalPages, start + 4);
  return Array.from({ length: Math.max(0, end - start + 1) }, (_, index) => start + index);
}

export function renderPagination(container, { page, pageSize, total, totalPages, onPage, onPageSize }) {
  if (!container) return;
  if (!total) {
    container.hidden = true;
    container.innerHTML = '';
    return;
  }
  container.hidden = false;
  container.innerHTML = `
    <span>共 ${total} 条，第 ${page} / ${totalPages} 页</span>
    <div class="redemption-pagination__actions">
      ${onPageSize ? `<label>每页
        <select data-page-size>
          ${[10, 20, 50].map((size) => `<option value="${size}" ${size === pageSize ? 'selected' : ''}>${size}</option>`).join('')}
        </select> 条
      </label>` : ''}
      <button type="button" class="redemption-pagination__button" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button>
      ${pageNumbers(page, totalPages).map((number) => `<button type="button" class="redemption-pagination__button redemption-pagination__page ${number === page ? 'active' : ''}" data-page="${number}">${number}</button>`).join('')}
      <button type="button" class="redemption-pagination__button" data-page="${page + 1}" ${page >= totalPages ? 'disabled' : ''}>下一页</button>
    </div>`;
  if (onPageSize) {
    container.querySelector('[data-page-size]')?.addEventListener('change', (event) => onPageSize(Number(event.target.value)));
  }
  container.querySelectorAll('[data-page]').forEach((button) => {
    button.addEventListener('click', () => onPage(Number(button.dataset.page)));
  });
}

export function closeDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog?.open) dialog.close();
}

export function openDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog && !dialog.open) dialog.showModal();
}

export function bindDialogClosers() {
  $$('[data-close-dialog]').forEach((button) => {
    button.addEventListener('click', () => closeDialog(button.dataset.closeDialog));
  });
}
