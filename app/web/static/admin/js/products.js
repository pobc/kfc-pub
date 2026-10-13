import { adminApi } from './api.js';
import {
  AUTO_PRODUCT_MATCH,
  allCouponCount,
  clearError,
  closeDialog,
  escapeHtml,
  handleError,
  openDialog,
  setBusy,
  showMessage,
  state,
} from './common.js';

let reloadAll = async () => {};
let imageUrl = '';
let imageUploading = false;
let optionEditorProduct = null;
let optionGroups = [];
let optionImageUploads = 0;

export function initProducts(context) {
  reloadAll = context.reloadAll;
  document.getElementById('product-form').addEventListener('submit', createProduct);
  document.getElementById('product-image').addEventListener('change', uploadCreateImage);
  document.getElementById('product-option-add-group').addEventListener('click', addOptionGroup);
  document.getElementById('product-option-save').addEventListener('click', saveProductOptions);
}

async function uploadCreateImage(event) {
  const input = event.target;
  const file = input.files?.[0];
  if (!file) return;
  clearError();
  imageUploading = true;
  const label = document.getElementById('product-image-label');
  label.textContent = '正在上传…';
  try {
    const result = await adminApi.uploadProductImage(file);
    imageUrl = result.imageUrl;
    const preview = document.getElementById('product-image-preview');
    preview.src = imageUrl;
    preview.hidden = false;
    label.textContent = '重新上传图片';
  } catch (error) {
    handleError(error);
    label.textContent = '上传餐品图片';
  } finally {
    imageUploading = false;
    input.value = '';
  }
}

async function createProduct(event) {
  event.preventDefault();
  if (imageUploading) return;
  clearError();
  const form = event.currentTarget;
  const submit = event.submitter;
  submit.disabled = true;
  try {
    await adminApi.post('products', {
      name: document.getElementById('product-name').value.trim(),
      description: document.getElementById('product-description').value.trim(),
      imageUrl: imageUrl || null,
      parentLinkId: AUTO_PRODUCT_MATCH,
      specLinkIds: [],
      condimentLinkIds: [],
    });
    form.reset();
    imageUrl = '';
    document.getElementById('product-image-preview').hidden = true;
    document.getElementById('product-image-label').textContent = '上传餐品图片';
    await reloadAll();
    showMessage('餐品创建成功');
  } catch (error) {
    handleError(error);
  } finally {
    submit.disabled = false;
  }
}

async function editProduct(item) {
  const name = window.prompt('餐品名称', item.name)?.trim();
  if (!name) return;
  const description = window.prompt('餐品描述', item.description || '');
  if (description === null) return;
  try {
    await adminApi.patch(`products/${item.id}`, { name, description: description.trim() });
    await reloadAll();
    showMessage('餐品修改成功');
  } catch (error) {
    handleError(error);
  }
}

async function replaceImage(item) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/jpeg,image/png,image/webp,image/gif';
  input.addEventListener('change', async () => {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const result = await adminApi.uploadProductImage(file);
      await adminApi.patch(`products/${item.id}`, { imageUrl: result.imageUrl });
      await reloadAll();
      showMessage('餐品图片已更新');
    } catch (error) {
      handleError(error);
    }
  });
  input.click();
}

async function clearImage(item) {
  try {
    await adminApi.patch(`products/${item.id}`, { imageUrl: '' });
    await reloadAll();
    showMessage('餐品图片已移除');
  } catch (error) {
    handleError(error);
  }
}

async function removeProduct(item) {
  if (!window.confirm(`确认删除餐品“${item.name}”？已被卡券或访问链接使用的餐品会拒绝删除。`)) return;
  try {
    await adminApi.delete(`products/${item.id}`);
    await reloadAll();
    showMessage('餐品已删除');
  } catch (error) {
    handleError(error);
  }
}

async function openProductOptions(item) {
  clearError();
  try {
    const result = await adminApi.get(`products/${item.id}/options`);
    optionEditorProduct = item;
    optionGroups = (result.groups || []).map((group) => ({
      name: group.name,
      selectCount: group.selectCount ?? 1,
      options: (group.options || []).map((option) => ({
        name: option.name,
        imageUrl: option.imageUrl || '',
      })),
    }));
    document.getElementById('product-options-title').textContent = `配置可选餐品 · ${item.name}`;
    renderOptionGroups();
    openDialog('product-options-dialog');
  } catch (error) {
    handleError(error);
  }
}

function addOptionGroup() {
  optionGroups.push({ name: '', selectCount: 1, options: [] });
  renderOptionGroups();
  const inputs = document.querySelectorAll('[data-option-group-name]');
  inputs[inputs.length - 1]?.focus();
}

function renderOptionItem(option, groupIndex, optionIndex, total) {
  return `
    <div class="product-option-item">
      <div class="product-option-item__image">
        ${option.imageUrl ? `<img src="${escapeHtml(option.imageUrl)}" alt="" />` : '<span class="product-option-item__placeholder"></span>'}
        <label class="product-option-image-button">
          <span>${option.imageUrl ? '换图' : '上传图片'}</span>
          <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" data-option-image="${groupIndex}:${optionIndex}" />
        </label>
        ${option.imageUrl ? `<button type="button" class="text-button text-button--muted" data-option-clear-image="${groupIndex}:${optionIndex}">移图</button>` : ''}
      </div>
      <label class="product-option-item__name">
        <span>餐品名称</span>
        <input maxlength="120" placeholder="手动填写可选餐品名称" value="${escapeHtml(option.name)}" data-option-name="${groupIndex}:${optionIndex}" />
      </label>
      <span class="product-option-item__actions">
        <button type="button" class="text-button text-button--muted" data-option-up="${groupIndex}:${optionIndex}" ${optionIndex === 0 ? 'disabled' : ''}>↑</button>
        <button type="button" class="text-button text-button--muted" data-option-down="${groupIndex}:${optionIndex}" ${optionIndex === total - 1 ? 'disabled' : ''}>↓</button>
        <button type="button" class="text-button" data-option-remove="${groupIndex}:${optionIndex}">移除</button>
      </span>
    </div>`;
}

function renderOptionGroups() {
  const host = document.getElementById('product-option-groups');
  if (!optionGroups.length) {
    host.innerHTML = `
      <div class="product-option-empty">
        <b>当前餐品没有可选项</b>
        <span>点击“添加分组”，例如建立“主食”“饮料”，组内餐品全部手动添加。</span>
      </div>`;
    return;
  }

  host.innerHTML = optionGroups.map((group, groupIndex) => `
    <section class="product-option-group" data-option-group="${groupIndex}">
      <div class="product-option-group__header">
        <label>
          <span>分组名称</span>
          <input maxlength="50" placeholder="例如：主食、饮料" value="${escapeHtml(group.name)}" data-option-group-name="${groupIndex}" />
        </label>
        <label class="product-option-group__count">
          <span>必选数量</span>
          <input type="number" min="1" max="${Math.max(1, group.options.length)}" step="1" value="${escapeHtml(group.selectCount)}" data-option-group-count="${groupIndex}" />
        </label>
        <span class="product-option-group__tools">
          <button type="button" class="text-button text-button--muted" data-group-up="${groupIndex}" ${groupIndex === 0 ? 'disabled' : ''}>上移</button>
          <button type="button" class="text-button text-button--muted" data-group-down="${groupIndex}" ${groupIndex === optionGroups.length - 1 ? 'disabled' : ''}>下移</button>
          <button type="button" class="text-button" data-group-remove="${groupIndex}">删除组</button>
        </span>
      </div>
      <p class="product-option-group__hint">顾客需选满指定数量，每种餐品限选一次。</p>
      <div class="product-option-items">
        ${group.options.length
          ? group.options.map((option, optionIndex) => renderOptionItem(option, groupIndex, optionIndex, group.options.length)).join('')
          : '<div class="product-option-items__empty">这个分组还没有餐品</div>'}
      </div>
      <button type="button" class="product-option-add-item" data-option-add="${groupIndex}">+ 手动添加餐品</button>
    </section>`).join('');

  bindOptionGroupEvents();
}

function swapItems(items, left, right) {
  [items[left], items[right]] = [items[right], items[left]];
}

async function uploadOptionImage(input) {
  const [groupIndex, optionIndex] = input.dataset.optionImage.split(':').map(Number);
  const file = input.files?.[0];
  if (!file) return;
  clearError();
  optionImageUploads += 1;
  input.disabled = true;
  try {
    const result = await adminApi.uploadProductImage(file);
    optionGroups[groupIndex].options[optionIndex].imageUrl = result.imageUrl;
    renderOptionGroups();
  } catch (error) {
    handleError(error);
  } finally {
    optionImageUploads -= 1;
  }
}

function bindOptionGroupEvents() {
  document.querySelectorAll('[data-option-group-count]').forEach((input) => {
    input.addEventListener('input', () => {
      optionGroups[Number(input.dataset.optionGroupCount)].selectCount = Number(input.value);
    });
  });
  document.querySelectorAll('[data-option-group-name]').forEach((input) => {
    input.addEventListener('input', () => {
      optionGroups[Number(input.dataset.optionGroupName)].name = input.value;
    });
  });

  document.querySelectorAll('[data-group-remove]').forEach((button) => {
    button.addEventListener('click', () => {
      optionGroups.splice(Number(button.dataset.groupRemove), 1);
      renderOptionGroups();
    });
  });

  document.querySelectorAll('[data-group-up]').forEach((button) => {
    button.addEventListener('click', () => {
      const index = Number(button.dataset.groupUp);
      swapItems(optionGroups, index, index - 1);
      renderOptionGroups();
    });
  });

  document.querySelectorAll('[data-group-down]').forEach((button) => {
    button.addEventListener('click', () => {
      const index = Number(button.dataset.groupDown);
      swapItems(optionGroups, index, index + 1);
      renderOptionGroups();
    });
  });

  document.querySelectorAll('[data-option-add]').forEach((button) => {
    button.addEventListener('click', () => {
      const groupIndex = Number(button.dataset.optionAdd);
      optionGroups[groupIndex].options.push({ name: '', imageUrl: '' });
      renderOptionGroups();
      const inputs = document.querySelectorAll(`[data-option-name^="${groupIndex}:"]`);
      inputs[inputs.length - 1]?.focus();
    });
  });

  document.querySelectorAll('[data-option-name]').forEach((input) => {
    input.addEventListener('input', () => {
      const [groupIndex, optionIndex] = input.dataset.optionName.split(':').map(Number);
      optionGroups[groupIndex].options[optionIndex].name = input.value;
    });
  });

  document.querySelectorAll('[data-option-image]').forEach((input) => {
    input.addEventListener('change', () => uploadOptionImage(input));
  });

  document.querySelectorAll('[data-option-clear-image]').forEach((button) => {
    button.addEventListener('click', () => {
      const [groupIndex, optionIndex] = button.dataset.optionClearImage.split(':').map(Number);
      optionGroups[groupIndex].options[optionIndex].imageUrl = '';
      renderOptionGroups();
    });
  });

  document.querySelectorAll('[data-option-remove]').forEach((button) => {
    button.addEventListener('click', () => {
      const [groupIndex, optionIndex] = button.dataset.optionRemove.split(':').map(Number);
      optionGroups[groupIndex].options.splice(optionIndex, 1);
      renderOptionGroups();
    });
  });

  document.querySelectorAll('[data-option-up]').forEach((button) => {
    button.addEventListener('click', () => {
      const [groupIndex, optionIndex] = button.dataset.optionUp.split(':').map(Number);
      swapItems(optionGroups[groupIndex].options, optionIndex, optionIndex - 1);
      renderOptionGroups();
    });
  });

  document.querySelectorAll('[data-option-down]').forEach((button) => {
    button.addEventListener('click', () => {
      const [groupIndex, optionIndex] = button.dataset.optionDown.split(':').map(Number);
      swapItems(optionGroups[groupIndex].options, optionIndex, optionIndex + 1);
      renderOptionGroups();
    });
  });
}

async function saveProductOptions() {
  if (!optionEditorProduct) return;
  clearError();
  if (optionImageUploads > 0) {
    handleError(new Error('图片还在上传，请稍后再保存'));
    return;
  }

  const groups = optionGroups.map((group) => ({
    name: group.name.trim(),
    selectCount: group.selectCount,
    options: group.options.map((option) => ({
      name: option.name.trim(),
      imageUrl: option.imageUrl || null,
    })),
  }));
  const invalidGroup = groups.find((group) => !group.name || !group.options.length);
  if (invalidGroup) {
    handleError(new Error(!invalidGroup.name ? '请填写分组名称' : `分组“${invalidGroup.name}”至少添加一个餐品`));
    return;
  }
  const invalidCount = groups.find((group) => !Number.isInteger(group.selectCount) || group.selectCount < 1 || group.selectCount > group.options.length);
  if (invalidCount) {
    handleError(new Error(`分组“${invalidCount.name}”的必选数量须为1至${invalidCount.options.length}的整数`));
    return;
  }
  const invalidOptionGroup = groups.find((group) => group.options.some((option) => !option.name));
  if (invalidOptionGroup) {
    handleError(new Error(`分组“${invalidOptionGroup.name}”存在未填写名称的餐品`));
    return;
  }

  const button = document.getElementById('product-option-save');
  setBusy(button, true, '保存中…');
  try {
    await adminApi.put(`products/${optionEditorProduct.id}/options`, { groups });
    closeDialog('product-options-dialog');
    optionEditorProduct = null;
    optionGroups = [];
    await reloadAll();
    showMessage('可选餐品配置已保存');
  } catch (error) {
    handleError(error);
  } finally {
    setBusy(button, false);
  }
}

export function renderProducts() {
  const rows = document.getElementById('product-rows');
  const empty = document.getElementById('product-empty');
  empty.hidden = state.products.length > 0;
  rows.innerHTML = state.products.map((item) => `
    <div class="data-row product-crud-row" data-product-id="${escapeHtml(item.id)}">
      <span class="product-cell">
        ${item.imageUrl ? `<img src="${escapeHtml(item.imageUrl)}" alt="" />` : ''}
        <span class="product-cell__copy"><b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.description || '暂无描述')}</small><code>${escapeHtml(item.parentLinkId || '')}</code></span>
      </span>
      <strong>${allCouponCount(item.id)}</strong>
      <span class="product-option-summary">
        ${item.optionGroupCount
          ? `<b>${item.optionGroupCount} 组</b><small>${item.optionCount} 个可选餐品</small>`
          : '<span class="badge badge--muted">未配置</span>'}
      </span>
      <span class="row-actions">
        <button class="text-button" type="button" data-action="options">可选餐品</button>
        <button class="text-button" type="button" data-action="edit">编辑</button>
        <button class="text-button" type="button" data-action="image">换图</button>
        ${item.imageUrl ? '<button class="text-button text-button--muted" type="button" data-action="clear-image">移图</button>' : ''}
        <button class="text-button text-button--muted" type="button" data-action="delete">删除</button>
      </span>
    </div>`).join('');

  rows.querySelectorAll('[data-product-id]').forEach((row) => {
    const item = state.products.find((product) => product.id === row.dataset.productId);
    row.querySelector('[data-action="options"]')?.addEventListener('click', () => openProductOptions(item));
    row.querySelector('[data-action="edit"]')?.addEventListener('click', () => editProduct(item));
    row.querySelector('[data-action="image"]')?.addEventListener('click', () => replaceImage(item));
    row.querySelector('[data-action="clear-image"]')?.addEventListener('click', () => clearImage(item));
    row.querySelector('[data-action="delete"]')?.addEventListener('click', () => removeProduct(item));
  });
}
