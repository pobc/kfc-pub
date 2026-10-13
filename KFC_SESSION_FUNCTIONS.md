# KFC Session 登录态功能说明

更新时间：2026-08-22

对应需求：`KFC_SESSION_REQUIREMENTS.md`

## 1. 功能概览

当前项目已经把参考项目中的 KFC 浏览器登录态保存思路移植到 NestJS 后台，并接入 KFC Ticket 管理页面。

管理员不需要先在终端运行脚本再手工导入状态。正常操作流程为：

```text
后台 KFC Ticket 页面
  ↓
点击“登录 KFC 并获取登录信息”
  ↓
点击“打开 KFC 登录页面”
  ↓
NestJS 在本机启动独立 Chrome
  ↓
Chrome 打开 KFC H5
  ↓
管理员完成手机号/验证码登录
  ↓
回到后台填写账号名称
  ↓
点击“登录完成，保存状态”
  ↓
NestJS 通过 CDP 抓取 Cookie + Storage + State
  ↓
提取 ticket / userCode / phone / openId
  ↓
写入 PostgreSQL kfc_session
  ↓
刷新运行时 KFC 下单登录信息
```

## 2. 前端实现

主要组件：

```text
apps/web/src/components/KfcTicketPanel.vue
```

后台工作区：

```text
apps/web/src/views/admin/AdminWorkspace.vue
```

### 2.1 KFC Ticket 页面

页面顶部有两个操作按钮：

- `登录 KFC 并获取登录信息`
- `＋ 新增 Ticket`

页面不再直接铺新增 Ticket 表单。

### 2.2 手工新增 Ticket 弹窗

点击 `新增 Ticket` 后通过 `Teleport` 打开后台弹窗。

表单内容包括名称、userCode、手机号、openId、Ticket、Cookie Header 和备注。

保存仍使用兼容接口：

```http
POST /api/admin/kfc-tickets
```

成功后关闭弹窗、刷新列表，并显示保存成功提示。

### 2.3 登录态捕获弹窗

捕获弹窗分两步。

第一步：

```text
打开 KFC 登录页面
```

前端调用：

```http
POST /api/admin/kfc-sessions/capture/start
```

返回：

```json
{
  "captureId": "临时捕获任务ID",
  "loginUrl": "KFC H5 地址",
  "startedAt": "启动时间"
}
```

第二步：

用户在 Chrome 登录完成后填写账号名称和可选备注，再点击：

```text
登录完成，保存状态
```

前端调用：

```http
POST /api/admin/kfc-sessions/capture/:captureId/save
```

保存成功后弹窗关闭并刷新 KFC Ticket 列表。

取消时调用：

```http
DELETE /api/admin/kfc-sessions/capture/:captureId
```

### 2.4 列表登录状态

前端根据服务端返回值显示：

- `hasAuthState=true`：完整登录态，并显示 `capturedAt`。
- 没有完整状态但 `hasCookie=true`：仅 Cookie。
- 两者都没有：手工 Ticket。

Ticket 本身只显示脱敏值。

## 3. 后端浏览器捕获

核心服务：

```text
apps/api/src/admin/kfc-session-capture.service.ts
```

控制器：

```text
apps/api/src/admin/kfc-session.controller.ts
```

### 3.1 启动 Chrome

`KfcSessionCaptureService.start()` 执行：

1. 生成随机 `captureId`。
2. 寻找可用 Chrome/Chromium。
3. 分配本机空闲 DevTools 端口。
4. 为本次任务建立独立浏览器 profile 目录。
5. 使用以下关键参数启动 Chrome：
   - `--remote-debugging-port`
   - `--remote-allow-origins=*`
   - `--user-data-dir`
   - `--new-window`
6. 打开 KFC H5 首页。
7. 等待 Chrome DevTools 接口可用。
8. 将捕获任务保存在当前 API 进程内存中。

默认页面：

```text
https://order.kfc.com.cn/preorder-taro/home
```

### 3.2 Chrome 路径

代码会根据操作系统寻找：

Windows：

- Program Files 下的 Google Chrome。
- Program Files (x86) 下的 Google Chrome。
- LocalAppData 下的 Google Chrome。

macOS：

- Google Chrome
- Chromium

Linux：

- `/usr/bin/google-chrome`
- `/usr/bin/google-chrome-stable`
- `/usr/bin/chromium`
- `/usr/bin/chromium-browser`

找不到时可以配置：

```env
KFC_CHROME_PATH=
```

浏览器 Profile 根目录可以配置：

```env
KFC_CHROME_PROFILE_DIR=
```

## 4. 登录态抓取内容

后端通过 Chrome DevTools Protocol 连接实际 KFC 页面。

### 4.1 页面 Storage

通过 `Runtime.evaluate` 在 KFC 页面执行只读脚本，获取：

- 当前页面 URL。
- 页面标题。
- 页面文本预览。
- `document.cookie`。
- 完整 `localStorage`。
- 完整 `sessionStorage`。
- Storage 中的 `__STATE` JSON（存在时）。

### 4.2 Cookie

优先调用：

```text
Network.getAllCookies
```

失败时回退：

```text
Storage.getCookies
```

因此可以得到 HttpOnly 等普通 `document.cookie` 无法读取的浏览器 Cookie。

### 4.3 登录字段扫描

系统会递归扫描 cookies、localStorage、sessionStorage、stateJson 中的 JSON/字符串内容。

候选字段包括：

- `ticket`
- `token`
- `userCode`
- `user_code`
- `payUserId`
- `phone`
- `openid`
- `openId`

扫描结果保存到 `auth_state.candidates`。

最终摘要保存到 `auth_state.summary`：

```json
{
  "ticket": "...",
  "userCode": "...",
  "payUserId": "...",
  "phone": "...",
  "openid": "..."
}
```

## 5. 数据库保存

实体目前仍沿用代码类名 `KfcTicketEntity` 以减少代码层兼容改动，但物理表是：

```text
kfc_session
```

### 5.1 登录态字段

关键字段：

```text
ticket
user_code
phone
open_id
cookie_header
auth_state
captured_at
```

其中：

- `auth_state` 保存完整浏览器状态 JSON。
- `captured_at` 保存本次浏览器状态实际抓取时间。
- `cookie_header` 从 KFC/YumChina 域 Cookie 组合生成。

### 5.2 同账号更新

保存完整登录态时使用 `userCode` 查找已有记录。

若已存在相同 `userCode`：

- 更新原 Session。
- 更新 Ticket。
- 更新手机号、openId、Cookie Header。
- 替换完整 `auth_state`。
- 更新 `captured_at`。
- 自动设为启用。

若不存在，则创建新 Session。

## 6. Session 导入服务

主要逻辑：

```text
apps/api/src/admin/kfc-ticket.service.ts
```

`importAuthState()` 负责：

1. 从完整状态中提取摘要。
2. 校验至少存在 `ticket` 和 `userCode`。
3. 组合 Cookie Header。
4. 按 userCode 查找或创建 `kfc_session`。
5. 保存完整 `auth_state`。
6. 保存 `captured_at`。
7. 调用运行时登录态刷新服务。

没有 `ticket` 或 `userCode` 时直接返回错误，不保存无效 Session。

## 7. 运行时下单登录信息

服务：

```text
apps/api/src/kfc/kfc-auth-runtime.service.ts
```

运行时从最近更新且启用的 `kfc_session` 加载：

```text
KFC_TICKET
KFC_USER_CODE
KFC_PHONE
KFC_OPEN_ID
KFC_COOKIE_HEADER
```

这里的环境变量只作为当前 Node.js 进程内部的兼容传递层，源数据已经是 PostgreSQL，不要求管理员在 `.env` 长期填写登录值。

当 KFC Session 新增、修改、删除或重新捕获后，会重新加载当前运行时登录信息。

## 8. 哪些数据不写入 Session 长期保存

以下属于每次订单/API 调用的临时数据：

- `sessionId`
- `currentTicket`
- `deviceId`
- cartId
- orderId

这些数据继续由真实 KFC 请求链路实时创建或刷新，不能把历史值当成下一笔订单的固定登录凭证。

## 9. API 说明

### 9.1 KFC Session

```http
GET    /api/admin/kfc-sessions
POST   /api/admin/kfc-sessions
PATCH  /api/admin/kfc-sessions/:id
DELETE /api/admin/kfc-sessions/:id
```

### 9.2 完整状态导入

```http
POST /api/admin/kfc-sessions/import-auth-state
```

用于命令行捕获工具或其他受信任的完整浏览器状态导入。

### 9.3 页面内浏览器捕获

开始：

```http
POST /api/admin/kfc-sessions/capture/start
```

保存：

```http
POST /api/admin/kfc-sessions/capture/:captureId/save
```

取消：

```http
DELETE /api/admin/kfc-sessions/capture/:captureId
```

### 9.4 兼容 Ticket 路由

当前后台原有 CRUD 仍可以走：

```http
GET    /api/admin/kfc-tickets
POST   /api/admin/kfc-tickets
PATCH  /api/admin/kfc-tickets/:id
DELETE /api/admin/kfc-tickets/:id
```

这些接口和 `/kfc-sessions` 操作的是同一张 `kfc_session` 表。

## 10. 命令行捕获工具

页面内功能之外仍保留：

```text
apps/api/tools/kfc-login-capture.ts
```

该工具可以独立启动 Chrome、等待用户登录、抓取完整状态，并通过：

```http
POST /api/admin/kfc-sessions/import-auth-state
```

写入数据库。

它主要用于后台页面不可用时的调试、迁移和恢复，不是日常后台操作的首选入口。

## 11. 后台 UI 调整

本轮同时完成：

- KFC Ticket 新增表单改成弹窗。
- KFC 登录状态抓取改成页面内两阶段弹窗流程。
- 删除后台所有普通页面 Header 上重复出现的“导入卡券”按钮。
- 导入卡券只通过左侧独立菜单进入。
- 恢复餐品管理新增区域的紧凑后台布局。
- 餐品图片上传改回统一的自定义上传控件和预览样式，避免浏览器原生文件框撑满页面。

## 12. 运行限制

当前 `captureId` 和 Chrome 调试端口保存在 API 进程内存中，因此：

- 捕获开始和保存必须命中同一个 API 进程。
- API 进程重启后未保存的捕获任务失效。
- 后端启动 Chrome 的机器就是浏览器实际弹出的机器。
- 部署在纯无桌面的远程 Linux 服务器时，不会在管理员本地电脑自动弹出浏览器；这种部署应使用有图形桌面的运行节点，或继续使用独立登录态导入工具。

该限制只影响“打开浏览器获取登录态”过程，不影响已经持久化到 PostgreSQL 的 Session 被真实下单流程使用。

## 13. 测试状态

本功能提交后已经执行项目 CI：

- TypeScript 类型检查通过。
- 单元测试通过。

相关类型检查同时修复了 `noUncheckedIndexedAccess` 下 JSON 首字符读取可能为 `undefined` 的问题。
