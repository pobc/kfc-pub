# 项目优化计划

> 文档状态：已执行并验证（2026-08-29）  
> 基线：GitHub `main`。  
> 验证结果：GitHub Actions `Unit Tests` run `33260894362` 全部通过；Python 语法检查、Ruff lint、Ruff format 均通过，真实 PostgreSQL 测试结果为 **90 passed, 2 skipped**。  
> 命名说明：本计划早期示例使用 `*_repository.py`；最终实现遵循 `AGENTS.md` 的项目硬约束，所有直接执行 SQL 的仓储文件统一使用 `*_db.py`，数据库连接文件使用 `db_connection.py`。

## 1. 优化原则

- [x] 所有优化直接基于 `main` 实施，不创建功能分支。
- [x] 不为了“架构好看”增加没有实际职责的中间层、抽象接口或通用框架。
- [x] 数据库并发控制继续以 PostgreSQL 事务、行锁、`FOR UPDATE SKIP LOCKED` 为主。
- [x] KFC 外部接口失败与本地数据库失败区分处理，不把两类错误混成一个状态。
- [x] 公网 API 不暴露 KFC 原始请求、原始响应和认证信息。
- [x] 测试禁止 Mock；涉及数据库的业务测试连接真实 PostgreSQL。

## 2. P0：业务一致性与恢复

### 2.1 用券临时锁增加超时恢复

- [x] `internal_locked_at` 只作为临时操作锁，不作为永久状态。
- [x] 判断锁是否有效时增加超时时间，默认 3 分钟。
- [x] 超过有效期的旧锁允许自动恢复并重新进入用券流程。
- [x] 正常失败时只释放临时锁，不改变已经建立的 coupon 分配关系。
- [x] 增加真实 PostgreSQL 测试，验证进程异常遗留的旧锁可以恢复。

实施结果：用券预约和底层分配逻辑都会验证临时锁；旧锁允许恢复，当前有效锁拒绝重复执行。

### 2.2 Coupon 只分配一次；恢复用券流程时绝不重新分配

固定业务原则：一个 `access_link` 一旦分配到 coupon，后续无论 KFC 调用成功、失败、超时、本地数据库写失败或用户再次发起请求，都继续使用第一次分配的同一张 coupon。

- [x] `access_links.coupon_code` 第一次成功写入后视为固定绑定关系。
- [x] 后续任何失败都不得清空 `access_links.coupon_code`。
- [x] 后续任何失败都不得把当前 access link 改为等待重新分配其他 coupon。
- [x] 已分配 coupon 不会因为 KFC 调用失败重新变成可供其他 access link 分配的 `wait` 状态。
- [x] 再次执行 KFC 用券业务时读取已绑定的原 `coupon_code`，继续使用同一张券。
- [x] 用券失败处理只释放本次操作临时锁，不释放 coupon 绑定。
- [x] 将 coupon 分配状态和 KFC 用券执行状态分开管理。
- [x] coupon 分配关系只建立一次；KFC 调用围绕同一 coupon 重试或恢复。
- [x] KFC 已成功、本地持久化失败时保留可恢复信息。
- [x] 增加 `coupon_application_attempts` 用券执行记录。
- [x] 执行状态区分 `PROCESSING`、`UPSTREAM_SUCCEEDED`、`COMPLETED`、`FAILED`、`UNKNOWN`。
- [x] KFC 已确认成功但本地最终状态未保存时，下次请求优先通过已记录 cartId 恢复本地状态。
- [x] KFC 返回原 coupon 已使用等业务失败时不会自动换券；该 access link 始终固定在原 coupon 上，后续只允许对原券恢复/核对/重试。
- [x] 真实数据库测试：同一个 access link 并发/重复调用分配逻辑 100 次，只产生一个 coupon 绑定和一条分配记录。
- [x] 真实数据库测试：第一次执行失败后再次执行，仍读取相同 coupon_code。
- [x] 真实数据库测试：`UNKNOWN/UPSTREAM_SUCCEEDED` 恢复时不能产生第二张 coupon 分配记录。

### 2.3 KFC Session 缓存与认证上下文失效

- [x] KFC 账号 ticket、deviceId 或手机号更新时，使相关手机号已有 `kfc_sessions` 立即失效。
- [x] 后续获取 sessionId 不只依赖“手机号相同且未过期”；认证信息变化时主动清理缓存。
- [x] 采用“认证信息变化即清除 session”的简单实现，没有增加无必要 fingerprint 表结构。
- [x] 增加真实数据库测试，验证同手机号认证信息更新后不会继续返回旧 sessionId。

### 2.4 KFC 成功与本地失败的可恢复状态

- [x] 不使用数据库事务包住整个 KFC HTTP 调用。
- [x] 执行 KFC 关键步骤前建立本地执行记录，并固定 access link 与原 coupon。
- [x] 获取 cartId 或明确 KFC 成功结果后尽快写入执行记录。
- [x] 最终更新 `access_links` 失败时，执行记录支持后续恢复。
- [x] 恢复过程中不重新执行 coupon 分配。
- [x] 无法确认 KFC 最终结果时标记 `UNKNOWN`，而不是允许换券重试。

## 3. P0：认证与敏感信息

### 3.1 生产环境禁止打印完整认证信息

- [x] development 环境允许完整认证诊断日志。
- [x] production/test 环境禁止打印完整 ticket、token、Cookie、sessionId、KEY_USER 和认证 localStorage。
- [x] 非开发环境日志只保留必要路径、状态、code/msg、耗时和脱敏标识。
- [x] 增加统一日志脱敏函数，KFC HTTP/client 不再各自随意输出完整敏感数据。

### 3.2 后台登录 Session 持久化并增加 TTL

- [x] 后台登录 Session 改为 PostgreSQL 持久化。
- [x] 数据库只保存 token SHA-256 hash，不保存浏览器拿到的原始 token。
- [x] Session 增加 `expires_at`。
- [x] logout 后立即删除/失效对应 token hash。
- [x] 支持多 worker，不依赖 Python 单进程内存集合。

## 4. P0：数据库与事务安全

### 4.1 卡券人工导入改用 `ON CONFLICT DO NOTHING`

- [x] 删除“先 SELECT 判断是否存在，再 INSERT”的并发窗口。
- [x] 使用 `INSERT ... ON CONFLICT (...) DO NOTHING RETURNING *`。
- [x] `RETURNING` 没有记录时直接记为重复导入，不让事务进入 aborted 状态。
- [x] 增加真实 PostgreSQL 并发导入测试；20 路并发验证只有一条成功插入，其余正常识别为重复。

### 4.2 Coupon 实体关联不要只依赖 coupon_code

- [x] 业务关联优先使用 `coupon_assignment_logs.coupon_id`。
- [x] `access_links.coupon_code` 保留为固定券码和展示字段，不作为跨表唯一实体 ID。
- [x] 必须按 coupon_code 查询的兼容路径带足够作用域，并对歧义结果拒绝继续处理。
- [x] 后台核销查询通过最新 `coupon_assignment_logs.coupon_id` 关联卡券实体。

### 4.3 无外键设计补充完整性检查

- [x] 增加 access_link → product 孤儿记录检查。
- [x] 增加 coupon → batch/product/account/platform 孤儿记录检查。
- [x] 增加 coupon_assignment_log → access_link/coupon 孤儿记录检查。
- [x] 增加 order → access_link 孤儿记录检查。
- [x] 完整性检查加入真实 PostgreSQL 测试。

实现位置：`app/database/integrity_db.py`、`tools/check_integrity.py`、`tests/test_referential_integrity_real_db.py`。

### 4.4 数据库结构修改从应用启动流程移出

- [x] 应用启动只负责连接数据库并检查必要表/字段，不执行 destructive schema migration。
- [x] 建立 `migrations/` SQL migration 目录和 `schema_migrations` 版本记录表。
- [x] 数据库结构变更使用独立 migration 文件。
- [x] `database.sql` 保持作为全新数据库初始化脚本。

已有数据库升级统一执行：

```bash
python tools/migrate.py
```

## 5. P1：KFC Client 稳定性

### 5.1 拆分过大的 `h5_client.py`

当前实现已按真实职责拆分：

```text
app/business/kfc/
    contracts.py
    h5_config.py
    http_client.py
    store_client.py
    checkout_client.py
    h5_client.py
    coupon_sync.py
    init_session.py
    query_order_detail.py
    session_capture.py
```

- [x] `http_client.py` 统一持有 requests.Session、timeout、JSON decode、HTTP 错误处理和安全日志。
- [x] `store_client.py` 负责餐厅相关 KFC 调用和门店字段整理。
- [x] `checkout_client.py` 负责购物车、用券、确认和订单状态所需的固定 KFC 调用。
- [x] `h5_client.py` 仅作为现有调用方的组合/facade 入口。
- [x] 没有增加无实际用途的 interface/abstract class。

### 5.2 KFC 成功码采用严格判断

- [x] 缺少 `code` 时不默认按成功处理。
- [x] 只有接口明确返回成功码时才视为成功。
- [x] 非数字错误码不会因为转换失败而被忽略。
- [x] H5 API 与 coupon API 的 code/errCode 规则分别处理，不做模糊猜测。

### 5.3 所有 HTTP 请求统一复用 `requests.Session`

- [x] `useCouponCode` 不再直接使用模块级 `requests.post()`。
- [x] 同一 client 的请求统一复用持久 `requests.Session`。
- [x] timeout、连接复用和 Cookie 行为由统一 HTTP client 管理。

## 6. P1：卡券同步数据质量

### 6.1 处理同步后未再次出现的旧 AVAILABLE 卡券

- [x] coupons 增加 `last_seen_at`。
- [x] 只有整次 KFC 卡券同步调用完整成功后才执行未出现数据处理。
- [x] 未再次出现的旧 `AVAILABLE + wait` 卡券标记为 `STALE`。
- [x] 分配 SQL 只选择明确 `kfc_status='AVAILABLE'` 且 `local_status='wait'` 的卡券。

### 6.2 商品名称匹配失败时不得清空历史 product_id

- [x] 本次同步匹配成功时更新 product_id。
- [x] 本次同步匹配失败时通过 `COALESCE` 保留已有 product_id。
- [x] 建立 KFC 商品名称到本地 product_id 的人工确认映射表/仓储，减少完全依赖字符串精确相等。

## 7. P1：Repository 与业务边界

### 7.1 `assign_coupon()` 自身验证业务前置条件

- [x] repository 内再次验证 access link 存在。
- [x] 验证 status 允许执行。
- [x] 验证本次用券临时锁有效。
- [x] 已经有 coupon_code 时只返回原有 coupon，不重新选择新券。
- [x] 调用方漏掉前置步骤时，底层仍保护固定分配业务不变量。

### 7.2 拆分过大的 AccessLink Repository

最终按 `AGENTS.md` 的数据库命名规则拆分为：

```text
access/access_link_db.py
access/access_link_redemption_db.py
access/coupon_application_db.py
coupon/application_attempt_db.py
```

- [x] AccessLink CRUD 与用券执行状态分开。
- [x] 后台核销查询与核心 AccessLink CRUD 分开。
- [x] 数据库事务和 SQL 保留在 database/repository 层，没有移动回 business。

### 7.3 清理 `repositories/__init__.py` 的动态兼容别名

- [x] 代码改为显式导入真实仓储模块路径。
- [x] 删除动态写 `sys.modules` 的旧路径兼容逻辑。
- [x] IDE、静态分析和测试看到的模块结构与真实目录一致。

### 7.4 减少纯转发 Service

- [x] Service 继续承担业务校验、流程编排、多个 repository 组合和公网 DTO 边界。
- [x] 删除确认无调用的卡券分配纯转发方法；不新增仅为了目录形式存在的空壳 service。
- [x] checkout、session、auth 等复杂流程保留明确 service 边界。

说明：现有少量 CRUD facade 被保留，是为了维持 `API -> business -> database` 依赖方向，不将 API 层直接耦合到 SQL 仓储；这类边界方法不再继续扩张为无意义中间层。

## 8. P1：测试与提交质量

### 8.1 修复已经落后于代码的架构测试

- [x] 架构测试重点限制错误依赖方向，不再写死允许存在的全部文件名。
- [x] 保留 business 不执行 SQL。
- [x] 保留 database 不依赖 business/KFC HTTP。
- [x] 保留 public API 不直接导入 KFC client。
- [x] 新模块只要符合职责边界，不会因为文件名不在旧固定集合而失败。
- [x] 增加数据库文件 `*_db.py` 命名约束检查和动态兼容别名检查。

### 8.2 修复 pre-commit

- [x] pre-commit 不再引用已删除的 `scripts/check.py`。
- [x] pre-commit 执行 Python syntax/compile 检查。
- [x] pre-commit 执行 Ruff lint 和 format check；完整真实 PostgreSQL pytest 留给 CI，避免本地每次提交都强制启动数据库测试。
- [x] CI 继续执行完整真实 PostgreSQL 测试。

### 8.3 增加 Ruff

- [x] 增加 `ruff check`。
- [x] 增加 `ruff format --check`。
- [x] CI 增加 Ruff 检查。
- [x] 暂不强行引入复杂类型检查，等 KFC DTO 稳定后再评估。

## 9. P1：运行与后台管理

### 9.1 `main.py` 区分开发和生产运行模式

- [x] host/port 使用统一 settings。
- [x] 只有 development 开启 `reload=True`。
- [x] 关闭 Uvicorn 自带 access log，由自定义 access middleware 统一打印请求耗时，避免重复日志。

### 9.2 公网访问 IP 统一由服务端解析

- [x] 公网业务日志/分配记录统一使用 `client_ip_from_request()` 的服务端解析结果。
- [x] 不直接把 `request.client.host` 当最终访客 IP。
- [x] 不信任前端传来的 visitorIp 作为审计 IP。
- [x] 当前公网 apply-coupon API 根本不接收 `visitorIp` 字段，因此无需额外持久化 `reported_ip`；数据库保存的是服务端观测/解析得到的 IP。

### 9.3 Capture 浏览器任务增加生命周期管理

- [x] CaptureTask 保存 Chrome pid 和过期时间。
- [x] 超时任务自动清理。
- [x] capture 关闭后清理浏览器 profile 目录。
- [x] Chrome 启动失败时清理已创建的 profile 和进程。
- [x] 应用关闭时统一清理任务，防止无限创建残留 Chrome 实例。

### 9.4 商品图片从数据库 Base64 迁出

- [x] 图片保存到本地 `static/product-images/` 静态文件目录。
- [x] products 表只保存图片 URL/path。
- [x] 上传时根据真实文件头校验 JPG、PNG、WEBP、GIF，不只信任 MIME header。
- [x] 商品列表接口不再携带数据库 Base64 大字段。

## 10. P2：文档与历史代码清理

### 10.1 清理失效文档

- [x] README 中技术栈、启动入口、目录结构、migration、测试和运行方式与当前代码一致。
- [x] 删除已经失效的 `IMPLEMENTATION_STATUS.md` 历史状态文档。
- [x] README 明确文档职责：README 负责运行方式，PRD 负责业务要求，AGENTS 负责开发约束，本文件负责优化计划和执行状态。
- [x] `AGENTS.md` 增加“Git 提交信息必须使用中文”的项目约束。

### 10.2 清理已经停用的订单死链路

- [x] 重新搜索并确认 `submit_order()`、订单状态、status_history 的真实调用关系。
- [x] 删除确认无调用的 `submit_order()` 死方法。
- [x] 订单状态查询和 status_history 经确认仍有现行业务依赖，明确保留，不做误删。
- [x] 删除前后均通过代码搜索、架构检查和真实 PostgreSQL 完整测试确认。

## 11. 执行顺序完成情况

### 第一批：先解决会造成错误业务结果的问题

- [x] 2.1 用券临时锁超时恢复。
- [x] 2.2 固定 coupon，只恢复用券流程，不重新分配。
- [x] 2.3 KFC Session 认证上下文失效。
- [x] 2.4 KFC 成功、本地失败可恢复状态。
- [x] 3.1 生产敏感日志隔离。
- [x] 3.2 后台 Session 持久化和 TTL。
- [x] 4.1 人工导入并发事务修复。

### 第二批：处理数据质量和测试基线

- [x] 4.2 Coupon 关联改为可靠 ID。
- [x] 4.3 无外键完整性检查。
- [x] 4.4 数据库 migration 从启动流程拆出。
- [x] 6.1 stale coupon 处理。
- [x] 6.2 商品映射保护。
- [x] 8.1 修复架构测试。
- [x] 8.2 修复 pre-commit。

### 第三批：结构和长期维护

- [x] 5.1 拆分 KFC H5 client。
- [x] 7.2 拆分 AccessLink repository/database 职责。
- [x] 7.3 清理 repository 兼容 import。
- [x] 7.4 清理无意义 service 转发。
- [x] 8.3 增加 Ruff。
- [x] 9.x 运行、IP、Capture、图片优化。
- [x] 10.x 文档和死代码清理。

## 12. 最终验证

当前 `main` 已完成完整 CI 验证：

```text
Python syntax check         PASS
Ruff check                  PASS
Ruff format --check         PASS
PostgreSQL 16 initialization PASS
pytest                      90 passed, 2 skipped
```

两个 skipped 为原有需要特定外部条件的测试，不属于本轮优化失败项。所有新增数据库业务测试均连接真实 PostgreSQL，没有使用 Mock。
