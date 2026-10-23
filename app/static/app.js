"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { view: "workspace", mode: "batch", sensitivity: "strict", files: [], jobs: [], library: [], detail: null, currentJob: null, status: null, busy: false, pollBusy: false, finding: null, evidenceImages: [], contextImages: [], evidenceMode: "side", loadToken: 0 };
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
  }
  function addFiles(files) {
    const valid = state.mode === "documents" ? /\.(pdf|docx)$/i : /\.(png|jpe?g|tiff?|bmp|webp)$/i;
    const errors = [];
    for (const file of files) {
      if (!valid.test(file.name)) { errors.push("格式不支持：" + file.name); continue; }
      const limit = state.status?.limits?.max_file_mb;
      if (limit && file.size > limit * 1048576) { errors.push(file.name + " 超过 " + limit + " MB 上限"); continue; }
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
      const remove = el("button", "icon-button"); remove.type = "button"; remove.setAttribute("aria-label", "移除 " + file.name); remove.append(icon("close")); remove.addEventListener("click", () => { state.files.splice(index, 1); renderFiles(); });
      row.append(icon(state.mode === "documents" ? "file" : "image"), name, el("span", "selected-file-size", bytes(file.size)), remove); target.append(row);
    });
    $("upload-summary").textContent = state.files.length ? "已选择 " + state.files.length + " 个文件 · " + bytes(state.files.reduce((n, f) => n + f.size, 0)) : "尚未选择文件";
    $("form-error").hidden = true;
  }
  function showFormError(message) { $("form-error").textContent = message; $("form-error").hidden = false; }
  async function submitJob(event) {
    event?.preventDefault();
    if (state.busy) return;
    if (!state.files.length) return showFormError("请先选择需要核验的本地文件。");
    if (state.mode === "pair" && state.files.length !== 2) return showFormError("双图对比需要且只接受 2 张单帧图片；多页 TIFF 请使用批量模式。");
    const form = new FormData(); state.files.forEach((file) => form.append("files", file));
    form.append("mode", state.mode); form.append("sensitivity", state.sensitivity); form.append("include_history", String(state.mode !== "pair" && $("include-history").checked)); form.append("add_to_library", String($("add-library").checked)); form.append("auto_panels", String($("auto-panels").checked)); form.append("name", $("task-name").value.trim());
    state.busy = true; $("start-button").disabled = true; $("start-button").textContent = "正在导入…"; $("form-error").hidden = true;
    try { const result = await api("/api/jobs", { method: "POST", body: form }); toast("任务已创建，正在本地核验"); state.files = []; renderFiles(); $("task-name").value = ""; await openJob(result.job_id); await Promise.allSettled([refreshStatus(), refreshJobs()]); }
    catch (error) { showFormError(error.message); }
    finally { state.busy = false; $("start-button").disabled = false; $("start-button").replaceChildren(document.createTextNode("开始核验 "), icon("arrow")); }
  }
  async function refreshStatus() {
    try {
      state.status = await api("/api/status");
      $("stat-library").textContent = fmt(state.status.library_count); $("stat-jobs").textContent = fmt(state.status.jobs_count); $("stat-active").textContent = fmt(Array.isArray(state.status.active_jobs) ? state.status.active_jobs.length : state.status.active_jobs);
      $("nav-job-count").textContent = fmt(state.status.jobs_count); $("connection-label").textContent = "本地引擎已就绪"; $("connection-dot").classList.remove("offline"); $("engine-version").textContent = "ENGINE · " + state.status.engine_version;
      const limits = state.status.limits; if (limits) $("file-limits").textContent = "最多 " + limits.max_files + " 个文件 · 单文件 ≤ " + limits.max_file_mb + " MB";
    } catch (error) { $("connection-label").textContent = "本地引擎暂时无法连接"; $("connection-dot").classList.add("offline"); }
  }
  function taskRow(job) {
    const row = el("button", "task-row"); row.type = "button"; const ico = el("span", "task-file-icon"); ico.append(icon(job.mode === "documents" ? "file" : job.mode === "pair" ? "pair" : "image"));
    const info = el("span", "task-info"); info.append(el("strong", "", job.name || "未命名核验任务"), el("small", "", label(job.mode) + " · " + formatDate(job.created_at) + " · " + fmt(job.images_count) + " 张图像"));
    const results = el("span", "task-results", fmt(job.findings_count) + " 条线索"); results.append(el("small", "", "证据需人工复核")); row.append(ico, info, results, el("span", "badge " + job.status, label(job.status)), el("span", "task-chevron", "›")); row.addEventListener("click", () => openJob(job.id)); return row;
  }
  function renderJobs() {
    const recent = clear($("recent-tasks")), all = clear($("all-tasks"));
    if (!state.jobs.length) { empty(recent, "准备好开始第一次核验了吗？", "选择文件并创建任务，结果将在这里保留。", "clock"); empty(all, "还没有检测记录", "从图像核验工作台创建第一个任务。", "clock"); return; }
    state.jobs.slice(0, 3).forEach((job) => recent.append(taskRow(job))); state.jobs.forEach((job) => all.append(taskRow(job)));
  }
  async function refreshJobs() { try { const data = await api("/api/jobs"); state.jobs = data.jobs || []; renderJobs(); } catch (error) { if (state.view === "tasks") { clear($("all-tasks")); empty($("all-tasks"), "暂时无法读取记录", error.message); } } }
  async function openJob(id) { state.currentJob = id; state.detail = null; clear($("findings-grid")); empty($("findings-grid"), "正在读取核验记录", "加载本地证据与复核状态…"); setView("result"); await loadJob(); }
  async function loadJob() { const id = state.currentJob; if (!id) return; try { const detail = await api("/api/jobs/" + encodeURIComponent(id)); if (id !== state.currentJob) return; state.detail = detail; renderJob(); } catch (error) { $("job-error").textContent = error.message; $("job-error").hidden = false; } }
  function renderJob() {
    const { job, images = [], findings = [] } = state.detail;
    $("result-title").textContent = job.name || "核验结果";
    $("result-subtitle").textContent = label(job.mode) + " · " + formatDate(job.created_at) + " · " + label(job.status) + (job.settings?.sensitivity ? " · " + (job.settings.sensitivity === "strict" ? "严格策略" : "均衡策略") : "");
    const active = activeStatuses.has(job.status); $("job-progress").hidden = !active; $("progress-label").textContent = job.status === "cancelling" ? "正在取消，将在当前图片对比较后停止" : job.status === "queued" ? "任务已排队，等待本地引擎" : "正在本地解析与比对";
    $("progress-bar").value = Number(job.progress || 0); $("progress-count").textContent = fmt(job.processed_pairs) + " / " + fmt(job.total_pairs) + " 对";
    $("cancel-job").hidden = !active; $("cancel-job").disabled = job.status === "cancelling";
    $("job-error").hidden = !job.error; $("job-error").textContent = job.error || "";
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
    renderFindings(); const imageTarget = clear($("extracted-images")); images.forEach((img) => imageTarget.append(imageCard(img))); $("extracted-count").textContent = images.length + " 张";
  }
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
    if (!state.detail) return; const target = clear($("findings-grid")); const filter = $("review-filter").value; const evidenceFilter = $("evidence-filter").value; const findings = (state.detail.findings || []).filter((f) => (filter === "all" || f.review_status === filter) && (evidenceFilter === "all" || (f.evidence_class || "legacy_unclassified") === evidenceFilter));
    if (!findings.length) {
      const job = state.detail.job;
      const title = filter !== "all" ? "当前筛选下没有线索" : activeStatuses.has(job.status) ? "正在寻找相似线索" : job.status !== "completed" ? "当前尚无可展示线索 · 检测未完成" : "当前规则下未检出相似线索";
      const description = filter !== "all" ? "切换为「全部线索」查看其他复核状态。" : job.status === "completed" ? "这不等于绝无重复。请结合导入警告、原始图像与检测能力边界继续复核。" : "已处理部分的结果将在这里展示，请同时关注任务进度与提示。";
      empty(target, title, description); return;
    }
    findings.forEach((finding) => target.append(findingCard(finding)));
  }
  function imageCard(img) { const card = el("article", "image-card"); const link = el("a"); link.href = img.url; link.target = "_blank"; link.rel = "noopener"; link.title = "打开查重视图"; const image = el("img"); image.src = img.thumbnail_url || img.url; image.alt = img.name || "科研图像查重视图"; image.loading = "lazy"; link.append(image); const info = el("div", "image-card-info"); const title = el("strong", "", img.name || "图像"); title.title = img.name || ""; const source = el("small", "", (img.source || "本地图像") + (img.page ? " · 第 " + img.page + " 页" : "")); source.title = source.textContent; info.append(title, source, el("small", "", img.width + " × " + img.height + " px · 查重视图")); card.append(link, info); return card; }
  async function loadLibrary() { try { const data = await api("/api/library"); state.library = data.images || []; renderLibrary(); } catch (error) { clear($("library-images")); empty($("library-images"), "暂时无法读取图库", error.message); } }
  function renderLibrary() { const target = clear($("library-images")); const term = $("library-search").value.trim().toLowerCase(); const list = state.library.filter((img) => (img.name + " " + img.source).toLowerCase().includes(term)); $("library-count").textContent = "共 " + state.library.length + " 张" + (term ? " · 当前显示 " + list.length + " 张" : ""); if (!list.length) empty(target, term ? "没有匹配的图像" : "课题组图库还是空的", term ? "试试其他文件名或来源关键词。" : "创建核验任务时勾选「检测图像加入本地图库」，完成后可用于跨任务比对。", "grid"); else list.forEach((img) => target.append(imageCard(img))); }
  async function loadDemo() {
    const button = $("demo-button"); button.disabled = true;
    try { const data = await api("/api/demo-files"); if (!data.files?.length) throw new Error("暂无演示文件，请导入自己的图像。"); const files = await Promise.all(data.files.map(async (file) => { const response = await fetch(file.url); if (!response.ok) throw new Error("演示文件读取失败"); return new File([await response.blob()], file.name, { type: "image/png" }); })); setMode("batch"); state.files = []; addFiles(files); $("task-name").value = "示例 · 科研图像核验"; $("include-history").checked = false; $("add-library").checked = false; toast("演示文件已载入，不会加入图库。点击「开始核验」运行。"); $("drop-zone").scrollIntoView({ behavior: "smooth", block: "center" }); }
    catch (error) { toast(error.message, true); } finally { button.disabled = false; }
  }
  async function openEvidence(finding) {
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
    if (!state.finding) return; const id = state.finding.id; const note = $("review-note").value.trim(); const buttons = document.querySelectorAll("[data-review]"); buttons.forEach((button) => { button.disabled = true; });
    try { await api("/api/findings/" + encodeURIComponent(id) + "/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status, note }) }); if (state.finding?.id === id) { state.finding.review_status = status; state.finding.review_note = note; $("review-message").textContent = "已保存 · " + label(status); } const local = state.detail?.findings.find((f) => f.id === id); if (local) { local.review_status = status; local.review_note = note; } renderJob(); toast("复核结论与备注已保存在本机"); }
    catch (error) { $("review-message").textContent = "保存失败：" + error.message; toast(error.message, true); }
    finally { buttons.forEach((button) => { button.disabled = false; }); }
  }
  async function cancelJob() { if (!state.currentJob) return; $("cancel-job").disabled = true; try { await api("/api/jobs/" + encodeURIComponent(state.currentJob) + "/cancel", { method: "POST" }); toast("取消请求已提交，将在当前比较完成后停止"); await loadJob(); } catch (error) { toast(error.message, true); $("cancel-job").disabled = false; } }
  function exportReport(format) { if (!state.currentJob) return; const link = el("a"); link.href = "/api/jobs/" + encodeURIComponent(state.currentJob) + "/report?format=" + format; link.download = ""; document.body.append(link); link.click(); link.remove(); }
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("evidence-filter").addEventListener("change", renderFindings);
  $("evidence-layer").addEventListener("change", setEvidenceLayer);
  document.querySelectorAll("[data-go]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.go)));
  document.querySelector(".brand").addEventListener("click", (event) => { event.preventDefault(); setView("workspace"); });
  document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
  document.querySelectorAll("[data-sensitivity]").forEach((button) => button.addEventListener("click", () => { state.sensitivity = button.dataset.sensitivity; document.querySelectorAll("[data-sensitivity]").forEach((item) => { item.classList.toggle("selected", item.dataset.sensitivity === state.sensitivity); item.setAttribute("aria-pressed", String(item.dataset.sensitivity === state.sensitivity)); }); }));
  $("drop-zone").addEventListener("click", () => $("file-input").click()); $("drop-zone").addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $("file-input").click(); } });
  $("file-input").addEventListener("change", (event) => { addFiles(event.target.files); event.target.value = ""; });
  ["dragenter", "dragover"].forEach((name) => $("drop-zone").addEventListener(name, (event) => { event.preventDefault(); $("drop-zone").classList.add("dragging"); }));
  ["dragleave", "drop"].forEach((name) => $("drop-zone").addEventListener(name, (event) => { event.preventDefault(); $("drop-zone").classList.remove("dragging"); if (name === "drop") addFiles(event.dataTransfer.files); }));
  window.addEventListener("dragover", (event) => event.preventDefault()); window.addEventListener("drop", (event) => event.preventDefault());
  $("upload-form").addEventListener("submit", submitJob); $("demo-button").addEventListener("click", loadDemo); $("back-tasks").addEventListener("click", () => setView("tasks")); $("review-filter").addEventListener("change", renderFindings); $("refresh-library").addEventListener("click", loadLibrary); $("library-search").addEventListener("input", renderLibrary);
  $("close-dialog").addEventListener("click", () => $("evidence-dialog").close()); $("evidence-dialog").addEventListener("click", (event) => { if (event.target === $("evidence-dialog")) { const rect = $("evidence-dialog").getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $("evidence-dialog").close(); } });
  $("view-side").addEventListener("click", () => { state.evidenceMode = "side"; updateEvidenceMode(); drawEvidence(); }); $("view-overlay").addEventListener("click", () => { state.evidenceMode = "overlay"; updateEvidenceMode(); drawEvidence(); });
  $("view-context").addEventListener("click", () => { state.evidenceMode = "context"; updateEvidenceMode(); drawEvidence(); });
  ["overlay-opacity", "evidence-zoom", "show-regions"].forEach((id) => $(id).addEventListener("input", () => { $("zoom-value").textContent = $("evidence-zoom").value + "%"; drawEvidence(); })); window.addEventListener("resize", () => { if ($("evidence-dialog").open) drawEvidence(); });
  document.querySelectorAll("[data-review]").forEach((button) => button.addEventListener("click", () => saveReview(button.dataset.review))); $("export-html").addEventListener("click", () => exportReport("html")); $("export-csv").addEventListener("click", () => exportReport("csv")); $("cancel-job").addEventListener("click", cancelJob);
  async function poll() { if (state.pollBusy || document.hidden) return; state.pollBusy = true; try { const requests = [refreshStatus(), refreshJobs()]; if (state.view === "result" && state.currentJob && (!state.detail || activeStatuses.has(state.detail.job.status))) requests.push(loadJob()); await Promise.allSettled(requests); } finally { state.pollBusy = false; } }
  setMode("batch"); poll(); setInterval(poll, 3000);
})();
