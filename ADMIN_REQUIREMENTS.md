# 后台管理页面与需求规划

> 技术栈：Vue 3 + TypeScript 前端；Python 3.12 + FastAPI + psycopg 3 后端；PostgreSQL 数据库。
>
> 数据库初始化：项目根目录 `database.sql`。FastAPI 启动不自动执行 migration。

## 后台结构

```text
后台管理
├── 登录
├── 总览
├── KFC Session
├── 餐品管理
├── 卡券管理
├── 导入卡券
├── 导入批次
├── 核销管理
└── 访问链接管理
```

后台当前只有一个管理员账号，不增加多角色权限体系。管理员账号从 PostgreSQL `users` 表读取。

## API

管理接口统一位于 `/api/admin/*`，FastAPI Swagger 位于 `/api/docs`。前端继续通过 Vite `/api` 代理访问后端，接口路径不因 NestJS → FastAPI 迁移而改变。

主要接口：

- `POST /api/admin/auth/login`
- `POST /api/admin/auth/logout`
- `GET /api/admin/dashboard`
- `GET/POST/PATCH/DELETE /api/admin/products`
- `GET/PATCH/DELETE /api/admin/coupons`
- `POST /api/admin/coupons/import`
- `POST /api/admin/coupons/sync-kfc`
- `GET/PATCH/DELETE /api/admin/coupon-batches`
- `GET/POST/PATCH/DELETE /api/admin/access-links`
- `GET /api/admin/redemptions`
- `GET/POST/PATCH/DELETE /api/admin/kfc-sessions`
- `POST /api/admin/kfc-sessions/capture/start`
- `POST /api/admin/kfc-sessions/capture/:captureId/save`

## 数据规则

- 正式数据只写 PostgreSQL。
- 禁止 Mock、内存假数据、假 Repository 和模拟业务接口。
- 业务写操作使用数据库事务；卡券分配使用行锁和 `SKIP LOCKED`。
- 数据库不使用外键约束，删除保护由 Python 服务层执行。
- 旧 TypeORM migration 已删除，不再作为数据库结构来源。
