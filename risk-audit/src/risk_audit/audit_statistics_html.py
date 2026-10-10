"""生成可离线打开的审核统计 HTML 报告。"""

from __future__ import annotations

import json


REPORT_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>审核意见</title>
  <style>
    :root {
      color-scheme: light dark;
      --report-bg: light-dark(#f5f7fa, #0d1521);
      --report-panel: light-dark(#ffffff, #141f2e);
      --report-soft: light-dark(#f6f8fb, #101a28);
      --report-border: light-dark(#e1e7ef, #2a3950);
      --report-text: light-dark(#172033, #eaf0f8);
      --report-muted: light-dark(#68768a, #9baabd);
      --report-blue: light-dark(#155eef, #8ab2ff);
      --report-blue-soft: light-dark(#edf3ff, #172f5c);
      --report-red: light-dark(#b42318, #ff9b9b);
      --report-red-soft: light-dark(#fff1f0, #3a181b);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      overflow: hidden;
      background: var(--report-bg);
      color: var(--report-text);
      font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    }
    button, input { font: inherit; }
    button { cursor: pointer; }
    h1, p { margin: 0; }
    .report-shell {
      height: 100%;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background: var(--report-panel);
    }
    .report-header {
      min-height: 84px;
      display: flex;
      align-items: center;
      gap: 20px;
      padding: 16px 22px;
      border-bottom: 1px solid var(--report-border);
    }
    .report-title { min-width: 0; }
    .report-title h1 { font-size: 24px; font-weight: 600; letter-spacing: -.02em; }
    .report-meta { margin-top: 5px; color: var(--report-muted); font-size: 12px; }
    .report-search {
      width: min(420px, 48%);
      min-height: 42px;
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 12px;
      border: 1px solid var(--report-border);
      border-radius: 10px;
      background: var(--report-soft);
      color: var(--report-muted);
    }
    .report-search:focus-within {
      border-color: var(--report-blue);
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--report-blue) 12%, transparent);
    }
    .report-search-symbol { font-size: 18px; line-height: 1; }
    .report-search input {
      width: 100%;
      border: 0;
      outline: 0;
      background: transparent;
      color: var(--report-text);
    }
    .current-unit {
      flex: none;
      display: grid;
      gap: 7px;
      padding: 13px 22px 14px;
      border-bottom: 1px solid var(--report-border);
    }
    .current-row {
      display: grid;
      grid-template-columns: 64px minmax(0, 1fr);
      align-items: baseline;
      gap: 10px;
    }
    .current-label { color: var(--report-muted); font-size: 11px; }
    .current-name { font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
    .current-path { color: var(--report-muted); font-size: 12px; line-height: 1.55; overflow-wrap: anywhere; }
    .workspace {
      min-height: 0;
      flex: 1;
      display: grid;
      grid-template-columns: 300px 260px minmax(0, 1fr);
      background: var(--report-panel);
    }
    .pane {
      min-width: 0;
      min-height: 0;
      display: flex;
      flex-direction: column;
    }
    .pane + .pane { border-left: 1px solid var(--report-border); }
    .pane-header {
      min-height: 58px;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--report-border);
    }
    .pane-title { min-width: 0; overflow: hidden; font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
    .pane-count { margin-left: auto; color: var(--report-muted); font-size: 11px; white-space: nowrap; }
    .pane-scroll { min-height: 0; flex: 1; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; }
    .item-list { padding: 7px; }
    .item-button {
      width: 100%;
      min-height: 48px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
      padding: 9px 10px;
      border: 0;
      border-radius: 8px;
      background: transparent;
      color: var(--report-text);
      text-align: left;
    }
    .item-button:hover { background: var(--report-soft); }
    .item-button[aria-selected="true"] { background: var(--report-blue-soft); color: var(--report-blue); }
    .item-button.is-failed { color: var(--report-red); }
    .item-name { display: block; min-width: 0; overflow: hidden; font-size: 13px; font-weight: 500; line-height: 1.45; text-overflow: ellipsis; white-space: nowrap; }
    .item-count { color: var(--report-muted); font-size: 11px; white-space: nowrap; }
    .item-button[aria-selected="true"] .item-count { color: currentColor; }
    .records { padding: 7px 22px 12px; }
    .record {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      align-items: start;
      gap: 12px;
      padding: 16px 0;
      border-bottom: 1px solid var(--report-border);
    }
    .record:last-child { border-bottom: 0; }
    .record-label {
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 0 8px;
      border-radius: 999px;
      background: var(--report-blue-soft);
      color: var(--report-blue);
      font-size: 12px;
      white-space: nowrap;
    }
    .record.is-failed .record-label { background: var(--report-red-soft); color: var(--report-red); }
    .record-message { line-height: 1.65; overflow-wrap: anywhere; }
    .record-count { min-width: 48px; padding-top: 3px; font-size: 13px; font-weight: 600; text-align: right; white-space: nowrap; }
    .empty-state { display: none; padding: 42px 18px; color: var(--report-muted); text-align: center; }
    .empty-state.is-visible { display: block; }
    .none-state { padding: 42px 18px; color: var(--report-muted); text-align: center; }
    @media (max-width: 820px) {
      body { overflow: auto; }
      .report-shell { height: auto; min-height: 100%; overflow: visible; }
      .report-header { display: block; }
      .report-search { width: 100%; margin: 14px 0 0; }
      .current-unit { padding: 13px 16px 14px; }
      .current-row { grid-template-columns: 1fr; gap: 3px; }
      .workspace { grid-template-columns: 1fr; }
      .pane + .pane { border-left: 0; border-top: 1px solid var(--report-border); }
      .units-pane .pane-scroll, .businesses-pane .pane-scroll { max-height: 280px; }
      .detail-pane .pane-scroll { overflow: visible; }
    }
    @media (max-width: 480px) {
      .report-header { padding: 16px; }
      .record { grid-template-columns: auto minmax(0, 1fr); }
      .record-count { grid-column: 2; text-align: left; }
    }
  </style>
</head>
<body>
  <div class="report-shell">
    <header class="report-header">
      <div class="report-title">
        <h1>审核意见</h1>
        <p class="report-meta" id="audit-report-meta"></p>
      </div>
      <label class="report-search">
        <span class="report-search-symbol" aria-hidden="true">⌕</span>
        <input id="audit-report-query" aria-label="搜索主体、业务或审核意见" placeholder="搜索主体、业务或审核意见">
      </label>
    </header>
    <section class="current-unit" id="audit-report-current-unit" aria-label="当前主体信息" aria-live="polite">
      <div class="current-row"><span class="current-label">主体名</span><strong class="current-name" id="audit-report-current-name"></strong></div>
      <div class="current-row"><span class="current-label">完整路径</span><span class="current-path" id="audit-report-current-path"></span></div>
    </section>
    <main class="workspace">
      <section class="pane units-pane" aria-label="主体列表">
        <header class="pane-header"><p class="pane-title">主体</p><span class="pane-count" id="audit-report-unit-count"></span></header>
        <div class="pane-scroll"><nav class="item-list" id="audit-report-units"></nav><div class="empty-state" id="audit-report-empty">没有匹配的审核意见</div></div>
      </section>
      <section class="pane businesses-pane" aria-label="业务列表">
        <header class="pane-header"><p class="pane-title">业务</p><span class="pane-count" id="audit-report-business-count"></span></header>
        <div class="pane-scroll"><nav class="item-list" id="audit-report-businesses"></nav></div>
      </section>
      <section class="pane detail-pane" aria-label="规则描述">
        <header class="pane-header"><p class="pane-title">规则描述</p></header>
        <div class="pane-scroll" id="audit-report-detail" aria-live="polite"></div>
      </section>
    </main>
  </div>
  <script type="application/json" id="audit-source-data">__AUDIT_SOURCE_DATA__</script>
  <script>
    "use strict";

    /** 判断完整文本是否包含全部关键词；searchText 为完整文本，keyword 为查询文本。 */
    function matchesSearch(searchText, keyword) {
      const content = String(searchText || "").toLocaleLowerCase("zh-CN");
      const terms = String(keyword || "").trim().toLocaleLowerCase("zh-CN").split(/\s+/).filter(Boolean);
      return terms.every((term) => content.includes(term));
    }

    /** 解析一条审核意见；rawRecord 为统计表原始记录。 */
    function parseRecord(rawRecord) {
      const match = String(rawRecord || "").match(/^【([^】]+)】([\s\S]*?)(?:\s+(\d+)条。?)?$/);
      if (!match) return { label: "", message: String(rawRecord || ""), count: null };
      return { label: match[1], message: match[2], count: match[3] ? Number(match[3]) : null };
    }

    /** 返回路径对应的主体名；path 为完整主体路径。 */
    function unitIdentity(path) {
      const fullPath = String(path || "").trim();
      const pathParts = fullPath.split("/").filter(Boolean);
      return { subjectName: pathParts.at(-1) || fullPath, fullPath };
    }

    const sourceData = JSON.parse(document.getElementById("audit-source-data").textContent);
    const query = document.getElementById("audit-report-query");
    const unitsContainer = document.getElementById("audit-report-units");
    const businessesContainer = document.getElementById("audit-report-businesses");
    const detailContainer = document.getElementById("audit-report-detail");
    const emptyState = document.getElementById("audit-report-empty");
    const unitCount = document.getElementById("audit-report-unit-count");
    const businessCount = document.getElementById("audit-report-business-count");
    const currentUnit = document.getElementById("audit-report-current-unit");
    const currentName = document.getElementById("audit-report-current-name");
    const currentPath = document.getElementById("audit-report-current-path");
    let selectedUnitIndex = 0;
    let selectedBusinessIndex = 0;

    /** 创建带类名和文本的元素；tagName 为标签名，className 为类名，text 为文本。 */
    function createElement(tagName, className, text) {
      const element = document.createElement(tagName);
      if (className) element.className = className;
      if (text !== undefined) element.textContent = text;
      return element;
    }

    /** 统计业务的审核意见类别；business 为业务数据。 */
    function opinionCount(business) {
      return business.records.filter((record) => record.startsWith("【")).length;
    }

    /** 判断业务是否命中查询；unit 为主体，business 为业务，keyword 为查询文本。 */
    function businessMatches(unit, business, keyword) {
      return matchesSearch([unit.path, business.name, ...business.records].join(" "), keyword);
    }

    /** 返回主体下命中的业务索引；unitIndex 为主体索引，keyword 为查询文本。 */
    function matchingBusinessIndexes(unitIndex, keyword) {
      const unit = sourceData.units[unitIndex];
      if (!unit) return [];
      return unit.businesses
        .map((business, businessIndex) => businessMatches(unit, business, keyword) ? businessIndex : -1)
        .filter((businessIndex) => businessIndex >= 0);
    }

    /** 更新顶部主体信息；unitIndex 为主体索引，无匹配时为 null。 */
    function renderCurrentUnit(unitIndex) {
      const unit = unitIndex === null ? null : sourceData.units[unitIndex];
      currentUnit.hidden = !unit;
      if (!unit) {
        currentName.textContent = "";
        currentPath.textContent = "";
        return;
      }
      const identity = unitIdentity(unit.path);
      currentName.textContent = identity.subjectName;
      currentPath.textContent = identity.fullPath;
    }

    /** 渲染主体列表；keyword 为查询文本，返回可见主体索引。 */
    function renderUnits(keyword) {
      unitsContainer.replaceChildren();
      const visibleIndexes = [];
      sourceData.units.forEach((unit, unitIndex) => {
        const businesses = matchingBusinessIndexes(unitIndex, keyword);
        if (businesses.length === 0) return;
        visibleIndexes.push(unitIndex);
        const button = createElement("button", "item-button");
        button.type = "button";
        button.setAttribute("aria-selected", String(unitIndex === selectedUnitIndex));
        button.setAttribute("aria-label", unit.path);
        button.append(
          createElement("span", "item-name", unitIdentity(unit.path).subjectName),
          createElement("span", "item-count", `${businesses.length} 项`),
        );
        button.addEventListener("click", () => {
          selectedUnitIndex = unitIndex;
          selectedBusinessIndex = businesses[0];
          renderAll();
        });
        unitsContainer.append(button);
      });
      unitCount.textContent = keyword.trim() ? `${visibleIndexes.length} 个匹配` : `${sourceData.units.length} 个`;
      emptyState.classList.toggle("is-visible", visibleIndexes.length === 0);
      return visibleIndexes;
    }

    /** 渲染当前主体业务；keyword 为查询文本，返回可见业务索引。 */
    function renderBusinesses(keyword) {
      businessesContainer.replaceChildren();
      const unit = sourceData.units[selectedUnitIndex];
      if (!unit) return [];
      const businessIndexes = matchingBusinessIndexes(selectedUnitIndex, keyword);
      businessCount.textContent = `${businessIndexes.length} 项`;
      businessIndexes.forEach((businessIndex) => {
        const business = unit.businesses[businessIndex];
        const failed = business.records.some((record) => record.startsWith("【审核状态】") && record.includes("未完成"));
        const button = createElement("button", `item-button${failed ? " is-failed" : ""}`);
        button.type = "button";
        button.setAttribute("aria-selected", String(businessIndex === selectedBusinessIndex));
        const countText = business.records.length === 1 && business.records[0] === "无" ? "无" : `${opinionCount(business)} 类`;
        button.append(createElement("span", "item-name", business.name), createElement("span", "item-count", countText));
        button.addEventListener("click", () => {
          selectedBusinessIndex = businessIndex;
          renderBusinesses(query.value);
          renderDetail();
        });
        businessesContainer.append(button);
      });
      return businessIndexes;
    }

    /** 渲染当前业务的规则列表；无参数。 */
    function renderDetail() {
      detailContainer.replaceChildren();
      const unit = sourceData.units[selectedUnitIndex];
      const business = unit && unit.businesses[selectedBusinessIndex];
      if (!business) return;
      if (business.records.length === 1 && business.records[0] === "无") {
        detailContainer.append(createElement("div", "none-state", "无"));
        return;
      }
      const records = createElement("div", "records");
      business.records.forEach((rawRecord) => {
        const parsed = parseRecord(rawRecord);
        const failed = parsed.label === "审核状态" && parsed.message.includes("未完成");
        const record = createElement("div", `record${failed ? " is-failed" : ""}`);
        record.append(
          createElement("span", "record-label", parsed.label),
          createElement("span", "record-message", parsed.message),
          createElement("span", "record-count", parsed.count === null ? "" : `${parsed.count} 条`),
        );
        records.append(record);
      });
      detailContainer.append(records);
    }

    /** 按当前查询统一刷新三栏；无参数。 */
    function renderAll() {
      const keyword = query.value;
      let visibleUnitIndexes = sourceData.units
        .map((unit, unitIndex) => matchingBusinessIndexes(unitIndex, keyword).length > 0 ? unitIndex : -1)
        .filter((unitIndex) => unitIndex >= 0);
      if (visibleUnitIndexes.length === 0) {
        renderUnits(keyword);
        renderCurrentUnit(null);
        businessesContainer.replaceChildren();
        detailContainer.replaceChildren();
        businessCount.textContent = "0 项";
        return;
      }
      if (!visibleUnitIndexes.includes(selectedUnitIndex)) selectedUnitIndex = visibleUnitIndexes[0];
      const businessIndexes = matchingBusinessIndexes(selectedUnitIndex, keyword);
      if (!businessIndexes.includes(selectedBusinessIndex)) selectedBusinessIndex = businessIndexes[0];
      renderCurrentUnit(selectedUnitIndex);
      visibleUnitIndexes = renderUnits(keyword);
      renderBusinesses(keyword);
      renderDetail();
    }

    const allBusinesses = sourceData.units.reduce((total, unit) => total + unit.businesses.length, 0);
    const allOpinions = sourceData.units.reduce((unitTotal, unit) => unitTotal + unit.businesses.reduce(
      (businessTotal, business) => businessTotal + opinionCount(business), 0,
    ), 0);
    document.getElementById("audit-report-meta").textContent = `${sourceData.units.length} 个主体 · ${allBusinesses} 项业务 · ${allOpinions} 类审核意见`;
    query.addEventListener("input", renderAll);
    renderAll();
  </script>
</body>
</html>
'''


def render_audit_statistics_html(source_data: dict[str, object]) -> str:
    """渲染完整 HTML 文档；source_data 为主体、业务和意见组成的报告数据。"""
    payload = json.dumps(source_data, ensure_ascii=False, separators=(",", ":"))
    # 转义 HTML 特殊字符，防止目录名或意见文本提前闭合 JSON script 标签。
    safe_payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return REPORT_TEMPLATE.replace("__AUDIT_SOURCE_DATA__", safe_payload)
