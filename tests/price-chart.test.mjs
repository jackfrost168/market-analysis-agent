import test from "node:test";
import assert from "node:assert/strict";
import { priceSeries, nearestPoint } from "../web/price-chart.mjs";

test("price preview ignores missing bars, sorts timestamps and handles duplicates", () => {
  const data = priceSeries([{time:300,price:120}, {time:100,price:100}, {time:200,price:null},
    {time:400,price:0}, {time:NaN,price:140}, {time:Infinity,price:140}, {time:500,price:-5},
    {time:300,price:125}, {time:600,price:Infinity}]);
  assert.deepEqual(data.points, [{time:100,price:100}, {time:300,price:125}]);
  assert.equal(data.change, 25);
  assert.equal(data.low, 100);
  assert.equal(data.high, 125);
});

test("empty, one bar and flat histories cannot cause a fabricated return or divide by zero", () => {
  assert.equal(priceSeries([]), null);
  assert.equal(priceSeries(null), null);
  assert.equal(priceSeries({}), null);
  assert.equal(priceSeries([{time:1,price:100}]), null);
  const flat = priceSeries([{time:1,price:100}, {time:2,price:100}]);
  assert.equal(flat.change, 0);
  assert.ok(flat.max > flat.min);
});

test("hover snaps to a real observation across market closures", () => {
  const points = [{time:100,price:100}, {time:200,price:95}, {time:500,price:90}];
  assert.equal(nearestPoint(points, 0), 0);
  assert.equal(nearestPoint(points, 290), 1);
  assert.equal(nearestPoint(points, 490), 2);
  assert.equal(nearestPoint(points, 900), 2);
});
