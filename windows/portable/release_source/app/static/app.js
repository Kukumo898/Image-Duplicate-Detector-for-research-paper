"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { view: "workspace", mode: "batch", sensitivity: "strict", files: [], jobs: [], library: [], detail: null, currentJob: null, status: null, busy: false, pollBusy: false, finding: null, evidenceImages: [], contextImages: [], evidenceMode: "side", loadToken: 0, resultPage: 1, selected: new Set(), pageFindings: [], reviewBusy: false, bulkBusy: false, detailRequest: 0, detailLoading: null, revision: null, lastOverview: 0 };
  const PAGE_SIZE = 24;
  const MAX_UPLOAD_BYTES = 500 * 1048576;
  // The selected files and review drafts stay in this page while the owned
  // backend reconnects. Only a verified backend identity updates this switch.
  state.modeSwitch = false; state.runtimeMode = null; state.modePollBusy = false;
  let evidenceReturnFocus = null;
  const labels = { batch: "批量查重", documents: "文档查重", pair: "双图对比", queued: "排队中", running: "检测中", processing: "检测中", completed: "检测完成", failed: "检测失败", error: "检测失败", cancelling: "正在取消", cancelled: "已取消 · 未全扫", interrupted: "已中断 · 未全扫", pending: "待复核", confirmed: "已确认", dismissed: "已排除", exact_pixels: "查重视图像素一致", near_duplicate: "整体近似重复", exact_duplicate: "查重视图像素一致", exact: "查重视图像素一致", global_reuse: "整体相似复用", global_duplicate: "整体相似复用", transformed_duplicate: "变换后整体一致", local_reuse: "局部相似复用" };
  const activeStatuses = new Set(["queued", "running", "processing", "cancelling"]);
  Object.assign(labels, { data_supported: "数据支持线索（仅视觉对应）", annotation_only: "仅标注/模板相似", inconclusive: "不确定/需复核", legacy_unclassified: "历史记录（未按数据区验证）", experimental_image_candidate: "实验图像候选（身份未核实）", schematic_icon: "示意图/图标", annotation_template: "标注/模板", unknown: "内容角色未知" });
  const semanticCountLabels = { total_records: "全部记录", visual_reuse_clues: "视觉复用线索", experimental_image_candidates: "实验图像候选", schematic_icons: "示意图/图标", annotation_records: "标注类", semantic_unknown: "语义无来源核实", content_role_unknown: "内容角色未知", inconclusive: "视觉证据不确定", established_experimental_reuse: "有来源依据的实验图像复用", semantic_based_records: "有来源/复核依据的角色" };
  state.evidenceLayer = "original"; state.layerImages = [];
  const metricNames = { pixel_correlation: "像素相关", gradient_agreement: "梯度一致", consistent_tile_fraction: "一致区块占比", informative_tiles: "有效区块", inliers: "几何内点", inlier_ratio: "内点占比", mutual_matches: "双向匹配", median_reprojection_error: "重投影误差", coverage_a: "A 图覆盖", coverage_b: "B 图覆盖", rotation_degrees: "旋转角度", mirrored: "镜像", exact_pixels: "查重视图像素一致", low_information: "低信息图像", brightness_gain: "亮度增益", engine_version: "引擎版本" };
  Object.assign(metricNames, { normalized_residual: "归一化残差", brightness_offset: "亮度偏移", verified_pixels: "核验像素数", template_correlation: "裁剪相关", template_scale: "裁剪缩放", width: "视图宽度", height: "视图高度" });
  Object.assign(metricNames, { channel_a: "A 图核验通道", channel_b: "B 图核验通道", local_inlier_ratio: "局部内点占比", region_index: "区域编号", region_count: "区域数量" });
  const diagnosticNames = { verified_findings: "有已验证线索", no_verified_match: "暂无已验证匹配", insufficient_information: "图像信息不足", redundant_region: "重复区域已合并", overlay_only_information: "仅标注有信息", insufficient_features: "特征不足", insufficient_matches: "对应点不足", insufficient_inliers: "几何内点不足", geometry_rejected: "几何验证未通过", pixel_verification_failed: "像素验证未通过", low_coverage: "覆盖范围不足", poor_spatial_support: "空间支持不足", pixel_validation_failed: "像素验证未通过", implausible_transform: "变换不可靠" };
  function channelLabel(value) { return ({ R: "红色 R", G: "绿色 G", B: "蓝色 B", gray: "灰度", RGB: "RGB" })[value] || value || "整体图像"; }
  function findingChannels(finding) { return [finding.channel_a || finding.metrics?.channel_a, finding.channel_b || finding.metrics?.channel_b]; }
  function gridSummary(finding) {
    const v = finding.metrics?.annotation_verification, g = v?.fixed_grid_evidence;
    if (!g || v.photometric_basis !== "fixed_grid_robust_partial_correspondence") return "";
    const effective = Math.max(1, (g.cells || []).reduce((sum, cell) => sum + Number(cell.effective_pixels || 0), 0));
    return "固定局部核验：支持 " + g.supporting_cells + "/" + (g.cells?.length || 256) + " 格；反证 " + g.counterevidence_cells + " 格；无足够信息 " + ((g.cells?.length || 256) - g.informative_cells) + " 格。支持像素 " + g.supporting_pixels + "/" + effective + "（" + (100 * g.supporting_pixels / effective).toFixed(1) + "%，核验网格 " + v.verification_grid + "）。仅部分对应，全部不一致像素保留，非整框一致。";
  }
  function el(tag, cls, value) { const item = document.createElement(tag); if (cls) item.className = cls; if (value !== undefined) item.textContent = value; return item; }
  function icon(name) { const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); const use = document.createElementNS("http://www.w3.org/2000/svg", "use"); use.setAttribute("href", "#i-" + name); svg.append(use); return svg; }
  function clear(node) { node.replaceChildren(); return node; }
  function formatDate(value) { if (!value) return ""; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }); }
  function fmt(value) { return Number(value || 0).toLocaleString("zh-CN"); }
  function bytes(value) { return value >= 1048576 ? (value / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(value / 1024)) + " KB"; }
  function label(key) { return labels[key] || key || "未知"; }
  function toast(message, error = false) { const node = $("toast"); node.textContent = message; node.className = "toast" + (error ? " error" : ""); node.hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => { node.hidden = true; }, 4500); }
  async function api(path, options = {}) {
    if (state.modeSwitch && options.method && !["GET", "HEAD"].includes(options.method) && path !== "/api/daily/mode") throw new Error("正在切换加速模式，请等待本机服务重新连接。当前输入已保留。");
    const response = await fetch(path, { cache: "no-store", ...options });
    const contentType = response.headers.get("content-type") || "";
    const result = contentType.includes("json") ? await response.json() : await response.text();
    if (!response.ok) { let message = typeof result === "object" ? result.detail || result.error : result; if (Array.isArray(message)) message = message.map((e) => e.msg || String(e)).join("；"); throw new Error(typeof message === "string" ? message : "请求失败（" + response.status + "）"); }
    return result;
  }
  function empty(target, title, description, iconName = "scan") { const node = el("div", "empty-state"); node.append(icon(iconName), el("h3", "", title), el("p", "", description)); target.append(node); }
  function setView(view) {
    state.view = view;
    document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === "view-" + view));
    document.querySelectorAll(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.view === (view === "result" ? "tasks" : view)));
    $("page-breadcrumb").textContent = ({ workspace: "图像核验", tasks: "检测记录", result: "核验结果", library: "课题组图库", guide: "使用与判读" })[view];
    window.scrollTo({ top: 0, behavior: "instant" });
    if (view === "library") loadLibrary();
    if (view === "tasks") refreshJobs();
  }
  function setMode(mode) {
    if (state.busy) return;
    state.mode = mode;
    document.querySelectorAll("[data-mode]").forEach((button) => { button.classList.toggle("selected", button.dataset.mode === mode); button.setAttribute("aria-pressed", String(button.dataset.mode === mode)); });
    const docs = mode === "documents";
    $("file-input").accept = docs ? ".pdf,.docx" : ".png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp";
    $("drop-title").textContent = docs ? "将论文或研究文档拖放到这里" : mode === "pair" ? "选择两张需要核验的图像" : "将科研图像拖放到这里";
    $("drop-formats").textContent = docs ? "支持 PDF、DOCX · 在本机提取图像" : "支持 PNG、JPG、TIFF、BMP、WebP";
    $("include-history").disabled = mode === "pair";
    $("include-history").closest("label").style.opacity = mode === "pair" ? ".5" : "1";
    $("auto-panels").checked = mode !== "pair";
    if (mode === "pair") $("include-history").checked = false;
    const allowed = docs ? /\.(pdf|docx)$/i : /\.(png|jpe?g|tiff?|bmp|webp)$/i;
    state.files = state.files.filter((file) => allowed.test(file.name));
    renderFiles();
    updateSettingsSummary();
  }
  function updateSettingsSummary() {
    $("settings-summary").textContent = (state.sensitivity === "strict" ? "严格" : "均衡") + " · 历史图库" + (state.mode !== "pair" && $("include-history").checked ? "开" : "关") + " · 入库" + ($("add-library").checked ? "开" : "关") + " · 拆分" + ($("auto-panels").checked ? "开" : "关");
  }
  function addFiles(files) {
    if (state.busy) return;
    const valid = state.mode === "documents" ? /\.(pdf|docx)$/i : /\.(png|jpe?g|tiff?|bmp|webp)$/i;
    const errors = [];
    for (const file of files) {
      if (!valid.test(file.name)) { errors.push("格式不支持：" + file.name); continue; }
      const limit = state.status?.limits?.max_file_mb;
      if (limit && file.size > limit * 1048576) { errors.push(file.name + " 超过 " + limit + " MB 上限"); continue; }
      if (state.files.reduce((sum, item) => sum + item.size, 0) + file.size > MAX_UPLOAD_BYTES) { errors.push("总上传量不能超过 500 MiB，未加入：" + file.name); continue; }
      // Metadata cannot establish content identity: keep every selected file.
      state.files.push(file);
    }
    const max = state.status?.limits?.max_files;
    if (max && state.files.length > max) { state.files = state.files.slice(0, max); errors.push("每次最多选择 " + max + " 个文件，超出文件未加入。"); }
    renderFiles();
    if (errors.length) showFormError(errors.slice(0, 5).join("\n"));
  }
  function renderFiles() {
    const target = clear($("selected-files")); target.hidden = state.files.length === 0;
    state.files.forEach((file, index) => {
      const row = el("div", "selected-file"); const name = el("span", "selected-file-name", file.name); name.title = file.name;
      const remove = el("button", "icon-button"); remove.type = "button"; remove.disabled = state.busy; remove.setAttribute("aria-label", "移除 " + file.name); remove.append(icon("close")); remove.addEventListener("click", () => { if (state.busy) return; state.files.splice(index, 1); renderFiles(); });
      row.append(icon(state.mode === "documents" ? "file" : "image"), name, el("span", "selected-file-size", bytes(file.size)), remove); target.append(row);
    });
    $("upload-summary").textContent = state.files.length ? "已选择 " + state.files.length + " 个文件 · " + bytes(state.files.reduce((n, f) => n + f.size, 0)) : "尚未选择文件";
    $("clear-files").hidden = !state.files.length; $("clear-files").disabled = state.busy;
    $("form-error").hidden = true;
  }
  function showFormError(message) { $("form-error").textContent = message; $("form-error").hidden = false; }
  async function submitJob(event) {
    event?.preventDefault();
    if (state.modeSwitch) return showFormError("正在切换加速模式，请稍候。已选文件和任务信息保持不变。");
    if (state.busy) return;
    if (!state.files.length) return showFormError("请先选择需要核验的本地文件。");
    if (state.files.reduce((sum, file) => sum + file.size, 0) > MAX_UPLOAD_BYTES) return showFormError("总上传量不能超过 500 MiB，请拆分为多个任务。");
    if (state.mode === "pair" && state.files.length !== 2) return showFormError("双图对比需要且只接受 2 张单帧图片；多页 TIFF 请使用批量模式。");
    const form = new FormData(); state.files.forEach((file) => form.append("files", file));
    form.append("mode", state.mode); form.append("sensitivity", state.sensitivity); form.append("include_history", String(state.mode !== "pair" && $("include-history").checked)); form.append("add_to_library", String($("add-library").checked)); form.append("auto_panels", String($("auto-panels").checked)); form.append("name", $("task-name").value.trim());
    state.busy = true; setUploadBusy(true); $("start-button").textContent = "正在导入…"; $("form-error").hidden = true;
    try { const result = await api("/api/jobs", { method: "POST", body: form }); toast("任务已创建，正在本地核验"); state.files = []; renderFiles(); $("task-name").value = ""; await openJob(result.job_id); await Promise.allSettled([refreshStatus(), refreshJobs()]); }
    catch (error) { showFormError(error.message); }
    finally { state.busy = false; setUploadBusy(false); $("start-button").replaceChildren(document.createTextNode("开始核验 "), icon("arrow")); }
  }
  function setUploadBusy(busy) {
    busy = busy || state.modeSwitch;
    document.querySelectorAll("#upload-form input, #upload-form button, [data-mode], [data-sensitivity], #demo-button").forEach((node) => { node.disabled = busy; });
    $("drop-zone").setAttribute("aria-disabled", String(busy));
    if (!busy) { $("include-history").disabled = state.mode === "pair"; updateSettingsSummary(); }
  }
  async function refreshStatus() {
    await refreshMode();
    try {
      const [status, daily] = await Promise.all([api("/api/status"), api("/api/daily/status")]);
      state.status = status;
      $("daily-mode").textContent = daily.draining ? "正在正常关闭 · 不再接收新任务和修改" : daily.mode_label;
      $("daily-versions").textContent = "APP " + daily.application_version + " · CACHE " + daily.cache_version;
      $("daily-active").textContent = "活动任务 " + fmt(daily.active_jobs) + " · 上传／保存 " + fmt(daily.inflight_mutations);
      $("daily-cache").textContent = daily.cache.label + (daily.cache.persistent_hits ? "（本进程累计 " + fmt(daily.cache.persistent_hits) + " 次）" : "");
      $("daily-cache").dataset.state = daily.cache.state;
      $("daily-data").textContent = daily.data_directory;
      $("stat-library").textContent = fmt(state.status.library_count); $("stat-jobs").textContent = fmt(state.status.jobs_count); $("stat-active").textContent = fmt(Array.isArray(state.status.active_jobs) ? state.status.active_jobs.length : state.status.active_jobs);
      $("nav-job-count").textContent = fmt(state.status.jobs_count); $("connection-label").textContent = "本机运行 · " + (daily.mode === "compatible" ? "兼容模式" : daily.mode === "accelerated" ? "加速模式" : daily.mode_label); $("connection-dot").classList.remove("offline"); $("engine-version").textContent = "ENGINE · " + state.status.engine_version;
      const runtimeMessages = [];
      if (daily.draining) runtimeMessages.push("正在正常关闭，暂不接收新任务和修改。重新启动后可继续使用。");
      if (daily.cache.state === "fallback") runtimeMessages.push(daily.cache.label + "。可展开运行状态查看详情与日志。");
      $("runtime-alert").textContent = runtimeMessages.join(" "); $("runtime-alert").hidden = !runtimeMessages.length; $("runtime-alert").dataset.state = daily.draining ? "draining" : daily.cache.state === "fallback" ? "fallback" : "normal";
      const limits = state.status.limits; if (limits) $("file-limits").textContent = "最多 " + limits.max_files + " 个文件 · 单文件 ≤ " + limits.max_file_mb + " MB";
    } catch (error) { $("connection-label").textContent = state.modeSwitch ? "正在切换 · 等待本机重连" : "本机服务暂时无法连接"; $("connection-dot").classList.add("offline"); $("daily-mode").textContent = state.modeSwitch ? "正在切换，尚未确认新模式" : "服务暂时无法连接"; $("daily-cache").textContent = state.modeSwitch ? "新进程连接后重新核实缓存状态。" : "如已关闭图片查重系统，请双击启动入口重新打开。"; $("runtime-alert").textContent = state.modeSwitch ? "正在安全切换加速模式，已选文件、任务信息和未保存备注保持不变。请等待重连。" : "本机服务暂时无法连接。如已关闭图片查重系统，请双击启动入口重新打开；未保存的内容请保留后重试。"; $("runtime-alert").hidden = false; $("runtime-alert").dataset.state = state.modeSwitch ? "switching" : "offline"; }
  }
  function showMode(mode) {
    const busy = Boolean(mode.switching || mode.state === "switching");
    if (busy && !state.modeSwitch) state.modeSwitchStarted = Date.now();
    if (!busy) state.modeSwitchStarted = null;
    state.modeSwitch = busy;
    const confirmed = mode.current_mode === "accelerated" || mode.current_mode === "compatible";
    if (confirmed) state.runtimeMode = mode.current_mode;
    const button = $("acceleration-toggle");
    button.setAttribute("aria-checked", String(state.runtimeMode === "accelerated"));
    button.disabled = busy || !confirmed;
    button.dataset.state = busy ? "switching" : state.runtimeMode || "unknown";
    $("acceleration-value").textContent = busy ? "切换中…" : confirmed ? state.runtimeMode === "accelerated" ? "已开启" : "已关闭" : "待确认";
    $("acceleration-message").textContent = mode.state === "failed" ? (mode.detail || mode.last_error || "切换失败") + (confirmed ? " 当前已确认：" + (state.runtimeMode === "accelerated" ? "加速开启。" : "加速关闭。") : " 当前模式尚未确认。") + "可重试开关；仍无法连接时，请正常关闭后通过启动图片查重系统入口重试。" : busy ? "正在正常关闭空闲后端并重连；当前输入保持不变。" : "重复查重时复用已有特征；关闭后使用原计算路径，检测标准不变。";
    $("acceleration-message").dataset.state = mode.state;
    setUploadBusy(state.busy);
  }
  async function refreshMode() {
    if (state.modePollBusy) return;
    state.modePollBusy = true;
    try { showMode(await api("/api/daily/mode")); }
    catch (error) {
      if (state.modeSwitch && Date.now() - state.modeSwitchStarted > 180000) {
        state.modeSwitch = false; state.runtimeMode = null; setUploadBusy(state.busy);
        $("acceleration-message").textContent = "切换等待已超时，暂时无法确认后端状态。已选文件和草稿保留；请先双击关闭图片查重系统，确认后再通过启动图片查重系统入口恢复，不要刷新本页。";
        $("acceleration-message").dataset.state = "failed";
      }
      if (!state.modeSwitch) {
        $("acceleration-toggle").disabled = true; $("acceleration-value").textContent = "待确认";
        if ($("acceleration-message").dataset.state !== "failed") $("acceleration-message").textContent = "暂时无法核实实际模式。输入仍保留，请等待重新连接或使用启动图片查重系统入口。";
      }
    }
    finally { state.modePollBusy = false; }
  }
  async function switchAcceleration() {
    if (state.modeSwitch || !state.runtimeMode) return;
    if (state.busy || state.reviewBusy || state.bulkBusy) { toast("当前上传或保存尚未结束，请完成后再切换。", true); return; }
    const previous = state.runtimeMode;
    showMode({ state: "switching", current_mode: previous });
    try {
      const result = await api("/api/daily/mode", { method: "POST", headers: { "Content-Type": "application/json", "X-Labimage-Mode": "switch" }, body: JSON.stringify({ mode: previous === "accelerated" ? "compatible" : "accelerated" }) });
      showMode(result); await refreshMode();
    } catch (error) {
      state.modeSwitch = false; setUploadBusy(state.busy);
      $("acceleration-message").textContent = error.message + " 当前输入已保留，正在重新核实实际模式。";
      toast(error.message, true); await refreshMode();
    }
  }
  function taskRow(job) {
    const row = el("button", "task-row"); row.type = "button"; const ico = el("span", "task-file-icon"); ico.append(icon(job.mode === "documents" ? "file" : job.mode === "pair" ? "pair" : "image"));
    const info = el("span", "task-info"); const name = el("strong", "", job.name || "未命名核验任务"); name.title = name.textContent; info.append(name, el("small", "", label(job.mode) + " · " + formatDate(job.created_at) + " · " + fmt(job.images_count) + " 张图像"));
    const results = el("span", "task-results", fmt(job.findings_count) + " 条线索"); results.append(el("small", "", "证据需人工复核")); row.append(ico, info, results, el("span", "badge " + job.status, label(job.status)), el("span", "task-chevron", "›")); row.addEventListener("click", () => openJob(job.id)); return row;
  }
  function renderJobs() {
    const recent = clear($("recent-tasks")), all = clear($("all-tasks"));
    if (!state.jobs.length) { empty(recent, "暂无最近任务", "选择文件后开始核验。", "clock"); empty(all, "还没有检测记录", "从图像核验工作台创建第一个任务。", "clock"); return; }
    state.jobs.slice(0, 3).forEach((job) => recent.append(taskRow(job))); state.jobs.forEach((job) => all.append(taskRow(job)));
  }
  async function refreshJobs() { try { const data = await api("/api/jobs"); state.jobs = data.jobs || []; renderJobs(); } catch (error) { if (state.view === "tasks") { clear($("all-tasks")); empty($("all-tasks"), "暂时无法读取记录", error.message); } } }
  async function openJob(id) {
    state.currentJob = id; state.detail = null; state.revision = null; state.resultPage = 1; state.selected.clear(); state.pageFindings = [];
    $("finding-search").value = ""; $("evidence-filter").value = "all"; $("review-filter").value = "all"; $("batch-message").textContent = "";
    $("evidence-dialog").close(); state.finding = null; ++state.loadToken; $("job-error").hidden = true;
    $("result-title").textContent = "正在读取核验记录…"; $("result-subtitle").textContent = "";
    clear($("findings-grid")); clear($("extracted-images")); $("extracted-panel").open = false; $("results-pagination").hidden = true;
    empty($("findings-grid"), "正在读取核验记录", "加载本地证据与复核状态…"); updateSelection(); setView("result"); await loadJob();
  }
  async function loadJob() {
    const id = state.currentJob; if (!id) return; const request = ++state.detailRequest; state.detailLoading = id;
    try { const detail = await api("/api/jobs/" + encodeURIComponent(id)); if (id !== state.currentJob || request !== state.detailRequest) return; state.detail = detail; state.revision = detail.revision ?? null; renderJob(); }
    catch (error) { if (id === state.currentJob && request === state.detailRequest) { $("job-error").textContent = "读取任务失败：" + error.message; $("job-error").hidden = false; } }
    finally { if (request === state.detailRequest) state.detailLoading = null; }
  }
  function renderProgress(job) {
    $("result-subtitle").textContent = label(job.mode) + " · " + formatDate(job.created_at) + " · " + label(job.status) + (job.settings?.sensitivity ? " · " + (job.settings.sensitivity === "strict" ? "严格策略" : "均衡策略") : "");
    const active = activeStatuses.has(job.status); $("job-progress").hidden = !active;
    $("progress-label").textContent = job.status === "cancelling" ? "正在取消，保留已完成部分" : job.status === "queued" ? "任务已排队，等待本地引擎" : job.total_pairs ? "正在比对图像" : "正在解析图像与准备比对";
    $("progress-bar").value = Number(job.progress || 0); $("progress-count").textContent = fmt(job.processed_pairs) + " / " + fmt(job.total_pairs) + " 对";
    $("cancel-job").hidden = !active; $("cancel-job").disabled = job.status === "cancelling";
    $("job-error").hidden = !job.error; $("job-error").textContent = job.error || "";
  }
  async function pollJobProgress() {
    const id = state.currentJob;
    if (!id || state.detailLoading || state.reviewBusy || state.bulkBusy) return;
    if (!state.detail) { await loadJob(); return; }
    const request = state.detailRequest;
    try {
      const progress = await api("/api/jobs/" + encodeURIComponent(id) + "/progress");
      if (id !== state.currentJob || request !== state.detailRequest || state.reviewBusy || state.bulkBusy) return;
      const old = state.detail.job, job = progress.job;
      const changed = progress.revision !== state.revision || ["status", "images_count", "findings_count", "error"].some((key) => job[key] !== old[key]);
      if (changed) await loadJob();
      else { Object.assign(old, job); renderProgress(old); }
    } catch (error) { if (id === state.currentJob) { $("job-error").textContent = "进度更新失败，将自动重试：" + error.message; $("job-error").hidden = false; } }
  }
  function renderJob() {
    if (!state.detail) return;
    const { job, images = [], findings = [] } = state.detail;
    $("result-title").textContent = job.name || "核验结果";
    renderProgress(job);
    const warnings = clear($("result-warnings"));
    if (["failed", "error", "cancelled", "interrupted", "cancelling"].includes(job.status)) warnings.append(el("div", "warning-item important-warning", "本任务未完成全量检测。当前结果仅代表已处理部分，不能据此判断不存在重复。"));
    (job.warnings || []).forEach((warning) => warnings.append(el("div", "warning-item" + (/未全扫|未导入|跳过|上限|失败|中断/.test(warning) ? " important-warning" : ""), warning)));
    renderDiagnostics(job);
    const classification = clear($("classification-counts"));
    const classCounts = state.detail.classification_counts || findings.reduce((counts, finding) => { const key = finding.evidence_class || "legacy_unclassified"; counts[key] = (counts[key] || 0) + 1; return counts; }, {});
    ["data_supported", "annotation_only", "inconclusive", "legacy_unclassified"].forEach((key) => classification.append(el("span", "classification-pill " + key, label(key) + " " + fmt(classCounts[key]))));
    const semantic = clear($("semantic-counts"));
    if (state.detail.semantic_counts) Object.entries(semanticCountLabels).forEach(([key, title]) => semantic.append(el("span", "classification-pill", title + " " + fmt(state.detail.semantic_counts[key]))));
    $("result-images").textContent = fmt(images.length); $("result-findings").textContent = fmt(findings.length); $("result-pending").textContent = fmt(findings.filter((f) => f.review_status === "pending").length); $("result-confirmed").textContent = fmt(findings.filter((f) => f.review_status === "confirmed").length); $("finding-count").textContent = "(" + findings.length + ")";
    renderFindings(); if ($("extracted-panel").open) renderExtractedImages(); else clear($("extracted-images")); $("extracted-count").textContent = images.length + " 张";
  }
  function renderExtractedImages() { const target = clear($("extracted-images")); (state.detail?.images || []).forEach((img) => target.append(imageCard(img))); }
  function renderDiagnostics(job) {
    const target = clear($("job-diagnostics")); const diagnostics = job.diagnostics;
    target.hidden = !diagnostics || typeof diagnostics !== "object" || !Object.keys(diagnostics).length;
    if (target.hidden) return;
    const heading = el("div", "diagnostics-heading"); heading.append(icon("scan"), el("strong", "", "本次检查范围")); target.append(heading);
    const summary = el("div", "diagnostic-counts"); const segmentation = diagnostics.segmentation || {};
    if (segmentation.sources !== undefined) summary.append(el("span", "", "来源图像 " + fmt(segmentation.sources)));
    if (segmentation.panels !== undefined) summary.append(el("span", "", "拆分面板 " + fmt(segmentation.panels)));
    summary.append(el("span", "", "已比对 " + fmt(job.processed_pairs) + " 对"));
    if (Number(diagnostics.structural_pairs_skipped)) summary.append(el("span", "", "来源结构关系跳过 " + fmt(diagnostics.structural_pairs_skipped) + " 对"));
    target.append(summary);
    if (segmentation.complete === false) target.append(el("p", "coverage-note", "自动拆分不保证覆盖每个面板。请查看解析图像，并回到原图核对遗漏；完成比对不等于排除全部重复。"));
    if (Object.keys(diagnostics.status_counts || {}).length || Object.keys(diagnostics.rejection_counts || {}).length) {
      const details = el("details", "diagnostic-details"); details.append(el("summary", "", "查看比对与验证记录"));
      const statuses = el("div", "metric-pills"); Object.entries(diagnostics.status_counts || {}).forEach(([key, value]) => statuses.append(el("span", "", (diagnosticNames[key] || key) + "：" + fmt(value) + " 对"))); details.append(statuses);
      const rejections = el("div", "metric-pills"); Object.entries(diagnostics.rejection_counts || {}).forEach(([key, value]) => rejections.append(el("span", "", (diagnosticNames[key] || key) + "：" + fmt(value))));
      if (rejections.childElementCount) { details.append(el("p", "", "以下是候选匹配的验证记录，不是已证实的非重复图像数量。"), rejections); }
      target.append(details);
    }
  }
  function imageCaption(img) { return (img.name || "图像") + (img.page ? " · 第 " + img.page + " 页" : "") + (img.parent_id && img.metadata?.panel_index !== undefined && !(img.name || "").includes("面板") ? " · 面板 " + img.metadata.panel_index : ""); }
  function findingCard(finding) {
    const card = el("article", "finding-card"); const head = el("div", "finding-head"); const title = el("span", "finding-title"); title.append(icon("scan"), document.createTextNode(label(finding.kind))); const score = el("span", "finding-score", "证据强度 "); score.append(el("b", "", Number(finding.score || 0).toFixed(1))); head.append(title, score);
    card.dataset.findingId = finding.id;
    const selectLabel = el("label", "finding-select"); const checkbox = el("input"); checkbox.type = "checkbox"; checkbox.checked = state.selected.has(finding.id); checkbox.disabled = state.bulkBusy || state.reviewBusy; checkbox.dataset.selectFinding = finding.id;
    checkbox.setAttribute("aria-label", "选择线索：" + imageCaption(finding.image_a) + " ↔ " + imageCaption(finding.image_b));
    checkbox.addEventListener("change", () => { if (checkbox.checked) state.selected.add(finding.id); else state.selected.delete(finding.id); updateSelection(); }); selectLabel.append(checkbox, document.createTextNode("选中")); head.prepend(selectLabel);
    const pair = el("button", "finding-pair"); pair.type = "button"; pair.setAttribute("aria-label", "查看 " + label(finding.kind) + " 的双图证据");
    [finding.image_a, finding.image_b].forEach((img, index) => { const figure = el("figure"); const image = el("img"); image.src = img.thumbnail_url || img.url; image.alt = img.name || "科研图像"; image.loading = "lazy"; const caption = el("figcaption", "", imageCaption(img)); caption.title = caption.textContent; figure.append(el("span", "image-label", index ? "图像 B" : "图像 A"), image, caption); pair.append(figure); });
    pair.addEventListener("click", () => openEvidence(finding));
    const body = el("div", "finding-body"); const tags = el("div", "finding-tags"); const channels = findingChannels(finding);
    tags.append(el("span", "badge evidence-class " + (finding.evidence_class || "legacy_unclassified"), label(finding.evidence_class || "legacy_unclassified")));
    tags.append(el("span", "badge content-role", label(finding.content_role || "unknown")));
    if (finding.region_index !== undefined && finding.region_index !== null) tags.append(el("span", "badge", "区域 " + finding.region_index));
    if (channels.some(Boolean)) tags.append(el("span", "badge channel-badge", channelLabel(channels[0]) + " ↔ " + channelLabel(channels[1])));
    if (finding.image_a.parent_id || finding.image_b.parent_id) tags.append(el("span", "badge", "可查看原图位置"));
    if (tags.childElementCount) body.append(tags);
    body.append(el("p", "", finding.explanation || "请查看双图证据并进行人工复核。"));
    if (gridSummary(finding)) body.append(el("p", "grid-evidence-summary", gridSummary(finding)));
    const foot = el("div", "finding-footer"); const open = el("button", "text-button", "查看证据与复核 ↗"); open.type = "button"; open.addEventListener("click", () => openEvidence(finding)); foot.append(el("span", "badge " + finding.review_status, label(finding.review_status)), open); body.append(foot); card.append(head, pair, body); return card;
  }
  function renderFindings() {
    if (!state.detail) return; const target = clear($("findings-grid")); const filter = $("review-filter").value; const evidenceFilter = $("evidence-filter").value; const term = $("finding-search").value.trim().toLocaleLowerCase();
    const findings = (state.detail.findings || []).filter((f) => (filter === "all" || f.review_status === filter) && (evidenceFilter === "all" || (f.evidence_class || "legacy_unclassified") === evidenceFilter) && (!term || [f.image_a?.name, f.image_a?.source, f.image_b?.name, f.image_b?.source, f.review_note].join(" ").toLocaleLowerCase().includes(term)));
    const pages = Math.max(1, Math.ceil(findings.length / PAGE_SIZE)); state.resultPage = Math.max(1, Math.min(state.resultPage, pages));
    state.pageFindings = findings.slice((state.resultPage - 1) * PAGE_SIZE, state.resultPage * PAGE_SIZE);
    const visibleIds = new Set(state.pageFindings.map((f) => f.id)); state.selected.forEach((id) => { if (!visibleIds.has(id)) state.selected.delete(id); });
    $("finding-count").textContent = "(" + findings.length + " / 全部 " + (state.detail.findings || []).length + ")";
    $("results-pagination").hidden = !findings.length; $("results-page-label").textContent = "第 " + state.resultPage + " / " + pages + " 页 · 本页 " + state.pageFindings.length + " 条 · 每页 " + PAGE_SIZE + " 条";
    $("results-prev").disabled = state.resultPage === 1 || state.bulkBusy; $("results-next").disabled = state.resultPage === pages || state.bulkBusy; updateSelection();
    if (!findings.length) {
      const job = state.detail.job;
      const filtered = filter !== "all" || evidenceFilter !== "all" || Boolean(term);
      const title = filtered ? "当前筛选下没有线索" : activeStatuses.has(job.status) ? "正在寻找相似线索" : job.status !== "completed" ? "当前尚无可展示线索 · 检测未完成" : "当前规则下未检出相似线索";
      const description = filtered ? "清空搜索或切换证据、复核筛选，查看其他记录。" : job.status === "completed" ? "这不等于绝无重复。请结合导入警告、原始图像与检测能力边界继续复核。" : "已处理部分的结果将在这里展示，请同时关注任务进度与提示。";
      empty(target, title, description); return;
    }
    state.pageFindings.forEach((finding) => target.append(findingCard(finding)));
  }
  function updateSelection() {
    const count = state.selected.size, pageCount = state.pageFindings.length, busy = state.bulkBusy || state.reviewBusy;
    $("selected-count").textContent = "已选 " + count + " 条（仅当前页）";
    $("select-page").checked = pageCount > 0 && count === pageCount; $("select-page").indeterminate = count > 0 && count < pageCount; $("select-page").disabled = !pageCount || busy;
    $("batch-apply").disabled = !count || busy; $("batch-status").disabled = busy;
    document.querySelectorAll("[data-select-finding]").forEach((node) => { node.checked = state.selected.has(node.dataset.selectFinding); node.disabled = busy; });
    document.querySelectorAll("[data-review]").forEach((node) => { node.disabled = busy; });
    ["finding-search", "evidence-filter", "review-filter"].forEach((id) => { $(id).disabled = state.bulkBusy; });
  }
  function resetResultFilter() { if (state.bulkBusy) return; state.resultPage = 1; state.selected.clear(); $("batch-message").textContent = ""; renderFindings(); }
  function changeResultPage(delta) { if (state.bulkBusy) return; state.resultPage += delta; state.selected.clear(); $("batch-message").textContent = ""; renderFindings(); $("results-toolbar").scrollIntoView({ block: "start", behavior: "instant" }); }
  async function applyBatchReview() {
    if (state.bulkBusy || state.reviewBusy || !state.currentJob || !state.selected.size) return;
    const jobId = state.currentJob, ids = Array.from(state.selected), status = $("batch-status").value;
    if (ids.length > 200) { $("batch-message").textContent = "一次最多复核 200 条，请缩小选择。"; return; }
    state.bulkBusy = true; ++state.detailRequest; state.detailLoading = null; updateSelection(); $("results-prev").disabled = true; $("results-next").disabled = true;
    $("batch-message").textContent = "正在将选中的 " + ids.length + " 条标记为「" + label(status) + "」…"; $("batch-message").classList.remove("error");
    try {
      const result = await api("/api/jobs/" + encodeURIComponent(jobId) + "/reviews", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, status }) });
      if (jobId !== state.currentJob) { toast("此前任务的 " + result.count + " 条复核已保存"); return; }
      state.selected.clear(); await loadJob(); $("batch-message").textContent = "已将 " + result.count + " 条标记为「" + label(status) + "」，各条原有备注已保留。"; toast("批量复核已保存");
    } catch (error) { if (jobId === state.currentJob) { $("batch-message").textContent = "批量复核失败，未更改本地显示：" + error.message; $("batch-message").classList.add("error"); } toast(error.message, true); }
    finally { state.bulkBusy = false; if (state.detail) renderFindings(); else updateSelection(); }
  }
  function imageCard(img) { const card = el("article", "image-card"); const link = el("a"); link.href = img.url; link.target = "_blank"; link.rel = "noopener"; link.title = "打开查重视图"; const image = el("img"); image.src = img.thumbnail_url || img.url; image.alt = img.name || "科研图像查重视图"; image.loading = "lazy"; link.append(image); const info = el("div", "image-card-info"); const title = el("strong", "", img.name || "图像"); title.title = img.name || ""; const source = el("small", "", (img.source || "本地图像") + (img.page ? " · 第 " + img.page + " 页" : "")); source.title = source.textContent; info.append(title, source, el("small", "", img.width + " × " + img.height + " px · 查重视图")); card.append(link, info); return card; }
  async function loadLibrary() { try { const data = await api("/api/library"); state.library = data.images || []; renderLibrary(); } catch (error) { clear($("library-images")); empty($("library-images"), "暂时无法读取图库", error.message); } }
  function renderLibrary() { const target = clear($("library-images")); const term = $("library-search").value.trim().toLowerCase(); const list = state.library.filter((img) => (img.name + " " + img.source).toLowerCase().includes(term)); $("library-count").textContent = "共 " + state.library.length + " 张" + (term ? " · 当前显示 " + list.length + " 张" : ""); if (!list.length) empty(target, term ? "没有匹配的图像" : "课题组图库还是空的", term ? "试试其他文件名或来源关键词。" : "创建核验任务时勾选「检测图像加入本地图库」，完成后可用于跨任务比对。", "grid"); else list.forEach((img) => target.append(imageCard(img))); }
  async function loadDemo() {
    if (state.busy) return; state.busy = true; setUploadBusy(true);
    try { const data = await api("/api/demo-files"); if (!data.files?.length) throw new Error("暂无演示文件，请导入自己的图像。"); const files = await Promise.all(data.files.map(async (file) => { const response = await fetch(file.url); if (!response.ok) throw new Error("演示文件读取失败"); return new File([await response.blob()], file.name, { type: "image/png" }); })); state.busy = false; setMode("batch"); state.files = []; addFiles(files); $("task-name").value = "示例 · 科研图像核验"; $("include-history").checked = false; $("add-library").checked = false; toast("演示文件已载入，不会加入图库。点击「开始核验」运行。"); $("drop-zone").scrollIntoView({ behavior: "smooth", block: "center" }); }
    catch (error) { toast(error.message, true); } finally { state.busy = false; setUploadBusy(false); }
  }
  async function openEvidence(finding) {
    evidenceReturnFocus = document.activeElement;
    state.finding = finding; state.evidenceImages = []; state.contextImages = []; state.layerImages = []; state.evidenceMode = "side"; state.evidenceLayer = "original"; $("evidence-layer").value = "original"; const token = ++state.loadToken;
    const hasLayers = [finding.image_a, finding.image_b].every((source) => source.annotations?.layers);
    $("evidence-layer").disabled = !hasLayers;
    $("evidence-layer").querySelector('[value="common_data"]').disabled = !finding.data_evidence_urls;
    $("layer-note").textContent = hasLayers ? "遮罩来自各自单图，红色为标注候选、黄色为不确定（保留），绿色为保留数据候选；绿色不代表匹配成功。" : "历史记录缺少本轮遮罩，不自动补写数据验证。";
    const hasContext = [finding.image_a, finding.image_b].some((source) => source.parent_url && source.bbox);
    $("view-context").hidden = !hasContext; $("view-context").disabled = hasContext;
    $("parent-context-note").textContent = "实线框标记面板在原图中的位置，虚线框标记当前线索区域。父图可能仅为PDF内嵌位图，不是完整页面；请下载来源PDF按页码核对实验组、时间点与通道标签。";
    $("review-note").value = finding.review_note || ""; $("review-message").textContent = "当前状态：" + label(finding.review_status); $("evidence-kind").textContent = label(finding.kind) + " · 证据强度 " + Number(finding.score || 0).toFixed(1); $("evidence-explanation").textContent = finding.explanation || ""; $("evidence-zoom").value = "100"; $("zoom-value").textContent = "100%";
    const metrics = clear($("evidence-metrics")); Object.entries(finding.metrics || {}).forEach(([key, value]) => { if (value !== null && typeof value !== "object") metrics.append(el("span", "", (metricNames[key] || key) + "：" + (typeof value === "boolean" ? value ? "是" : "否" : String(value)))); });
    metrics.prepend(el("span", "evidence-class", label(finding.evidence_class || "legacy_unclassified")));
    metrics.append(el("span", "content-role", label(finding.content_role || "unknown")), el("span", "", finding.review_scope?.established_experimental_reuse ? "有来源依据的实验图像复用" : "未建立实验身份/语义结论"));
    if (gridSummary(finding)) metrics.append(el("span", "grid-evidence-summary", gridSummary(finding)));
    const verification = finding.metrics?.annotation_verification;
    const evidenceDetails = clear($("verification-details"));
    evidenceDetails.append(el("summary", "", "视觉证据 · 内容角色 · 语义依据 · 两侧覆盖分母"), el("pre", "", JSON.stringify({ visual_evidence: finding.visual_evidence, content_role: finding.content_role, semantic_basis: finding.semantic_basis, review_scope: finding.review_scope, annotation_verification: verification || "历史记录没有数据区验证。" }, null, 2)));
    const channelContext = clear($("channel-context")); const channels = findingChannels(finding); channelContext.hidden = !channels.some(Boolean) && finding.review_context !== "shared_channel_content";
    if (!channelContext.hidden) { channelContext.append(el("strong", "", "共享通道内容 · A " + channelLabel(channels[0]) + " ↔ B " + channelLabel(channels[1])), el("p", "", "当前画布保留完整彩色图，匹配证据针对上述色彩分量，不代表整张 Merge 图相同。同组单通道与 Merge 的共享可能是正常实验呈现，请核对实验组、视野与图注。")); }
    const sources = clear($("evidence-names"));
    [finding.image_a, finding.image_b].forEach((source, index) => {
      const item = el("span", "evidence-source");
      item.append(el("strong", "", "查重视图 " + (index ? "B" : "A") + " · " + imageCaption(source)), el("small", "", "来源：" + (source.source || source.name || "本地文件")));
      if (source.original_url) { const download = el("a", "text-button source-download", "下载来源文件"); download.href = source.original_url; download.download = ""; download.append(icon("download")); download.title = "下载原始上传来源；文档提取图像对应其 PDF 或 DOCX 文件"; item.append(download); }
      if (source.parent_url) { const parent = el("a", "text-button parent-source-link", "打开来源图版 ↗"); parent.href = source.parent_url; parent.target = "_blank"; parent.rel = "noopener"; item.append(parent); }
      sources.append(item);
    });
    updateEvidenceMode(); $("evidence-dialog").showModal(); drawEvidence();
    try {
      const images = await Promise.all([finding.image_a, finding.image_b].map((source) => loadEvidenceImage(source.url)));
      if (token !== state.loadToken) return; state.evidenceImages = images; drawEvidence();
      if (hasContext) {
        const parents = await Promise.allSettled([finding.image_a, finding.image_b].map((source, index) => source.parent_url && source.bbox ? loadEvidenceImage(source.parent_url) : Promise.resolve(images[index])));
        if (token !== state.loadToken) return;
        state.contextImages = parents.map((result, index) => result.status === "fulfilled" ? result.value : images[index]);
        if (parents.some((result) => result.status === "rejected")) $("parent-context-note").textContent = "部分来源图版加载失败，该侧暂时显示当前面板；可通过下方链接打开来源图版。成功加载的一侧以实线标记面板、虚线标记线索区域。";
        $("view-context").disabled = false; drawEvidence();
      }
    } catch (error) { toast(error.message, true); }
  }
  function loadEvidenceImage(url) { return new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img); img.onerror = () => reject(new Error("查重视图读取失败，请刷新后重试。")); img.src = url; }); }
  function updateEvidenceMode() { const overlay = state.evidenceMode === "overlay", context = state.evidenceMode === "context"; [["view-side", "side"], ["view-overlay", "overlay"], ["view-context", "context"]].forEach(([id, mode]) => { $(id).classList.toggle("selected", state.evidenceMode === mode); $(id).setAttribute("aria-pressed", String(state.evidenceMode === mode)); }); $("overlay-label").hidden = !overlay; $("overlay-caution").hidden = !overlay; $("parent-context-note").hidden = !context; }
  function regionValues(region) { if (Array.isArray(region)) return region; if (region?.bbox) return region.bbox; return [region?.x || 0, region?.y || 0, region?.width ?? region?.w ?? 0, region?.height ?? region?.h ?? 0]; }
  function drawEvidence() {
    const canvas = $("evidence-canvas"); const ctx = canvas.getContext("2d"); const available = Math.max(280, $("evidence-dialog").clientWidth - 34); const zoom = Number($("evidence-zoom").value) / 100; const width = Math.round(available * zoom); const overlay = state.evidenceMode === "overlay"; const height = Math.round(Math.min(420, available * (overlay ? .62 : .42)) * zoom);
    const ratio = window.devicePixelRatio || 1; canvas.width = width * ratio; canvas.height = height * ratio; canvas.style.width = width + "px"; canvas.style.height = height + "px"; ctx.scale(ratio, ratio); ctx.fillStyle = "#e8ede2"; ctx.fillRect(0, 0, width, height);
    if (state.evidenceImages.length !== 2) { ctx.fillStyle = "#889b74"; ctx.font = "12px sans-serif"; ctx.textAlign = "center"; ctx.fillText("正在载入查重视图…", width / 2, height / 2); return; }
    const gap = 25 * zoom; const boxes = []; const inContext = state.evidenceMode === "context"; const photos = inContext && state.contextImages.length === 2 ? state.contextImages : state.evidenceLayer !== "original" && state.layerImages.length === 2 ? state.layerImages : state.evidenceImages;
    photos.forEach((photo, i) => { const spaceW = overlay ? width : (width - gap) / 2; const xStart = overlay ? 0 : i * (spaceW + gap); let iw, ih;
      if (overlay) { const scale = Math.min((width - 20 * zoom) / photos[0].naturalWidth, (height - 20 * zoom) / photos[0].naturalHeight); iw = photos[0].naturalWidth * scale; ih = photos[0].naturalHeight * scale; } else { const scale = Math.min((spaceW - 20 * zoom) / photo.naturalWidth, (height - 20 * zoom) / photo.naturalHeight); iw = photo.naturalWidth * scale; ih = photo.naturalHeight * scale; }
      const box = { x: xStart + (spaceW - iw) / 2, y: (height - ih) / 2, w: iw, h: ih, sx: iw / photo.naturalWidth, sy: ih / photo.naturalHeight }; boxes.push(box); ctx.globalAlpha = overlay && i === 1 ? Number($("overlay-opacity").value) / 100 : 1; ctx.drawImage(photo, box.x, box.y, box.w, box.h); ctx.globalAlpha = 1;
    });
    if (!$("show-regions").checked) return;
    const colors = ["#159876", "#dc9d3e"];
    const sources = [state.finding.image_a, state.finding.image_b];
    const point = (i, x, y) => {
      const source = sources[i], box = boxes[i];
      if (inContext && source.parent_url && source.bbox && photos[i] !== state.evidenceImages[i]) {
        const [cx, cy, cw, ch] = regionValues(source.bbox); const metadata = source.metadata || {};
        x = (cx + x * cw / (source.width || state.evidenceImages[i].naturalWidth)) * photos[i].naturalWidth / (metadata.parent_width || photos[i].naturalWidth);
        y = (cy + y * ch / (source.height || state.evidenceImages[i].naturalHeight)) * photos[i].naturalHeight / (metadata.parent_height || photos[i].naturalHeight);
      }
      if (!inContext) { x *= photos[i].naturalWidth / (source.width || photos[i].naturalWidth); y *= photos[i].naturalHeight / (source.height || photos[i].naturalHeight); }
      return [box.x + x * box.sx, box.y + y * box.sy];
    };
    if (inContext) sources.forEach((source, i) => {
      if (!source.parent_url || !source.bbox || photos[i] === state.evidenceImages[i]) return;
      const [x, y] = point(i, 0, 0), [right, bottom] = point(i, source.width || state.evidenceImages[i].naturalWidth, source.height || state.evidenceImages[i].naturalHeight);
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 5; ctx.strokeRect(x, y, right - x, bottom - y); ctx.strokeStyle = colors[i]; ctx.lineWidth = 2.5; ctx.strokeRect(x, y, right - x, bottom - y);
    });
    [state.finding.regions_a || [], state.finding.regions_b || []].forEach((regions, i) => { ctx.strokeStyle = colors[i]; ctx.fillStyle = i ? "#dc9d3e19" : "#15987619"; ctx.lineWidth = 2; ctx.setLineDash(inContext ? [5, 4] : []); regions.forEach((region) => { const [x, y, w, h] = regionValues(region); if (![x, y, w, h].every(Number.isFinite)) return; const [left, top] = point(i, x, y), [right, bottom] = point(i, x + w, y + h); ctx.fillRect(left, top, right - left, bottom - top); ctx.strokeRect(left, top, right - left, bottom - top); }); });
    ctx.setLineDash([]);
    if (inContext) return;
    (state.finding.matches || []).slice(0, 35).forEach((match) => { const a = Array.isArray(match) ? match[0] : match.a || match.point_a; const b = Array.isArray(match) ? match[1] : match.b || match.point_b; if (!Array.isArray(a) || !Array.isArray(b)) return; const [ax, ay] = point(0, a[0], a[1]), [bx, by] = point(1, b[0], b[1]); ctx.strokeStyle = "#3eb69480"; ctx.lineWidth = .8; ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke(); [[ax, ay], [bx, by]].forEach(([x, y], i) => { ctx.fillStyle = colors[i]; ctx.beginPath(); ctx.arc(x, y, 2.1, 0, Math.PI * 2); ctx.fill(); }); });
  }
  async function setEvidenceLayer() {
    const selected = $("evidence-layer").value; const token = state.loadToken;
    state.evidenceLayer = "original"; state.layerImages = [];
    if (selected === "original") { drawEvidence(); return; }
    try {
      const layers = await Promise.all([state.finding.image_a, state.finding.image_b].map((source,index) => loadEvidenceImage(selected === "common_data" ? state.finding.data_evidence_urls[index ? "b" : "a"] : source.annotations.layers[selected])));
      if (token !== state.loadToken || $("evidence-layer").value !== selected) return;
      state.evidenceMode = "side"; state.evidenceLayer = selected; state.layerImages = layers; updateEvidenceMode(); drawEvidence();
    } catch (error) { $("evidence-layer").value = "original"; toast(error.message, true); }
  }
  async function saveReview(status) {
    if (!state.finding || state.reviewBusy || state.bulkBusy) return; const id = state.finding.id, jobId = state.currentJob; const note = $("review-note").value.trim();
    state.reviewBusy = true; ++state.detailRequest; state.detailLoading = null; updateSelection(); const buttons = document.querySelectorAll("[data-review]"); buttons.forEach((button) => { button.disabled = true; });
    try { await api("/api/findings/" + encodeURIComponent(id) + "/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status, note }) }); if (state.finding?.id === id) { state.finding.review_status = status; state.finding.review_note = note; $("review-message").textContent = "已保存 · " + label(status); } if (jobId === state.currentJob) await loadJob(); toast("复核结论与备注已保存在本机"); }
    catch (error) { if (state.finding?.id === id) $("review-message").textContent = "保存失败：" + error.message; toast(error.message, true); }
    finally { state.reviewBusy = false; buttons.forEach((button) => { button.disabled = false; }); updateSelection(); }
  }
  async function cancelJob() { if (!state.currentJob) return; const id = state.currentJob; $("cancel-job").disabled = true; try { await api("/api/jobs/" + encodeURIComponent(id) + "/cancel", { method: "POST" }); toast("取消请求已提交，将保留已完成结果"); if (id === state.currentJob) await loadJob(); } catch (error) { toast(error.message, true); if (id === state.currentJob) $("cancel-job").disabled = false; } }
  function exportReport(format) { if (!state.currentJob) return; const link = el("a"); link.href = "/api/jobs/" + encodeURIComponent(state.currentJob) + "/report?format=" + format; link.download = ""; document.body.append(link); link.click(); link.remove(); }
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("evidence-filter").addEventListener("change", resetResultFilter);
  $("finding-search").addEventListener("input", resetResultFilter);
  $("select-page").addEventListener("change", (event) => { if (state.bulkBusy || state.reviewBusy) return; state.selected.clear(); if (event.target.checked) state.pageFindings.forEach((finding) => state.selected.add(finding.id)); updateSelection(); });
  $("batch-apply").addEventListener("click", applyBatchReview);
  $("results-prev").addEventListener("click", () => changeResultPage(-1)); $("results-next").addEventListener("click", () => changeResultPage(1));
  $("extracted-panel").addEventListener("toggle", () => { if ($("extracted-panel").open) renderExtractedImages(); });
  $("clear-files").addEventListener("click", () => { if (state.busy) return; state.files = []; $("file-input").value = ""; renderFiles(); });
  $("focus-review").addEventListener("click", () => { $("review-note").closest(".review-form").scrollIntoView({ block: "center", behavior: "instant" }); $("review-note").focus({ preventScroll: true }); });
  $("evidence-layer").addEventListener("change", setEvidenceLayer);
  document.querySelectorAll("[data-go]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.go)));
  document.querySelector(".brand").addEventListener("click", (event) => { event.preventDefault(); setView("workspace"); });
  document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
  document.querySelectorAll("[data-sensitivity]").forEach((button) => button.addEventListener("click", () => { state.sensitivity = button.dataset.sensitivity; document.querySelectorAll("[data-sensitivity]").forEach((item) => { item.classList.toggle("selected", item.dataset.sensitivity === state.sensitivity); item.setAttribute("aria-pressed", String(item.dataset.sensitivity === state.sensitivity)); }); updateSettingsSummary(); }));
  ["include-history", "add-library", "auto-panels"].forEach((id) => $(id).addEventListener("change", updateSettingsSummary));
  $("drop-zone").addEventListener("click", () => { if (!state.busy) $("file-input").click(); }); $("drop-zone").addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (!state.busy) $("file-input").click(); } });
  $("file-input").addEventListener("change", (event) => { addFiles(event.target.files); event.target.value = ""; });
  ["dragenter", "dragover"].forEach((name) => $("drop-zone").addEventListener(name, (event) => { event.preventDefault(); $("drop-zone").classList.add("dragging"); }));
  ["dragleave", "drop"].forEach((name) => $("drop-zone").addEventListener(name, (event) => { event.preventDefault(); $("drop-zone").classList.remove("dragging"); if (name === "drop") addFiles(event.dataTransfer.files); }));
  window.addEventListener("dragover", (event) => event.preventDefault()); window.addEventListener("drop", (event) => event.preventDefault());
  $("upload-form").addEventListener("submit", submitJob); $("demo-button").addEventListener("click", loadDemo); $("back-tasks").addEventListener("click", () => setView("tasks")); $("review-filter").addEventListener("change", resetResultFilter); $("refresh-library").addEventListener("click", loadLibrary); $("library-search").addEventListener("input", renderLibrary);
  $("close-dialog").addEventListener("click", () => $("evidence-dialog").close()); $("evidence-dialog").addEventListener("click", (event) => { if (event.target === $("evidence-dialog")) { const rect = $("evidence-dialog").getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $("evidence-dialog").close(); } });
  $("evidence-dialog").addEventListener("close", () => {
    if (state.view !== "result") return;
    const card = Array.from(document.querySelectorAll(".finding-card")).find((node) => node.dataset.findingId === state.finding?.id);
    const target = evidenceReturnFocus?.isConnected ? evidenceReturnFocus : card?.querySelector(".finding-pair");
    if (target && !target.closest("dialog")) target.focus({ preventScroll: true });
    evidenceReturnFocus = null;
  });
  $("view-side").addEventListener("click", () => { state.evidenceMode = "side"; updateEvidenceMode(); drawEvidence(); }); $("view-overlay").addEventListener("click", () => { state.evidenceMode = "overlay"; updateEvidenceMode(); drawEvidence(); });
  $("view-context").addEventListener("click", () => { state.evidenceMode = "context"; updateEvidenceMode(); drawEvidence(); });
  ["overlay-opacity", "evidence-zoom", "show-regions"].forEach((id) => $(id).addEventListener("input", () => { $("zoom-value").textContent = $("evidence-zoom").value + "%"; drawEvidence(); })); window.addEventListener("resize", () => { if ($("evidence-dialog").open) drawEvidence(); });
  document.querySelectorAll("[data-review]").forEach((button) => button.addEventListener("click", () => saveReview(button.dataset.review))); $("export-html").addEventListener("click", () => exportReport("html")); $("export-csv").addEventListener("click", () => exportReport("csv")); $("export-zip").addEventListener("click", () => exportReport("zip")); $("cancel-job").addEventListener("click", cancelJob);
  async function poll() { if (state.pollBusy || document.hidden) return; state.pollBusy = true; try { const requests = []; if (Date.now() - state.lastOverview >= 15000) { state.lastOverview = Date.now(); requests.push(refreshStatus(), refreshJobs()); } if (state.view === "result" && state.currentJob) requests.push(pollJobProgress()); await Promise.allSettled(requests); } finally { state.pollBusy = false; } }
  $("acceleration-toggle").addEventListener("click", switchAcceleration);
  setMode("batch"); poll(); setInterval(poll, 3000); setInterval(() => { if (state.modeSwitch) refreshMode(); }, 800);
})();
