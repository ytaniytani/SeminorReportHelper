"use strict";

const $ = (id) => document.getElementById(id);

let config = null;
let jobId = null;
let report = null;
let autoOpenWindow = null;

// 複数動画を選択したときのキュー。1本のみの選択時は空のままで、
// 既存の単一ジョブの経路(JOB_KEY 側)をそのまま使う。
let queue = [];
let queueIndex = -1;
let queueSettings = null;
let queueAborted = false;

// 中止まわり。アップロード中はまだジョブが無いので、転送している XHR 自体を
// 打ち切る必要がある。cancelRequested は「中断は利用者の意思によるものか」を
// 判別するためのフラグ(失敗表示にしないため)。
let cancelRequested = false;
let currentXhr = null;

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

// バッチ(複数動画)の進行状況。File オブジェクトは保存できないため、
// ジョブIDと状態だけを永続化する。リロード後は「まだ送信していない動画」は
// 復元できない(ブラウザの制約)。
const QUEUE_KEY = "seminar-report:queue";

function saveQueue() {
  try {
    localStorage.setItem(
      QUEUE_KEY,
      JSON.stringify(queue.map((item) => ({ name: item.name, jobId: item.jobId, status: item.status, error: item.error })))
    );
  } catch {
    /* 同上 */
  }
}

function clearQueue() {
  try {
    localStorage.removeItem(QUEUE_KEY);
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
  updateAutoOpenAvailability(0);

  await resumeState();
}

/** バッチが途中だった場合はそちらを優先し、無ければ単一ジョブの復帰を試みる。 */
async function resumeState() {
  let saved = null;
  try {
    saved = localStorage.getItem(QUEUE_KEY);
  } catch {
    saved = null;
  }
  if (saved) {
    try {
      await resumeQueue(JSON.parse(saved));
      return;
    } catch {
      clearQueue();
    }
  }
  await resumePreviousJob();
}

/** バッチ処理の途中でリロードされた場合、既に送信済みのジョブの状態を復元する。 */
async function resumeQueue(saved) {
  queue = saved.map((item) => ({
    file: null,
    name: item.name,
    jobId: item.jobId,
    status: item.status,
    error: item.error || null,
  }));
  queueIndex = queue.findIndex((item) => item.status === "processing");

  if (queueIndex === -1) {
    // 処理中のものが無い = 前回のバッチは完了(または未開始)している
    finishQueue();
    return;
  }

  jobId = queue[queueIndex].jobId;

  // サーバー再起動でジョブがメモリから消えていると、listen() の SSE がすぐ
  // 失敗してポーリングに落ち、404 を延々と繰り返すことになる。先に存在を
  // 確認し、無ければバッチごと諦めて投入画面に戻す。
  let exists = true;
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    exists = response.ok;
  } catch {
    // 応答が取れない場合は判断できないので、いつも通り再接続を試みる
    exists = true;
  }
  if (!exists) {
    clearQueue();
    queue = [];
    queueIndex = -1;
    throw new Error("job not found");
  }

  showView("progress");
  beginProgressView();
  showBatchPanel();
  renderBatchList();
  $("batch-heading").textContent = `動画 ${queueIndex + 1}/${queue.length}: ${queue[queueIndex].name}`;
  $("progress-label").textContent = "処理中のジョブに再接続しています…";
  listen();
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
    hideBatchPanel();
    showFailure(data.error, data.traceback);
  } else if (data.status === "cancelled") {
    showView("progress");
    hideBatchPanel();
    showCancelled();
  } else {
    showView("progress");
    beginProgressView();
    hideBatchPanel();
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
  const files = Array.from(videoInput.files);
  if (!files.length) return;

  if (files.length === 1) {
    const file = files[0];
    const mb = (file.size / 1024 / 1024).toFixed(1);
    $("drop-label").textContent = `${file.name}（${mb} MB）`;
  } else {
    const totalMb = (files.reduce((sum, f) => sum + f.size, 0) / 1024 / 1024).toFixed(1);
    $("drop-label").textContent =
      `${files.length} 件選択（合計 ${totalMb} MB）: ${files.map((f) => f.name).join(", ")}`;
  }
  $("submit").disabled = false;
  updateAutoOpenAvailability(files.length);
}

// ポップアップは同期的なユーザー操作の直後にしか開けない。複数動画は
// 非同期に完了していくため、2本目以降の自動タブオープンはブロックされる。
// バッチ時は自動オープンを諦め、完了した動画ごとに手動リンクを出す。
function updateAutoOpenAvailability(count) {
  const checkbox = $("auto-open-html");
  const hint = $("auto-open-hint");
  if (count > 1) {
    checkbox.disabled = true;
    hint.textContent =
      "（複数選択時は無効。完了した動画ごとにリンクから開いてください）";
  } else {
    checkbox.disabled = false;
    hint.textContent = "";
  }
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
// 動画の切り出し範囲・LLMプロバイダ/モデル・詳細度などの生成条件は、
// フォームから一度だけ読み取ってキュー内の全動画で使い回す。
function readFormSettings() {
  return {
    detail: $("detail").value,
    target_chars: $("target-chars").value,
    provider: $("provider").value,
    model: $("model").value,
    whisper_model: $("whisper-model").value,
    audio_language: $("audio-language").value,
    verify_captures: $("verify-captures").checked,
    include_images: $("include-images").checked,
    crop: $("crop").value,
    user_request: $("user-request").value,
  };
}

function buildJobForm(file, settings) {
  const form = new FormData();
  form.append("video", file);
  form.append("detail", settings.detail);
  form.append("target_chars", settings.target_chars);
  form.append("provider", settings.provider);
  form.append("model", settings.model);
  form.append("whisper_model", settings.whisper_model);
  form.append("audio_language", settings.audio_language);
  form.append("verify_captures", settings.verify_captures ? "true" : "false");
  form.append("include_images", settings.include_images ? "true" : "false");
  form.append("crop", settings.crop);
  form.append("user_request", settings.user_request);
  return form;
}

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = Array.from(videoInput.files);
  if (!files.length) return;

  const settings = readFormSettings();

  if (files.length === 1) {
    queue = [];
    clearQueue();
    await submitSingle(files[0], settings);
    return;
  }

  queue = files.map((file) => ({
    file,
    name: file.name,
    jobId: null,
    status: "pending",
    error: null,
  }));
  queueIndex = -1;
  queueAborted = false;
  saveQueue();

  showView("progress");
  beginProgressView();
  showBatchPanel();
  $("log").innerHTML = "";
  await advanceQueue(settings);
});

async function submitSingle(file, settings) {
  // ジョブ完了は非同期(SSE/ポーリング)で分かるため、そのタイミングで
  // window.open() してもポップアップブロックされる。クリックというユーザー
  // 操作の直後・同期的なうちに空タブを開いておき、完了時に navigate する。
  autoOpenWindow =
    $("auto-open-html").checked ? window.open("about:blank", "_blank") : null;

  showView("progress");
  beginProgressView();
  hideBatchPanel();
  $("log").innerHTML = "";
  $("progress-label").textContent = "アップロードしています…";
  $("bar-fill").style.width = "0%";

  let jobIdFromUpload;
  try {
    jobIdFromUpload = await uploadJob(buildJobForm(file, settings));
  } catch (error) {
    // アップロード中に中止ボタンを押した場合は、失敗ではなく中止として扱う
    if (cancelRequested) {
      showCancelled();
      return;
    }
    showFailure(error.message);
    return;
  }

  rememberJob(jobIdFromUpload);
  lastStep = null;
  listen();
}

/** キューの次の動画を送信する。全件終わっていればバッチ完了画面を出す。 */
async function advanceQueue(settings) {
  if (settings) queueSettings = settings;

  // 中止された場合は次の動画に進まず、未処理分をまとめて中止扱いにする
  if (queueAborted) {
    for (const item of queue) {
      if (item.status === "pending") item.status = "cancelled";
    }
    finishQueue();
    return;
  }

  queueIndex += 1;

  if (queueIndex >= queue.length) {
    finishQueue();
    return;
  }

  const item = queue[queueIndex];
  item.status = "processing";
  saveQueue();
  renderBatchList();
  $("batch-heading").textContent = `動画 ${queueIndex + 1}/${queue.length}: ${item.name}`;
  $("progress-label").textContent = "アップロードしています…";
  $("progress-detail").textContent = "";
  $("bar-fill").style.width = "0%";
  $("log").innerHTML = "";
  lastStep = null;

  if (!item.file) {
    // リロードでFile参照を失った(未送信のまま残っていた)動画は送り直せない
    item.status = "failed";
    item.error = "リロード前に未送信だった動画です。もう一度選び直してください";
    saveQueue();
    renderBatchList();
    await advanceQueue();
    return;
  }

  let jobIdFromUpload;
  try {
    jobIdFromUpload = await uploadJob(buildJobForm(item.file, queueSettings));
  } catch (error) {
    // アップロード中の中止は失敗として数えない
    if (queueAborted) {
      item.status = "cancelled";
    } else {
      item.status = "failed";
      item.error = error.message;
    }
    saveQueue();
    renderBatchList();
    await advanceQueue();
    return;
  }

  item.jobId = jobIdFromUpload;
  jobId = jobIdFromUpload;
  saveQueue();
  listen();
}

// fetch() は送信side の進捗を取れないため XHR を使う。数百MB〜GB の動画では
// 進捗が出ないと「固まった」と誤解され、リロードや強制終了を招く。
function uploadJob(form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    // 中止ボタンから転送を打ち切れるように参照を持っておく
    currentXhr = xhr;
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
      currentXhr = null;
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

    xhr.onerror = () => {
      currentXhr = null;
      reject(new Error("サーバーに接続できません。起動したままか確認してください"));
    };
    xhr.onabort = () => {
      currentXhr = null;
      reject(new Error("アップロードが中断されました"));
    };

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
  // 中止を要求した後は「停止しています…」の表示を残す。停止は次の関門まで
  // 数秒かかることがあり、その間に進捗が上書きされると押せていないように見える。
  if (!cancelRequested) {
    $("progress-label").textContent = data.label;
    $("progress-detail").textContent = data.detail || "";
  }
  $("bar-fill").style.width = `${Math.round((data.ratio || 0) * 100)}%`;

  if (data.step !== lastStep) {
    lastStep = data.step;
    const item = document.createElement("li");
    item.textContent = data.label;
    $("log").appendChild(item);
  }
}

// ---- 中止 ----
function setProgressButtons({ cancel, back }) {
  $("cancel-job").hidden = !cancel;
  $("cancel-job").disabled = false;
  $("progress-back").hidden = !back;
}

/** 進捗画面に入るときの初期状態(中止ボタンだけを出す)。 */
function beginProgressView() {
  cancelRequested = false;
  setProgressButtons({ cancel: true, back: false });
}

function cancelConfirmMessage() {
  if (queue.length) {
    const remaining = queue.length - queueIndex - 1;
    const current = queue[queueIndex]?.name ?? "";
    return (
      `処理を中止しますか？\n\n` +
      `処理中の「${current}」を中止し、残り ${remaining} 件の動画も処理しません。\n` +
      `完了済みの動画のレポートはそのまま残ります。`
    );
  }
  return (
    "処理を中止しますか？\n\n" +
    "ここまでの処理内容は破棄され、レポートは作成されません。\n" +
    "（文字起こしが終わっていればキャッシュに残るため、やり直しは速くなります）"
  );
}

$("cancel-job").addEventListener("click", async () => {
  if (!confirm(cancelConfirmMessage())) return;

  cancelRequested = true;
  if (queue.length) queueAborted = true;

  $("cancel-job").disabled = true;
  $("progress-label").textContent = "停止しています…";
  $("progress-detail").textContent = "現在の処理の区切りで停止します。少しお待ちください";

  // アップロード中はまだジョブ ID が無い。転送そのものを打ち切る。
  if (currentXhr) {
    currentXhr.abort();
    return;
  }
  if (!jobId) return;

  try {
    await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  } catch {
    // 応答が取れなくてもサーバー側のフラグは立っている可能性がある。
    // 実際の停止は SSE / ポーリングの結果で拾う。
  }
});

/** 中止が完了したときの表示(単一動画の場合)。 */
function showCancelled() {
  stopPolling();
  $("progress-label").textContent = "処理を中止しました";
  $("progress-detail").textContent = "レポートは作成されていません。";
  $("progress-detail").style.whiteSpace = "normal";
  $("bar-fill").style.width = "0%";
  closeAutoOpenWindow();
  clearJob();
  setProgressButtons({ cancel: false, back: true });
}

/** 送信時に開いておいた空タブを閉じる(使い道が無くなった場合)。 */
function closeAutoOpenWindow() {
  if (autoOpenWindow && !autoOpenWindow.closed) {
    autoOpenWindow.close();
  }
  autoOpenWindow = null;
}

$("progress-back").addEventListener("click", () => resetToUploadView());

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
  closeAutoOpenWindow();

  clearJob();
  // 失敗した画面で手詰まりにさせない
  setProgressButtons({ cancel: false, back: true });
}

function listen() {
  const source = new EventSource(`/api/jobs/${jobId}/events`);

  source.onmessage = (event) => showStep(JSON.parse(event.data));

  source.addEventListener("end", async (event) => {
    source.close();
    await onJobFinished(JSON.parse(event.data));
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
    let response;
    try {
      response = await fetch(`/api/jobs/${jobId}`);
    } catch {
      // ネットワーク瞬断など。回復する見込みがあるのでリトライを続ける。
      $("progress-detail").textContent =
        "サーバーに接続できません。起動したままか確認してください（再試行中）";
      return;
    }

    if (response.status === 404) {
      // サーバーの再起動などでジョブ自体が消えている。何度リトライしても
      // 成功しないので、ここで諦めて利用者に知らせる(でないと 404 を
      // 3秒おきに延々と繰り返すだけになる)。
      stopPolling();
      await handleJobNotFound();
      return;
    }
    if (!response.ok) {
      $("progress-detail").textContent =
        "サーバーに接続できません。起動したままか確認してください（再試行中）";
      return;
    }

    const data = await response.json();
    if (data.status === "failed" || data.status === "done" || data.status === "cancelled") {
      stopPolling();
      await onJobFinished(data);
      return;
    }
    $("progress-label").textContent = "処理中です…";
    $("progress-detail").textContent = "接続が切れたため、状態を定期確認しています";
  }, 3000);
}

/** ジョブがサーバー側から見つからなくなった場合の処理(主にサーバー再起動)。
 * リトライしても回復しないため、ポーリングを止めて分かりやすく知らせる。 */
async function handleJobNotFound() {
  if (queue.length) {
    const item = queue[queueIndex];
    item.status = "failed";
    item.error = "サーバーが再起動されたため、ジョブの情報が失われました";
    saveQueue();
    renderBatchList();
    await advanceQueue();
    return;
  }

  $("progress-label").textContent = "ジョブが見つかりません";
  $("progress-detail").textContent =
    "サーバーが再起動された可能性があります。もう一度動画を選択してやり直してください。";
  $("progress-detail").style.whiteSpace = "normal";
  $("bar-fill").style.width = "0%";
  closeAutoOpenWindow();
  clearJob();
  setProgressButtons({ cancel: false, back: true });
}

/** ジョブ1本の完了(成功/失敗)を、単一動画とバッチ動画それぞれの流儀で処理する。 */
async function onJobFinished(data) {
  if (queue.length) {
    const item = queue[queueIndex];
    if (data.status === "cancelled") {
      item.status = "cancelled";
      // 中止は「このバッチをやめる」という意思表示。リロードを挟んで中止済みの
      // ジョブに再接続した場合も、残りを勝手に処理し始めないようにする。
      queueAborted = true;
    } else if (data.status === "failed") {
      item.status = "failed";
      item.error = data.error;
    } else {
      item.status = "done";
    }
    saveQueue();
    renderBatchList();
    // 中止されていれば advanceQueue が残りを処理せずに打ち切る
    await advanceQueue();
    return;
  }

  if (data.status === "cancelled") {
    showCancelled();
    return;
  }
  if (data.status === "failed") {
    showFailure(data.error, data.traceback);
    return;
  }
  await loadReport();
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

/** 投入画面に戻して、次の動画を受け付けられる状態にする。 */
function resetToUploadView() {
  stopPolling();
  clearJob();
  clearQueue();
  jobId = null;
  report = null;
  lastStep = null;
  queue = [];
  queueIndex = -1;
  queueSettings = null;
  queueAborted = false;
  cancelRequested = false;
  currentXhr = null;
  $("log").innerHTML = "";
  videoInput.value = "";
  $("drop-label").textContent = "ここに .mp4 をドロップ、またはクリックして選択（複数選択で連続処理）";
  $("submit").disabled = true;
  updateAutoOpenAvailability(0);
  setStatus("");
  showView("upload");
}

$("restart").addEventListener("click", resetToUploadView);

// ---- バッチ(複数動画)の進捗・完了表示 ----
function showBatchPanel() {
  $("batch-status").hidden = false;
}

function hideBatchPanel() {
  $("batch-status").hidden = true;
}

const BATCH_STATE_LABELS = {
  pending: "待機中",
  processing: "処理中…",
  done: "完了",
  failed: "失敗",
  cancelled: "中止",
};

function renderBatchList() {
  const list = $("batch-list");
  list.innerHTML = "";
  for (const item of queue) {
    const li = document.createElement("li");
    li.className = item.status;

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = item.name;
    li.appendChild(name);

    const state = document.createElement("span");
    state.className = "state";
    state.textContent =
      item.status === "failed" && item.error
        ? `失敗: ${item.error}`
        : BATCH_STATE_LABELS[item.status] || item.status;
    li.appendChild(state);

    if (item.status === "done" && item.jobId) {
      const link = document.createElement("a");
      link.href = `/api/jobs/${item.jobId}/html`;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "report.html を開く";
      li.appendChild(link);
    }

    list.appendChild(li);
  }
}

/** バッチの全動画が終わったら、動画ごとのリンク一覧を出す完了画面へ切り替える。 */
function finishQueue() {
  clearQueue();
  setProgressButtons({ cancel: false, back: false });

  const doneCount = queue.filter((item) => item.status === "done").length;
  const cancelledCount = queue.filter((item) => item.status === "cancelled").length;
  let summary = `${queue.length} 件中 ${doneCount} 件成功`;
  if (cancelledCount) summary += `（${cancelledCount} 件は中止）`;
  $("batch-summary").textContent = summary;

  const list = $("batch-result-list");
  list.innerHTML = "";
  for (const item of queue) {
    const li = document.createElement("li");
    li.className = item.status;

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = item.name;
    li.appendChild(name);

    if (item.status === "done" && item.jobId) {
      const htmlLink = document.createElement("a");
      htmlLink.href = `/api/jobs/${item.jobId}/html`;
      htmlLink.target = "_blank";
      htmlLink.rel = "noopener";
      htmlLink.textContent = "report.html を開く";
      li.appendChild(htmlLink);

      const zipLink = document.createElement("a");
      zipLink.href = `/api/jobs/${item.jobId}/export`;
      zipLink.textContent = "ZIP";
      li.appendChild(zipLink);
    } else {
      const state = document.createElement("span");
      state.className = "state";
      state.textContent =
        item.status === "cancelled" ? "中止しました" : `失敗: ${item.error || ""}`;
      li.appendChild(state);
    }

    list.appendChild(li);
  }

  showView("batch");
}

$("batch-restart").addEventListener("click", resetToUploadView);

init();
