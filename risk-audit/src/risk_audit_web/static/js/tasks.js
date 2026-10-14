"use strict";

let pollingTimer = null;
let currentTaskId = null;
let renderedTasks = [];
let selectedTaskIds = new Set();
let batchDeleteInProgress = false;

const terminalTaskStatuses = new Set([
  "completed", "partial", "failed", "cancelled", "interrupted",
]);

const statusLabels = {
  running: "审核中", cancelling: "停止中", completed: "已完成", partial: "部分完成",
  failed: "失败", cancelled: "已取消", interrupted: "意外中断",
};

/** 判断任务能否删除；task 为任务响应，返回布尔值。 */
function isTaskDeletable(task) {
  return Boolean(task && terminalTaskStatuses.has(task.status));
}

/** 清除已失效或不可删除的选择；selectedIds/tasks 为原选择和最新任务。 */
function reconcileTaskSelection(selectedIds, tasks) {
  const deletableIds = new Set(
    tasks.filter(isTaskDeletable).map((task) => task.task_id),
  );
  return new Set(Array.from(selectedIds).filter((taskId) => deletableIds.has(taskId)));
}

/** 计算表头复选框状态；selectedIds/tasks 为当前选择和最新任务。 */
function taskSelectionState(selectedIds, tasks) {
  const deletableIds = tasks.filter(isTaskDeletable).map((task) => task.task_id);
  const selectedCount = deletableIds.filter((taskId) => selectedIds.has(taskId)).length;
  return {
    checked: deletableIds.length > 0 && selectedCount === deletableIds.length,
    indeterminate: selectedCount > 0 && selectedCount < deletableIds.length,
    selectedCount,
    deletableCount: deletableIds.length,
  };
}

/** 读取当前焦点对应的任务复选框编号；element 为当前焦点元素。 */
function focusedTaskCheckboxId(element) {
  if (!element?.classList?.contains("task-select-checkbox")) return null;
  return element.dataset.taskId || null;
}

/** 将焦点恢复到指定任务的复选框；taskId 为任务编号。 */
function restoreTaskCheckboxFocus(taskId) {
  if (!taskId) return;
  const checkbox = Array.from(document.querySelectorAll(".task-select-checkbox"))
    .find((control) => control.dataset.taskId === taskId);
  if (checkbox && !checkbox.disabled) checkbox.focus();
}

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

/** 生成任务选择单元格；task 为任务响应，返回 td 元素。 */
function taskSelectionCell(task) {
  const cell = document.createElement("td");
  cell.className = "task-selection-cell";
  const checkbox = document.createElement("input");
  checkbox.className = "form-check-input task-select-checkbox";
  checkbox.type = "checkbox";
  checkbox.dataset.taskId = task.task_id;
  checkbox.setAttribute("aria-label", `选择任务 ${task.display_name}`);
  checkbox.disabled = !isTaskDeletable(task);
  checkbox.checked = selectedTaskIds.has(task.task_id);
  if (checkbox.disabled) checkbox.title = "任务结束后才能删除";
  checkbox.addEventListener("change", () => {
    if (batchDeleteInProgress) return;
    if (checkbox.checked) selectedTaskIds.add(task.task_id);
    else selectedTaskIds.delete(task.task_id);
    updateTaskSelectionUi();
  });
  cell.appendChild(checkbox);
  return cell;
}

/** 更新选择行、全选框和批量操作栏；无参数。 */
function updateTaskSelectionUi() {
  const state = taskSelectionState(selectedTaskIds, renderedTasks);
  const hasSelection = state.selectedCount > 0;
  const selectAll = document.getElementById("select-all-tasks");
  selectAll.checked = state.checked;
  selectAll.indeterminate = state.indeterminate;
  selectAll.disabled = state.deletableCount === 0;
  document.getElementById("selected-tasks-count").textContent = `已选择 ${state.selectedCount} 个任务`;
  const defaultActions = document.getElementById("tasks-default-actions");
  const bulkActions = document.getElementById("tasks-bulk-actions");
  defaultActions.classList.toggle("is-hidden", hasSelection);
  defaultActions.setAttribute("aria-hidden", String(hasSelection));
  bulkActions.classList.toggle("is-hidden", !hasSelection);
  bulkActions.setAttribute("aria-hidden", String(!hasSelection));
  document.getElementById("delete-selected-tasks-button").disabled = state.selectedCount === 0;
  for (const checkbox of document.querySelectorAll(".task-select-checkbox")) {
    checkbox.checked = selectedTaskIds.has(checkbox.dataset.taskId);
    checkbox.closest("tr").classList.toggle("task-row-selected", checkbox.checked);
  }
}

/** 渲染任务列表；tasks 为任务数组。 */
function renderTasks(tasks) {
  const body = document.getElementById("tasks-table-body");
  const empty = document.getElementById("tasks-empty-state");
  const focusedTaskId = focusedTaskCheckboxId(document.activeElement);
  renderedTasks = tasks;
  // 每次轮询只保留仍存在且处于终态的选择。
  selectedTaskIds = reconcileTaskSelection(selectedTaskIds, tasks);
  body.replaceChildren();
  empty.classList.toggle("d-none", tasks.length !== 0);
  for (const task of tasks) {
    const row = document.createElement("tr");
    const selectionCell = taskSelectionCell(task);
    const nameCell = document.createElement("td");
    nameCell.appendChild(taskNameCell(task));
    const regionCell = document.createElement("td");
    regionCell.textContent = task.region_name || "—";
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
    row.append(selectionCell, nameCell, regionCell, statusCell, createdCell, stageCell, progress, actionCell);
    body.appendChild(row);
  }
  updateTaskSelectionUi();
  // 列表每秒重绘后恢复同一复选框的焦点，保证键盘操作连续。
  restoreTaskCheckboxFocus(focusedTaskId);
}

/** 切换全部可删除任务的选择；event 为表头复选框变更事件。 */
function toggleAllTaskSelection(event) {
  if (batchDeleteInProgress) return;
  const shouldSelect = event.currentTarget.checked;
  for (const task of renderedTasks.filter(isTaskDeletable)) {
    if (shouldSelect) selectedTaskIds.add(task.task_id);
    else selectedTaskIds.delete(task.task_id);
  }
  updateTaskSelectionUi();
}

/** 清空任务选择；无参数。 */
function clearTaskSelection() {
  if (batchDeleteInProgress) return;
  selectedTaskIds.clear();
  updateTaskSelectionUi();
}

/** 显示批量删除确认框；无参数。 */
function showBatchDeleteModal() {
  if (batchDeleteInProgress) return;
  const state = taskSelectionState(selectedTaskIds, renderedTasks);
  if (state.selectedCount === 0) return;
  document.getElementById("batch-delete-task-count").textContent = String(state.selectedCount);
  document.getElementById("batch-delete-progress").classList.add("d-none");
  bootstrap.Modal.getOrCreateInstance(document.getElementById("batch-delete-tasks-modal")).show();
}

/** 阻止删除执行期间关闭确认弹窗；event 为 Bootstrap 弹窗关闭事件。 */
function preventBatchDeleteDismiss(event) {
  if (batchDeleteInProgress) event.preventDefault();
}

/** 计算当前页面对应的轮询类型；tasksActive/detailActive 表示页面是否可见。 */
function pagePollingMode(tasksActive, detailActive) {
  if (tasksActive) return "tasks";
  if (detailActive) return "detail";
  return null;
}

/** 恢复当前可见页面的轮询；无参数。 */
function resumeVisiblePagePolling() {
  const mode = pagePollingMode(
    document.getElementById("tasks-page").classList.contains("active"),
    document.getElementById("detail-page").classList.contains("active"),
  );
  if (mode === "tasks") startTasksPolling();
  else if (mode === "detail") startDetailPolling();
}

/** 切换批量删除执行状态；isBusy 表示是否正在发送删除请求。 */
function setBatchDeleteBusy(isBusy) {
  batchDeleteInProgress = isBusy;
  const confirmButton = document.getElementById("confirm-batch-delete-button");
  const cancelButton = document.getElementById("cancel-batch-delete-button");
  const closeButton = document.getElementById("close-batch-delete-button");
  const progress = document.getElementById("batch-delete-progress");
  confirmButton.disabled = isBusy;
  cancelButton.disabled = isBusy;
  closeButton.disabled = isBusy;
  confirmButton.textContent = isBusy ? "正在删除…" : "确认删除";
  progress.classList.toggle("d-none", !isBusy);
}

/** 执行可部分成功的批量删除。
 * taskIds 为任务编号数组，deleteTask 为单任务删除函数，onProgress 为进度回调。
 */
async function deleteTaskBatch(taskIds, deleteTask, onProgress = () => {}) {
  const deletedTaskIds = [];
  const failedTaskIds = [];
  let sessionExpired = false;
  for (const [index, taskId] of taskIds.entries()) {
    onProgress(index + 1, taskIds.length);
    try {
      await deleteTask(taskId);
      deletedTaskIds.push(taskId);
    } catch (error) {
      failedTaskIds.push(taskId);
      if (error.message === "SESSION_EXPIRED") {
        sessionExpired = true;
        break;
      }
    }
  }
  return { deletedTaskIds, failedTaskIds, sessionExpired };
}

/** 逐个删除所选任务并保留失败项；无参数。 */
async function deleteSelectedTasks() {
  const taskIds = Array.from(selectedTaskIds);
  if (taskIds.length === 0) return;
  const progress = document.getElementById("batch-delete-progress");
  const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById("batch-delete-tasks-modal"));
  let sessionExpired = false;
  setBatchDeleteBusy(true);
  stopPolling();
  try {
    const result = await deleteTaskBatch(
      taskIds,
      async (taskId) => {
        await apiRequest(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
      },
      (current, total) => {
        progress.textContent = `正在删除 ${current}/${total}`;
      },
    );
    sessionExpired = result.sessionExpired;
    for (const taskId of result.deletedTaskIds) {
      selectedTaskIds.delete(taskId);
    }
    if (sessionExpired) return;
    if (result.failedTaskIds.length === 0) {
      showToast(`已删除 ${result.deletedTaskIds.length} 个任务及全部相关数据`);
    } else {
      showToast(`已删除 ${result.deletedTaskIds.length} 个任务，${result.failedTaskIds.length} 个删除失败，可重新尝试`);
    }
  } finally {
    setBatchDeleteBusy(false);
    modal.hide();
    updateTaskSelectionUi();
    // 会话失效时 apiRequest 已停止轮询，不得在收尾阶段重新启动。
    if (!sessionExpired) resumeVisiblePagePolling();
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
    // 同步最新任务数量，保证退出提示与任务列表一致。
    runningTaskCount = Number(payload.running_count || 0);
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
  setText("detail-region", task.region_name);
  setText("detail-rule-version", task.rulepack_version);
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
  setText("result-incomplete", summary.incomplete_items);
  const cancelButton = document.getElementById("cancel-task-button");
  const cancellationPending = task.status === "cancelling";
  // 停止请求提交后禁止重复点击，并明确展示后端正在安全停止。
  cancelButton.disabled = task.status !== "running";
  document.getElementById("cancel-task-button-label").textContent = cancellationPending ? "正在安全停止" : "安全停止任务";
  const deleteButton = document.getElementById("delete-task-button");
  // Worker 仍可能写入任务数据时，页面不提供删除入口。
  deleteButton.disabled = ["running", "cancelling"].includes(task.status);
  const excelOption = document.querySelector('[data-report-format="excel"]');
  const htmlOption = document.querySelector('[data-report-format="html"]');
  excelOption.disabled = !summary.audit_statistics_report;
  htmlOption.disabled = !summary.audit_statistics_html_report;
  document.getElementById("open-statistics-button").disabled = excelOption.disabled && htmlOption.disabled;
  const incompleteButton = document.getElementById("view-incomplete-items-button");
  incompleteButton.disabled = !summary.incomplete_items_report || !summary.incomplete_items;
  setText(
    "incomplete-items-button-label",
    summary.incomplete_items ? `查看未完成项（${summary.incomplete_items}）` : "查看未完成项",
  );
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
