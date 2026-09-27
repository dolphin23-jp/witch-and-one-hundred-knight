// 魔女と百騎兵 原作音源フォルダの受け口（Claude Code のクラウドセッション用）
//
// Drive コネクタでは 10MB を超える音声を読み書きできないため、この受け口を通す。
// 読み書きできるのは「魔女と百騎兵 原作音源」フォルダの中だけ。
// 呼び出しには鍵が要る。鍵は最初の呼び出しで作られ、フォルダ内の「Claude受け口_鍵.txt」に置かれる
// （Claude は Drive コネクタでこのファイルを読む。URL だけでは何もできない）。
//
// 置き方: script.google.com で新しいプロジェクトを作り、このファイルの中身を貼って保存し、
// ウェブアプリとしてデプロイする（実行ユーザー: 自分、アクセスできるユーザー: 全員）。手順は setup/drive_bridge.md。

const ROOT_ID = '1TMvhdD_r2KuW6iblN86JY567IpwalXSz';   // 魔女と百騎兵 原作音源
const KEY_FILE = 'Claude受け口_鍵.txt';
const API = 'https://www.googleapis.com/drive/v3/files';
const UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files';
const FOLDER = 'application/vnd.google-apps.folder';

function doGet(e) { return handle_(e); }
function doPost(e) { return handle_(e); }

function handle_(e) {
  const p = Object.assign({}, (e && e.parameter) || {});
  if (e && e.postData && String(e.postData.type).indexOf('json') >= 0) {
    Object.assign(p, JSON.parse(e.postData.contents));
  }
  let out;
  try {
    out = route_(p);
  } catch (err) {
    out = { ok: false, error: String((err && err.message) || err) };
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

function route_(p) {
  const props = PropertiesService.getScriptProperties();
  let key = props.getProperty('KEY');
  if (!p.action || p.action === 'init') {
    if (!key) {
      key = Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, '');
      props.setProperty('KEY', key);
      DriveApp.getFolderById(ROOT_ID).createFile(KEY_FILE, key, MimeType.PLAIN_TEXT);
    }
    return { ok: true, message: '受け口は動いています。鍵は「魔女と百騎兵 原作音源」フォルダの ' + KEY_FILE + ' にあります。' };
  }
  if (!key || p.key !== key) throw new Error('鍵が違います');
  switch (p.action) {
    case 'list': return { ok: true, files: list_(p.folder || ROOT_ID) };
    case 'mkdir': return { ok: true, id: mkdir_(p.path) };
    case 'info': check_(p.id); return { ok: true, file: meta_(p.id, 'id,name,mimeType,size,md5Checksum,parents,webViewLink') };
    case 'upload': return { ok: true, url: startUpload_(p) };
    case 'put': return put_(p);
    case 'download': return download_(p);
  }
  throw new Error('知らない action: ' + p.action);
}

// ---------- Drive API ----------

function api_(url, opt) {
  opt = Object.assign({}, opt || {});
  opt.headers = Object.assign({ Authorization: 'Bearer ' + ScriptApp.getOAuthToken() }, opt.headers || {});
  opt.muteHttpExceptions = true;
  const r = UrlFetchApp.fetch(url, opt);
  const code = r.getResponseCode();
  if (code >= 400) throw new Error(code + ' ' + r.getContentText().slice(0, 300));
  return r;
}

function meta_(id, fields) {
  return JSON.parse(api_(API + '/' + id + '?fields=' + encodeURIComponent(fields)).getContentText());
}

// 原作音源フォルダの中か（親をたどる）
function check_(id) {
  let cur = id;
  for (let i = 0; i < 12; i++) {
    if (cur === ROOT_ID) return;
    const m = meta_(cur, 'parents');
    if (!m.parents || !m.parents.length) break;
    cur = m.parents[0];
  }
  throw new Error('原作音源フォルダの外です: ' + id);
}

function list_(folder) {
  check_(folder);
  const q = "'" + folder + "' in parents and trashed = false";
  const fields = 'nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime)';
  let files = [];
  let token = '';
  do {
    const url = API + '?pageSize=1000&q=' + encodeURIComponent(q) + '&fields=' + encodeURIComponent(fields) +
      (token ? '&pageToken=' + encodeURIComponent(token) : '');
    const r = JSON.parse(api_(url).getContentText());
    files = files.concat(r.files || []);
    token = r.nextPageToken || '';
  } while (token);
  return files;
}

// "分離/パート17" のような道筋のフォルダを、なければ作る
function mkdir_(path) {
  let parent = ROOT_ID;
  String(path || '').split('/').filter(String).forEach(function (name) {
    const found = list_(parent).filter(function (f) { return f.name === name && f.mimeType === FOLDER; });
    if (found.length) {
      parent = found[0].id;
      return;
    }
    const r = api_(API + '?fields=id', {
      method: 'post', contentType: 'application/json; charset=UTF-8',
      payload: JSON.stringify({ name: name, mimeType: FOLDER, parents: [parent] })
    });
    parent = JSON.parse(r.getContentText()).id;
  });
  return parent;
}

// 大きいファイルの書き込み: 再開可能アップロードの窓口を作り、その URL を返す（中身はそこへ直接送る）
function startUpload_(p) {
  check_(p.folder);
  if (String(p.replace) === '1') {
    list_(p.folder).filter(function (f) { return f.name === p.name; }).forEach(function (f) {
      api_(API + '/' + f.id, { method: 'patch', contentType: 'application/json', payload: JSON.stringify({ trashed: true }) });
    });
  }
  const r = api_(UPLOAD + '?uploadType=resumable&fields=id,name,size,md5Checksum', {
    method: 'post', contentType: 'application/json; charset=UTF-8',
    headers: { 'X-Upload-Content-Type': p.mime || 'application/octet-stream' },
    payload: JSON.stringify({ name: p.name, parents: [p.folder] })
  });
  const h = r.getHeaders();
  return h.Location || h.location;
}

// 予備: 窓口へ直接送れない場合に、受け口を経由して一切れずつ送る
function put_(p) {
  if (String(p.url).indexOf(UPLOAD + '?') !== 0) throw new Error('url が違います');
  const bytes = Utilities.base64Decode(p.data);
  const start = Number(p.start);
  const end = start + bytes.length - 1;
  const r = UrlFetchApp.fetch(p.url, {
    method: 'put', payload: bytes, contentType: 'application/octet-stream',
    headers: { 'Content-Range': 'bytes ' + start + '-' + end + '/' + p.total },
    muteHttpExceptions: true, followRedirects: false
  });
  const code = r.getResponseCode();
  if (code === 308) return { ok: true, done: false };
  if (code === 200 || code === 201) return { ok: true, done: true, file: JSON.parse(r.getContentText()) };
  throw new Error(code + ' ' + r.getContentText().slice(0, 300));
}

// 読み込み: 指定した範囲のバイトを base64 で返す（1回 8MB まで）
function download_(p) {
  check_(p.id);
  const start = Number(p.start || 0);
  const len = Math.min(Number(p.length || 8388608), 8388608);
  const r = api_(API + '/' + p.id + '?alt=media', { headers: { Range: 'bytes=' + start + '-' + (start + len - 1) } });
  let bytes = r.getContent();
  if (r.getResponseCode() === 200 && bytes.length > len) bytes = bytes.slice(start, start + len);
  return { ok: true, start: start, length: bytes.length, data: Utilities.base64Encode(bytes) };
}
