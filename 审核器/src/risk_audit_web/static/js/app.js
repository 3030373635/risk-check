"use strict";

let systemStatus = null;

/** 显示短暂消息；message 为用户可读文本。 */
function showToast(message) {
  document.getElementById("toast-message").textContent = message;
  bootstrap.Toast.getOrCreateInstance(document.getElementById("app-toast"), { delay: 4200 }).show();
}

/** 切换单页区域；pageId 为目标 section 编号。 */
function showPage(pageId) {
  for (const page of document.querySelectorAll(".page")) page.classList.toggle("active", page.id === pageId);
  for (const link of document.querySelectorAll("[data-page]")) link.classList.toggle("active", link.dataset.page === pageId && link.classList.contains("nav-link"));
  document.querySelector(".sidebar").classList.remove("open");
  if (pageId === "tasks-page") startTasksPolling();
  else if (pageId === "detail-page") startDetailPolling();
  else stopPolling();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/** 生成诊断列表项；item 为后端诊断记录，返回 li 元素。 */
function diagnosticItem(item) {
  const row = document.createElement("li");
  const icon = document.createElement("i");
  icon.className = `bi ${item.ok ? "bi-check-circle-fill ok" : "bi-x-circle-fill error"}`;
  icon.setAttribute("aria-hidden", "true");
  const content = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = item.ok ? "检查通过" : "需要处理";
  const message = document.createElement("small");
  message.textContent = item.message;
  content.append(title, message);
  row.append(icon, content);
  return row;
}

/** 读取并渲染本机服务状态；无参数。 */
async function loadSystemStatus() {
  try {
    systemStatus = await apiRequest("/api/v1/system/status");
    document.getElementById("service-status-dot").classList.add("online");
    document.getElementById("service-status-text").textContent = "本机服务运行正常";
    document.getElementById("rule-version").textContent = systemStatus.rule_version || "不可用";
    document.getElementById("sidebar-running-count").textContent = String(systemStatus.running_count || 0);
    const list = document.getElementById("diagnostic-list");
    list.replaceChildren(...(systemStatus.diagnostics || []).map(diagnosticItem));
    updateCreateButton();
  } catch (error) {
    if (error.message !== "SESSION_EXPIRED") {
      document.getElementById("service-status-text").textContent = "本机服务连接失败";
      showToast(error.message);
    }
  }
}

/** 根据表单与运行资源刷新创建按钮；无参数。 */
function updateCreateButton() {
  const hasInput = Boolean(document.getElementById("input-directory").value);
  const hasName = Boolean(document.getElementById("task-name").value.trim());
  document.getElementById("create-task-button").disabled = !(hasInput && hasName && systemStatus?.can_create_task);
}

/** 打开本机目录选择器并预览输出路径；无参数。 */
async function selectInputDirectory() {
  const button = document.getElementById("input-directory-button");
  button.disabled = true;
  try {
    const selection = await apiRequest("/api/v1/input-directory-selections", { method: "POST", body: "{}" });
    if (!selection.selected) return;
    document.getElementById("input-directory").value = selection.path;
    const name = selection.path.split(/[\\/]/).filter(Boolean).pop() || "审核任务";
    if (!document.getElementById("task-name").value.trim()) document.getElementById("task-name").value = name;
    const preview = await apiRequest("/api/v1/output-path-previews", {
      method: "POST", body: JSON.stringify({ input_root: selection.path }),
    });
    document.getElementById("output-path-preview").value = preview.output_root;
  } catch (error) {
    if (error.message !== "SESSION_EXPIRED") showToast(error.message);
  } finally {
    button.disabled = false;
    updateCreateButton();
  }
}

/** 提交新任务；event 为表单提交事件。 */
async function createTask(event) {
  event.preventDefault();
  const button = document.getElementById("create-task-button");
  button.disabled = true;
  try {
    const task = await apiRequest("/api/v1/tasks", {
      method: "POST",
      body: JSON.stringify({
        display_name: document.getElementById("task-name").value.trim(),
        input_root: document.getElementById("input-directory").value,
      }),
    });
    document.getElementById("output-path-preview").value = task.output_root;
    showToast("任务已创建，审核正在后台运行");
    showTaskDetail(task.task_id);
  } catch (error) {
    if (error.message !== "SESSION_EXPIRED") showToast(error.message);
  } finally {
    updateCreateButton();
  }
}

/** 调用当前任务动作；suffix 为 REST 子资源名。 */
async function postTaskAction(suffix) {
  const taskId = currentTaskIdValue();
  if (!taskId) return;
  try {
    await apiRequest(`/api/v1/tasks/${encodeURIComponent(taskId)}/${suffix}`, { method: "POST", body: "{}" });
    await loadTaskDetail();
  } catch (error) {
    if (error.message !== "SESSION_EXPIRED") showToast(error.message);
  }
}

/** 请求立即退出或安全停止后退出；无参数。 */
async function requestShutdown() {
  const running = Number(document.getElementById("sidebar-running-count").textContent || 0);
  const mode = running > 0 ? "cancel_active_tasks" : "immediate";
  try {
    await apiRequest("/api/v1/shutdown-requests", { method: "POST", body: JSON.stringify({ mode }) });
    stopPolling();
    document.getElementById("session-alert").textContent = running > 0 ? "正在安全停止全部任务，完成后将退出。" : "本机服务正在退出，可以关闭此页面。";
    document.getElementById("session-alert").classList.remove("d-none");
    bootstrap.Modal.getOrCreateInstance(document.getElementById("shutdown-modal")).hide();
  } catch (error) {
    showToast(error.message);
  }
}

/** 绑定页面交互事件；无参数。 */
function bindEvents() {
  for (const control of document.querySelectorAll("[data-page]")) control.addEventListener("click", () => showPage(control.dataset.page));
  document.getElementById("menu-button").addEventListener("click", () => document.querySelector(".sidebar").classList.toggle("open"));
  document.getElementById("refresh-tasks-button").addEventListener("click", loadTasks);
  document.getElementById("input-directory-button").addEventListener("click", selectInputDirectory);
  document.getElementById("task-name").addEventListener("input", updateCreateButton);
  document.getElementById("create-task-form").addEventListener("submit", createTask);
  document.getElementById("cancel-task-button").addEventListener("click", () => postTaskAction("cancellations"));
  document.getElementById("open-output-button").addEventListener("click", () => postTaskAction("output-openings"));
  document.getElementById("open-statistics-button").addEventListener("click", () => postTaskAction("statistics-openings"));
  document.getElementById("open-review-button").addEventListener("click", () => postTaskAction("review-openings"));
  document.getElementById("shutdown-button").addEventListener("click", () => {
    const running = Number(document.getElementById("sidebar-running-count").textContent || 0);
    document.getElementById("shutdown-message").textContent = running > 0 ? `仍有 ${running} 个任务运行。确认全部安全停止后退出？` : "确认关闭本机审核服务？";
    bootstrap.Modal.getOrCreateInstance(document.getElementById("shutdown-modal")).show();
  });
  document.getElementById("confirm-shutdown-button").addEventListener("click", requestShutdown);
}

document.addEventListener("DOMContentLoaded", async () => {
  initializeSessionToken();
  bindEvents();
  await loadSystemStatus();
  startTasksPolling();
});

window.showPage = showPage;
window.showToast = showToast;
