import test from "node:test";
import assert from "node:assert/strict";
import { sectorMetricLabels } from "../web/sector-performance.mjs";

test("sector returns use percent and their difference uses percentage points", () => {
  const labels = sectorMetricLabels({success:true, stock_return_pct:10, benchmark_return_pct:6, excess_return_pp:4});
  assert.equal(labels.stock, "+10.00%");
  assert.equal(labels.benchmark, "+6.00%");
  assert.equal(labels.excess, "+4.00 pp");
  assert.equal(labels.direction, "Outperformed sector benchmark");
  assert.equal(sectorMetricLabels({success:true, stock_return_pct:-10, benchmark_return_pct:-6, excess_return_pp:-4}).direction, "Underperformed sector benchmark");
});

test("unknown or invalid comparisons never display zero as a measured result", () => {
  for (const result of [{}, {success:false}, {success:true,stock_return_pct:0,benchmark_return_pct:null,excess_return_pp:0},
                        {success:true,stock_return_pct:NaN,benchmark_return_pct:1,excess_return_pp:0}]) {
    assert.equal(sectorMetricLabels(result), null);
  }
  assert.equal(sectorMetricLabels({success:true,stock_return_pct:0,benchmark_return_pct:0,excess_return_pp:0}).direction, "Matched sector benchmark");
});
