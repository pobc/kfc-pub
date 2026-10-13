import { state } from './common.js';

export function renderDashboard() {
  document.getElementById('stat-tickets').textContent = String(state.stats?.kfcTickets ?? 0);
  document.getElementById('stat-products').textContent = String(state.stats?.products ?? 0);
  document.getElementById('stat-coupons').textContent = String(state.stats?.coupons ?? 0);
  document.getElementById('stat-access-links').textContent = String(state.stats?.accessLinks ?? 0);
  document.getElementById('stat-invalid-access-links').textContent = String(state.stats?.invalidAccessLinks ?? 0);
}
