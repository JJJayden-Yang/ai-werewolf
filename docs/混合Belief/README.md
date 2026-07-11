# 混合 Belief 实验

本目录只保留开源分支所需的 README。实验报告、批跑结果和 trace 不随仓库提交；需要时可用脚本本地复现。

## 复现

```bash
# concurrency 实测 8 零限流；单局 ~11-14 min
python scripts/run_mixed_batch.py --arm-wolves v1 --arm-villagers v0 \
  --games 30 --seed-start 300 --concurrency 8 --temperature 0.6 \
  --out <dir>/wolves_only_v1/report.json --trace-dir <dir>/wolves_only_v1/traces

python scripts/run_mixed_batch.py --arm-wolves v0 --arm-villagers v1 \
  --games 30 --seed-start 400 --concurrency 8 --temperature 0.6 \
  --out <dir>/villagers_only_v1/report.json --trace-dir <dir>/villagers_only_v1/traces
```
