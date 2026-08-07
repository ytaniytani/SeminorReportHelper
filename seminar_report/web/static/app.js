"use strict";

const $ = (id) => document.getElementById(id);

let config = null;
let jobId = null;
let report = null;
let autoOpenWindow = null;

// ジョブ ID を変数だけで持つと、リロードした瞬間に走行中のジョブへ戻れなくなる。
const JOB_KEY = "seminar-report:job";

function rememberJob(id) {
  jobId = id;
  try {
    localStorage.setItem(JOB_KEY, id);
  } catch {
    // プライベートモード等で保存できなくても動作自体は続けられる
  }
}

function clearJob() {
  try {
    localStorage.removeItem(JOB_KEY);
  } catch {
    /* 同上 */
  }
}

// 切り出し範囲は動画ごとに変わるものではなく、同じ登壇環境で撮った動画に
// 繰り返し使うことが多いため、次回のフォーム表示にも引き継ぐ。
const CROP_KEY = "seminar-report:crop";

function rememberCrop(value) {
  try {
    if (value) {
      localStorage.setItem(CROP_KEY, value);
    } else {
      localStorage.removeItem(CROP_KEY);
    }
  } catch {
    /* 同上 */
  }
}

function restoreCrop() {
  try {
    return localStorage.getItem(CROP_KEY) || "";
  } catch {
    return "";
  }
}

// 完了時に新しいタブでレポートを開くかどうかの設定。ZIP を落として展開して
// 開く手間を省くための機能なので、既定は有効。
const AUTO_OPEN_KEY = "seminar-report:auto-open-html";

function rememberAutoOpen(enabled) {
  try {
    localStorage.setItem(AUTO_OPEN_KEY, enabled ? "1" : "0");
  } catch {
    /* 同上 */
  }
}

function restoreAutoOpen() {
  try {
    const saved = localStorage.getItem(AUTO_OPEN_KEY);
    return saved === null ? true : saved === "1";
  } catch {
    return true;
  }
}

// ---- 画面遷移 ----
function showView(name) {
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("active", view.id === `view-${name}`);
  }
}

function setStatus(message, kind = "") {
  const el = $("status");
  el.textContent = message;
  el.className = `status ${kind}`;
}

// ---- 初期化 ----
async function init() {
  config = await (await fetch("/api/config")).json();

  const detail = $("detail");
  for (const preset of config.presets) {
    const option = document.createElement("option");
    option.value = preset.value;
    option.textContent = `${preset.label}（約${preset.target_chars}字 / 画像 目安${preset.max_captures}枚）`;
    detail.appendChild(option);
  }
  detail.value = "standard";

  const provider = $("provider");
  for (const name of config.providers) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    provider.appendChild(option);
  }
  provider.value = config.current_provider;

  updateModelChoices();
  provider.addEventListener("change", updateModelChoices);

  if (!config.confluence_configured) {
    $("publish").disabled = true;
    $("publish").title = ".env に Confluence の接続情報が未設定です";
  }

  $("crop").value = restoreCrop();
  $("crop").addEventListener("input", () => rememberCrop($("crop").value));

  $("auto-open-html").checked = restoreAutoOpen();
  $("auto-open-html").addEventListener("change", () =>
    rememberAutoOpen($("auto-open-html").checked)
  );

  await resumePreviousJob();
}

/** 前回のジョブが残っていれば、その状態に復帰する。 */
async function resumePreviousJob() {
  let saved = null;
  try {
    saved = localStorage.getItem(JOB_KEY);
  } catch {
    return;
  }
  if (!saved) return;

  let data;
  try {
    const response = await fetch(`/api/jobs/${saved}`);
    if (!response.ok) {
      clearJob();
      return;
    }
    data = await response.json();
  } catch {
    return;
  }

  jobId = saved;
  if (data.status === "done") {
    report = data.report;
    renderReport();
    showView("result");
    setStatus("前回のジョブを復帰しました");
  } else if (data.status === "failed") {
    showView("progress");
    showFailure(data.error, data.traceback);
  } else {
    showView("progress");
    $("progress-label").textContent = "処理中のジョブに再接続しています…";
    listen();
  }
}

function updateModelChoices() {
  const providerName = $("provider").value;
  const list = $("model-list");
  list.innerHTML = "";
  for (const name of config.model_choices[providerName] || []) {
    const option = document.createElement("option");
    option.value = name;
    list.appendChild(option);
  }
  const modelInput = $("model");
  modelInput.value = "";
  modelInput.placeholder = `既定（${config.current_models[providerName] ?? "未設定"}）を使う`;
}

// ---- ファイル選択 ----
const drop = $("drop");
const videoInput = $("video");

drop.addEventListener("click", () => videoInput.click());
drop.addEventListener("dragover", (event) => {
  event.preventDefault();
  drop.classList.add("over");
});
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  drop.classList.remove("over");
  if (event.dataTransfer.files.length) {
    videoInput.files = event.dataTransfer.files;
    onFileChosen();
  }
});
videoInput.addEventListener("change", onFileChosen);

function onFileChosen() {
  const file = videoInput.files[0];
  if (!file) return;
  const mb = (file.size / 1024 / 1024).toFixed(1);
  $("drop-label").textContent = `${file.name}（${mb} MB）`;
  $("submit").disabled = false;
}

// ---- 切り出し範囲ピッカー ----
// 画像を貼り付け/ドロップし、その上でドラッグした矩形を "crop" 欄へ
// リアルタイムに反映する。値は画像の表示サイズに対する割合(0〜1)なので、
// 動画のフレームと縦横比さえ合っていれば実ピクセルサイズは問わない。
(() => {
  const cropInput = $("crop");
  const toggle = $("crop-picker-toggle");
  const panel = $("crop-picker-panel");
  const dropZone = $("crop-drop");
  const fileInput = $("crop-image-input");
  const canvasWrap = $("crop-canvas-wrap");
  const previewImage = $("crop-preview-image");
  const rectEl = $("crop-rect");
  const readout = $("crop-picker-readout");
  const clearBtn = $("crop-picker-clear");

  let objectUrl = null;
  let dragOrigin = null; // { x, y, imageRect } (imageRect は表示中の img の境界)

  toggle.addEventListener("click", () => {
    const opening = panel.hidden;
    panel.hidden = !opening;
    toggle.textContent = opening ? "画像ピッカーを閉じる" : "画像で範囲を指定";
  });

  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) loadImage(fileInput.files[0]);
  });

  dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropZone.classList.add("over");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("over"));
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("over");
    const file = event.dataTransfer.files[0];
    if (file) loadImage(file);
  });

  // クリップボード貼り付け(Ctrl+V)。ピッカーを開いている間だけ拾う。
  document.addEventListener("paste", (event) => {
    if (panel.hidden) return;
    const item = [...(event.clipboardData?.items || [])].find(
      (i) => i.type && i.type.startsWith("image/")
    );
    if (!item) return;
    const file = item.getAsFile();
    if (file) loadImage(file);
  });

  function loadImage(file) {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    previewImage.onload = () => {
      dropZone.hidden = true;
      canvasWrap.hidden = false;
      rectEl.style.display = "none";
      readout.textContent = "画像上をドラッグして範囲を選択してください";
    };
    previewImage.src = objectUrl;
  }

  clearBtn.addEventListener("click", () => {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
    previewImage.removeAttribute("src");
    canvasWrap.hidden = true;
    dropZone.hidden = false;
    rectEl.style.display = "none";
    readout.textContent = "";
  });

  previewImage.addEventListener("mousedown", (event) => {
    event.preventDefault();
    const imageRect = previewImage.getBoundingClientRect();
    dragOrigin = {
      x: clamp(event.clientX - imageRect.left, 0, imageRect.width),
      y: clamp(event.clientY - imageRect.top, 0, imageRect.height),
      imageRect,
    };
    rectEl.style.display = "block";
  });

  window.addEventListener("mousemove", (event) => {
    if (!dragOrigin) return;
    const { imageRect } = dragOrigin;
    const x = clamp(event.clientX - imageRect.left, 0, imageRect.width);
    const y = clamp(event.clientY - imageRect.top, 0, imageRect.height);

    const left = Math.min(dragOrigin.x, x);
    const top = Math.min(dragOrigin.y, y);
    const width = Math.abs(x - dragOrigin.x);
    const height = Math.abs(y - dragOrigin.y);

    rectEl.style.left = `${left}px`;
    rectEl.style.top = `${top}px`;
    rectEl.style.width = `${width}px`;
    rectEl.style.height = `${height}px`;

    applyFractions(
      left / imageRect.width,
      top / imageRect.height,
      (left + width) / imageRect.width,
      (top + height) / imageRect.height
    );
  });

  window.addEventListener("mouseup", () => {
    dragOrigin = null;
  });

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function applyFractions(left, top, right, bottom) {
    // 幅・高さ 0 のドラッグ開始直後は crop 欄を汚さない
    if (right - left < 0.01 || bottom - top < 0.01) return;
    const fmt = (v) => v.toFixed(3);
    cropInput.value = `${fmt(left)},${fmt(top)},${fmt(right)},${fmt(bottom)}`;
    readout.textContent =
      `left=${fmt(left)} top=${fmt(top)} right=${fmt(right)} bottom=${fmt(bottom)}`;
    // .value への直接代入は input イベントを発火しないため、保存処理へ明示的に伝える
    cropInput.dispatchEvent(new Event("input"));
  }
})();

// ---- 送信 ----
$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = videoInput.files[0];
  if (!file) return;

  // ジョブ完了は非同期(SSE/ポーリング)で分かるため、そのタイミングで
  // window.open() してもポップアップブロックされる。クリックというユーザー
  // 操作の直後・同期的なうちに空タブを開いておき、完了時に navigate する。
  autoOpenWindow =
    $("auto-open-html").checked ? window.open("about:blank", "_blank") : null;

  const form = new FormData();
  form.append("video", file);
  form.append("detail", $("detail").value);
  form.append("target_chars", $("target-chars").value);
  form.append("provider", $("provider").value);
  form.append("model", $("model").value);
  form.append("whisper_model", $("whisper-model").value);
  form.append("audio_language", $("audio-language").value);
  form.append("verify_captures", $("verify-captures").checked ? "true" : "false");
  form.append("include_images", $("include-images").checked ? "true" : "false");
  form.append("crop", $("crop").value);
  form.append("user_request", $("user-request").value);

  showView("progress");
  $("log").innerHTML = "";
  $("progress-label").textContent = "アップロードしています…";
  $("bar-fill").style.width = "0%";

  let jobIdFromUpload;
  try {
    jobIdFromUpload = await uploadJob(form);
  } catch (error) {
    showFailure(error.message);
    return;
  }

  rememberJob(jobIdFromUpload);
  lastStep = null;
  listen();
});

// fetch() は送信side の進捗を取れないため XHR を使う。数百MB〜GB の動画では
// 進捗が出ないと「固まった」と誤解され、リロードや強制終了を招く。
function uploadJob(form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const ratio = event.loaded / event.total;
      $("bar-fill").style.width = `${Math.round(ratio * 100)}%`;
      $("progress-detail").textContent =
        `${formatMB(event.loaded)} / ${formatMB(event.total)} (${Math.round(ratio * 100)}%)`;
    };

    // 送信完了後もサーバー側のディスク書き込みが続く。ここで一度表示を変えないと
    // 100% のまま止まって見える。
    xhr.upload.onload = () => {
      $("progress-label").textContent = "サーバーで受け取っています…";
      $("progress-detail").textContent = "動画を保存しています。しばらくお待ちください";
    };

    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(`アップロードに失敗しました (HTTP ${xhr.status})`));
        return;
      }
      try {
        resolve(JSON.parse(xhr.responseText).job_id);
      } catch {
        reject(new Error("サーバーの応答を解釈できませんでした"));
      }
    };

    xhr.onerror = () =>
      reject(new Error("サーバーに接続できません。起動したままか確認してください"));
    xhr.onabort = () => reject(new Error("アップロードが中断されました"));

    xhr.send(form);
  });
}

function formatMB(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(0)} MB`;
}

// ---- 進捗 ----
// 一次経路は SSE。切れた場合はポーリングに切り替える。長時間ジョブでは
// 接続が落ちることがあるが、ジョブ自体はサーバー側で走り続けているため、
// UI を諦めさせずに結果まで辿り着かせる。
let lastStep = null;
let pollTimer = null;

function showStep(data) {
  $("progress-label").textContent = data.label;
  $("progress-detail").textContent = data.detail || "";
  $("bar-fill").style.width = `${Math.round((data.ratio || 0) * 100)}%`;

  if (data.step !== lastStep) {
    lastStep = data.step;
    const item = document.createElement("li");
    item.textContent = data.label;
    $("log").appendChild(item);
  }
}

function showFailure(error, trace) {
  $("progress-label").textContent = "失敗しました";

  // エラー文は複数行で対処手順を含むことがある。HTML では改行が潰れるため
  // pre-wrap で保持する。
  const detail = $("progress-detail");
  detail.textContent = error || "";
  detail.style.whiteSpace = "pre-wrap";

  // スタックトレースは既定で畳んでおく。普段は邪魔だが、原因究明には要る。
  const log = $("log");
  log.innerHTML = "";
  if (trace) {
    const box = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "詳細（スタックトレース）";
    const pre = document.createElement("pre");
    pre.textContent = trace;
    pre.style.whiteSpace = "pre-wrap";
    pre.style.overflowX = "auto";
    box.append(summary, pre);
    log.appendChild(box);
  }

  // 開いておいた空タブは使い道が無くなったので閉じる(自動生成した about:blank
  // タブが残り続けると混乱するため)。
  if (autoOpenWindow && !autoOpenWindow.closed) {
    autoOpenWindow.close();
  }
  autoOpenWindow = null;

  clearJob();
}

function listen() {
  const source = new EventSource(`/api/jobs/${jobId}/events`);

  source.onmessage = (event) => showStep(JSON.parse(event.data));

  source.addEventListener("end", async (event) => {
    source.close();
    const data = JSON.parse(event.data);
    if (data.status === "failed") {
      showFailure(data.error, data.traceback);
      return;
    }
    await loadReport();
  });

  source.onerror = () => {
    source.close();
    // 接続が切れてもジョブは動き続けている。ポーリングで追いかける。
    $("progress-detail").textContent = "接続が切れました。状態を確認しています…";
    startPolling();
  };
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    let data;
    try {
      const response = await fetch(`/api/jobs/${jobId}`);
      if (!response.ok) throw new Error(String(response.status));
      data = await response.json();
    } catch {
      $("progress-detail").textContent =
        "サーバーに接続できません。起動したままか確認してください（再試行中）";
      return;
    }

    if (data.status === "failed") {
      stopPolling();
      showFailure(data.error, data.traceback);
      return;
    }
    if (data.status === "done") {
      stopPolling();
      await loadReport();
      return;
    }
    $("progress-label").textContent = "処理中です…";
    $("progress-detail").textContent = "接続が切れたため、状態を定期確認しています";
  }, 3000);
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

// ---- 結果表示 ----
async function loadReport() {
  const data = await (await fetch(`/api/jobs/${jobId}`)).json();
  report = data.report;
  renderReport();
  showView("result");
  openReportHtml();
}

/** 送信時に開いておいた空タブに report.html を読み込ませる。 */
function openReportHtml() {
  if (!autoOpenWindow || autoOpenWindow.closed) {
    autoOpenWindow = null;
    return;
  }
  autoOpenWindow.location.href = `/api/jobs/${jobId}/html`;
  autoOpenWindow = null;
}

function formatDuration(seconds) {
  const total = Math.round(seconds || 0);
  return `${Math.floor(total / 60)}分${String(total % 60).padStart(2, "0")}秒`;
}

function renderReport() {
  $("report-title").value = report.title;
  const included = report.captures.filter((c) => c.included).length;
  $("report-meta").textContent =
    `本文 ${report.char_count} 字 / ${report.sections.length} セクション / 画像 ${included} 枚 ` +
    `・元動画 ${report.source_video ?? ""}（${formatDuration(report.duration)}）`;

  $("overview").value = report.overview;
  $("key-points").value = report.key_points.join("\n");

  const container = $("sections");
  container.innerHTML = "";
  report.sections.forEach((section, index) => {
    const block = document.createElement("div");
    block.className = "block";
    block.innerHTML = `
      <input type="text" class="section-title" data-index="${index}" />
      <textarea class="section-body" data-index="${index}" rows="10"></textarea>
      <p class="hint">[[capture:...]] の行がそのまま画像の挿入位置です。移動・削除できます。</p>
    `;
    block.querySelector(".section-title").value = section.title;
    block.querySelector(".section-body").value = section.body;
    container.appendChild(block);
  });

  renderCaptures();
}

function renderCaptures() {
  const container = $("captures");
  container.innerHTML = "";

  for (const capture of report.captures) {
    const card = document.createElement("div");
    card.className = "capture";

    if (capture.url) {
      const image = document.createElement("img");
      image.src = `${capture.url}?t=${Date.now()}`;
      image.alt = capture.caption;
      image.title = "クリックで別のフレームに差し替え";
      image.addEventListener("click", () => swap(capture));
      card.appendChild(image);
    } else {
      const missing = document.createElement("div");
      missing.className = "missing";
      if (capture.candidate_count) {
        missing.classList.add("clickable");
        missing.textContent = "自動選択されませんでした。クリックして候補から選ぶ";
        missing.addEventListener("click", () => swap(capture));
      } else {
        missing.textContent = "適切なフレームが見つかりませんでした";
      }
      card.appendChild(missing);
    }

    const label = document.createElement("label");
    label.className = "check";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = capture.included;
    checkbox.disabled = !capture.url;
    checkbox.addEventListener("change", () => {
      capture.included = checkbox.checked;
    });
    label.appendChild(checkbox);
    label.append(` ${capture.timestamp} ${capture.caption}`);
    card.appendChild(label);

    container.appendChild(card);
  }
}

const swapCounters = {};

async function swap(capture) {
  if (!capture.candidate_count) return;
  swapCounters[capture.marker_id] = ((swapCounters[capture.marker_id] ?? 0) + 1) % capture.candidate_count;

  const response = await fetch(`/api/jobs/${jobId}/swap`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      marker_id: capture.marker_id,
      candidate_index: swapCounters[capture.marker_id],
    }),
  });
  if (!response.ok) {
    setStatus("差し替えに失敗しました", "error");
    return;
  }

  // 未採用だったキャプチャは、候補を選んだ時点で採用扱いにする
  if (!capture.url) {
    capture.url = `/api/jobs/${jobId}/images/${capture.marker_id}.jpg`;
    capture.included = true;
  }
  renderCaptures();
  setStatus("画像を差し替えました。「編集を保存」で出力に反映されます。");
}

// ---- 保存 / 出力 ----
$("save").addEventListener("click", async () => {
  const sections = report.sections.map((section, index) => ({
    title: document.querySelector(`.section-title[data-index="${index}"]`).value,
    body: document.querySelector(`.section-body[data-index="${index}"]`).value,
  }));

  const payload = {
    title: $("report-title").value,
    overview: $("overview").value,
    key_points: $("key-points").value.split("\n").map((s) => s.trim()).filter(Boolean),
    sections,
    excluded_captures: report.captures.filter((c) => !c.included).map((c) => c.marker_id),
  };

  setStatus("保存しています…");
  const response = await fetch(`/api/jobs/${jobId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    setStatus("保存に失敗しました", "error");
    return;
  }
  report = (await response.json()).report;
  renderReport();
  setStatus("保存しました", "ok");
});

$("download").addEventListener("click", () => {
  window.location.href = `/api/jobs/${jobId}/export`;
});

$("publish").addEventListener("click", async () => {
  const space = prompt("Confluence のスペースキー", config.confluence_space ?? "");
  if (space === null) return;

  setStatus("投稿しています…");
  const response = await fetch(`/api/jobs/${jobId}/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ space_key: space || null, title: $("report-title").value }),
  });

  const data = await response.json();
  if (!response.ok) {
    setStatus(`投稿に失敗しました: ${data.detail ?? ""}`, "error");
    return;
  }
  setStatus(`投稿しました（画像 ${data.attached} 枚）: ${data.url}`, "ok");
  window.open(data.url, "_blank");
});

$("restart").addEventListener("click", () => {
  stopPolling();
  clearJob();
  jobId = null;
  report = null;
  lastStep = null;
  $("log").innerHTML = "";
  videoInput.value = "";
  $("drop-label").textContent = "ここに .mp4 をドロップ、またはクリックして選択";
  $("submit").disabled = true;
  setStatus("");
  showView("upload");
});

init();
