import { adminApi } from './api.js';
import {
  clearError,
  closeDialog,
  dateTime,
  escapeHtml,
  handleError,
  openDialog,
  showMessage,
  state,
} from './common.js';

let reloadAll = async () => {};
let captureId = '';
let syncTargetId = '';

export function initTickets(context) {
  reloadAll = context.reloadAll;
  document.getElementById('ticket-open').addEventListener('click', () => openDialog('ticket-dialog'));
  document.getElementById('ticket-form').addEventListener('submit', createTicket);
  document.getElementById('capture-open').addEventListener('click', openCaptureDialog);
  document.getElementById('capture-start').addEventListener('click', startCapture);
  document.getElementById('capture-save-form').addEventListener('submit', saveCapture);
  document.getElementById('ticket-sync-dialog-form').addEventListener('submit', syncCoupons);
  document.getElementById('capture-dialog').addEventListener('close', () => {
    if (captureId) void cancelCapture();
  });
}

async function createTicket(event) {
  event.preventDefault();
  const submit = event.submitter;
  submit.disabled = true;
  clearError();
  try {
    const phone = document.getElementById('ticket-phone').value.trim();
    await adminApi.post('kfc-tickets', {
      name: phone,
      ticket: document.getElementById('ticket-ticket').value.trim(),
      userCode: document.getElementById('ticket-user-code').value.trim(),
      phone,
      openId: document.getElementById('ticket-open-id').value.trim(),
      cookie: document.getElementById('ticket-cookie').value.trim(),
      remark: document.getElementById('ticket-remark').value.trim(),
    });
    event.currentTarget.reset();
    closeDialog('ticket-dialog');
    await reloadAll();
    showMessage('KFC Ticket 已保存');
  } catch (error) {
    handleError(error);
  } finally {
    submit.disabled = false;
  }
}

async function editTicket(item) {
  const userCode = window.prompt('userCode', item.userCode)?.trim();
  if (!userCode) return;
  const phone = window.prompt('手机号', item.phone || item.name || '')?.trim();
  if (!phone) return;
  const openId = window.prompt('openId（可空）', item.openId || '');
  if (openId === null) return;
  const remark = window.prompt('备注', item.remark || '');
  if (remark === null) return;
  const ticket = window.prompt('新 ticket（留空表示保持不变）', '');
  if (ticket === null) return;
  const cookie = window.prompt('新 Cookie Header（留空表示保持不变）', '');
  if (cookie === null) return;
  const patch = {
    name: phone,
    userCode,
    phone,
    openId: openId.trim(),
    remark: remark.trim(),
  };
  if (ticket.trim()) patch.ticket = ticket.trim();
  if (cookie.trim()) patch.cookie = cookie.trim();
  try {
    await adminApi.patch(`kfc-tickets/${item.id}`, patch);
    await reloadAll();
    showMessage('KFC Ticket 已更新');
  } catch (error) {
    handleError(error);
  }
}

async function toggleTicket(item) {
  try {
    await adminApi.patch(`kfc-tickets/${item.id}`, { enabled: !item.enabled });
    await reloadAll();
    showMessage(item.enabled ? 'KFC Ticket 已停用' : 'KFC Ticket 已启用');
  } catch (error) {
    handleError(error);
  }
}

async function removeTicket(item) {
  if (!window.confirm(`确认删除 KFC Ticket “${item.name}”？历史卡券不会被删除。`)) return;
  try {
    await adminApi.delete(`kfc-tickets/${item.id}`);
    await reloadAll();
    showMessage('KFC Ticket 已删除');
  } catch (error) {
    handleError(error);
  }
}

async function openTicketBrowser(item, button) {
  if (!item.enabled || !item.hasAuthState) {
    handleError(new Error('当前 KFC Ticket 没有可直接恢复的完整登录状态'));
    return;
  }
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = '正在打开…';
  try {
    await adminApi.post(`kfc-tickets/${item.id}/open-browser`, {});
    showMessage(`已打开 ${item.phone || item.name} 的 KFC 登录窗口`);
  } catch (error) {
    handleError(error);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

function openSyncDialog(item) {
  if (!item.enabled) {
    handleError(new Error('KFC Ticket 已停用，无法同步卡券'));
    return;
  }
  syncTargetId = item.id;
  document.getElementById('ticket-sync-help').textContent = `使用 KFC Ticket “${item.name}” 拉取卡券。`;
  document.getElementById('ticket-sync-batch').value = '';
  openDialog('ticket-sync-dialog');
}

async function syncCoupons(event) {
  event.preventDefault();
  if (!syncTargetId) return;
  const submit = event.submitter;
  submit.disabled = true;
  try {
    await adminApi.post('coupons/sync-kfc', {
      ticketId: syncTargetId,
      batchName: document.getElementById('ticket-sync-batch').value.trim() || undefined,
    });
    closeDialog('ticket-sync-dialog');
    syncTargetId = '';
    await reloadAll();
    showMessage('KFC 卡券同步完成');
  } catch (error) {
    handleError(error);
  } finally {
    submit.disabled = false;
  }
}

function openCaptureDialog() {
  captureId = '';
  document.getElementById('capture-start-view').hidden = false;
  document.getElementById('capture-save-form').hidden = true;
  document.getElementById('capture-save-form').reset();
  openDialog('capture-dialog');
}

async function startCapture() {
  const button = document.getElementById('capture-start');
  button.disabled = true;
  button.textContent = '正在打开…';
  try {
    const result = await adminApi.post('kfc-sessions/capture/start', {});
    captureId = result.captureId;
    document.getElementById('capture-start-view').hidden = true;
    document.getElementById('capture-save-form').hidden = false;
    showMessage('KFC 登录页面已打开，请完成手机号/验证码登录');
  } catch (error) {
    handleError(error);
  } finally {
    button.disabled = false;
    button.textContent = '打开 KFC 登录页面';
  }
}

async function saveCapture(event) {
  event.preventDefault();
  if (!captureId) return;
  const submit = event.submitter;
  submit.disabled = true;
  try {
    await adminApi.post(`kfc-sessions/capture/${captureId}/save`, {
      remark: document.getElementById('capture-remark').value.trim(),
    });
    captureId = '';
    closeDialog('capture-dialog');
    await reloadAll();
    showMessage('KFC 登录状态已抓取并保存');
  } catch (error) {
    handleError(error);
  } finally {
    submit.disabled = false;
  }
}

async function cancelCapture() {
  const id = captureId;
  captureId = '';
  if (!id) return;
  try {
    await adminApi.delete(`kfc-sessions/capture/${id}`);
  } catch {
    // 浏览器窗口可能已被手工关闭。
  }
}

export function renderTickets() {
  const rows = document.getElementById('ticket-rows');
  document.getElementById('ticket-empty').hidden = state.tickets.length > 0;
  rows.innerHTML = state.tickets.map((item) => `
    <div class="data-row ticket-admin-row" data-ticket-id="${escapeHtml(item.id)}">
      <strong>${escapeHtml(item.name)}</strong>
      <span><code>${escapeHtml(item.ticketMasked)}</code><small>${escapeHtml(item.userCode)}</small></span>
      <span class="ticket-open-id"><code>${escapeHtml(item.openId || '—')}</code></span>
      <b class="badge ${item.enabled ? 'badge--success' : 'badge--danger'}">${item.enabled ? '启用' : '停用'}</b>
      <span>${item.hasAuthState ? '<b class="badge badge--success">完整登录态</b>' : ''}<small>${item.hasAuthState ? dateTime(item.capturedAt) : item.hasCookie ? '仅 Cookie' : '手工 Ticket'}</small></span>
      <span>${escapeHtml(item.remark || '—')}</span>
      <span class="row-actions"><button class="text-button" type="button" data-action="edit">编辑</button><button class="text-button" type="button" data-action="toggle">${item.enabled ? '停用' : '启用'}</button><button class="text-button text-button--muted" type="button" data-action="delete">删除</button></span>
      <span class="ticket-sync-action"><button class="button button--primary" type="button" data-action="sync" ${item.enabled ? '' : 'disabled'}>同步卡券</button></span>
      <span class="ticket-browser-action"><button class="button button--dark" type="button" data-action="login" ${item.enabled && item.hasAuthState ? '' : 'disabled'} title="${item.hasAuthState ? '使用当前 Ticket 保存的登录状态打开 Chrome' : '需要先获取完整登录状态'}">直接登录使用</button></span>
    </div>`).join('');
  rows.querySelectorAll('[data-ticket-id]').forEach((row) => {
    const item = state.tickets.find((ticket) => ticket.id === row.dataset.ticketId);
    row.querySelector('[data-action="edit"]')?.addEventListener('click', () => editTicket(item));
    row.querySelector('[data-action="toggle"]')?.addEventListener('click', () => toggleTicket(item));
    row.querySelector('[data-action="delete"]')?.addEventListener('click', () => removeTicket(item));
    row.querySelector('[data-action="sync"]')?.addEventListener('click', () => openSyncDialog(item));
    row.querySelector('[data-action="login"]')?.addEventListener('click', (event) => openTicketBrowser(item, event.currentTarget));
  });
}
