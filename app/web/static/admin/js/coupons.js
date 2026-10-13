import { adminApi } from './api.js';
import {
  OTHER_GROUP,
  dateTime,
  enabledTickets,
  escapeHtml,
  handleError,
  matchesProductGroup,
  productGroupName,
  replaceOptions,
  showMessage,
  state,
} from './common.js';

let reloadAll = async () => {};
let couponProductInitialized = false;

function kfcStatusLabel(status) {
  return ({ AVAILABLE: '有效', USED: '已使用', EXPIRED: '已过期', REFUNDED: '已退款' })[status] || '未知';
}
function textIncludes(value, keyword) {
  return String(value || '').toLocaleLowerCase().includes(keyword.trim().toLocaleLowerCase());
}

function currentQuery() {
  return {
    couponCode: document.getElementById('coupon-code-query').value.trim(),
    productName: document.getElementById('coupon-product-query').value.trim(),
    useScope: document.getElementById('coupon-scope-query').value,
    kfcStatus: document.getElementById('coupon-status-query').value,
    localStatus: document.getElementById('coupon-local-status-query').value,
    batchId: document.getElementById('coupon-batch-query').value,
    createdFrom: document.getElementById('coupon-created-from').value,
    createdTo: document.getElementById('coupon-created-to').value,
  };
}

function queryFilteredCoupons() {
  const query = currentQuery();
  const from = query.createdFrom ? new Date(`${query.createdFrom}T00:00:00`).getTime() : -Infinity;
  const to = query.createdTo ? new Date(`${query.createdTo}T23:59:59.999`).getTime() : Infinity;
  return state.coupons.filter((item) => {
    if (query.couponCode && !textIncludes(item.couponCode, query.couponCode)) return false;
    if (query.productName && !textIncludes(item.productName || productGroupName(item.productId), query.productName)) return false;
    if (query.useScope && item.useScope !== query.useScope) return false;
    if (query.kfcStatus && item.kfcStatus !== query.kfcStatus) return false;
    if (query.localStatus && item.localStatus !== query.localStatus) return false;
    if (query.batchId && item.batchId !== query.batchId) return false;
    const created = new Date(item.createdAt).getTime();
    return created >= from && created <= to;
  }).sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
}

function filteredCoupons() {
  const items = queryFilteredCoupons();
  return state.couponProductId ? items.filter((item) => matchesProductGroup(item.productId, state.couponProductId)) : items;
}

function couponCount(productId) {
  return queryFilteredCoupons().filter((item) => matchesProductGroup(item.productId, productId)).length;
}

export function initCoupons(context) {
  reloadAll = context.reloadAll;
  document.getElementById('coupon-import-form').addEventListener('submit', importCoupons);
  document.getElementById('coupon-sync-form').addEventListener('submit', syncCoupons);
  document.getElementById('coupon-query-form').addEventListener('input', renderCoupons);
  document.getElementById('coupon-query-form').addEventListener('change', renderCoupons);
  document.getElementById('coupon-reset').addEventListener('click', resetQuery);
}

async function importCoupons(event) {
  event.preventDefault();
  const submit = event.submitter;
  submit.disabled = true;
  try {
    const couponCodes = document.getElementById('import-codes').value.split(/[\r\n,]+/).map((value) => value.trim()).filter(Boolean);
    const result = await adminApi.post('coupons/import', {
      productId: document.getElementById('import-product').value,
      batchName: document.getElementById('import-batch-name').value.trim(),
      couponCodes,
    });
    document.getElementById('import-codes').value = '';
    const resultBox = document.getElementById('import-result');
    const failures = result.failures || [];
    resultBox.hidden = false;
    resultBox.innerHTML = `<strong>最近一次导入：成功 ${(result.items || []).length}，失败 ${failures.length}</strong>${failures.length ? `<ul>${failures.map((item) => `<li>第 ${item.row} 行 ${escapeHtml(item.couponCode || '空值')}：${escapeHtml(item.reason)}</li>`).join('')}</ul>` : ''}`;
    await reloadAll();
    showMessage(`成功导入 ${(result.items || []).length} 张卡券`);
  } catch (error) {
    handleError(error);
  } finally {
    submit.disabled = false;
  }
}

async function syncCoupons(event) {
  event.preventDefault();
  const submit = event.submitter;
  submit.disabled = true;
  try {
    await adminApi.post('coupons/sync-kfc', {
      ticketId: document.getElementById('sync-ticket').value,
      batchName: document.getElementById('sync-batch-name').value.trim() || undefined,
    });
    await reloadAll();
    showMessage('KFC 卡券同步完成');
  } catch (error) {
    handleError(error);
  } finally {
    submit.disabled = false;
  }
}

function resetQuery() {
  document.getElementById('coupon-code-query').value = '';
  document.getElementById('coupon-product-query').value = '';
  document.getElementById('coupon-scope-query').value = '';
  document.getElementById('coupon-status-query').value = 'AVAILABLE';
  document.getElementById('coupon-local-status-query').value = '';
  document.getElementById('coupon-batch-query').value = '';
  document.getElementById('coupon-created-from').value = '';
  document.getElementById('coupon-created-to').value = '';
  state.couponProductId = state.products[0]?.id || OTHER_GROUP;
  renderCoupons();
}

async function editCoupon(item) {
  const couponCode = window.prompt('卡券码', item.couponCode)?.trim();
  if (!couponCode) return;
  try {
    await adminApi.patch(`coupons/${item.id}`, { couponCode });
    await reloadAll();
    showMessage('卡券修改成功');
  } catch (error) { handleError(error); }
}

async function resetCoupon(item) {
  try {
    await adminApi.post(`coupons/${item.id}/reset`, {});
    await reloadAll();
    showMessage('卡券状态已重置为 wait');
  } catch (error) { handleError(error); }
}

async function removeCoupon(item) {
  if (!window.confirm('确认删除该卡券？已分配或已下单的卡券不允许删除。')) return;
  try {
    await adminApi.delete(`coupons/${item.id}`);
    await reloadAll();
    showMessage('卡券已删除');
  } catch (error) { handleError(error); }
}

async function editBatch(item) {
  const name = window.prompt('批次名称', item.name)?.trim();
  if (!name) return;
  try {
    await adminApi.patch(`coupon-batches/${item.id}`, { name });
    await reloadAll();
    showMessage('批次名称已更新');
  } catch (error) { handleError(error); }
}

async function removeBatch(item) {
  if (!window.confirm(`确认删除批次“${item.name}”？批次内卡券只有全部未分配且未下单时才能一起删除。`)) return;
  try {
    await adminApi.delete(`coupon-batches/${item.id}`);
    await reloadAll();
    showMessage('导入批次已删除');
  } catch (error) { handleError(error); }
}

export function renderCoupons() {
  const productIds = new Set(state.products.map((item) => item.id));
  if (!couponProductInitialized) {
    state.couponProductId = state.products[0]?.id || OTHER_GROUP;
    couponProductInitialized = true;
  } else if (state.couponProductId !== OTHER_GROUP && state.couponProductId && !productIds.has(state.couponProductId)) {
    state.couponProductId = state.products[0]?.id || OTHER_GROUP;
  }

  const importProduct = document.getElementById('import-product');
  replaceOptions(importProduct, state.products, { emptyLabel: '请选择', selected: importProduct.value || state.products[0]?.id || '' });
  const syncTicket = document.getElementById('sync-ticket');
  const tickets = enabledTickets();
  replaceOptions(syncTicket, tickets, { emptyLabel: '选择用于同步的 Ticket', selected: syncTicket.value || tickets[0]?.id || '' });

  const batchQuery = document.getElementById('coupon-batch-query');
  const selectedBatch = batchQuery.value;
  batchQuery.innerHTML = `<option value="">全部批次</option>${state.batches.map((batch) => `<option value="${escapeHtml(batch.id)}">${escapeHtml(batch.name)}</option>`).join('')}`;
  if (state.batches.some((batch) => batch.id === selectedBatch)) batchQuery.value = selectedBatch;

  const queryItems = queryFilteredCoupons();
  const items = filteredCoupons();
  document.getElementById('coupon-all-count').textContent = String(state.coupons.length);
  document.getElementById('coupon-filter-count').textContent = String(items.length);
  document.getElementById('coupon-category-count').textContent = `${items.length} 张`;
  const query = currentQuery();
  const hasFilter = state.couponProductId !== '' || Object.values(query).some(Boolean);
  document.getElementById('coupon-filter-badge').hidden = !hasFilter;

  const tabs = document.getElementById('coupon-product-tabs');
  tabs.innerHTML = [
    ...state.products.map((product) => `<button type="button" role="tab" data-product="${escapeHtml(product.id)}" class="${state.couponProductId === product.id ? 'active' : ''}"><span>${escapeHtml(product.name)}</span><small>${couponCount(product.id)}</small></button>`),
    `<button type="button" role="tab" data-product="${OTHER_GROUP}" class="${state.couponProductId === OTHER_GROUP ? 'active' : ''}"><span>其他</span><small>${couponCount(OTHER_GROUP)}</small></button>`,
    `<button type="button" role="tab" data-product="" class="${state.couponProductId === '' ? 'active' : ''}"><span>全部</span><small>${queryItems.length}</small></button>`,
  ].join('');
  tabs.querySelectorAll('[data-product]').forEach((button) => button.addEventListener('click', () => { state.couponProductId = button.dataset.product; renderCoupons(); }));

  const rows = document.getElementById('coupon-rows');
  const empty = document.getElementById('coupon-empty');
  empty.hidden = items.length > 0;
  empty.querySelector('strong').textContent = hasFilter ? '没有匹配的卡券' : '暂无卡券';
  empty.querySelector('small').textContent = hasFilter ? '请调整或重置筛选条件后重试' : '请先选择 KFC Ticket 同步卡券';
  rows.innerHTML = items.map((item) => `<div class="data-row coupon-admin-row" data-coupon-id="${escapeHtml(item.id)}">
    <code>${escapeHtml(item.id)}</code><code>${escapeHtml(item.couponCode)}</code>
    <span>${escapeHtml(item.productName || productGroupName(item.productId))}</span>
    <span>${escapeHtml(kfcStatusLabel(item.kfcStatus))}</span><span>${escapeHtml(item.localStatus || 'wait')}</span>
    <span>${escapeHtml(state.batches.find((batch) => batch.id === item.batchId)?.name || '—')}</span>
    <span>${escapeHtml(dateTime(item.createdAt))}</span>
    <span class="row-actions"><button class="text-button" type="button" data-action="edit">编辑</button><button class="text-button text-button--muted" type="button" data-action="delete">删除</button><button class="text-button" type="button" data-action="reset" ${item.localStatus === 'wait' ? 'disabled' : ''}>重置</button></span>
  </div>`).join('');
  rows.querySelectorAll('[data-coupon-id]').forEach((row) => {
    const item = state.coupons.find((coupon) => coupon.id === row.dataset.couponId);
    row.querySelector('[data-action="edit"]').addEventListener('click', () => editCoupon(item));
    row.querySelector('[data-action="delete"]').addEventListener('click', () => removeCoupon(item));
    row.querySelector('[data-action="reset"]').addEventListener('click', () => resetCoupon(item));
  });

  renderBatches();
}

export function renderBatches() {
  const rows = document.getElementById('batch-rows');
  document.getElementById('batch-empty').hidden = state.batches.length > 0;
  rows.innerHTML = state.batches.map((item) => `<div class="data-row batch-admin-row" data-batch-id="${escapeHtml(item.id)}">
    <strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.productId ? productGroupName(item.productId) : '自动分类')}</span>
    <span>${item.successCount} / ${item.failedCount}</span><span>${escapeHtml(dateTime(item.importedAt))}</span>
    <span class="row-actions"><button class="text-button" type="button" data-action="edit">编辑</button><button class="text-button text-button--muted" type="button" data-action="delete">删除</button></span>
  </div>`).join('');
  rows.querySelectorAll('[data-batch-id]').forEach((row) => {
    const item = state.batches.find((batch) => batch.id === row.dataset.batchId);
    row.querySelector('[data-action="edit"]').addEventListener('click', () => editBatch(item));
    row.querySelector('[data-action="delete"]').addEventListener('click', () => removeBatch(item));
  });
}
