# KFC 专属链接点餐系统需求文档

> 文档状态：执行中

## 1. 项目概述

系统包含 Vue 3 顾客 H5、Vue 3 管理后台和 Python FastAPI API。管理员向顾客发送带 16 位唯一 ID 的专属链接；该 ID 对应餐品或餐品组，顾客端不存在付款步骤。

后端技术栈固定为 Python 3.12 + FastAPI + Pydantic + psycopg 3 + PostgreSQL；肯德基上游 HTTP 使用 httpx。除 Vue 前端外，不再使用 NestJS、TypeORM、Jest 或 TypeScript 后端代码。

顾客能力由 ID 状态决定：

- `UNUSED`：选择城市、搜索餐厅、查看链接绑定餐品并使用卡券。
- `PICKING_UP`：只查看真实订单排队号、取餐码和状态。
- `FAILED`：只显示失败状态。
- `COMPLETED`：只显示订单完结。

## 2. 顾客端

### 2.1 专属链接

- 每次进入页面、用券和查询状态前重新读取 PostgreSQL。
- ID 必须绑定一个餐品或餐品组。
- 不存在额外“授权”模型或 `use_scope` 访问授权判断；链接关联关系只表示当前链接对应的餐品。
- 并发关键流程必须使用 PostgreSQL 行锁。

### 2.2 城市和餐厅

- 中国城市列表保留在 Vue 静态数据中。
- 餐厅搜索由 FastAPI 内部业务服务调用肯德基 `searchByCityCodeAndKeyword`，`/api/public/*` 只返回本项目定义的门店 DTO，禁止返回 KFC 原始门店对象。
- 搜索结果展示门店名称、地址、营业时间、营业状态和肯德基返回的图片。

### 2.3 餐品

- 页面展示访问链接绑定的餐品描述和图片。
- 不因为所选餐厅菜单里是否存在该餐品而阻止页面列出链接绑定餐品。
- 真正调用肯德基购物车前，FastAPI 再读取实时菜单并解析肯德基内部 `linkId`。

### 2.4 卡券分配

首次分配时必须在同一个 PostgreSQL 事务中：

1. 对 `access_links` 当前记录 `FOR UPDATE`。
2. 按链接绑定餐品筛选 `kfc_status='AVAILABLE'`、`local_status='wait'` 的卡券。
3. 按卡券 `createdAt ASC, id ASC` 取最早一张，并使用 `FOR UPDATE SKIP LOCKED`。
4. 写 `access_links.coupon_code`。
5. 将 `coupons.local_status` 改为 `assigned`。
6. 写 `coupon_assignment_logs`，记录访问者 IP。

已分配链接重复请求时复用原卡券，不重新分配。

### 2.5 用券确认

- 用券前先锁定 `access_links`，避免重复点击并发执行。
- FastAPI 内部业务服务调用 KFC `cart/create`、`cart/useCouponCode` 和 `cart/confirm`；公网接口不得直接暴露这些 KFC 接口或原始参数。
- KFC 成功后保存 `coupon_cart_id`、`coupon_applied_at`。
- 本地失败但 KFC 已成功时必须记录高优先级错误日志，避免盲目重复调用。

### 2.6 Public API 与 KFC 上游边界

- `/api/public/*` 是严格公网边界，只能暴露本项目定义的业务接口和业务 DTO，禁止实现任意 KFC 接口代理、路径转发或参数透传。
- `app/api/routers/public/access.py`、`app/api/routers/public/checkout.py`、`app/business/public_service.py` 禁止直接导入或调用 KFC HTTP 客户端、KFC 接口常量、签名逻辑或认证逻辑。
- Public 请求体禁止接受 KFC 原始请求对象或原始门店 payload；只允许 `storeCode`、业务餐品 ID、数量、取餐方式等本项目明确声明的字段，额外字段必须拒绝。
- Public 响应禁止返回 KFC 原始响应、`rawResponse`、原始门店 payload、ticket、cookie、openId、deviceId、KBS key/secret/签名、sessionId、cartId、externalOrderId、parentLinkId、specLinkIds、condimentLinkIds 等 KFC 协议、认证或内部标识字段。
- KFC 上游返回值必须经过白名单 DTO 转换后才能进入 Public 响应；门店只允许返回 `storeCode`、名称、地址、城市、营业状态、营业时间、图片等页面必需字段；餐品和订单同样只返回页面实际使用的业务字段。
- KFC 上游错误原文不得透传给 Public 客户端；公网只返回本项目定义的稳定业务错误，具体上游错误保留在服务器日志。
- 确需访问 KFC 时，只允许内部业务服务执行固定业务流程，认证信息和 KFC 原始请求/响应始终留在服务端。

## 3. 管理后台

后台只有一个管理员账号，不设计多角色权限体系。

### 3.1 登录

- 用户名、bcrypt 密码哈希只从 PostgreSQL `users` 表读取。
- 登录失败只返回通用错误。
- 本地指定主机可以按 `.env` 的 `LOCAL_ADMIN_HOSTNAME` 免登录。

### 3.2 餐品

- 管理名称、描述、图片、肯德基 `parent_link_id` 和启用状态。
- 图片上传最大 2MB，支持 JPG/PNG/WEBP/GIF。

### 3.3 卡券

- 卡券保存自增 ID、卡券码、本地状态、KFC 状态、KFC 餐品名、平台、账号、餐品、批次、创建和同步时间。
- 卡券同步只写 PostgreSQL，不修改 `.kfc/fixtures/kfc-coupon-list-20260822.json`。

### 3.4 访问链接

- 支持分页、搜索、状态筛选、批量创建、修改关联餐品、删除。
- `access_links.id` 使用 BIGINT identity。
- 链接进入分配/下单流程后禁止修改餐品和删除。

### 3.5 核销管理

- 核销管理直接基于 `access_links + coupons + orders` 汇总，不再维护独立 `redemptions` 表。
- 支持分页和按餐品/状态筛选。

### 3.6 KFC Session

- KFC Session 保存在 `kfc_tickets`。
- FastAPI 可启动本机 Chrome，通过 CDP 抓取 cookies、localStorage、sessionStorage 和摘要字段。
- 相同 `userCode` 再次导入时更新原记录。

## 4. 数据与持久化

- 正式业务数据全部保存 PostgreSQL。
- 禁止 Mock、内存假 Repository、假接口和假业务数据。
- 数据库无外键约束，关联一致性由 Python 服务层和事务保证。
- 根目录 `database.sql` 是全新数据库结构基线。
- 不维护 TypeORM migration；`apps/api/src/database/migrations` 必须不存在。
- FastAPI 启动不得自动 drop、truncate、重建或迁移业务表。

## 5. 技术与运行

- API：Python 3.12 + FastAPI + Uvicorn。
- 数据库：PostgreSQL + psycopg 3。
- 参数校验：Pydantic。
- HTTP：httpx。
- 后端测试：pytest，必须连接真实 PostgreSQL。
- Web：Vue 3 + TypeScript + Vite。
- 根目录不再使用 `npm run dev`；Windows 使用 `start-local.cmd`，其内部调用 Python 启动器。

## 6. 验收标准

- [ ] `apps/api` 不存在 NestJS/TypeORM/TypeScript 后端源码。
- [ ] `apps/api/src/database/migrations` 不存在。
- [ ] `/api/admin/*`、`/api/public/*` 主要前端契约保持兼容。
- [ ] `/api/public/*` 不接受或返回 KFC 原始请求/响应、认证字段或原始门店 payload。
- [ ] Public 路由与 `public_service.py` 不直接依赖 KFC HTTP 客户端。
- [ ] Swagger 位于 `/api/docs`。
- [ ] 卡券并发分配只会把同一张卡券分给一个链接。
- [ ] 后端测试使用真实 PostgreSQL，不使用 Mock。
- [ ] Vue 类型检查、测试、构建通过。
