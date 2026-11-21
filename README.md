# 造纸浆料产量配额

River Paper 纸厂用来把浆料批次、纤维配比与机台工况转成可审批的周产量配额，再约束领料计划。项目聚焦造纸车间的实际决策链，包含配比验证、机台安全边界、Random Forest 产量回归、分位数配额带、拆周、审批与物料领用。

## 真实场景

模拟客户是 River Paper 纸厂的 2 条纸机生产计划团队。过去由班组长在 Excel 中拼接浆料批次、纤维比例、机速和蒸汽压力，计划员手工估算周产量，仓库再凭邮件领料。主要痛点是配比越界直到投料时才被发现，预测上界可能跨过机台安全容量，领料单也容易超过主管批准的吨位。上游是实验室纤维配比与机台抄表，下游是生产排程和仓库发料。

## 快速启动

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:18266/api/health
```

首次启动会创建表结构并执行幂等 seed。worker 负责 Redis 队列中的 RandomForestRegressor 试算任务。

访问 `http://localhost:18266`。

## 演示账号

| 角色 | 邮箱 | 密码 | 能力 |
|---|---|---|---|
| 操作员 | operator@riverpaper.test | RiverOp2026! | 登记批次、配比、创建试算、拆分、申请领料 |
| 主管 | supervisor@riverpaper.test | RiverSup2026! | 操作员能力 + 批准配额和领料、查看审计 |
| 查看员 | viewer@riverpaper.test | RiverView2026! | 只读查看批次、配额和领料 |

## 业务模块

- 浆料批次。纸种等级、浆源组合、目标水分、责任人、绑定配比和机台。
- 纤维配比。原生浆、再生浆、损纸、含水率、灰分和工艺窗校验。
- 机台工况。机速、定量、烘缸温度、蒸汽压力、安全周产能。
- 产量配额。RandomForestRegressor 预测均值和 10%/90% 分位数带，跨安全上限时拆成两周。
- 领料计划。只允许在已批准配额内累计请求物料。

## 算法口径

worker 使用固定随机种子 42 生成 160 条带噪合成训练样本，特征顺序是 `virgin_pct, recycled_pct, broke_pct, moisture_pct, line_speed_mpm, basis_weight_gsm, dryer_temp_c, steam_pressure_kpa, safety_capacity_tons`。RandomForestRegressor 使用 90 棵树，输出树预测分布的均值、10% 分位数和 90% 分位数。失败态是批次不存在、配比未验证或配比越界，均在进入队列前拒绝。

## 枚举出现位置

- 角色 `operator/supervisor/viewer`：`backend/src/schema.sql`、`backend/src/auth.py`、`backend/src/main.py`、`frontend/src/app.js`。
- 批次状态 `registered/measured/forecasted/allocated/blocked`：`backend/src/schema.sql`、`backend/src/main.py`、`frontend/src/app.js`。
- 配额状态 `queued/ready/split_required/approved/rejected`：`backend/src/schema.sql`、`backend/src/main.py`、`frontend/src/app.js`。
- 领料状态 `requested/approved/issued/rejected`：`backend/src/schema.sql`、`backend/src/main.py`、`frontend/src/app.js`。

## 目录与技术栈

- `backend/src`。FastAPI 路由、PostgreSQL 领域表、Redis worker、认证、业务校验和算法。
- `backend/scripts`。seed 与维护脚本。
- `frontend/src`。原生 HTML/CSS/JS，Nginx 静态托管与 API 反代。
- Python 3.11、FastAPI、PostgreSQL 16、Redis 7、Nginx 1.27、scikit-learn。

## 环境变量

见 `.env.example`。端口为前端 `18266`、后端 `19266`、PostgreSQL `17766`，Redis 使用 compose 内网端口。

## License

MIT


## 业务核验模块

登录后打开页面右下角的「业务核验」。可从实时业务台账筛选记录、导出所选结果，主管可对当前证据签认并填写说明。

- **机台安全余量** 比较预测区间上界与机台安全线，将需要拆分的配额交给计划员。
- **配额领用余额** 汇总每条计划下实际发料和待批领料，计算尚可安排的物料吨数。
- **浆料水分对照** 将批次目标水分与当前纤维配比实测水分对照，超过 2 个百分点提示质检复核。

### 二次扩展核验

- **机台周配额并单核对** 按机台与周合并已批准配额，用同周批准单中的最低安全线核对并单结果。
- **发料签批链核销** 逐张核对领料申请、批准人和出库时点，标记出库凭证缺口与越权遗留。

## 业务处置与验收

登录后打开「配额结算与发料交接」。操作员提交结果、凭证号和说明，主管可复核关闭或退回补证，观察员可查看记录。关闭时复查源业务状态，处置历史保留每次操作。

- 机台周量结算
- 仓库发料凭证回收
