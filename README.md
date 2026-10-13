# KFC（公开脱敏版本）

> 该仓库从私有项目代码创建，已移除历史提交、真实会话、优惠券响应、硬编码外部 Webhook 和个人信息。请勿上传运行时 Cookie、Ticket、手机号、浏览器配置与数据库备份。

> 安装数据库时请使用独立强密码，并通过 `tools/create_admin.py` 首次创建管理员。请勿直接用于生产，必须自行完成配置和安全评估。

# KFC 专属链接点餐系统

当前运行时为 Python 3.12 + FastAPI/Uvicorn + PostgreSQL。顾客页和后台页使用原生 HTML / CSS / JavaScript，由 FastAPI 同端口托管；不依赖 Node.js、Vue、Vite 或前端打包产物。

## 文档职责

- `README.md`：运行方式、目录结构、数据库升级和质量检查。
- `PRD.md`：现行业务需求与业务规则。
- `AGENTS.md`：开发、测试、提交和公网边界约束。
- `PROJECT_OPTIMIZATION.md`：代码优化计划与执行状态。

## 代码分层

```text
main.py                                  # 项目唯一运行入口
app/
├── api/                                 # FastAPI HTTP 层、请求模型、鉴权依赖、路由
│   ├── routers/admin/                   # 后台接口
│   └── routers/public/                  # 顾客公网接口
├── service/                             # 业务规则与流程编排，小项目保持单层结构
│   ├── access_link_service.py           # 访问链接、核销查询
│   ├── admin_service.py                 # 后台商品、卡券、批次管理
│   ├── auth_service.py                  # 后台认证
│   ├── checkout_service.py              # 顾客下单、用券流程
│   ├── kfc_account_service.py           # KFC 账号、登录态、卡券同步编排
│   ├── taobao_price_scraper.py          # Playwright 淘宝页面采集与解析
│   ├── taobao_price_service.py          # 价格查询与后台采集任务编排
│   └── kfc/                             # KFC 接口实现文件较多，单独归类
│       ├── contracts.py                 # KFC 请求协议常量
│       ├── http_client.py               # requests.Session、超时、JSON/HTTP 错误
│       ├── h5_config.py                 # H5 动态配置和签名配置提取
│       ├── store_client.py              # 餐厅相关 KFC 调用
│       ├── checkout_client.py           # 购物车、用券、确认、订单状态
│       ├── h5_client.py                 # 对现有调用方保持稳定的组合入口
│       ├── coupon_sync.py               # KFC 卡券同步
│       ├── init_session.py              # KFC sessionId 初始化和缓存
│       ├── query_order_detail.py        # 订单详情查询
│       └── session_capture.py           # Chrome 登录态捕获
├── database/                            # PostgreSQL 数据库层
│   ├── db_connection.py                 # 连接池
│   ├── schema.py                        # 启动时只检查必要结构，不执行 migration
│   ├── integrity_db.py                  # 无外键设计的逻辑完整性检查
│   └── repositories/                    # SQL、事务、行锁；数据库文件统一 *_db.py
├── web/                                 # FastAPI 托管的网页层
│   ├── templates/                       # HTML 页面和后台 HTML 片段
│   │   ├── admin/
│   │   └── public/
│   ├── static/                          # 页面 CSS / JavaScript 静态资源
│   │   ├── admin/
│   │   └── public/
│   └── routes.py                        # 页面与静态资源挂载
├── config.py                            # 统一运行配置
├── logging_utils.py                     # 生产日志脱敏
├── network.py                           # 客户端 IP 解析
└── errors.py                            # 统一错误/响应
migrations/                              # 已有数据库的显式 SQL migration
tests/                                   # Python 测试，数据库业务测试使用真实 PostgreSQL
tools/                                   # migration、完整性检查等工具
static/                                  # 全局静态文件，如 favicon、商品图片
└── product-images/                      # 商品图片文件；数据库只保存 URL/path
database.sql                             # 全新 PostgreSQL 数据库初始化脚本
requirements.txt
```

依赖方向固定为 `API -> service -> database / KFC integration`。`api/` 和 `service/` 不直接执行 SQL；SQL、事务和 PostgreSQL 行锁统一收口在数据库层。公网 `/api/public/*` 不允许成为任意 KFC 接口代理，也不返回 KFC 原始认证信息或原始响应。

## 技术栈

- Python 3.12
- FastAPI / Pydantic / Uvicorn
- PostgreSQL / psycopg 3
- requests / requests.Session
- pytest
- Ruff
- 浏览器原生 HTML / CSS / JavaScript

业务测试禁止 Mock；涉及数据库的业务测试必须连接真实 PostgreSQL。

## 本地安装

```bash
cp .env.example .env
python -m pip install -r requirements.txt
```

Windows 可直接按项目现有批处理脚本启动 PostgreSQL 和应用。

## 数据库初始化与升级

全新数据库执行：

```bash
psql -d <数据库名> -f database.sql
```

已有数据库升级执行：

```bash
python tools/migrate.py
```

migration 按 `migrations/*.sql` 文件名顺序执行，并写入 `schema_migrations` 记录。应用启动只检查运行必需的表和字段，不执行 `ALTER TABLE`、`DROP COLUMN` 等结构升级。

项目明确不使用数据库外键约束；逻辑关联完整性可执行：

```bash
python tools/check_integrity.py
```

## 启动

项目根目录执行：

```bash
python main.py
```

`HOST`、`PORT` 统一由环境配置读取，默认分别为 `0.0.0.0` 和 `3000`。只有 `APP_ENV=development` 时启用 Uvicorn reload；Uvicorn 自带 access log 关闭，由项目中间件统一记录请求耗时。

默认地址：

- 顾客页：`http://localhost:3000/order/<16位专属ID>`
- 管理后台：`http://localhost:3000/<ADMIN_WEB_MD5>/admin`
- 管理后台登录：`http://localhost:3000/<ADMIN_WEB_MD5>/admin/login`
- Swagger：`http://localhost:3000/api/docs`

## 淘宝价格采集

后台“淘宝价格”页面先通过“登录淘宝”打开可见的 Playwright 浏览器。完成登录后点击“保存登录信息”，系统会把 Cookies、localStorage 与 sessionStorage 保存到 PostgreSQL，再允许开始抓取。抓取时载入已保存的登录态，使用页面所选关键词配置的淘宝搜索链接，只采集第一页商品的规格与价格，并按“搜索关键词 + 商品链接 + 规格名称”写入或更新。

“淘宝搜索关键词”页面支持新增、查询、修改、删除配置。`taobao_search_keywords` 表包含数字自增 `id`、`keyword`、`url`、`created_at`、`updated_at`；时间戳由数据库生成，修改保留插入时间。已有数据库执行 `python tools/migrate.py` 应用 `019_taobao_search_keywords.sql`，新库使用最新 `database.sql`。

业务职责分为三部分：关键词配置由独立的 API、service 和 `taobao_keyword_db.py` 管理，输入为关键词与淘宝搜索 URL，输出为带 ID 和时间戳的配置；采集服务使用所选配置的 URL 编排任务，配置删除不影响价格；价格仓储负责筛选和历史清理，页面负责选择、筛选与错误展示，HTTP 边界统一记录接口异常，采集任务边界记录抓取和清理失败。

价格页可勾选已保存的关键词，或输入以中英文逗号、分号分隔的关键词，最多 20 个、每个最多 200 字。任意关键词包含于来源关键词、店铺、商品项或商品链接即匹配；与原搜索条件取交集，排序与数量统计均使用筛选结果。抓取区域只选择已保存的关键词，直接使用其配置 URL；不再提供手动链接输入框，未选择关键词时不能开始抓取。接口使用重复查询参数，例如 `/api/admin/taobao-prices?keywords=鸡腿堡&keywords=可乐`。

每次正式开始抓取时，只删除本次搜索关键词对应、且 `scraped_at` 早于北京时间当天零点的价格。来源关键词使用抓取链接中按声明编码解码的 `q` 参数，入库保存为 `search_keyword`，清理采用精确匹配，不根据商品标题或页面筛选条件猜测归属。同一商品出现在多个关键词下时分别保存，保留其他关键词的数据。执行 `python tools/migrate.py` 应用 `020_taobao_price_search_keyword.sql`；迁移保留已有记录，其未知来源记为空字符串，不参与自动清理。

关键词归属由采集服务从链接解析并贯穿任务，价格仓储负责保存归属、限定清理范围及同关键词内的更新。缺失、过长或不明确的搜索关键词在启动前拒绝。URL 或登录校验失败、重复点击沿用运行中任务时不清理。清理失败会终止本次任务；清理成功后即使抓取失败，该关键词的历史清理也已提交，今天已写入的价格仍保留。任务状态和日志包含来源关键词及清理条数。

相关验证：`python -m pytest tests/integration/test_taobao_keyword_cleanup_db.py tests/integration/test_taobao_keyword_management_db.py tests/unit/test_taobao_price_keyword.py tests/unit/test_taobao_search_url.py -q`。测试使用真实 `kfc` 数据库和独立 HTTP 端口，并保留测试记录；清理用例只删除专用测试关键词的历史价格，同时验证其他关键词与无归属记录不受影响。设置 `TAOBAO_ADMIN_BROWSER_TEST=1` 可额外执行真实 Chrome 页面验证。

可通过 `.env` 调整浏览器程序路径、是否无头运行、单次最大商品数、页面超时和登录窗口有效期。登录窗口始终可见；保存登录信息后，正式抓取可以按配置使用无头模式。原始 Cookies 和 Storage 不会返回后台页面或写入日志。

淘宝浏览器的权限初始化由 `taobao_browser.py` 负责：每次任务创建独立资料目录后，在 Chrome 启动前写入本机和局域网访问的默认屏蔽规则，避免出现“访问此设备上的其他应用和服务”的权限弹窗。规则覆盖搜索页、登录页和新标签页，并兼容旧版合并权限与新版拆分权限；其他权限沿用 Chrome 默认值，用户日常浏览器配置不受影响。配置写入失败由现有浏览器任务边界记录错误并清理临时目录。设置 `TAOBAO_PERMISSION_BROWSER_TEST=1` 后运行 `python -m pytest tests/integration/test_taobao_browser_permissions.py -q`，可使用真实 Chrome 和淘宝页面验证权限状态。

## KFC Session 与认证

- KFC `sessionId` 按手机号缓存 10 分钟。
- 同一账号的 ticket、deviceId 或手机号发生变化时，旧缓存立即失效。
- 后台管理员登录 Session 保存在 PostgreSQL，只保存 token SHA-256 hash，并带过期时间；服务重启和多 worker 不影响登录态共享。
- production/test 日志不打印完整 ticket、Cookie、sessionId、KEY_USER 或认证 localStorage；完整认证诊断仅允许 development 环境。

## 卡券分配与用券恢复

访问链接第一次分配卡券后，`access_links.coupon_code` 为固定绑定：

1. 锁定当前 `access_links` 并验证链接状态和临时操作锁。
2. 查询 `kfc_status='AVAILABLE'`、`local_status='wait'`、餐品匹配且创建时间最早的卡券。
3. 使用 `FOR UPDATE SKIP LOCKED` 锁定候选卡券。
4. 将固定 `coupon_code` 写入 `access_links`，卡券改为 `assigned`。
5. 写入 `coupon_assignment_logs`，后续优先使用日志中的 `coupon_id` 识别实体。
6. 后续失败、超时或重试都不得重新分配其他卡券。

用券执行另外记录在 `coupon_application_attempts`，状态包括 `PROCESSING`、`UPSTREAM_SUCCEEDED`、`COMPLETED`、`FAILED`、`UNKNOWN`。KFC 已成功但本地最终保存失败时，下次请求优先从原 cartId 恢复；结果不确定时也只能围绕原卡券继续核对或重试。

`internal_locked_at` 只是短期操作锁，默认超过 3 分钟自动视为旧锁并允许恢复。

## 卡券同步

- `coupons.last_seen_at` 记录本次完整同步是否再次看到卡券。
- 完整同步成功后，未再次出现的旧 `AVAILABLE + wait` 卡券标记为 `STALE`，不会继续参与分配。
- KFC 商品名本次无法匹配本地商品时，不会清空历史 `product_id`。
- 可通过 KFC 商品名称映射表人工维护 KFC 商品名到本地商品的对应关系。

## 可选餐品

餐品管理的“可选餐品”配置支持按分组设置“必选数量”，默认为 1；例如“2+1随心配”可设为餐品组必选 2 个、饮料及甜品组必选 1 个。每种餐品限选一次，核销页显示已选进度，选满后才能确认，后台会再次校验各组数量。已有数据库先执行 `python tools/migrate.py` 应用 `021_product_option_select_count.sql`，已有分组仍保持单选，需要多选的分组再从后台修改数量。相关回归验证：`python -m pytest tests/integration/test_product_option_selection_real_db.py`（使用本机真实 PostgreSQL，前端逻辑验证需要 Node.js）。

## 商品图片

商品图片上传时根据真实文件头识别 JPG、PNG、WEBP、GIF，不信任客户端 MIME。图片写入 `static/product-images/`，`products.image_url` 只保存 URL/path，不再保存 Base64 数据。

## 质量检查

提交前至少执行：

```bash
python -m compileall -q app tools tests
python -m ruff check app tools tests
python -m ruff format --check app tools tests
```

完整测试执行：

```bash
python -m pytest
```

涉及数据库的测试需要可用的真实 PostgreSQL，并以 `database.sql` 初始化测试库。GitHub Actions 会执行 PostgreSQL 16 初始化、Python 语法检查、Ruff 和完整 pytest。

仓库 `.githooks/pre-commit` 执行语法检查、Ruff lint 和 Ruff format check；Git 提交信息统一使用中文。
