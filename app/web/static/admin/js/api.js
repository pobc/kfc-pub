export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request(url, init = {}) {
  const headers = new Headers(init.headers || {});
  if (!(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(url, { ...init, headers });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = payload && typeof payload.message === 'string'
      ? payload.message
      : '请求失败，请稍后重试';
    throw new ApiError(message, response.status);
  }
  return payload?.data;
}

export function adminToken() {
  return localStorage.getItem('kfc-admin-token') || '';
}

function adminHeaders() {
  const token = adminToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export const adminApi = {
  localBypass: () => request('/api/admin/auth/local-bypass'),
  login: (username, password) => request('/api/admin/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  get: (path) => request(`/api/admin/${path}`, { headers: adminHeaders() }),
  post: (path, body) => request(`/api/admin/${path}`, {
    method: 'POST',
    headers: adminHeaders(),
    body: JSON.stringify(body),
  }),
  put: (path, body) => request(`/api/admin/${path}`, {
    method: 'PUT',
    headers: adminHeaders(),
    body: JSON.stringify(body),
  }),
  patch: (path, body) => request(`/api/admin/${path}`, {
    method: 'PATCH',
    headers: adminHeaders(),
    body: JSON.stringify(body),
  }),
  delete: (path) => request(`/api/admin/${path}`, {
    method: 'DELETE',
    headers: adminHeaders(),
  }),
  uploadProductImage: (file) => {
    const body = new FormData();
    body.append('image', file);
    return request('/api/admin/product-images', {
      method: 'POST',
      headers: adminHeaders(),
      body,
    });
  },
};
