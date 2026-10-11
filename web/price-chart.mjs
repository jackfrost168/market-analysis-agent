// Daily price preview is independent of analysis and never calls the model.
import { createSectorComparison } from "./sector-performance.mjs?v=20261011-sector1";
export function priceSeries(rows = []) {
  if (!Array.isArray(rows)) return null;
  const byTime = new Map();
  for (const row of rows) {
    if (typeof row?.time !== "number" || typeof row?.price !== "number") continue;
    if (!Number.isFinite(row.time) || !Number.isFinite(row.price) || row.time <= 0 || row.price <= 0) continue;
    if (!Number.isFinite(new Date(row.time * 1000).getTime())) continue;
    byTime.set(row.time, { time: row.time, price: row.price });
  }
  const points = [...byTime.values()].sort((a, b) => a.time - b.time);
  if (points.length < 2) return null;
  const prices = points.map(point => point.price);
  const low = Math.min(...prices), high = Math.max(...prices);
  const padding = Math.max((high - low) * 0.12, high * 0.002);
  return { points, low, high, min: low - padding, max: high + padding,
    change: (points.at(-1).price / points[0].price - 1) * 100 };
}

export function nearestPoint(points, time) {
  let lo = 0, hi = points.length - 1;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (points[mid].time < time) lo = mid + 1;
    else hi = mid;
  }
  return lo > 0 && time - points[lo - 1].time < points[lo].time - time ? lo - 1 : lo;
}

export function createPriceChart(panel) {
  const sectorComparison = createSectorComparison(panel.querySelector("[data-sector-panel]"));
  const title = panel.querySelector("[data-chart-title]");
  const status = panel.querySelector("[data-chart-status]");
  const content = panel.querySelector("[data-chart-content]");
  const plot = panel.querySelector("[data-chart-plot]");
  const readout = panel.querySelector("[data-chart-readout]");
  const change = panel.querySelector("[data-chart-change]");
  const source = panel.querySelector("[data-chart-source]");
  const controls = [...panel.querySelectorAll("[data-chart-range]")];
  let currentAsset = null, currentRange = "6mo", controller = null, series = null, history = null;
  let width = 760, selectedIndex = 0;
  const height = 240, left = 12, right = 80, top = 18, bottom = 38;

  function money(value) {
    const digits = value < 1 ? 6 : 2;
    const options = { minimumFractionDigits: 2, maximumFractionDigits: digits };
    // Keep Yahoo quote units exact: GBp is pence, not GBP pounds.
    const amount = new Intl.NumberFormat("en-US", options).format(value);
    return history.currency ? `${amount} ${history.currency}` : amount;
  }
  function date(time, short = false) {
    const options = { timeZone: history.exchange_timezone || "UTC", month: "short", day: "numeric" };
    if (!short) options.year = "numeric";
    try { return new Intl.DateTimeFormat("en-US", options).format(time * 1000); }
    catch { return new Intl.DateTimeFormat("en-US", { ...options, timeZone: "UTC" }).format(time * 1000); }
  }
  function clear(message = "Enter a company or symbol to see its price history.") {
    sectorComparison.clear(message);
    controller?.abort();
    controller = null;
    currentAsset = null;
    series = null;
    history = null;
    title.textContent = "Price history";
    status.textContent = message;
    status.hidden = false;
    content.hidden = true;
    panel.setAttribute("aria-busy", "false");
    controls.forEach(button => { button.disabled = true; });
  }
  function inspect(index) {
    if (!series) return;
    selectedIndex = index;
    const point = series.points[index];
    readout.textContent = `${date(point.time)} · ${money(point.price)}`;
    const x = xFor(point.time), y = yFor(point.price);
    const line = plot.querySelector(".price-crosshair");
    const dot = plot.querySelector(".price-point");
    line.setAttribute("x1", x); line.setAttribute("x2", x);
    dot.setAttribute("cx", x); dot.setAttribute("cy", y);
  }
  function xFor(time) {
    const first = series.points[0].time, last = series.points.at(-1).time;
    return left + (time - first) / (last - first) * (width - left - right);
  }
  function yFor(price) {
    return top + (series.max - price) / (series.max - series.min) * (height - top - bottom);
  }
  function draw() {
    if (!series) return;
    width = Math.max(280, Math.round(plot.clientWidth || 760));
    const path = series.points.map((point, index) => `${index ? "L" : "M"}${xFor(point.time).toFixed(2)},${yFor(point.price).toFixed(2)}`).join(" ");
    const lastX = width - right, baseY = height - bottom;
    // Only numeric coordinates enter markup; all external labels use textContent.
    plot.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" tabindex="0">
      <title></title><desc>Available daily closes. Use Left and Right arrow keys to inspect dates.</desc>
      <path class="price-area" d="${path} L${lastX},${baseY} L${left},${baseY} Z"/>
      <path class="price-line" d="${path}"/>
      <line class="price-crosshair" y1="${top}" y2="${baseY}"/>
      <circle class="price-point" r="4"/>
    </svg>`;
    const svg = plot.querySelector("svg");
    svg.querySelector("title").textContent = `${currentAsset.asset_name || currentAsset.symbol} daily close price in ${history.currency || "quote currency"}`;
    const ns = "http://www.w3.org/2000/svg";
    for (let i = 0; i < 3; i++) {
      const price = series.min + (series.max - series.min) * i / 2;
      const y = yFor(price);
      const line = document.createElementNS(ns, "line");
      for (const [key, value] of Object.entries({ x1: left, x2: lastX, y1: y, y2: y, class: "price-gridline" })) line.setAttribute(key, value);
      svg.prepend(line);
      const text = document.createElementNS(ns, "text");
      text.setAttribute("x", lastX + 9); text.setAttribute("y", y + 4);
      text.textContent = new Intl.NumberFormat("en-US", { maximumFractionDigits: price < 1 ? 4 : 2, notation: "compact" }).format(price);
      svg.append(text);
    }
    for (const index of new Set([0, Math.floor((series.points.length - 1) / 2), series.points.length - 1])) {
      const point = series.points[index], text = document.createElementNS(ns, "text");
      text.setAttribute("x", xFor(point.time)); text.setAttribute("y", height - 9);
      text.setAttribute("text-anchor", index === 0 ? "start" : index === series.points.length - 1 ? "end" : "middle");
      text.textContent = date(point.time, true);
      svg.append(text);
    }
    inspect(Math.min(selectedIndex, series.points.length - 1));
    svg.addEventListener("pointermove", event => {
      if (!series) return;
      const bounds = svg.getBoundingClientRect();
      const fraction = Math.max(0, Math.min(1, ((event.clientX - bounds.left) * width / bounds.width - left) / (width - left - right)));
      const time = series.points[0].time + fraction * (series.points.at(-1).time - series.points[0].time);
      inspect(nearestPoint(series.points, time));
    });
    svg.addEventListener("pointerleave", () => { if (series) inspect(series.points.length - 1); });
    svg.addEventListener("keydown", event => {
      if (!series) return;
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const last = series.points.length - 1;
      inspect(event.key === "Home" ? 0 : event.key === "End" ? last : Math.max(0, Math.min(last, selectedIndex + (event.key === "ArrowRight" ? 1 : -1))));
    });
  }
  async function load(asset = currentAsset) {
    if (!asset?.symbol) { clear(); return; }
    controller?.abort();
    const request = new AbortController();
    controller = request;
    currentAsset = asset;
    sectorComparison.load(asset, currentRange);
    series = null;
    title.textContent = `${asset.symbol} · Price history`;
    content.hidden = true;
    status.hidden = false;
    status.textContent = "Loading daily prices…";
    panel.setAttribute("aria-busy", "true");
    controls.forEach(button => { button.disabled = false; button.setAttribute("aria-pressed", String(button.dataset.chartRange === currentRange)); });
    const timeout = setTimeout(() => request.abort(), 25000);
    try {
      const params = new URLSearchParams({ asset: asset.quote_symbol || asset.symbol, range: currentRange });
      const response = await fetch(`/api/price-history?${params}`, { signal: request.signal, cache: "no-store" });
      if (!response.ok) throw new Error(`Price history request failed (${response.status}).`);
      const payload = await response.json();
      if (request !== controller) return;
      history = payload.history || {};
      const prepared = priceSeries(history.points);
      if (!history.success || !prepared) throw new Error("Price history is unavailable for this asset. Try another range or retry.");
      series = prepared;
      selectedIndex = series.points.length - 1;
      status.hidden = true;
      content.hidden = false;
      change.textContent = `${series.change >= 0 ? "+" : ""}${series.change.toFixed(2)}% over displayed period`;
      change.className = `price-chart-change ${series.change < 0 ? "negative" : "positive"}`;
      const retrieved = new Date(history.retrieved_at || payload.retrieved_at);
      source.textContent = `Yahoo Finance · ${history.currency || "Currency unavailable"} · ${series.points.length} observations · Retrieved ${Number.isNaN(retrieved.getTime()) ? "at unknown time" : retrieved.toLocaleString()}`;
      draw();
    } catch (error) {
      if (request !== controller) return;
      status.textContent = request.signal.aborted ? "Price history request timed out. Choose a range or retry." : error.message;
      status.hidden = false;
      content.hidden = true;
    } finally {
      clearTimeout(timeout);
      if (request === controller) panel.setAttribute("aria-busy", "false");
    }
  }
  controls.forEach(button => button.addEventListener("click", () => { currentRange = button.dataset.chartRange; load(); }));
  panel.querySelector("[data-chart-retry]").addEventListener("click", () => load());
  new ResizeObserver(() => { if (series && !content.hidden) draw(); }).observe(plot);
  clear();
  return { load, clear };
}
