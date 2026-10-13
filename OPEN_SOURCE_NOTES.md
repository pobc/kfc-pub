# 公开版本准备记录

从私有仓库 `pobc/kfc` 的固定快照导出；创建独立公开 Git 历史，不复制私有仓库的任何旧提交。

## 已移除的内容

- `.kfc/fixtures/kfc-coupon-list-20260822.json`：真实账号关联优惠券数据
- `tests/test_kfc_kbck_extraction.py`：生产抓包数据
- `tests/manual_use_coupon_code.py`、`tests/use_coupon_doc_request.py`：真实样式认证材料
- `tests/manual_get_ads_collection.py`：硬编码第三方 Cookie
- `.github/workflows/notify-wecom.yml`：带 Webhook Key 的推送工作流
- `AGENTS.md`：仅供私有开发仓库使用的内部操作约定

## 已修改的内容

- 数据库初始化不再内置真实管理员账户或密码哈希
- 新增 `tools/create_admin.py` 创建或重置管理员
- 环境示例、应用默认值和本机路径改为通用配置
- KFC 签名配置的日志不打印密钥
- CI 不再自动提交或推送修改到 `main`
- 测试代码中手机号改为虚构示例值或环境变量

原有私有仓库未做任何更改。

**注意：这是源码敏感信息清理，不等于独立安全审计，也不能使原来已经生成的令牌自动失效。**
