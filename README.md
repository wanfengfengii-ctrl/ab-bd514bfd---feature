# 同步辐射束线 · 探测器曝光排程系统

值班工程师在页面录入 5–10 项探测器曝光（持续时间、最早/最晚开始时刻、设备、结束后冷却时间）
与曝光间的最小/最大衔接间隔，服务端用 CP-SAT 联合求出全部**整数**开始时刻，并按以下顺序
**字典序**优化：

1. 最终结束时刻（所有曝光结束时刻的最大值）最小；
2. 开始时刻总和最小；
3. 按录入顺序展开的开始时刻序列字典序最小。

约束：

- 每项曝光满足其可开始时间窗；
- 同一设备同一时刻只能执行一项曝光，且后续曝光不得早于前序“曝光 + 冷却”结束；
- 每条衔接约束同时满足 `min_gap ≤ s_b − (s_a + d_a) ≤ max_gap`（最大间隔可留空）。

### 共享低温冷量（可选）

部分探测器在曝光**启动瞬间**瞬时占用同一套低温机组的冷量。在草稿中勾选“启用共享冷量”，
填写冷量容量 `capacity`、时刻零的初始量 `initial`、每整数时刻恢复量 `recovery`，
并为每项曝光填写 `startup_consumption`（启动耗量）。服务端把**全部开始时刻与全局启动
事件顺序联合求解**：

- 从时刻零到首次启动、以及相邻不同启动时刻之间，按经过时间恢复
  （`Δt · recovery`，恢复后不超过容量）；
- 同一整数时刻启动的项目按**录入顺序**连续扣减，中间**不得恢复**；
- 每次扣减后冷量均不得为负。

该账本以排序/排名变量直接建在 CP-SAT 模型内（启动时刻决定全局启动顺序），参与上述
**三级优化的每一阶段**，冷量紧张时会迫使部分启动延后，而不是求出方案后再筛选。
冷量不足导致无解时返回 `feasible=false`，**不返回任何部分排程**，并附带
`cooling_obstruction` 指出在仅考虑时间窗与冷量（放宽设备与衔接）时是哪一项曝光使问题
首次不可行；`initial > capacity` 等属于输入错误（400）。成功时逐项返回
`cooling_trace`：按 `(启动时刻, 录入顺序)` 排列的每次启动的本次恢复量、扣减前冷量、
启动耗量与扣减后冷量。未启用时请求与响应字段、最优时刻与错误口径完全保持兼容。

输入合法但无解时返回 `feasible=false`，**绝不返回部分曝光方案，也不沿用旧方案**；
草稿一经修改，前端立即作废旧结果并提示重新求解。

## 目录结构

```
.
├── docker-compose.yml        # api + web + verify（一次性）
├── .env.example              # 宿主机端口 API_PORT / WEB_PORT
├── backend/
│   ├── Dockerfile            # FastAPI + uvicorn，含 HEALTHCHECK
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py           # /health、/api/schedule
│   │   ├── scheduler.py      # CP-SAT 三阶段字典序优化 + 共享冷量全局事件约束
│   │   └── schemas.py        # 请求/响应模型（SharedCooling / CoolingStep …）
│   ├── scripts/verify.py     # 一次性校验脚本（退出码位掩码）
│   └── tests/                # pytest：调度器/冷量 + API 冒烟（40 项）
└── web/
    ├── Dockerfile            # nginx 静态站 + 反代 /api，含 HEALTHCHECK
    ├── nginx.conf            # /health 与 /api 反代
    └── src/                  # index.html / app.js / styles.css（无构建步骤）
```

## 启动

```bash
cp .env.example .env          # 可选：修改 API_PORT / WEB_PORT
docker compose build
docker compose up -d
```

- 页面：`http://localhost:${WEB_PORT:-8080}`
- API 健康检查：`http://localhost:${API_PORT:-8000}/health`
- Web 健康检查：`http://localhost:${WEB_PORT:-8080}/health`

宿主机端口通过环境变量配置，例如 `API_PORT=9000 WEB_PORT=9090 docker compose up -d`。

## 一次性校验服务

```bash
docker compose run --rm verify
```

`verify` 服务**自行退出**，退出码为位掩码（0 表示全部通过）：

| 位 | 值 | 检查内容 |
|----|----|----------|
| 0 | 1  | 代码测试（pytest，40 项） |
| 1 | 2  | 构建/完整性（字节码编译、应用导入、静态资源非空） |
| 2 | 4  | 可行排程（独立复核时间窗、设备占用含冷却、衔接间隔） |
| 3 | 8  | 无解 API 冒烟（200 + feasible=false 且无部分解；对比 400 输入错误；Web/API 健康） |
| 4 | 16 | 共享冷量 API 冒烟（冷量迫使启动延后并独立复核台账；冷量不足 200 + feasible=false、无部分解且给出耗尽项；initial>capacity 为 400） |

## HTTP 约定

| 场景 | 状态码 | 响应 |
|------|--------|------|
| 可行 | 200 | `feasible=true` + starts/finishes/makespan/sum_starts/slacks/equipment_orders；启用共享冷量时另含 `cooling_trace[]` |
| 输入合法但无可行时序 | 200 | `feasible=false, reason="no_schedule"`，所有方案字段为 `null`；启用共享冷量且冷量本身是梗阻时另含 `cooling_obstruction` |
| 输入错误（编号重复、时间窗倒置、未知引用、初始量越界等） | 400 | `reason="input_error"` + `field_errors[]` |
| 字段级 schema 错误 | 422 | FastAPI 校验明细 |
| 求解器超时未能判定 | 503 | `reason="solver_timeout"`（不谎称为无解） |

页面区分三类结果面板：**输入错误**（红）、**无可执行时序**（黄）、**最优排程**，
可行时展示 SVG 时间轴（曝光段/冷却段/时间窗）、各设备执行顺序及每条约束的实际间隔与对最大间隔的余量。
启用共享冷量后，时间轴在每次启动处标注冷量扣减菱形（扣减后为 0 时高亮），
并额外给出按全局启动顺序排列的冷量台账，逐行说明本次为何仍有足够冷量
（时刻零/相邻不同时刻恢复、容量封顶、同刻连续扣减不恢复）；无解时在黄色面板指出冷量在哪一步耗尽。

## 本地开发（无 Docker）

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
cd ../web/src && python3 -m http.server 8080   # 仅静态预览；API 需同源反代
python3 -m pytest backend/tests -q
python3 backend/scripts/verify.py              # 默认访问 http://api:8000，用 API_URL 覆盖
```
