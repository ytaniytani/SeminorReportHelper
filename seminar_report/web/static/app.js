"use strict";

const $ = (id) => document.getElementById(id);

let config = null;
let jobId = null;
let report = null;

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
    option.textContent = `${preset.label}（約${preset.target_chars}字 / 画像${preset.max_captures}枚）`;
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
    showFailure(data.error);
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

// ---- 送信 ----
$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = videoInput.files[0];
  if (!file) return;

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

  showView("progress");
  $("log").innerHTML = "";
  $("progress-label").textContent = "アップロードしています…";

  const response = await fetch("/api/jobs", { method: "POST", body: form });
  if (!response.ok) {
    $("progress-label").textContent = "アップロードに失敗しました";
    return;
  }
  rememberJob((await response.json()).job_id);
  lastStep = null;
  listen();
});

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

function showFailure(error) {
  $("progress-label").textContent = "失敗しました";
  $("progress-detail").textContent = error || "";
  clearJob();
}

function listen() {
  const source = new EventSource(`/api/jobs/${jobId}/events`);

  source.onmessage = (event) => showStep(JSON.parse(event.data));

  source.addEventListener("end", async (event) => {
    source.close();
    const data = JSON.parse(event.data);
    if (data.status === "failed") {
      showFailure(data.error);
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
      showFailure(data.error);
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
