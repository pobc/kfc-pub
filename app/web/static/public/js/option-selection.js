export function requiredCount(group) {
  const count = Number(group.selectCount ?? 1);
  return Number.isInteger(count) && count > 0 ? count : 1;
}

export function selectedOptions(group, ids = []) {
  const selected = new Set(ids);
  return (group.options || []).filter((option) => selected.has(Number(option.id)));
}

export function initialSelection(group, ids) {
  if (Array.isArray(ids)) return selectedOptions(group, ids).map((option) => Number(option.id));
  return requiredCount(group) === 1 && group.options?.length ? [Number(group.options[0].id)] : [];
}

export function selectionComplete(group, ids) {
  return selectedOptions(group, ids).length === requiredCount(group);
}

export function toggleSelection(group, ids, optionId) {
  const current = selectedOptions(group, ids).map((option) => Number(option.id));
  if (!(group.options || []).some((option) => Number(option.id) === optionId)) return current;
  if (requiredCount(group) === 1) return [optionId];
  if (current.includes(optionId)) return current.filter((id) => id !== optionId);
  if (current.length >= requiredCount(group)) return current;
  return [...current, optionId];
}
