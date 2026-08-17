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

// ---------------- 领星 ERP 数据接入 ----------------
function lxMsg(text, isErr) {
  const el = document.getElementById("lxMsg");
  el.textContent = text || "";
  el.className = "msg" + (isErr ? " err" : " ok");
}

async function loadLingxingStatus() {
  try {
    const s = await getJSON("/api/lingxing/status");
    const badge = document.getElementById("lxStatusBadge");
    badge.textContent = s.configured ? "● 已配置" : "○ 未配置（可走 Excel 上传兜底）";
    badge.className = "sub" + (s.configured ? " ok" : " warn");
    document.getElementById("lxLastSync").textContent = "上次同步：" + (s.last_sync || "从未");
    // 回填表单
    document.getElementById("lxAppId").value = s.app_id_set ? "（已保存）" : "";
    document.getElementById("lxHost").value = s.host;
    document.getElementById("lxSidsAmazon").value = s.sids_amazon;
    document.getElementById("lxSidsWalmart").value = s.sids_walmart;
    document.getElementById("lxSidsOther").value = s.sids_other;
    document.getElementById("lxSidsTemu").value = s.sids_temu;
    document.getElementById("lxAutoSync").value = s.auto_sync;
    document.getElementById("lxSyncHour").value = s.auto_sync_hour;
  } catch (e) {
    console.error(e);
  }
}

document.getElementById("lxSyncBtn").addEventListener("click", async () => {
  const btn = document.getElementById("lxSyncBtn");
  btn.disabled = true; lxMsg("同步中…");
  try {
    const res = await getJSON("/api/data/sync/lingxing");
    if (res.ok) {
      const warns = (res.warnings || []).length ? "；警告：" + res.warnings.join("；") : "";
      lxMsg("同步完成：" + (res.steps || []).join("，") + warns);
      loadAll();
    } else {
      lxMsg("同步失败：" + (res.error || "未知错误") + (res.detail ? "（" + res.detail + "）" : ""), true);
    }
  } catch (e) {
    lxMsg("请求失败：" + e.message, true);
  } finally {
    btn.disabled = false;
    loadLingxingStatus();
  }
});

document.getElementById("lxTestBtn").addEventListener("click", async () => {
  lxMsg("测试连接中…");
  try {
    const res = await fetch(API + "/api/lingxing/test", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    }).then(r => r.json());
    if (res.ok) lxMsg("连接成功，凭证有效。");
    else lxMsg("连接失败：" + (res.error || ""), true);
  } catch (e) {
    lxMsg("请求失败：" + e.message, true);
  }
});

document.getElementById("lxSaveBtn").addEventListener("click", async () => {
  const payload = {
    lingxing_app_id: document.getElementById("lxAppId").value || undefined,
    lingxing_app_secret: document.getElementById("lxAppSecret").value || undefined,
    lingxing_host: document.getElementById("lxHost").value,
    lingxing_sids_amazon: document.getElementById("lxSidsAmazon").value,
    lingxing_sids_walmart: document.getElementById("lxSidsWalmart").value,
    lingxing_sids_other: document.getElementById("lxSidsOther").value,
    lingxing_sids_temu: document.getElementById("lxSidsTemu").value,
    lingxing_auto_sync: document.getElementById("lxAutoSync").value,
    lingxing_auto_sync_hour: document.getElementById("lxSyncHour").value,
  };
  try {
    const res = await fetch(API + "/api/lingxing/config", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }).then(r => r.json());
    if (res.ok) lxMsg("设置已保存。");
    else lxMsg("保存失败。", true);
  } catch (e) {
    lxMsg("请求失败：" + e.message, true);
  }
  loadLingxingStatus();
});

loadAll().catch(e => alert("加载失败: " + e.message));
loadLingxingStatus();
