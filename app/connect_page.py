"""Self-contained HTML for GET /connect — the "连接本地 FreeCAD" workbench page.

Kept out of app/main.py (which is already large) as a single inline
HTML/CSS/JS string constant. No build step, no external assets: this is
served as-is by a plain FastAPI route (see app/main.py's `connect` handler).

The page does two things:
  1. Lets a signed-in user issue/list/revoke per-install API tokens via the
     existing P1 endpoints (POST/GET/DELETE /api/tokens) — same-origin
     fetches with `credentials: "same-origin"`, no bearer token needed since
     those endpoints sit behind the platform SSO edge, not GUARDED_PREFIXES.
  2. Documents how to install the `fouryi_cad_companion` FreeCAD addon from
     this repository's source archive into the exact Addons folder reported by
     FreeCAD, and how to point it at this instance.
"""

from __future__ import annotations

CONNECT_PAGE_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>连接本地 FreeCAD - 4yi CAD</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #0b0d12;
    --panel: #151821;
    --panel-border: #262b38;
    --text: #e6e8ee;
    --muted: #9aa2b1;
    --accent: #5b8cff;
    --accent-text: #0b0d12;
    --danger: #ff6b6b;
    --ok: #34d399;
    --code-bg: #0e1016;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 2rem 1.25rem 4rem;
    background: var(--bg);
    color: var(--text);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
      "Microsoft YaHei", sans-serif;
  }
  main { max-width: 760px; margin: 0 auto; }
  h1 { font-size: 1.6rem; margin: 0 0 0.35rem; }
  h2 { font-size: 1.15rem; margin: 2.25rem 0 0.75rem; }
  p.lede { color: var(--muted); margin: 0 0 1.5rem; }
  section.panel {
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1.25rem;
  }
  .row { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
  input[type="text"] {
    flex: 1 1 220px;
    min-width: 160px;
    background: var(--code-bg);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    padding: 0.5rem 0.7rem;
    color: var(--text);
    font-size: 0.9rem;
  }
  button {
    background: var(--accent);
    color: var(--accent-text);
    border: none;
    border-radius: 8px;
    padding: 0.5rem 0.9rem;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
  }
  button:hover { filter: brightness(1.08); }
  button.secondary {
    background: transparent;
    color: var(--text);
    border: 1px solid var(--panel-border);
  }
  button.danger { background: var(--danger); color: #1a0505; }
  button:disabled { opacity: 0.55; cursor: not-allowed; }
  code, pre, .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.85rem;
  }
  pre {
    background: var(--code-bg);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    padding: 0.75rem 0.9rem;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .token-reveal {
    margin-top: 1rem;
    padding: 0.9rem 1rem;
    border: 1px solid var(--ok);
    border-radius: 8px;
    background: rgba(52, 211, 153, 0.08);
  }
  .token-reveal .warn { color: var(--ok); font-weight: 600; margin: 0 0 0.5rem; }
  table { width: 100%; border-collapse: collapse; margin-top: 0.75rem; font-size: 0.88rem; }
  th, td { text-align: left; padding: 0.45rem 0.5rem; border-bottom: 1px solid var(--panel-border); }
  th { color: var(--muted); font-weight: 500; }
  td.status-revoked { color: var(--danger); }
  td.status-active { color: var(--ok); }
  .muted { color: var(--muted); }
  .error-banner {
    display: none;
    margin-top: 0.75rem;
    padding: 0.6rem 0.8rem;
    border: 1px solid var(--danger);
    border-radius: 8px;
    color: var(--danger);
    background: rgba(255, 107, 107, 0.08);
    font-size: 0.88rem;
  }
  ol { padding-left: 1.3rem; }
  ol li { margin-bottom: 0.4rem; }
</style>
</head>
<body>
<main>
  <h1>连接本地 FreeCAD</h1>
  <p class="lede">
    在本地原生 FreeCAD 中安装 4yi addon,连接到本实例即可获得同一套 AI 能力
    (Prompt / Explain / Patch / Bundle),无需使用云端 kiosk。
  </p>

  <section class="panel" id="token-panel">
    <h2>API Token 管理</h2>
    <p class="muted">
      本页面通过你当前的登录会话直接调用 token 接口,无需手动携带 token。
      生成的 token 用于本地 FreeCAD addon 连接本实例时的身份验证。
    </p>
    <div class="row">
      <input type="text" id="token-label" placeholder="Token 标签(可选,例如「我的笔记本」)" />
      <button id="create-token-btn" type="button">生成 Token</button>
    </div>

    <div class="token-reveal" id="token-reveal" style="display:none;">
      <p class="warn">仅显示一次 —— 请立即复制并妥善保存,离开本页后将无法再次查看明文。</p>
      <div class="row">
        <code class="mono" id="token-value" style="flex:1 1 auto; word-break: break-all;"></code>
        <button type="button" id="copy-token-btn">复制</button>
      </div>
    </div>

    <div class="error-banner" id="token-error"></div>

    <table id="token-table">
      <thead>
        <tr>
          <th>标签</th>
          <th>创建时间</th>
          <th>最近使用</th>
          <th>状态</th>
          <th></th>
        </tr>
      </thead>
      <tbody id="token-table-body">
        <tr><td colspan="5" class="muted">加载中…</td></tr>
      </tbody>
    </table>
  </section>

  <section class="panel">
    <h2>安装指引</h2>

    <p><strong>Addon Manager 安装(当前推荐)</strong></p>
    <p class="muted">
      当前要求插件版本 <code>0.5.2</code>。分发仓会从 4yi CAD 主仓自动同步。
    </p>
    <ol>
      <li>打开 FreeCAD「首选项」→「Addon Manager」,在
        「Custom repositories」区域点击「+」。</li>
      <li>Repository URL 填入:
        <div class="row">
          <pre id="addon-repo-url" style="flex:1 1 auto; margin:0;">https://github.com/4yi-ai/cad-addon</pre>
          <button type="button" id="copy-addon-repo-btn">复制</button>
        </div>
        Branch 填 <code>main</code>,然后确认并关闭首选项。
      </li>
      <li>打开「工具」→「Addon Manager」,搜索仓库名「cad-addon」并安装。
        FreeCAD 1.1 的自定义仓库搜索暂时不匹配显示名「4yi CAD Companion」。</li>
      <li>重新启动 FreeCAD,在工作台下拉框选择「4yi CAD」。打开「支持包」并确认
        <code>addon_version</code> 为 <code>0.5.2</code>。</li>
    </ol>
    <details>
      <summary>Addon Manager 无法使用时:源码 zip 手工安装</summary>
      <p>在「视图」→「面板」→「Python 控制台」执行
        <code>print(App.getUserAppDataDir() + "Mod")</code> 确认插件目录。本机 FreeCAD
        1.1.3 实测为 <code>~/Library/Application Support/FreeCAD/v1-1/Mod</code>。</p>
      <p>下载并解压
        <a href="https://github.com/4yi-ai/4yi-cad/archive/refs/heads/main.zip">4yi CAD main.zip</a>,
        完全退出 FreeCAD,再将
        <code>4yi-cad-main/freecad-addon/fouryi_cad_companion</code> 复制到上述
        <code>Mod</code> 目录。</p>
    </details>
  </section>

  <section class="panel">
    <h2>配置连接</h2>
    <p class="muted">
      安装完成后,在 FreeCAD 里打开「4yi CAD」面板 →「连接设置」,填入以下信息:
    </p>
    <table>
      <tbody>
        <tr>
          <td class="muted" style="width:8rem;">App URL</td>
          <td><code id="origin-url" class="mono"></code></td>
        </tr>
        <tr>
          <td class="muted">API Token</td>
          <td class="muted">上方生成的 token(仅显示一次,请提前复制保存)</td>
        </tr>
      </tbody>
    </table>
    <p class="muted" style="margin-top:0.75rem;">
      点击「测试连接」确认地址可达,再点击「保存」;保存后完全退出并重新启动 FreeCAD 生效。
    </p>
  </section>
</main>

<script>
(function () {
  "use strict";

  document.getElementById("origin-url").textContent = window.location.origin;

  var errorBanner = document.getElementById("token-error");
  function showError(message) {
    errorBanner.textContent = message;
    errorBanner.style.display = "block";
  }
  function clearError() {
    errorBanner.style.display = "none";
    errorBanner.textContent = "";
  }

  function fmtTime(value) {
    return value ? value : "—";
  }

  async function fetchTokens() {
    clearError();
    var tbody = document.getElementById("token-table-body");
    try {
      var resp = await fetch("/api/tokens", { credentials: "same-origin" });
      if (!resp.ok) {
        throw new Error("加载 token 列表失败(HTTP " + resp.status + ")");
      }
      var data = await resp.json();
      var tokens = data.tokens || [];
      if (tokens.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="muted">还没有生成任何 token。</td></tr>';
        return;
      }
      tbody.innerHTML = "";
      tokens.forEach(function (tok) {
        var tr = document.createElement("tr");

        var labelTd = document.createElement("td");
        labelTd.textContent = tok.label || "(未命名)";
        tr.appendChild(labelTd);

        var createdTd = document.createElement("td");
        createdTd.textContent = fmtTime(tok.created_at);
        tr.appendChild(createdTd);

        var lastUsedTd = document.createElement("td");
        lastUsedTd.textContent = fmtTime(tok.last_used_at);
        tr.appendChild(lastUsedTd);

        var statusTd = document.createElement("td");
        var revoked = !!tok.revoked_at;
        statusTd.textContent = revoked ? "已吊销" : "有效";
        statusTd.className = revoked ? "status-revoked" : "status-active";
        tr.appendChild(statusTd);

        var actionTd = document.createElement("td");
        if (!revoked) {
          var revokeBtn = document.createElement("button");
          revokeBtn.type = "button";
          revokeBtn.className = "danger";
          revokeBtn.textContent = "吊销";
          revokeBtn.addEventListener("click", function () {
            revokeToken(tok.id);
          });
          actionTd.appendChild(revokeBtn);
        }
        tr.appendChild(actionTd);

        tbody.appendChild(tr);
      });
    } catch (err) {
      tbody.innerHTML = '<tr><td colspan="5" class="muted">加载失败。</td></tr>';
      showError(err.message || "加载 token 列表失败");
    }
  }

  async function createToken() {
    clearError();
    var labelInput = document.getElementById("token-label");
    var createBtn = document.getElementById("create-token-btn");
    var label = labelInput.value.trim();
    createBtn.disabled = true;
    try {
      var resp = await fetch("/api/tokens", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: label || null }),
      });
      if (!resp.ok) {
        throw new Error("生成 token 失败(HTTP " + resp.status + ")");
      }
      var created = await resp.json();
      var reveal = document.getElementById("token-reveal");
      var tokenValue = document.getElementById("token-value");
      tokenValue.textContent = created.token;
      reveal.style.display = "block";
      labelInput.value = "";
      await fetchTokens();
    } catch (err) {
      showError(err.message || "生成 token 失败");
    } finally {
      createBtn.disabled = false;
    }
  }

  async function revokeToken(tokenId) {
    clearError();
    try {
      var resp = await fetch("/api/tokens/" + encodeURIComponent(tokenId), {
        method: "DELETE",
        credentials: "same-origin",
      });
      if (!resp.ok) {
        throw new Error("吊销 token 失败(HTTP " + resp.status + ")");
      }
      await fetchTokens();
    } catch (err) {
      showError(err.message || "吊销 token 失败");
    }
  }

  function copyToClipboard(text, button) {
    function feedback(ok) {
      var original = "复制";
      button.textContent = ok ? "已复制" : "复制失败,请手动选择";
      setTimeout(function () { button.textContent = original; }, 2000);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { feedback(true); },
        function () { feedback(false); }
      );
      return;
    }
    // 非安全上下文(如内网 http)没有 navigator.clipboard,退回 execCommand。
    try {
      var scratch = document.createElement("textarea");
      scratch.value = text;
      scratch.style.position = "fixed";
      scratch.style.opacity = "0";
      document.body.appendChild(scratch);
      scratch.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(scratch);
      feedback(ok);
    } catch (err) {
      feedback(false);
    }
  }

  document.getElementById("create-token-btn").addEventListener("click", createToken);
  document.getElementById("copy-token-btn").addEventListener("click", function () {
    copyToClipboard(document.getElementById("token-value").textContent, this);
  });
  document.getElementById("copy-addon-repo-btn").addEventListener("click", function () {
    copyToClipboard(document.getElementById("addon-repo-url").textContent, this);
  });

  fetchTokens();
})();
</script>
</body>
</html>
"""
