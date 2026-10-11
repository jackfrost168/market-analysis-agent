export function sectorMetricLabels(comparison) {
  const signed = (value, unit) => `${value >= 0 ? "+" : ""}${value.toFixed(2)}${unit}`;
  const values = [comparison.stock_return_pct, comparison.benchmark_return_pct, comparison.excess_return_pp];
  if (!comparison.success || values.some(value => typeof value !== "number" || !Number.isFinite(value))) return null;
  const excess = comparison.excess_return_pp;
  return {
    stock: signed(values[0], "%"),
    benchmark: signed(values[1], "%"),
    excess: signed(excess, " pp"),
    direction: excess > 0 ? "Outperformed sector benchmark" : excess < 0 ? "Underperformed sector benchmark" : "Matched sector benchmark",
  };
}

export function createSectorComparison(panel) {
  const status = panel.querySelector("[data-sector-status]");
  const content = panel.querySelector("[data-sector-content]");
  let controller = null;
  function clear(message = "Enter a stock to compare with its sector benchmark.") {
    controller?.abort();
    controller = null;
    status.textContent = message;
    status.hidden = false;
    content.hidden = true;
    panel.setAttribute("aria-busy", "false");
  }
  async function load(asset, range) {
    clear();
    if (asset.asset_type !== "stock") {
      status.textContent = "Sector comparison is available for US-listed stocks. This asset has no US equity sector benchmark.";
      return;
    }
    const request = new AbortController();
    controller = request;
    status.textContent = "Loading sector classification and benchmark prices…";
    panel.setAttribute("aria-busy", "true");
    const timeout = setTimeout(() => request.abort(), 50000);
    try {
      const params = new URLSearchParams({ asset: asset.quote_symbol || asset.symbol, range });
      const response = await fetch(`/api/sector-performance?${params}`, { signal: request.signal, cache: "no-store" });
      if (!response.ok) throw new Error(`Sector comparison request failed (${response.status}).`);
      const payload = await response.json();
      if (request !== controller) return;
      const comparison = payload.comparison || {}, labels = sectorMetricLabels(comparison);
      if (!labels) throw new Error(comparison.reason || "Sector comparison is unavailable for this asset.");
      panel.querySelector("[data-sector-identity]").textContent = `${comparison.sector}${comparison.industry ? ` · ${comparison.industry}` : ""}`;
      panel.querySelector("[data-sector-stock-label]").textContent = `${asset.symbol} price return`;
      panel.querySelector("[data-sector-stock]").textContent = labels.stock;
      panel.querySelector("[data-sector-benchmark-label]").textContent = `${comparison.benchmark.symbol} sector proxy`;
      panel.querySelector("[data-sector-benchmark]").textContent = labels.benchmark;
      const excess = panel.querySelector("[data-sector-excess]");
      excess.textContent = labels.excess;
      excess.className = comparison.excess_return_pp < 0 ? "negative" : "positive";
      panel.querySelector("[data-sector-direction]").textContent = labels.direction;
      panel.querySelector("[data-sector-dates]").textContent = `${comparison.start_date} → ${comparison.end_date} · ${comparison.shared_sessions} shared sessions · USD`;
      const link = panel.querySelector("[data-sector-benchmark-link]");
      // The benchmark documentation URL is a fixed provider URL, not arbitrary upstream HTML.
      link.href = "https://www.ssga.com/us/en/intermediary/capabilities/equities/sector-investing/select-sector-etfs";
      link.textContent = `${comparison.benchmark.name} methodology`;
      panel.querySelector("[data-sector-note]").textContent = comparison.note;
      panel.querySelector("[data-sector-retrieved]").textContent = `Classification and prices: Yahoo Finance · Retrieved ${new Date(comparison.retrieved_at).toLocaleString()}`;
      status.hidden = true;
      content.hidden = false;
    } catch (error) {
      if (request !== controller) return;
      status.textContent = request.signal.aborted ? "Sector comparison timed out. Refresh the chart to retry." : error.message;
    } finally {
      clearTimeout(timeout);
      if (request === controller) panel.setAttribute("aria-busy", "false");
    }
  }
  clear();
  return { clear, load };
}
