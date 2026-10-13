export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function request(url, init = {}) {
  const headers = new Headers(init.headers || {});
  if (!(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(url, { ...init, headers });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 428) window.dispatchEvent(new CustomEvent('public-verification-required'));
    throw new ApiError(payload?.message || '请求失败，请稍后重试', response.status);
  }
  return payload?.data;
}

export const publicApi = {
  confirmStore: (id, store, cityName) => request(`/api/public/access-links/${encodeURIComponent(id)}/stores/confirm`, { method: 'POST', body: JSON.stringify({ storeCode: store.storeCode, storeAddress: store.address, storeName: store.name, storeImageUrl: store.imageUrl, cityName }) }),
  getLink: (id) => request(`/api/public/access-links/${encodeURIComponent(id)}`),
  searchStores: async (id, gbCityCode, keyword) => {
    const data = await request(`/api/public/access-links/${encodeURIComponent(id)}/stores/search`, { method: 'POST', body: JSON.stringify({ gbCityCode, keyword }) });
    return { ...data, stores: (data?.stores || []).map((store) => ({ ...store, storePayload: true })) };
  },
  applyCoupon: (id, storeCode, _store, packType, items, optionIds = []) => request(`/api/public/access-links/${encodeURIComponent(id)}/coupon/apply`, { method: 'POST', body: JSON.stringify({ storeCode, packType, items, optionIds }) }),
  getOrderInfo: (id) => request(`/api/public/access-links/${encodeURIComponent(id)}/orders/getOrderInfo`),
  getVerificationChallenge: (id) => request(`/api/public/verification/challenge?accessId=${encodeURIComponent(id)}`),
  verifyChallenge: (id, challengeToken, answer) => request(`/api/public/verification/verify?accessId=${encodeURIComponent(id)}`, {
    method: 'POST',
    body: JSON.stringify({ challengeToken, answer }),
  }),
};
