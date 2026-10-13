// Match KFC's StorageWith15Minutes/Taro serialization. A fresh authenticated
// Chrome profile has a session cache, but may have no confirmed restaurant.
storeCode => {
  const sessionPrefix = 'PERSISTENCE_SESSION_ID_BY_15MIN_';
  const storePrefix = 'PERSISTENCE_STORE_CODE_PARENT_BY_15MIN_';
  const keys = Object.keys(localStorage).filter(key => key.startsWith(sessionPrefix + 'preorder_'));
  if (!keys.length) throw new Error('KFC 门店缓存缺少已初始化的自取会话');
  const value = JSON.stringify({data: JSON.stringify({
    data: {storeCodeParent: storeCode, storeCodeCurrent: storeCode, confirm: true},
    expireTime: Date.now() + 15 * 60 * 1000
  })});
  const written = keys.map(key => storePrefix + key.slice(sessionPrefix.length));
  for (const key of written) localStorage.setItem(key, value);
  return written;
}
