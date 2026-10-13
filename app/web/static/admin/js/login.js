import { adminApi } from './api.js';
import { adminBasePath } from './common.js';

const form = document.getElementById('login-form');
const errorBox = document.getElementById('login-error');
const submit = document.getElementById('login-submit');

async function checkBypass() {
  try {
    const { enabled } = await adminApi.localBypass();
    if (enabled) window.location.replace(adminBasePath());
  } catch {
    // 登录页仍可手工登录。
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  submit.disabled = true;
  submit.textContent = '正在登录…';
  try {
    const result = await adminApi.login(
      document.getElementById('username').value.trim(),
      document.getElementById('password').value,
    );
    localStorage.setItem('kfc-admin-token', result.token);
    window.location.replace(adminBasePath());
  } catch (error) {
    errorBox.textContent = error instanceof Error ? error.message : '登录失败';
    errorBox.hidden = false;
  } finally {
    submit.disabled = false;
    submit.textContent = '登录';
  }
});

void checkBypass();
