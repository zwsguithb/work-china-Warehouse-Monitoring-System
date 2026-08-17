// 中国仓库存监控系统 - 前端看板逻辑
const API = "";

async function getJSON(url) {
  const res = await fetch(API + url);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function fillTable(tableId, rows, cols) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.innerHTML = "";
  rows.forEach((r, i) => {
    const tr = document.createElement("tr");
    cols.forEach(c => {
      const td = document.createElement("td");
      let v = r[c.key];
      if (c.fmt) v = c.fmt(v, r);
      td.textContent = (v === null || v === undefined) ? "——" : v;
      if (c.warn && c.warn(r)) td.className = "warn";
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

function renderCounts(elId, counts) {
  const el = document.getElementById(elId);
  el.innerHTML = "";
  Object.entries(counts).forEach(([k, v]) => {
    const span = document.createElement("span");
    span.className = "countchip";
    span.textContent = `${k}: ${v}`;
    if (v > 0) span.classList.add("alert");
    el.appendChild(span);
  });
}

async function loadAll() {
  // 淘汰款
  const elim = await getJSON("/api/dashboard/eliminated");
  fillTable("elimInvTable", elim.top_inventory, [
    { key: "rank" }, { key: "style_code" },
    { key: "total_inventory" }, { key: "sellable_days" }, { key: "total_sales" },
  ]);
  fillTable("elim60Table", elim.top_60day, [
    { key: "rank" }, { key: "style_code" },
    { key: "sales_60d" }, { key: "total_inventory" },
  ]);
  // 给淘汰表加排名
  addRank("elimInvTable"); addRank("elim60Table");

  // temu
  const temu = await getJSON("/api/dashboard/temu");
  fillTable("temuTable", temu.items, [
    { key: "style_code" }, { key: "temu_sales" },
    { key: "temu_inventory" }, { key: "sellable_days" },
  ]);
  addRank("temuTable");

  // 亚马逊
  const amazon = await getJSON("/api/dashboard/amazon");
  fillTable("amazonTable", amazon.items, [
    { key: "style_code" }, { key: "amazon_recent_sales" },
    { key: "china_inventory" }, { key: "sellable_days" },
  ]);
  addRank("amazonTable");

  // 低库存 SKU
  const sku = await getJSON("/api/dashboard/low-stock/sku");
  renderCounts("skuCounts", sku.counts);
  fillTable("lowSkuTable", sku.items, [
    { key: "sku" }, { key: "style_code" }, { key: "lifecycle" },
    { key: "sellable_days", warn: r => r.sellable_days < r.threshold },
    { key: "threshold" }, { key: "z" },
  ]);

  // 低库存 款号
  const style = await getJSON("/api/dashboard/low-stock/style");
  renderCounts("styleCounts", style.counts);
  fillTable("lowStyleTable", style.items, [
    { key: "style_code" }, { key: "lifecycle" },
    { key: "sellable_days", warn: r => r.sellable_days < r.threshold },
    { key: "threshold" }, { key: "z" },
  ]);
}

function addRank(tableId) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  Array.from(tbody.children).forEach((tr, i) => {
    const td = document.createElement("td");
    td.textContent = i + 1;
    tr.insertBefore(td, tr.firstChild);
  });
}

// Tab 切换
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    document.getElementById("tab-sku").style.display = tab === "sku" ? "" : "none";
    document.getElementById("tab-style").style.display = tab === "style" ? "" : "none";
  });
});

document.getElementById("refreshBtn").addEventListener("click", loadAll);
document.getElementById("exportSkuBtn").addEventListener("click", () => {
  window.location.href = "/api/dashboard/low-stock/sku?export=true";
});

loadAll().catch(e => alert("加载失败: " + e.message));
