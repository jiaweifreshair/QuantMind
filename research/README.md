# 只读研究运行

上游是公开仓库 [qusong0627/QuantMind](https://github.com/qusong0627/QuantMind)，许可证 AGPL-3.0。本次固定提交：

`1ba0b4d818ab8aa4910b3439f3842a5ba86a0247`

pyproject 版本 `1.0.0`。GitHub Releases 为空。本仓库不修改、不推送上游。

## 不会做的事

不执行 `deploy/full-deploy.sh`（该脚本是 `curl | sudo bash`）。不启动 `backend/main_oss.py`。不调用券商、QMT、通达信下单或模拟撮合。不注册密钥，不新增定时任务。

## 依赖

只安装行情库，版本按上游源码仍在调用的函数名选定：

```bash
python3 -m pip install --target /tmp/qm-py \
  'akshare==1.20.45' 'baostock>=0.8.9' 'pandas>=2.0' 'httpx>=0.27' 'duckdb>=1.4.4'
```

`akshare>=1.20.46` 把 `stock_zh_a_hist`、`stock_hk_daily` 改名，上游源码会直接报错。1.20.45 仍保留这些名字。不要安装 `futu-api`、`tigeropen`、`xtquant`。

取得上游：

```bash
git clone --depth 1 https://github.com/qusong0627/QuantMind.git /tmp/quantmind-upstream
git -C /tmp/quantmind-upstream fetch --depth 1 origin 1ba0b4d818ab8aa4910b3439f3842a5ba86a0247
git -C /tmp/quantmind-upstream checkout 1ba0b4d818ab8aa4910b3439f3842a5ba86a0247
```

## 运行

```bash
PYTHONPATH="/tmp/qm-py:/tmp/quantmind-upstream" \
QUANTMIND_ROOT=/tmp/quantmind-upstream \
TZ=Asia/Shanghai \
python3 research/run_readonly_research.py
```

产物在 `research/output/`：

- `analysis.html`：自包含中文记录
- `run_manifest.json`：SHA、命令、成败、耗时、文件哈希
- `results.json`：每只股票的原始汇总
- `bars/*.csv`：实际写入计算的日线

校验：

```bash
python3 research/test_analysis_html.py
```

## 数据边界

A 股使用 `BaostockAdapter`。东方财富 push2 在准备阶段对 `stock_zh_a_hist` 返回限流后，不再调用 `AkShareProvider` 与 `EastMoneyProvider`。

港股日线使用 `backend/scripts/akshare_kline.py` 的 `_fetch_stock`（`stock_hk_daily`，不复权）。后复权需要付费 CSV，没有运行。

`scripts/stock_9layer_fetch.py` 依赖本机不存在的 QuantDB parquet。Qlib 与 LLM 研报未运行。区间涨跌是收盘价计算，不是上涨概率。
