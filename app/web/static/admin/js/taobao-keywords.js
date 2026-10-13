import { adminApi } from './api.js';
import { clearError, dateTime, escapeHtml, handleError, renderPagination, setBusy, showMessage } from './common.js';

let items = [];
let editingId = null;
let saving = false;
let page = 1;
let pageSize = 20;

export function initTaobaoKeywords() {
  document.getElementById('taobao-keyword-form').addEventListener('submit', saveKeyword);
  document.getElementById('taobao-keyword-cancel').addEventListener('click', resetEditor);
  document.getElementById('taobao-keyword-filter').addEventListener('submit', async (event) => {
    event.preventDefault();
    page = 1;
    clearError();
    try { await loadTaobaoKeywords(); } catch (error) { handleError(error); }
  });
  document.getElementById('taobao-keyword-reset').addEventListener('click', () => {
    document.getElementById('taobao-keyword-search').value = '';
    page = 1;
    renderKeywords();
  });
  document.getElementById('taobao-keyword-rows').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-keyword-action]');
    if (!button || saving) return;
    const id = Number(button.dataset.id);
    clearError();
    try {
      if (button.dataset.keywordAction === 'edit') {
        const item = await adminApi.get(`taobao-keywords/${id}`);
        editingId = item.id;
        document.getElementById('taobao-keyword-name').value = item.keyword;
        document.getElementById('taobao-keyword-url').value = item.url;
        document.getElementById('taobao-keyword-save').textContent = '保存修改';
        document.getElementById('taobao-keyword-cancel').hidden = false;
        document.getElementById('taobao-keyword-name').focus();
      } else {
        const item = items.find((entry) => entry.id === id);
        if (!window.confirm(`确认删除关键词“${item?.keyword || id}”？`)) return;
        button.disabled = true;
        await adminApi.delete(`taobao-keywords/${id}`);
        if (editingId === id) resetEditor();
        await loadTaobaoKeywords();
        showMessage('关键词已删除');
      }
    } catch (error) {
      handleError(error);
    } finally {
      button.disabled = false;
    }
  });
}

export async function loadTaobaoKeywords() {
  const data = await adminApi.get('taobao-keywords');
  items = data.items || [];
  renderKeywords();
  document.dispatchEvent(new CustomEvent('taobao-keywords-changed', { detail: items }));
}

function resetEditor() {
  if (saving) return;
  editingId = null;
  document.getElementById('taobao-keyword-form').reset();
  document.getElementById('taobao-keyword-save').textContent = '新增关键词';
  document.getElementById('taobao-keyword-cancel').hidden = true;
}

async function saveKeyword(event) {
  event.preventDefault();
  if (saving) return;
  clearError();
  const button = document.getElementById('taobao-keyword-save');
  const body = {
    keyword: document.getElementById('taobao-keyword-name').value.trim(),
    url: document.getElementById('taobao-keyword-url').value.trim(),
  };
  const id = editingId;
  saving = true;
  setBusy(button, true, '正在保存…');
  try {
    if (id === null) await adminApi.post('taobao-keywords', body);
    else await adminApi.put(`taobao-keywords/${id}`, body);
    saving = false;
    resetEditor();
    page = 1;
    await loadTaobaoKeywords();
    showMessage(id === null ? '关键词已新增' : '关键词已更新');
  } catch (error) {
    handleError(error);
  } finally {
    saving = false;
    setBusy(button, false);
    button.textContent = editingId === null ? '新增关键词' : '保存修改';
  }
}

function renderKeywords() {
  const search = document.getElementById('taobao-keyword-search').value.trim().toLocaleLowerCase();
  const filtered = items.filter((item) => [item.keyword, item.url].some((value) => value.toLocaleLowerCase().includes(search)));
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  page = Math.min(page, totalPages);
  document.getElementById('taobao-keyword-count').textContent = `共 ${filtered.length} 条`;
  document.getElementById('taobao-keyword-empty').hidden = filtered.length > 0;
  document.getElementById('taobao-keyword-rows').innerHTML = filtered.slice((page - 1) * pageSize, page * pageSize).map((item) => `
    <div class="data-row taobao-keyword-row">
      <span>${item.id}</span><strong>${escapeHtml(item.keyword)}</strong>
      <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.url)}</a>
      <span>${escapeHtml(dateTime(item.createdAt))}</span><span>${escapeHtml(dateTime(item.updatedAt))}</span>
      <span class="taobao-keyword-actions">
        <button class="text-button" data-keyword-action="edit" data-id="${item.id}" type="button">编辑</button>
        <button class="text-button text-button--muted" data-keyword-action="delete" data-id="${item.id}" type="button">删除</button>
      </span>
    </div>`).join('');
  renderPagination(document.getElementById('taobao-keyword-pagination'), {
    page, pageSize, total: filtered.length, totalPages,
    onPage: (value) => { page = value; renderKeywords(); },
    onPageSize: (value) => { pageSize = value; page = 1; renderKeywords(); },
  });
}
