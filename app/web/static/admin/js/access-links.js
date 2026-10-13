import { adminApi } from './api.js';
import {
  clearError,
  dateTime,
  escapeHtml,
  handleError,
  renderPagination,
  replaceOptions,
  showMessage,
  state,
} from './common.js';

let reloadAll = async () => {};
let searchTimer;
let requestSequence = 0;
let editingId = '';

function statusLabel(status) {
  return ({ UNUSED: '待使用', PROCESSING: '核销处理中', PICKING_UP: '待取餐', FAILED: '下单失败', COMPLETED: '订单完结' })[status] || status;
}

function statusClass(status) {
  if (status === 'UNUSED') return 'badge--success';
  if (status === 'PROCESSING' || status === 'PICKING_UP') return 'badge--warning';
  if (status === 'FAILED') return 'badge--danger';
  return 'badge--muted';
}

function listPath() {
  const query = new URLSearchParams({ page: String(state.accessPage), pageSize: String(state.accessPageSize) });
  if (state.accessSearch) query.set('query', state.accessSearch);
  if (state.accessStatus) query.set('status', state.accessStatus);
  return `access-links?${query.toString()}`;
}

function applyPage(data) {
  state.accessLinks = data.items || [];
  state.accessPage = data.page || 1;
  state.accessPageSize = data.pageSize || 20;
  state.accessTotal = data.total || 0;
  state.accessTotalPages = data.totalPages || 1;
  state.accessAllTotal = data.allTotal || 0;
}

export async function loadAccessLinks() {
  const seq = ++requestSequence;
  try {
    const data = await adminApi.get(listPath());
    if (seq !== requestSequence) return;
    applyPage(data);
    renderAccessLinks();
  } catch (error) {
    if (seq === requestSequence) handleError(error);
  }
}

export function initAccessLinks(context) {
  reloadAll = context.reloadAll;
  document.getElementById('access-create-form').addEventListener('submit', createLinks);
  document.getElementById('access-search').addEventListener('input', (event) => {
    window.clearTimeout(searchTimer);
    state.accessSearch = event.target.value.trim();
    state.accessPage = 1;
    searchTimer = window.setTimeout(() => void loadAccessLinks(), 250);
  });
  document.getElementById('access-status').addEventListener('change', (event) => {
    state.accessStatus = event.target.value;
    state.accessPage = 1;
    void loadAccessLinks();
  });
}

async function createLinks(event) {
  event.preventDefault();
  clearError();
  const submit = event.submitter;
  submit.disabled = true;
  try {
    const result = await adminApi.post('access-links', {
      productId: document.getElementById('access-product').value,
      count: Number(document.getElementById('access-count').value),
    });
    await reloadAll();
    showMessage(`已生成 ${result.count} 条访问链接`);
  } catch (error) {
    handleError(error);
  } finally {
    submit.disabled = false;
  }
}

async function copyLink(item) {
  const url = new URL(`/order/${encodeURIComponent(item.accessId)}`, window.location.origin).href;
  try {
    if (!navigator.clipboard) throw new Error('clipboard unavailable');
    await navigator.clipboard.writeText(url);
    showMessage('访问链接已复制');
  } catch {
    window.prompt('复制访问链接', url);
  }
}

async function saveLink(item, row) {
  const productId = row.querySelector('[data-edit-product]').value;
  if (!productId) return;
  try {
    await adminApi.patch(`access-links/${item.id}`, { productId });
    editingId = '';
    await reloadAll();
    showMessage('访问链接关联餐品已更新');
  } catch (error) {
    handleError(error);
  }
}

async function removeLink(item) {
  if (!window.confirm(`确认删除访问链接 ${item.accessId}？`)) return;
  try {
    await adminApi.delete(`access-links/${item.id}`);
    editingId = '';
    await reloadAll();
    showMessage('访问链接已删除');
  } catch (error) {
    handleError(error);
  }
}

export function renderAccessLinks() {
  const products = state.products;
  replaceOptions(document.getElementById('access-product'), products, {
    emptyLabel: '请选择餐品',
    selected: document.getElementById('access-product').value || products[0]?.id || '',
  });
  document.getElementById('access-all-total').textContent = String(state.accessAllTotal || 0);
  document.getElementById('access-total').textContent = String(state.accessTotal || 0);
  document.getElementById('access-search').value = state.accessSearch;
  document.getElementById('access-status').value = state.accessStatus;

  const rows = document.getElementById('access-rows');
  const empty = document.getElementById('access-empty');
  empty.hidden = state.accessLinks.length > 0;
  empty.querySelector('strong').textContent = state.accessSearch || state.accessStatus ? '没有匹配的链接' : '暂无访问链接';
  rows.innerHTML = state.accessLinks.map((item) => {
    const editing = editingId === item.id;
    const productCell = editing
      ? `<select class="access-link-inline-select" data-edit-product>${products.map((product) => `<option value="${escapeHtml(product.id)}" ${product.id === item.productId ? 'selected' : ''}>${escapeHtml(product.name)}</option>`).join('')}</select>`
      : `<strong>${escapeHtml(item.productName || '—')}</strong>`;
    return `<div class="data-row access-link-row" data-link-id="${escapeHtml(item.id)}">
      <span class="access-link-cell"><code>${escapeHtml(item.accessId)}</code><a href="/order/${encodeURIComponent(item.accessId)}" target="_blank" rel="noopener noreferrer">/order/${escapeHtml(item.accessId)}</a></span>
      ${productCell}
      <b class="badge ${statusClass(item.status)}">${escapeHtml(statusLabel(item.status))}</b>
      <span>${escapeHtml(dateTime(item.createdAt))}</span>
      <span class="row-actions">
        ${editing ? '<button class="text-button" data-action="save" type="button">保存</button><button class="text-button text-button--muted" data-action="cancel" type="button">取消</button>' : '<button class="text-button" data-action="copy" type="button">复制</button><button class="text-button" data-action="edit" type="button">修改餐品</button><button class="text-button text-button--muted" data-action="delete">删除</button>'}
      </span>
    </div>`;
  }).join('');

  rows.querySelectorAll('[data-link-id]').forEach((row) => {
    const item = state.accessLinks.find((link) => link.id === row.dataset.linkId);
    row.querySelector('[data-action="copy"]')?.addEventListener('click', () => copyLink(item));
    row.querySelector('[data-action="edit"]')?.addEventListener('click', () => { editingId = item.id; renderAccessLinks(); });
    row.querySelector('[data-action="cancel"]')?.addEventListener('click', () => { editingId = ''; renderAccessLinks(); });
    row.querySelector('[data-action="save"]')?.addEventListener('click', () => saveLink(item, row));
    row.querySelector('[data-action="delete"]')?.addEventListener('click', () => removeLink(item));
  });

  renderPagination(document.getElementById('access-pagination'), {
    page: state.accessPage,
    pageSize: state.accessPageSize,
    total: state.accessTotal,
    totalPages: state.accessTotalPages,
    onPage: (page) => { state.accessPage = page; void loadAccessLinks(); },
    onPageSize: (size) => { state.accessPageSize = size; state.accessPage = 1; void loadAccessLinks(); },
  });
}
