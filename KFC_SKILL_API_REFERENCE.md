# `kfc-skill` 接口提取与本项目接入说明

> 文档版本：v0.1  
> 提取来源：[`LeonSGP43/kfc-skill`](https://github.com/LeonSGP43/kfc-skill)  
> 源码快照：[`0973310`](https://github.com/LeonSGP43/kfc-skill/commit/0973310dbba76af816b56bc4c7918237537d5a44)  
> 提取范围：登录态、动态配置、餐厅搜索、预点会话、菜单、购物车、提交订单、订单状态查询，以及源码中存在但本项目不使用的支付接口。  
> 说明：本文是从参考仓库源码提取的行为记录，不是肯德基官方接口文档。

## 1. 本项目采用的调用链

本项目不提供付款流程。顾客访问的唯一 ID 已经代表其可选择的一组产品。本项目只采用以下链路：

1. 获取肯德基 H5 动态配置与路由 Cookie。
2. 加载服务端保存的肯德基登录态。
3. 根据全国城市静态数据中的 `gbCityCode` 和关键词搜索餐厅。
4. 使用餐厅编码初始化预点 session。
5. 刷新登录 ticket。
6. 查询餐厅实时菜单和餐品详情。
7. 将 ID 授权餐品组与实时可售菜单取交集。
8. 更新购物车并取得 `cartId`。
9. 确认购物车。
10. 提交订单。
11. 使用订单号查询状态、排队号和取餐信息。

源码中的收银台、支付宝交接、余额支付、风险评估和最终付款接口不进入本项目顾客流程，也不应由顾客 H5 调用。

## 2. 基础地址与动态配置

### 2.1 基础地址

| 项目 | 值 |
| --- | --- |
| 肯德基 H5 基础地址 | `https://order.kfc.com.cn` |
| H5 首页 | `/preorder-taro/home` |
| 订单详情页 | `/preorder-taro/order/kfcOrderDetails/detail/index?orderId={orderId}&opener=settlement&type=1&paystatus=&isSettlePage=true` |

### 2.2 动态配置获取

参考文件：`tools/kfc_safe_probe.py`

1. `GET /preorder-taro/home`。
2. 从 HTML 中提取当前 H5 App JS 路径和 `buildVersion`。
3. 下载 App JS。
4. 从 App JS 中提取：
   - `kbsInfo` 中 `kfc.WEB` 的 `client_key`、`client_sec`；
   - `apiFVersion` 中 KFC 对应版本。
5. 从响应 Cookie 中读取 `route-cell`。

参考仓库源码存在 KBS Key、KBS Secret 和 API 版本的回退常量。本项目文档不复制这些值，也不能长期依赖回退常量；运行时应优先从当前 H5 App JS 动态读取，并把相关材料作为服务端敏感配置处理。

## 3. 公共参数、请求头与签名

### 3.1 公共请求参数

参考文件：`tools/kfc_safe_probe.py`

所有主要 JSON POST 请求均合并以下公共参数：

| 参数 | 参考值/来源 |
| --- | --- |
| `portalType` | `WAP` |
| `channelName` | `Mobile Web` |
| `channelId` | `13` |
| `brand` | `KFC_PRE` |
| `business` | `preorder` |
| `deviceId` | 每个调用上下文生成的无横线 UUID |
| `clientVersion` | 从 H5 首页提取的 `buildVersion` |
| `fversion` | 从 App JS 提取的 KFC API 版本 |
| `versionNum` | `5` |
| `env` | `qcpil` |

### 3.2 关键请求头

| 请求头 | 值/来源 |
| --- | --- |
| `origin` | `https://order.kfc.com.cn` |
| `referer` | 肯德基 H5 页面 |
| `content-type` | `application/json` |
| `x-yumc-client-deviceid` | 当前 `deviceId` |
| `x-yumc-route-cell` | 首页 Cookie 中的 `route-cell`，参考默认 `yumc4` |
| `x-yumc-route-channel` | `Mobile Web` |
| `x-yumc-route-usercode` | 登录态中的 `userCode`；未登录安全查询时可为空 |
| `sysbrand` | `kfc` |
| `syschannel` | `preorder` |
| `kbck` | 动态 KBS Key |
| `kbcts` | 当前毫秒时间戳 |
| `kbsv` | 请求签名 |
| `rcsbcid` / `rcsdcid` | 基于 `deviceId` 生成的客户端标识 |

### 3.3 签名规则

参考仓库按以下内容拼接后计算 MD5：

```text
KBS_KEY + "\t" + KBS_SECRET + "\t" + 毫秒时间戳 + "\t" + API路径 + "\t\t" + 紧凑JSON请求体
```

结果写入 `kbsv`；KBS Key 和时间戳分别写入 `kbck`、`kbcts`。

`init/combine/preorder` 在参考实现中使用 `sign=False`；其他菜单、购物车、登录、提交和状态接口使用签名请求。

## 4. 登录态

参考文件：`tools/kfc_auth_state.py`、`tools/kfc_cart_probe.py`

参考仓库通过可调试 Chrome 让管理员完成一次肯德基 H5 登录，然后保存：

- 肯德基/百胜域名 Cookie；
- `localStorage`；
- `sessionStorage`；
- 页面 `__STATE`；
- 候选 `ticket` / `token`；
- `userCode`；
- `payUserId`；
- 手机号；
- `openid`。

本项目要求：

- 上述登录态只保存在服务端，不返回给顾客浏览器。
- 订单相关请求至少需要有效 `ticket` 和 `userCode`。
- 每次业务链路建立 session 后调用 `user/login`，使用返回的最新 ticket。
- 登录态过期必须由后台重新建立，不能让顾客处理肯德基登录。
- 不保存付款密码；本项目也没有付款步骤。

## 5. 接口总表

| 顺序 | 方法 | 接口 | 用途 | 本项目是否使用 |
| --- | --- | --- | --- | --- |
| 1 | GET | `/preorder-taro/home` | 获取首页、Cookie、动态 App JS 路径和版本 | 使用 |
| 2 | POST | `/preorder-portal/api/v2/store/searchByCityCodeAndKeyword` | 按城市编码和关键词搜索餐厅 | 使用 |
| 3 | POST | `/preorder-portal/api/v2/init/combine/preorder` | 为选中餐厅建立预点 session | 使用 |
| 4 | POST | `/preorder-portal/api/v2/user/login` | 使用保存的 ticket 登录/刷新当前 ticket | 使用 |
| 5 | POST | `/preorder-portal/api/v2/menu/list` | 获取餐厅实时菜单 | 使用 |
| 6 | POST | `/preorder-portal/api/v2/menu/detail` | 获取餐品规格和配料详情 | 使用 |
| 7 | POST | `/preorder-portal/api/v2/cart/update` | 加入/更新购物车，返回 `cartId` | 使用 |
| 8 | POST | `/preorder-portal/api/v2/cart/confirm` | 确认购物车 | 使用 |
| 10 | POST | `/preorder-portal/api/v2/order/getOrderStatusById` | 查询订单状态 | 使用 |
| 11 | POST | `/preorder-portal/api/v2/init/initSession` | 初始化收银台 session；状态查询脚本也用它刷新查询 session | 状态查询前使用；收银用途不使用 |
| 12 | POST | `/cashier/unifiedOrderPay` | 初始化余额/支付宝收银台 | 不使用 |
| 13 | POST | `/card-pay/rest/assessRisk` | 支付风险评估，存在于旧版 `H5/business_flow.py` | 不使用 |
| 14 | POST | `/card-pay/servlet/cashier` | 最终付款，参考仓库新工具明确禁止调用 | 不使用 |

## 6. 餐厅搜索接口

### 6.1 请求

`POST /preorder-portal/api/v2/store/searchByCityCodeAndKeyword`

除公共参数外，请求体包括：

| 参数 | 说明 |
| --- | --- |
| `keyword` | 顾客输入的餐厅关键词 |
| `gbCityCode` | 页面全国城市静态数据中所选城市的编码 |
| `pageNum` | 参考值 `1` |
| `pageSize` | 参考值 `20` |
| `mylat` / `mylng` | 参考仓库传空字符串 |
| `mylatPhone` / `mylngPhone` | 参考仓库传空字符串 |

### 6.2 响应提取

餐厅列表路径：`data.stores`。

参考仓库读取字段：

- `storecode` / `storeCode`：餐厅编码；
- `storename`：餐厅名称；
- `address`：地址；
- `status`：餐厅状态；
- `starttime`：营业开始时间；
- `endtime`：营业结束时间。
- `storePic.storeSelfPic[]`：门店自身图片；
- `storePic.storeEnvirPic[]`：门店环境图片；
- `storePic.storeBuzAreaPic[]`：门店商圈图片。

页面按门店自身图片、环境图片、商圈图片的顺序取第一张；三个数组均为空时不显示占位图。

本项目不能简单自动选择第一家餐厅，必须把搜索结果展示给顾客选择。

## 7. 初始化预点会话

### 7.1 请求

`POST /preorder-portal/api/v2/init/combine/preorder`

除公共参数外，请求体包括：

| 参数 | 说明 |
| --- | --- |
| `body.geoLocation` | 参考仓库为空对象 `{}` |
| `storeCodeParent` | 所选餐厅编码 |
| `storeCodeCurrent` | 所选餐厅编码 |
| `bookingDate` | 预点时间，毫秒时间戳 |

### 7.2 响应提取

从 `data.sessionId` 获取后续菜单、登录、购物车和订单请求使用的 session。

参考实现为该接口设置 `sign=False`。

## 8. 刷新登录 ticket

### 8.1 请求

`POST /preorder-portal/api/v2/user/login`

除公共参数外，请求体包括：

| 参数 | 说明 |
| --- | --- |
| `sessionId` | 当前预点或查询 session |
| `ticket` | 服务端登录态中保存的 ticket |

请求头 `x-yumc-route-usercode` 使用保存的 `userCode`。

### 8.2 响应提取

参考仓库优先读取 `data.user.ticket["0"]` 作为当前 ticket；兼容 ticket 为字符串或嵌套字段的情况。

## 9. 菜单接口

### 9.1 菜单列表

`POST /preorder-portal/api/v2/menu/list`

除公共参数外，请求体包括：

| 参数 | 说明 |
| --- | --- |
| `sessionId` | 预点 session |
| `store` | 餐厅搜索返回的完整餐厅对象 |
| `timeA` | 预点时间毫秒值 |
| `callScene` | `parallel` |
| `curTime` | 当前毫秒时间戳 |

响应主要使用 `data`。参考仓库递归遍历菜单对象，提取带 `linkId` 和名称的项目。

菜单摘要字段包括：

- `nameCn` / `showNameCn` / `abbrDesc`；
- `linkId`；
- `menuFlag`；
- `apiPrice` / `price`；
- `disabledStatus`；
- `minQty` / `maxQty`；
- `descCn`；
- 所属 `classNameCn`。

### 9.2 菜单详情

`POST /preorder-portal/api/v2/menu/detail`

除公共参数外，请求体包括：

| 参数 | 说明 |
| --- | --- |
| `sessionId` | 预点 session |
| `linkId` | 父级餐品 `linkId` |
| `descNeed` | `true` |

参考仓库从 `data.groupRoundList[].groupItemList[]` 中提取规格，并从规格的 `condimentRoundList[].condimentItemList[]` 中提取配料/口味。

本项目展示规则：只保留“当前 ID 授权餐品组”与“餐厅实时菜单”都存在且 `disabledStatus` 可售的项目。

## 10. 更新购物车

### 10.1 请求

`POST /preorder-portal/api/v2/cart/update`

请求体为公共参数加：

| 参数 | 说明 |
| --- | --- |
| `sessionId` | 当前预点 session |
| `ticket` | `user/login` 返回的当前 ticket |
| `selectProductId` | 参考仓库传空字符串 |
| `items` | 购物车商品数组 |
| `scenceType` | 参考仓库传空字符串 |

商品项可能包括：

- `linkId`：规格餐品 ID；
- `showNameCn`；
- `menuFlag` / `menuFlagType`；
- `staticPrice` / `price` / `originPrice`；
- `itemType`；
- `quantity`；
- `productGroupId`：父级餐品 `linkId`；
- `condimentItems`；
- `appendRelationInfo`、`activityIds`、`appendId`、`promotionCode` 等从菜单原样带出的可选字段。

配料项可能包括：

- `linkId`；
- `itemType`：参考值 `11`；
- `showNameCn`；
- `quantity`；
- `mealdealId`；
- `menuFlag` / `menuFlagType`。

### 10.2 成功判断与响应提取

参考仓库以顶层 `code == 0` 判断一种商品结构被接受，并读取：

| 字段路径 | 用途 |
| --- | --- |
| `code` / `msg` / `traceId` | 结果与排障 |
| `data.cart.id` | `cartId`，后续确认和提交订单使用 |
| `data.cart.itemsCount` | 购物车商品数量 |
| `data.cartAmount.payAmount` | 实时应付金额/订单金额字段 |

本项目对同一 ID 只能创建一个逻辑订单；`cart/update` 前必须建立独立的订单调用上下文，不能让不同顾客共享 session 或购物车。

## 11. 确认购物车

### 11.1 请求

`POST /preorder-portal/api/v2/cart/confirm`

请求体为公共参数加：

| 参数 | 说明 |
| --- | --- |
| `sessionId` | 当前预点 session |
| `ticket` | 当前 ticket |
| `cartId` | `cart/update` 返回的 `data.cart.id` |

参考仓库以顶层 `code == 0` 判断成功；失败时停止，不再提交订单。

## 12. 提交订单

### 12.1 请求

请求体为公共参数加：

| 参数 | 说明 |
| --- | --- |
| `sessionId` | 当前预点 session |
| `ticket` | 当前 ticket |
| `phone` | 登录态中的手机号 |
| `packType` | 参考值 `1`，打包带走 |
| `iremark` | 参考仓库传空字符串 |
| `orderId` | `cartId` |
| `queryThirdParam` | 参考仓库传空字符串 |
| `riskCtrlParam.channelName` | `Mobile Web` |
| `riskCtrlParam.openId` | 登录态中的 `openid`，可为空 |
| `riskCtrlParam.browserId` | `bid_` + 当前 `deviceId` |
| `riskCtrlParam.deviceId` | 当前 `deviceId` |
| `riskCtrlParam.td_id` | 参考仓库传空字符串 |

请求头 `x-yumc-route-usercode` 使用登录态中的 `userCode`。

### 12.2 成功与失败

参考仓库以顶层 `code == 0` 判断提交成功，并记录 `msg`、`traceId` 和是否存在 `data`。

需要注意：参考仓库把 `cartId` 同时作为提交请求的 `orderId`，并在后续状态查询中作为订单号使用。本项目集成时必须用真实响应验证最终查询 ID 是否始终等于 `cartId`，如响应另有正式订单号则应优先保存正式订单号。

本项目状态规则：

- `code == 0` 且取得可查询订单标识：ID 从“待使用”进入“待取餐”。
- 明确业务失败：ID 进入“下单失败”。
- 超时、断网或结果未知：保持内部锁定并核对，不得重新开放同一 ID 下单。

## 13. 查询订单状态

### 13.1 查询前初始化

参考文件：`tools/kfc_order_status.py`

状态查询脚本不是直接调用状态接口，而是：

1. 加载登录态，要求 `ticket` 和 `userCode` 存在。
2. 获取最新 H5 动态配置。
3. 调用 `POST /preorder-portal/api/v2/init/initSession`：请求体为公共参数加旧 ticket。
4. 从 `data.sessionId` 取得查询 session。
5. 调用 `POST /preorder-portal/api/v2/user/login` 刷新 ticket。
6. 再调用订单状态接口。

### 13.2 状态请求

`POST /preorder-portal/api/v2/order/getOrderStatusById`

请求体为公共参数加：

| 参数 | 说明 |
| --- | --- |
| `sessionId` | `initSession` 返回的查询 session |
| `ticket` | 刷新后的当前 ticket |
| `orderId` | 待查询的肯德基订单号/参考实现中的 `cartId` |

请求头 `x-yumc-route-usercode` 使用登录态中的 `userCode`。

### 13.3 响应提取

参考仓库读取：

- 顶层 `code`、`msg`、`traceId`；
- 完整 `data`；
- `data.status` 作为原始订单状态。

参考仓库目前唯一明确映射：

| 原始状态 | 已观察页面状态 |
| --- | --- |
| `504` | 订单已取消 |

参考仓库没有给出排队号、取餐码、制作中、请取餐和已完成的字段名或状态码。本项目必须保存真实接口的完整 `data`，用至少一组完整订单采集这些阶段后才能建立正式映射，不能编造状态码。

### 13.4 本项目 3 秒查询规则

- 仅 ID 为“待取餐”时允许查询。
- 顾客页面每次请求本项目服务端，本项目服务端再调用或读取肯德基状态；顾客端不能直接调用肯德基接口。
- 前一次查询完成 3 秒后再发下一次，避免请求重叠。
- 显示“请取餐”后停止顾客页面轮询。
- 肯德基返回取消/明确失败后，将 ID 变为“下单失败”，显示申请退款。
- 肯德基返回最终完成状态后，将 ID 变为“订单完结”，不再提供查询。
- 连续接口错误时保留“待取餐”，显示暂时无法查询，不能错误改成待使用。

## 14. 参考仓库中存在但本项目不用的接口

### 14.1 收银 session

`POST /preorder-portal/api/v2/init/initSession`

该接口在参考仓库中同时用于收银前初始化和订单状态查询前初始化。本项目只保留状态查询需要的用法。

### 14.2 初始化收银台

`POST /cashier/unifiedOrderPay`

参考仓库支持余额和支付宝渠道，返回 `paymentUrl`。本项目没有顾客付款流程，不调用该接口。

### 14.3 风险评估

`POST https://card.yumchina.com/card-pay/rest/assessRisk`

仅存在于旧版 `H5/business_flow.py`，属于付款链路，本项目不调用。

### 14.4 最终付款

`POST https://card.yumchina.com/card-pay/servlet/cashier`

参考仓库的新工具明确把它列为禁止调用的最终付款接口。本项目不调用、不保存付款密码，也不向顾客展示支付页面。

## 15. 全国城市数据要求

参考仓库没有城市列表接口，只接收调用方传入的 `gbCityCode`；示例使用上海 `310100`。

本项目不要求管理员逐个配置城市，也不向用户询问支持哪些城市。系统页面内置全国城市静态数据，至少包括：

- 城市名称；
- `gbCityCode`；
- 名称搜索关键词；
- 拼音或首字母索引（用于快速选择）。

顾客选中城市后，将对应 `gbCityCode` 传给餐厅搜索接口。若肯德基在该城市没有返回餐厅，则展示无搜索结果，不从全国列表中删除该城市。

## 16. 本项目服务端需要封装的能力

| 服务端能力 | 输入 | 输出 |
| --- | --- | --- |
| 校验 ID | 唯一 ID | ID 状态、授权餐品组、关联订单 |
| 搜索餐厅 | 唯一 ID、`gbCityCode`、关键词 | 可选择的肯德基餐厅列表 |
| 查询授权菜单 | 唯一 ID、餐厅编码 | 授权组与实时菜单的可售交集 |
| 提交订单 | 唯一 ID、餐厅、所选餐品/规格 | ID 新状态、肯德基订单号或确定失败信息 |
| 查询待取餐状态 | 唯一 ID | 排队号、取餐码、页面状态、ID 新状态 |
| 登录态检查 | 后台管理员 | 登录态是否有效、是否需要重新登录 |
| 异常核对 | 结果未知的调用记录 | 实际订单结果、人工处理结论 |

## 17. 仍需通过真实调用补齐的内容

这部分不是产品需求不清楚，而是参考仓库源码本身没有样本：

1. 全国城市名称到肯德基 `gbCityCode` 的完整静态数据源。
2. `order/submit` 成功响应中正式订单号的实际字段。
3. `getOrderStatusById.data` 中排队号和取餐码的字段。
4. 制作中、请取餐、已完成对应的原始状态值。
5. 显示“请取餐”后，肯德基何时返回最终完成状态。
6. 同一登录账号并发创建多个独立 session/购物车时是否互相影响。
7. 登录态有效期、刷新失败码和重新登录触发条件。

开发前应使用测试订单保存每个阶段的脱敏响应样本，再冻结状态映射表和异常处理规则。
