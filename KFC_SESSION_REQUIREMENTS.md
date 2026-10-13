# KFC Session 与 KFC Ticket 管理需求

更新时间：2026-08-22

## 1. 目标

后台需要直接完成肯德基登录态获取和保存，不再要求管理员手工从浏览器开发者工具复制完整登录信息。

本需求覆盖以下内容：

- KFC Ticket 管理页面内直接打开肯德基 H5 登录页面。
- 用户完成手机号/验证码登录后，由系统抓取浏览器完整登录状态。
- 完整登录状态写入 PostgreSQL `kfc_session` 表。
- 从完整登录状态中提取真实下单需要的 `ticket`、`user_code`、`phone`、`open_id`、`cookie_header`。
- 保留手工新增 Ticket 能力，但新增表单必须使用弹窗，不允许常驻铺在列表页面。
- 后台各页面不再统一显示“导入卡券”按钮；导入卡券只保留左侧独立菜单入口。

## 2. KFC Ticket 管理页面

### 2.1 页面入口

左侧导航保留 `KFC Ticket` 页面。

页面顶部只提供以下主要操作：

- [x] `登录 KFC 并获取登录信息`
- [x] `新增 Ticket`

不得在该页面或其他普通后台页面右上角重复放置“导入卡券”按钮。

### 2.2 Ticket 列表

列表至少展示：

| 字段 | 说明 |
| --- | --- |
| 名称 | 后台识别该 KFC 账号的名称 |
| Ticket / userCode | Ticket 只显示脱敏值，userCode 可显示 |
| 状态 | 启用 / 停用 |
| 登录状态 | 完整登录态 / 仅 Cookie / 手工 Ticket |
| 备注 | 管理员备注 |
| 操作 | 编辑、启用/停用、删除 |

完整 Ticket 不得通过列表接口返回明文。

## 3. 登录 KFC 并抓取登录态

### 3.1 打开登录页

点击 `登录 KFC 并获取登录信息` 后打开登录弹窗。

弹窗第一步提供 `打开 KFC 登录页面` 按钮。

点击后：

- [x] 后端查找本机 Chrome/Chromium。
- [x] 后端启动独立 Chrome 用户目录。
- [x] Chrome 开启本机 DevTools 调试端口。
- [x] 自动打开 `https://order.kfc.com.cn/preorder-taro/home`。
- [x] 后台返回一个临时 `captureId` 标识本次登录捕获任务。

### 3.2 登录完成后保存

管理员在弹出的 Chrome 中完成手机号和验证码登录。

确认登录成功后，在后台弹窗填写：

- [x] 账号名称，必填。
- [x] 备注，可选。

点击 `登录完成，保存状态` 后：

- [x] 后端通过 Chrome DevTools Protocol 读取当前 KFC 页面。
- [x] 抓取完整 cookies。
- [x] 抓取 `localStorage`。
- [x] 抓取 `sessionStorage`。
- [x] 抓取页面中的 `stateJson`。
- [x] 扫描并提取 `ticket/token`、`userCode/user_code`、`phone`、`openid/openId`、`payUserId` 等候选字段。
- [x] 生成 KFC Cookie Header。
- [x] 将完整浏览器状态写入 PostgreSQL。
- [x] 保存成功后关闭本次捕获 Chrome。

若没有捕获到 `ticket` 或 `userCode`，必须拒绝保存并提示用户确认 KFC 页面已经登录成功。

### 3.3 取消登录捕获

用户关闭登录弹窗或点击取消时：

- [x] 取消本次 `captureId`。
- [x] 尝试关闭对应 Chrome。
- [x] 已被用户手工关闭 Chrome 时不额外报错。

## 4. 手工新增 Ticket

手工新增功能保留，但必须通过弹窗完成，不允许把大段表单直接铺在 KFC Ticket 页面上。

弹窗字段：

- [x] 名称，必填。
- [x] `userCode`，必填。
- [x] 手机号，可选。
- [x] `openId`，可选。
- [x] Ticket，必填。
- [x] Cookie Header，可选。
- [x] 备注，可选。

手工 Ticket 没有完整 `auth_state` 时，列表登录状态显示为“仅 Cookie”或“手工 Ticket”。

## 5. PostgreSQL 持久化

### 5.1 表名

原 `kfc_tickets` 已迁移为：

```sql
kfc_session
```

原 `cookie` 字段迁移为：

```sql
cookie_header
```

迁移必须原地保留已有数据，不重新生成历史记录。

### 5.2 核心字段

`kfc_session` 至少保存：

| 字段 | 用途 |
| --- | --- |
| `id` | 数据库主键 |
| `name` | 后台名称 |
| `ticket` | KFC 登录 Ticket |
| `user_code` | KFC 用户标识 |
| `phone` | 下单提交使用手机号 |
| `open_id` | 风控参数，可为空 |
| `cookie_header` | KFC/YumChina Cookie Header |
| `auth_state` | 完整浏览器登录状态 JSON |
| `captured_at` | 登录态抓取时间 |
| `enabled` | 是否启用 |
| `last_synced_at` | 最近同步卡券时间 |
| `remark` | 备注 |
| `createdAt` / `updatedAt` | 记录时间 |

### 5.3 `auth_state` 内容

完整登录态至少保存以下结构：

- `createdAt`
- `baseUrl`
- 当前页面地址和标题
- `documentCookie`
- `cookies[]`
- `localStorage`
- `sessionStorage`
- `stateJson`
- 字段扫描 `candidates`
- 提取结果 `summary`

`summary` 至少包含：

- `ticket`
- `userCode`
- `phone`
- `openid`
- `payUserId`（若页面存在）

## 6. 下单运行时规则

长期持久化的是登录身份和浏览器登录状态，不持久化每笔订单运行时生成的数据。

以下内容每次请求/下单重新生成或由 KFC 接口刷新：

- [x] `sessionId`
- [x] `currentTicket`
- [x] `deviceId`
- [x] 购物车 ID
- [x] 订单 ID

服务启动或登录态保存后，从最近更新且 `enabled=true` 的 `kfc_session` 记录加载：

- `KFC_TICKET`
- `KFC_USER_CODE`
- `KFC_PHONE`
- `KFC_OPEN_ID`
- `KFC_COOKIE_HEADER`

这些运行时变量由数据库记录派生，不再要求人工长期维护对应环境变量。

## 7. 后端接口

完整 Session 接口：

- `GET /api/admin/kfc-sessions`
- `POST /api/admin/kfc-sessions`
- `PATCH /api/admin/kfc-sessions/:id`
- `DELETE /api/admin/kfc-sessions/:id`
- `POST /api/admin/kfc-sessions/import-auth-state`
- `POST /api/admin/kfc-sessions/capture/start`
- `POST /api/admin/kfc-sessions/capture/:captureId/save`
- `DELETE /api/admin/kfc-sessions/capture/:captureId`

现有后台为了兼容仍保留以下 Ticket CRUD 路由：

- `GET /api/admin/kfc-tickets`
- `POST /api/admin/kfc-tickets`
- `PATCH /api/admin/kfc-tickets/:id`
- `DELETE /api/admin/kfc-tickets/:id`

数据库物理表以 `kfc_session` 为准。

## 8. 后台布局要求

- [x] KFC Ticket 页面新增操作使用弹窗。
- [x] KFC 登录态获取使用独立弹窗。
- [x] 后台主 Header 不再根据页面统一显示“导入卡券”。
- [x] 导入卡券仅保留左侧 `导入卡券` 菜单入口。
- [x] 餐品管理新增表单保持紧凑后台样式，不退化为浏览器默认文件输入布局。
- [x] 餐品图片上传控件、预览、按钮保持统一后台视觉样式。

## 9. 运行条件

浏览器登录抓取由 NestJS API 进程启动 Chrome，因此：

- API 与需要弹出 Chrome 的桌面必须运行在同一台机器上，才能看到实际弹出的浏览器窗口。
- 默认自动寻找 Chrome/Chromium。
- 找不到浏览器时可通过 `KFC_CHROME_PATH` 指定 Chrome 可执行文件。
- `KFC_CHROME_PROFILE_DIR` 可指定登录捕获使用的浏览器用户数据目录根路径。
- Node.js 运行环境需要支持 WebSocket；项目 CI 和当前实现使用 Node.js 22。

## 10. 验收标准

- [x] 从 KFC Ticket 页面点击按钮可以启动 Chrome 并进入 KFC H5。
- [x] 完成手机号/验证码登录后可以一键保存完整登录态。
- [x] 数据库 `kfc_session.auth_state` 保存 cookies、Storage 和摘要信息。
- [x] 同时提取并更新真实下单使用的 Ticket、userCode、phone、openId、Cookie Header。
- [x] 已有相同 `userCode` 的 Session 更新原记录，不重复制造同账号记录。
- [x] 新增 Ticket 表单只在弹窗内出现。
- [x] 后台普通页面不再重复显示“导入卡券”快捷按钮。
- [x] 相关 TypeScript 类型检查和单元测试通过。
