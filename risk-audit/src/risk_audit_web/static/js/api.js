"use strict";

/** 从启动 URL 保存会话令牌并清理地址栏；无参数，返回令牌或 null。 */
function initializeSessionToken() {
  const url = new URL(window.location.href);
  const queryToken = url.searchParams.get("token");
  if (queryToken) {
    sessionStorage.setItem("riskAuditToken", queryToken);
    url.searchParams.delete("token");
    history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }
  return sessionStorage.getItem("riskAuditToken");
}

/** 显示会话失效提示；无参数和返回值。 */
function showSessionExpiredMessage() {
  const alert = document.getElementById("session-alert");
  alert.textContent = "本机会话已失效，请重新双击风控矩阵审核器打开页面。";
  alert.classList.remove("d-none");
}

/** 解析 API 响应；response 为 Fetch Response，返回 JSON 数据或抛出稳定错误。 */
async function parseApiResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.message || "本机服务请求失败");
    error.code = payload.error_code || "REQUEST_FAILED";
    error.details = payload.details || {};
    throw error;
  }
  return payload;
}

/** 调用本机 API；path 为同源路径，options 为 Fetch 选项，返回解析后数据。 */
async function apiRequest(path, options = {}) {
  const token = sessionStorage.getItem("riskAuditToken");
  const headers = { "X-Local-Token": token || "", ...(options.headers || {}) };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    stopPolling();
    showSessionExpiredMessage();
    throw new Error("SESSION_EXPIRED");
  }
  return parseApiResponse(response);
}

window.initializeSessionToken = initializeSessionToken;
window.apiRequest = apiRequest;
window.showSessionExpiredMessage = showSessionExpiredMessage;
