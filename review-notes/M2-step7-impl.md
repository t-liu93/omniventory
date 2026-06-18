# M2 Step 7 实现简报

## a) 本轮实现了什么（逐文件）

### `frontend/src/i18n/locales/en/stock.json` + `zh/stock.json`

在已有的 `trackingMode` / `stockLevel` / `movementType` 三组 key 基础上，新增：
- `actions.*`：六个操作标签（consume/intake/move/adjust/discard/reverse），对应各操作按钮文本。
- `consumeModal.*` / `intakeModal.*` / `adjustModal.*` / `discardModal.*` / `moveModal.*` / `reverseModal.*`：各操作模态框的标题、提示语、字段标签。
- `lowStockBadge`：低库存徽章文本。
- `history.*`：移动历史表的列头、空态、撤销链接、Reverse 按钮文本等。

en/zh 两侧完全对称，catalog 对称测试通过。

---

### `frontend/src/i18n/locales/en/instances.json` + `zh/instances.json`

在已有 key 基础上新增：
- `detail.actionsTitle`：批次操作区标题。
- `detail.levelBadgeAriaLabel`：水位 badge 的 aria-label。
- `success.intake` / `discard` / `adjust` / `move` / `reverse`：各操作成功通知文本。

---

### `frontend/src/pages/Items.tsx`（`ItemDetail` 组件）

**新增 state**（在 `ItemDetail` 函数顶部）：
- `consumeOpen / consumeForm / consumeBusy / consumeError`：Consume (FIFO) 模态框状态。
- `ledgerAction / ledgerForm / ledgerBusy / ledgerError`：每批次操作（intake/discard/adjust/move）的共享模态框状态。

**新增处理函数**：
- `openConsume / closeConsume / handleConsume`：向 `POST /api/definitions/{id}/consume` 发送 `{ quantity: string, note? }` 请求。
- `openLedgerAction / closeLedgerAction / handleLedgerAction`：根据 `kind` 分发到四个端点（intake/discard/adjust/move），全部以 string 传 quantity。
- `isLowStock`（渲染时计算）：从已加载实例求和与 `min_stock` 做 `<` 比较，纯客户端推算——不新增 API 调用（见"低库存徽章数据来源"说明）。

**渲染变更**：
- 实例区标题右侧加低库存徽章（`data-testid="low-stock-badge"`，仅 exact 且 total < min_stock 时显示）。
- 数量列按 mode 分支渲染：`exact`（或未设置，默认）→ `formatQuantity`；`level` → 水位 badge；`none` → "—"。
- 每行加 `Menu`（`data-testid="lot-actions-{id}"`），仅 exact mode 显示，含 intake / adjust / discard / move 四项。
- 添加 Consume (FIFO) 模态框 JSX + 每批次操作模态框 JSX（共用）。

**`tInst` 钩子**：新增 `useTranslation("instances")` 并命名为 `tInst`，用于操作标题与成功通知。

---

### `frontend/src/pages/InstanceDetail.tsx`（完整重写）

从原来的只读展示页，扩展为：

**新增 state**：
- `movements`：从 `GET /api/instances/{id}/movements` 加载的移动历史（API 返回 newest-first）。
- `ledgerAction / ledgerForm / ledgerBusy / ledgerError`：批次操作模态框。
- `reverseMovementId / reverseNote / reverseBusy / reverseError`：撤销操作模态框。

**新增加载逻辑**：
- `loadAll` 中额外调用 `GET /api/instances/{id}/movements`，与 def/locations/allDefs 并发获取。
- `reloadMovements`：操作/撤销后只刷新 instance（quantity 可能变化）和 movements，避免重载全部。

**批次操作按钮**（exact mode 下方）：
- Intake / Adjust / Discard / Move 四个按钮，各有 `data-testid`。
- 共用 `ledgerAction` 模态框，逻辑与 `Items.tsx` 相同（分发到对应端点，quantity 以 string 传输）。

**移动历史表**（exact mode 才显示）：
- 列：类型 badge / 带符号 delta / from 位置 / to 位置 / occurred_at / "撤销 #N" / Reverse 按钮。
- Delta 渲染：正数绿色 `+N`，负数红色，零灰色 `0`，均经 `formatQuantity` 格式化。
- 位置 ID 通过 `locations` 列表解析为名称，找不到则 fallback 显示 ID。
- `data-testid="movement-row-{id}"` / `movement-delta-{id}` / `reversal-link-{id}`。

**可撤销性判断（客户端）**：
```
isReversible(mov):
  if mov.reverses_movement_id != null → false  (本身是撤销记录)
  if movements.some(m => m.reverses_movement_id === mov.id) → false  (已被撤销)
  else → true
```

**模式感知渲染**：
- detail 字段区：exact → 数量文本；level → 水位 badge（`data-testid="inst-level-badge"`）；none → "—"。

---

### `frontend/src/__tests__/M2Step7.test.tsx`（新建）

按 §5/§7.6/§10 Step 7 的要求，覆盖：

| 测试组 | 覆盖点 |
|---|---|
| ItemDetail — Consume | 仅 exact 显示；调用正确端点；quantity 为 string；服务端错误经 mapApiError 呈现 |
| ItemDetail — 批次操作菜单 | intake/adjust/discard/move 各调用对应端点；move 使用 to_location_id（number）；服务端错误呈现 |
| ItemDetail — 低库存徽章 | qty < min_stock 显示；qty ≥ min_stock 不显示；level mode 不显示 |
| ItemDetail — 数量/水位渲染 | exact 显数字；level 显 badge；none 列为 — |
| InstanceDetail — 历史表 | 行存在；delta 正确；"撤销 #N" 链接出现 |
| InstanceDetail — Reverse | 可撤销行才有按钮（已撤销/自身是撤销则无）；调用正确端点；刷新历史；服务端错误呈现 |
| InstanceDetail — 批次操作 | exact 显示按钮，level 不显示；intake/adjust 调 string；服务端错误呈现 |

共 28 个用例，Mock 风格与 M2Step6 一致（mock typed client，en pinned）。

---

## b) 自动化测试结果

```
make check 全绿：
  Backend:  592 passed, 394 warnings  (ruff / mypy / pytest)
  Frontend: 319 passed  (eslint / tsc / vitest)
  contract gate: no drift
```

---

## c) 人工走查步骤

1. **启动服务**：`docker compose up -d`（或 `make docker-dev`），登录。
2. **创建 exact-mode 定义**（例：AA Batteries，mode=exact，unit=pcs，min_stock=4）并登记一批次（quantity=10）。
3. **FIFO Consume**：在 AA Batteries 详情页点击 "Consume (FIFO)" 按钮，输入 3，提交 → 检查批次数量变为 7，无低库存徽章。
4. **低库存徽章**：再消耗 4（consume 4）→ 数量变 3，低于 min_stock=4，验证 **"Low stock"** 徽章出现在实例列表标题旁。
5. **批次操作菜单**：点击实例行右侧 ⋮ → 选 Intake，输入 2，提交 → 数量变 5，低库存徽章消失。
6. **Adjust**：⋮ → Adjust，输入绝对值 8 → 数量变 8（delta = +3）。
7. **Discard**：⋮ → Discard，输入 1 → 数量变 7。
8. **Move**：⋮ → Move，选目标位置 → 批次移位，验证位置变更。
9. **进入 InstanceDetail**：点实例行链接 → 查看移动历史表（intake/consume/adjust/discard/move 行均应出现），验证 delta 符号与颜色正确，"reversal of #N" 链接出现在撤销行。
10. **Reverse（undo）**：点某条 consume 行的 Reverse 按钮 → 确认弹出模态框，提交 → 历史追加 correction 行，原 consume 行 Reverse 按钮消失（已被撤销），数量恢复。
11. **Level mode**：创建 level mode 定义（Assorted Screws），登记批次（stock_level=low）→ 在详情页数量列显示红色 "Low" 徽章；InstanceDetail 详情区显示水位 badge；**无** 历史表和操作按钮。
12. **None mode**：创建 none mode 定义，登记批次 → 实例行数量列为 "—"；InstanceDetail 亦如此；无操作区无历史。

---

## 审查者注意事项

### 低库存徽章数据来源（Step 7 范围内）

选择**客户端推算**（从已加载 `instances` 求和与 `min_stock` 对比）而非额外调用 `GET /low-stock`。原因：
- `instances` 列表在 `loadAll` 时已全部加载，无需额外网络请求。
- 规范允许此做法（§7.3："prefer whichever is cleaner"）。
- Step 8 的 Dashboard 低库存磁贴**必须**使用 `GET /low-stock`（不重新推算规则）——两者不冲突。

### 可撤销性计算（客户端）

`isReversible(mov)` 完全基于已加载的 `movements` 数组：
1. `mov.reverses_movement_id !== null` → 本身是撤销记录，不可逆。
2. `movements.some(m => m.reverses_movement_id === mov.id)` → 已被其他行撤销，不可逆。
3. 否则可逆。

此逻辑与 §4.4 的后端语义一致，且无需额外 API 请求。

### i18n key 布局

- `stock` namespace：操作标签和历史相关 key（actions、*Modal、lowStockBadge、history）。
- `instances` namespace：实例详情和操作成功通知（detail.actionsTitle、detail.levelBadgeAriaLabel、success.*）。
- errors namespace 未变（M2 error codes 在 Step 6 已完整映射）。

### quantity 线上传输

所有 `quantity` 值均以 **string** 发送（`NumberInput.onChange` 转为 `String(v)`），满足 Decimal-on-wire 规范（roadmap §2.9）。

---

## 返工（rework）— 盲审发现修复

### 发现 1（major）：移动历史表缺少 actor（操作人）列

**修复位置**：`frontend/src/pages/InstanceDetail.tsx`（表头 + 每行）。

**改动**：
- 表头新增 `<Table.Th>{tStock("history.colActor")}</Table.Th>`，使用已预留的 `stock:history.colActor` key（"By" / "操作人"）。
- 每行在 `occurred_at` 列之后新增 actor 列，带 `data-testid="movement-actor-{id}"`：
  - `mov.user_id != null` → 显示 `String(mov.user_id)`（即数字 ID）。
  - `mov.user_id == null` → 显示 `tStock("history.unknownActor")`（"—"，对应系统/回填移动记录）。

**actor 显示方式的选择**：M2 只有单管理员，且设计文档 §1 明确说明"No multi-user / roles beyond M0"——当前阶段无需用户名解析。页面现有数据加载中没有用户列表，引入新的 `/api/users` 依赖会超出 Step 7 范围，也会引入不必要的复杂度。因此采用**最简正确方案**：`user_id` 非空时显示用户 ID 字符串，为 null 时显示 `unknownActor` 占位符（"—"）。这与 M6 扩展方向兼容——届时只需把 `String(mov.user_id)` 替换为用户名查找即可，不改变列结构。

**测试**：在 `M2Step7.test.tsx` 的"InstanceDetail — movement-history table"测试组中新增一个用例：`renders actor column: user_id as string when set, unknownActor placeholder when null`，验证 `data-testid="movement-actor-{id}"` 中分别显示 `"5"`（user_id=5 时）和 `"—"`（user_id=null 时）。

---

### 发现 2（minor）：`success.consume` i18n key 缺失 + `as never` 类型逃逸

**修复位置**：
- `frontend/src/i18n/locales/en/instances.json`：在 `success` 段补充 `"consume": "Consumed."`。
- `frontend/src/i18n/locales/zh/instances.json`：对称补充 `"consume": "已消耗。"`。
- `frontend/src/pages/Items.tsx`（第 1029 行）：将 `tInst("success.consume" as never, { defaultValue: "Consumed." })` 改为 `tInst("success.consume")`——移除 `as never` 类型逃逸和 `defaultValue` 兜底，恢复 tsc 对该 key 的类型保护。

en/zh 两侧现已对称，catalog 对称测试（`i18n-catalog.test.ts`）通过。

---

### 测试结果

```
make check 全绿：
  Backend:  592 passed, 394 warnings  (ruff / mypy / pytest)
  Frontend: 320 passed  (eslint / tsc / vitest)  ← 新增 1 个 actor 列渲染测试
  contract gate: no drift
```
