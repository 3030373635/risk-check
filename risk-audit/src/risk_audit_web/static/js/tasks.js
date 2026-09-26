"use strict";

let pollingTimer = null;
let currentTaskId = null;

const statusLabels = {
  running: "审核中", cancelling: "停止中", completed: "已完成", partial: "部分完成",
  failed: "失败", cancelled: "已取消", interrupted: "意外中断",
};

/** 停止当前页面轮询；无参数。 */
function stopPolling() {
  if (pollingTimer !== null) {
    window.clearInterval(pollingTimer);
    pollingTimer = null;
  }
}

/** 生成任务状态标签；status 为后端状态值，返回 span 元素。 */
function statusBadge(status) {
  const badge = document.createElement("span");
  badge.className = `status-badge status-${status}`;
  badge.textContent = statusLabels[status] || status;
  return badge;
}

/** 生成任务进度单元格；task 为任务响应，返回容器元素。 */
function progressCell(task) {
  const wrapper = document.createElement("div");
  wrapper.className = "task-progress";
  const progress = document.createElement("progress");
  progress.className = "audit-progress";
  progress.max = 100;
  const value = Number.isInteger(task.progress_percent) ? task.progress_percent : 0;
  progress.value = value;
  progress.setAttribute("aria-label", "任务进度");
  const label = document.createElement("small");
  label.textContent = Number.isInteger(task.progress_percent) ? `${task.progress_percent}%` : "扫描中";
  wrapper.append(progress, label);
  return wrapper;
}

/** 生成任务名称单元格；task 为任务响应，返回容器元素。 */
function taskNameCell(task) {
  const wrapper = document.createElement("div");
  const name = document.createElement("div");
  name.className = "task-name";
  name.textContent = task.display_name;
  const identifier = document.createElement("div");
  identifier.className = "task-id";
  identifier.textContent = task.task_id;
  wrapper.append(name, identifier);
  return wrapper;
}

/** 渲染任务列表；tasks 为任务数组。 */
function renderTasks(tasks) {
  const body = document.getElementById("tasks-table-body");
  const empty = document.getElementById("tasks-empty-state");
  body.replaceChildren();
  empty.classList.toggle("d-none", tasks.length !== 0);
  for (const task of tasks) {
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    nameCell.appendChild(taskNameCell(task));
    const statusCell = document.createElement("td");
    statusCell.appendChild(statusBadge(task.status));
    const createdCell = document.createElement("td");
    createdCell.textContent = formatDate(task.created_at);
    const stageCell = document.createElement("td");
    stageCell.textContent = task.stage || "—";
    const progress = document.createElement("td");
    progress.appendChild(progressCell(task));
    const actionCell = document.createElement("td");
    actionCell.className = "text-end";
    const button = document.createElement("button");
    button.className = "btn btn-sm btn-light";
    button.type = "button";
    button.textContent = "查看详情";
    button.addEventListener("click", () => showTaskDetail(task.task_id));
    actionCell.appendChild(button);
    row.append(nameCell, statusCell, createdCell, stageCell, progress, actionCell);
    body.appendChild(row);
  }
}

/** 格式化 ISO 时间；value 为时间文本，返回本地化文本。 */
function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

/** 读取并渲染任务列表；无参数。 */
async function loadTasks() {
  try {
    const payload = await apiRequest("/api/v1/tasks");
    renderTasks(payload.tasks || []);
    document.getElementById("metric-running").textContent = String(payload.running_count || 0);
    document.getElementById("sidebar-running-count").textContent = String(payload.running_count || 0);
    const today = new Date().toDateString();
    const completed = (payload.tasks || []).filter((task) => task.status === "completed" && new Date(task.created_at).toDateString() === today).length;
    const review = (payload.tasks || []).reduce((total, task) => total + Number(task.result_summary?.warnings || 0), 0);
    document.getElementById("metric-completed").textContent = String(completed);
    document.getElementById("metric-review").textContent = String(review);
  } catch (error) {
    if (error.message !== "SESSION_EXPIRED") showToast(error.message);
  }
}

/** 安全更新文本；id 为元素编号，value 为待显示值。 */
function setText(id, value) {
  document.getElementById(id).textContent = value ?? "—";
}

/** 渲染单任务详情；task 为任务响应。 */
function renderTaskDetail(task) {
  currentTaskId = task.task_id;
  setText("detail-title", task.display_name);
  setText("detail-task-id", task.task_id);
  const status = document.getElementById("detail-status");
  status.className = `status-badge status-${task.status}`;
  status.textContent = statusLabels[task.status] || task.status;
  setText("detail-stage", task.stage);
  setText("detail-message", task.message);
  setText("detail-entity", task.current_entity);
  setText("detail-business", task.current_business);
  setText("detail-file", task.current_file);
  setText("detail-input-root", task.input_root);
  setText("detail-output-root", task.output_root);
  setText("detail-created-at", formatDate(task.created_at));
  const progress = Number.isInteger(task.progress_percent) ? task.progress_percent : 0;
  document.getElementById("detail-progress-bar").value = progress;
  setText("detail-progress-text", Number.isInteger(task.progress_percent) ? `${progress}%` : "扫描中");
  const summary = task.result_summary || {};
  setText("result-files", summary.input_files);
  setText("result-findings", summary.findings);
  setText("result-warnings", summary.warnings);
  setText("result-limitations", summary.limitations);
  document.getElementById("cancel-task-button").disabled = !["running", "cancelling"].includes(task.status);
  document.getElementById("open-statistics-button").disabled = !summary.audit_statistics_report;
  document.getElementById("view-unaudited-files-button").disabled = !summary.unaudited_files_report;
}

/** 刷新当前任务详情；无参数。 */
async function loadTaskDetail() {
  if (!currentTaskId) return;
  try {
    renderTaskDetail(await apiRequest(`/api/v1/tasks/${encodeURIComponent(currentTaskId)}`));
  } catch (error) {
    if (error.message !== "SESSION_EXPIRED") showToast(error.message);
  }
}

/** 打开指定任务详情；taskId 为任务编号。 */
async function showTaskDetail(taskId) {
  currentTaskId = taskId;
  showPage("detail-page");
  await loadTaskDetail();
}

/** 启动任务列表轮询；无参数。 */
function startTasksPolling() {
  stopPolling();
  loadTasks();
  pollingTimer = window.setInterval(loadTasks, 1000);
}

/** 启动任务详情轮询；无参数。 */
function startDetailPolling() {
  stopPolling();
  loadTaskDetail();
  pollingTimer = window.setInterval(loadTaskDetail, 1000);
}

window.stopPolling = stopPolling;
window.startTasksPolling = startTasksPolling;
window.startDetailPolling = startDetailPolling;
window.showTaskDetail = showTaskDetail;
window.loadTasks = loadTasks;
window.currentTaskIdValue = () => currentTaskId;
