# pointwise — 跨幅净位移核算

源库（source）与离线库（offline）两套 SQLite 存储，纯 Python 标准库实现。
导入幅件修订、发行可按号补领的幅件；撤源后仅凭幅件登记固定对照、核算并签发
跨幅净位移报告。所有命令输出一行规范 JSON（`sort_keys`、紧凑分隔符）。

## 依赖与准备

- Python 3.8+，标准库 `sqlite3`（无需第三方包）。
- 无安装步骤。任意目录可用 `python3 -B -m pointwise ...`，工作目录须能导入本包
  （仓库根目录即可）。

## 概念

- **幅（sheet / 幅件）**：一份测量图幅。源库中一次导入产生一个不可变**修订号**；
  取幅时发行一个**幅件号**。
- **测项（item）**：公开的测试项，携带整数读值（微米）。对应源内的原测行。
- **端子（terminal）**：公开的参考位置，可用共边互联。
- **参考名 / 原测号**：内部标识，只留在源库；幅件只暴露公开端子与测项名前后的
  可推同反向关系与独立性。
- **同反向关系**：`+1` 同向，`-1` 反向。由源内方向边与离线共边共同构成带符号
  连通图。

## 源库命令

```bash
# 导入一幅。--measure 为 原测号:参考名:整数读值，--edge 为 参考:参考:±1
python3 -B -m pointwise source import \
  --db src.db --sheet A --unit 微米 \
  --measure b1:p:7 --edge u:r:+1 --edge p:r:-1
# -> {"revision": 1}

# 列修订 / 看某修订
python3 -B -m pointwise source revisions --db src.db
python3 -B -m pointwise source revision  --db src.db --revision 1

# 发行幅件：--item 公开测项名:原测号，--terminal 公开端子名:参考名
python3 -B -m pointwise source issue \
  --db src.db --revision 1 --item p:b1 --terminal u:u
# -> {"artifact": 1}

# 列幅件 / 按号原样补领（重复请求同一映射返回同一幅件号）
python3 -B -m pointwise source artifacts --db src.db
python3 -B -m pointwise source pull --db src.db --artifact 1 --out a1.json
```

导入拒绝：单位非微米（`refused`）、原测重号（`refused`）、源内方向矛盾
（`conflict`）。发行时未知修订或未知映射目标报缺（`missing`）。

## 离线库命令

```bash
# 收取幅件文件（校验内容摘要）
python3 -B -m pointwise offline receive --db off.db --file a1.json

# 登记固定对照：--artifact 列父件；--shared 为 A:端子=B:端子:±1；
# --absolute 为 幅件:端子:±1 给端子定绝对正负
python3 -B -m pointwise offline control --db off.db \
  --artifact 1 --artifact 2 --shared '1:u=2:v:+1'
# -> {"control": 1}

# 核算并签发报告：--select 为 幅件号:测项名（非空、无重、限父件）
python3 -B -m pointwise offline report --db off.db --control 1 \
  --select 1:p --select 2:q
# -> {"report_number":1,"state":"signed","net":0,...}

python3 -B -m pointwise offline report-get --db off.db --report 1
python3 -B -m pointwise offline reports    --db off.db
```

收取拒绝：坏件（`corrupt`，含摘要不符/非法结构）、同号异件（`duplicate`）。
登记对照拒绝：坏件、同号异件、未列端子/未列父件（`refused`）。
报告拒绝：未知对照、空选或重复选、测项不属于父件、父件不在对照（`refused`）；
未知报告号报缺（`missing`）。拒办或写败不留新报告；报告不可变，按号原样领取。

## 报告语义

报告按所选测项触及的带符号连通组处理：

1. **矛盾**：任一被触及连通组存在同反向矛盾 → `state=contradiction`，
   无净值（`net=null`）。
2. **方向未定**：存在无绝对定向、且翻转会改变总和的连通组 →
   `state=undetermined`，无净值。
3. **签整数净值**：其余情形，含“无绝对定向但翻转不改变总和”的连通组，
   `state=signed`，`net` 为整数（微米）。

## 运行清单与校验

`.pointwise-runtime.json` 给出 `setup`/`verify`/`demo`（均为 argv 数组）：

- `verify`：`python3 -B -m unittest discover -s tests`
- `demo`：`python3 -B demo.py`

## 样例闭环（demo 输出）

- A 的 u 经私有参考 r 以同向 (+1)、反向 (-1) 两边连 p；B 的 v 与 q 同向。
  两读均 7。
- 共边 u=v 同向：p 与 q 反向 → 签 **0**。
- 无共边：各自可翻转、总和不定 → **方向未定**。
- 同反共边并存：被触及组矛盾 → **证据矛盾**。
- B 另修为 5，保留同向共边且 v 定正（q 随之为正）→ 签 **−2**；
  旧报告号仍按原样领 **0**。
