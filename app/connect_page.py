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
<title>连接本地 FreeCAD - FreeCAD Addon</title>
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
  .page-header { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; }
  .language-switcher {
    display: inline-flex;
    flex: 0 0 auto;
    padding: 0.2rem;
    border: 1px solid var(--panel-border);
    border-radius: 10px;
    background: var(--panel);
  }
  .language-switcher button { min-width: 4.5rem; background: transparent; color: var(--muted); }
  .language-switcher button[aria-pressed="true"] { background: var(--accent); color: var(--accent-text); }
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
  @media (max-width: 560px) {
    .page-header { flex-direction: column-reverse; }
    .language-switcher { align-self: flex-end; }
  }
</style>
</head>
<body>
<main>
  <div class="page-header">
    <div>
      <h1 data-i18n="pageTitle">连接本地 FreeCAD</h1>
      <p class="lede" data-i18n="pageLede">
    在本地原生 FreeCAD 中安装 4yi addon,连接到本实例即可获得同一套 AI 能力
    (Prompt / Explain / Patch / Bundle),无需使用云端 kiosk。
      </p>
    </div>
    <div class="language-switcher" role="group" aria-label="语言 / Language">
      <button type="button" data-locale="zh" aria-pressed="true">中文</button>
      <button type="button" data-locale="en" aria-pressed="false">English</button>
    </div>
  </div>

  <section class="panel" id="token-panel">
    <h2 data-i18n="tokenTitle">API Token 管理</h2>
    <p class="muted" data-i18n="tokenDescription">
      本页面通过你当前的登录会话直接调用 token 接口,无需手动携带 token。
      生成的 token 用于本地 FreeCAD addon 连接本实例时的身份验证。
    </p>
    <div class="row">
      <input type="text" id="token-label" placeholder="Token 标签(可选,例如「我的笔记本」)" data-i18n-placeholder="tokenPlaceholder" />
      <button id="create-token-btn" type="button" data-i18n="createToken">生成 Token</button>
    </div>

    <div class="token-reveal" id="token-reveal" style="display:none;">
      <p class="warn" data-i18n="tokenWarning">仅显示一次 —— 请立即复制并妥善保存,离开本页后将无法再次查看明文。</p>
      <div class="row">
        <code class="mono" id="token-value" style="flex:1 1 auto; word-break: break-all;"></code>
        <button type="button" id="copy-token-btn" data-i18n="copy">复制</button>
      </div>
    </div>

    <div class="error-banner" id="token-error"></div>

    <table id="token-table">
      <thead>
        <tr>
          <th data-i18n="label">标签</th>
          <th data-i18n="createdAt">创建时间</th>
          <th data-i18n="lastUsed">最近使用</th>
          <th data-i18n="status">状态</th>
          <th></th>
        </tr>
      </thead>
      <tbody id="token-table-body">
        <tr><td colspan="5" class="muted" data-i18n="loading">加载中…</td></tr>
      </tbody>
    </table>
  </section>

  <section class="panel">
    <h2 data-i18n="installTitle">安装指引</h2>

    <p><strong data-i18n="addonManagerTitle">Addon Manager 安装(当前推荐)</strong></p>
    <p class="muted" data-i18n-html="versionRequirement">
      当前要求插件版本 <code>0.5.2</code>。分发仓会从 4yi CAD 主仓自动同步。
    </p>
    <ol>
      <li data-i18n-html="installStep1">打开 FreeCAD「首选项」→「Addon Manager」,在
        「Custom repositories」区域点击「+」。</li>
      <li><span data-i18n="installStep2">Repository URL 填入:</span>
        <div class="row">
          <pre id="addon-repo-url" style="flex:1 1 auto; margin:0;">https://github.com/4yi-ai/cad-addon</pre>
          <button type="button" id="copy-addon-repo-btn" data-i18n="copy">复制</button>
        </div>
        <span data-i18n-html="installStep2Branch">Branch 填 <code>main</code>,然后确认并关闭首选项。</span>
      </li>
      <li data-i18n-html="installStep3">打开「工具」→「Addon Manager」,搜索仓库名「cad-addon」并安装。
        FreeCAD 1.1 的自定义仓库搜索暂时不匹配显示名「4yi CAD Companion」。</li>
      <li data-i18n-html="installStep4">重新启动 FreeCAD,在工作台下拉框选择「4yi CAD」。打开「支持包」并确认
        <code>addon_version</code> 为 <code>0.5.2</code>。</li>
    </ol>
    <details>
      <summary data-i18n="manualInstallTitle">Addon Manager 无法使用时:源码 zip 手工安装</summary>
      <p data-i18n-html="manualInstallPath">在「视图」→「面板」→「Python 控制台」执行
        <code>print(App.getUserAppDataDir() + "Mod")</code> 确认插件目录。本机 FreeCAD
        1.1.3 实测为 <code>~/Library/Application Support/FreeCAD/v1-1/Mod</code>。</p>
      <p data-i18n-html="manualInstallCopy">下载并解压
        <a href="https://github.com/4yi-ai/4yi-cad/archive/refs/heads/main.zip">4yi CAD main.zip</a>,
        完全退出 FreeCAD,再将
        <code>4yi-cad-main/freecad-addon/fouryi_cad_companion</code> 复制到上述
        <code>Mod</code> 目录。</p>
    </details>
  </section>

  <section class="panel">
    <h2 data-i18n="configureTitle">配置连接</h2>
    <p class="muted" data-i18n="configureDescription">
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
          <td class="muted" data-i18n="tokenValueHelp">上方生成的 token(仅显示一次,请提前复制保存)</td>
        </tr>
      </tbody>
    </table>
    <p class="muted" style="margin-top:0.75rem;" data-i18n="configureFinish">
      点击「测试连接」确认地址可达,再点击「保存」;保存后完全退出并重新启动 FreeCAD 生效。
    </p>
  </section>
</main>

<script>
(function () {
  "use strict";

  var messages = {
    zh: {
      documentTitle: "连接本地 FreeCAD - FreeCAD Addon",
      pageTitle: "连接本地 FreeCAD",
      pageLede: "在本地原生 FreeCAD 中安装 FreeCAD Addon，连接到本实例即可使用 Prompt、Explain、Patch 和 Bundle 等 AI 能力，无需使用云端 kiosk。",
      tokenTitle: "API Token 管理",
      tokenDescription: "本页面通过你当前的登录会话直接调用 Token 接口，无需手动携带 Token。生成的 Token 用于本地 FreeCAD Addon 连接本实例时的身份验证。",
      tokenPlaceholder: "Token 标签（可选，例如“我的笔记本”）",
      createToken: "生成 Token",
      tokenWarning: "仅显示一次——请立即复制并妥善保存，离开本页后将无法再次查看明文。",
      copy: "复制",
      copied: "已复制",
      copyFailed: "复制失败，请手动选择",
      label: "标签",
      createdAt: "创建时间",
      lastUsed: "最近使用",
      status: "状态",
      loading: "加载中…",
      emptyTokens: "还没有生成任何 Token。",
      loadFailed: "加载失败。",
      unnamed: "（未命名）",
      revoked: "已吊销",
      active: "有效",
      revoke: "吊销",
      loadTokensError: "加载 Token 列表失败",
      createTokenError: "生成 Token 失败",
      revokeTokenError: "吊销 Token 失败",
      installTitle: "安装指引",
      addonManagerTitle: "通过 Addon Manager 安装（推荐）",
      versionRequirement: "当前要求插件版本 <code>0.5.2</code>。分发仓会从 4yi CAD 主仓自动同步。",
      installStep1: "打开 FreeCAD“首选项”→“Addon Manager”，在“Custom repositories”区域点击“+”。",
      installStep2: "Repository URL 填入：",
      installStep2Branch: "Branch 填 <code>main</code>，然后确认并关闭首选项。",
      installStep3: "打开“工具”→“Addon Manager”，搜索仓库名 <code>cad-addon</code> 并安装。FreeCAD 1.1 的自定义仓库搜索暂时不匹配显示名“4yi CAD Companion”。",
      installStep4: "重新启动 FreeCAD，在工作台下拉框选择“4yi CAD”。打开“支持包”并确认 <code>addon_version</code> 为 <code>0.5.2</code>。",
      manualInstallTitle: "Addon Manager 无法使用时：通过源码 ZIP 手动安装",
      manualInstallPath: "在“视图”→“面板”→“Python 控制台”执行 <code>print(App.getUserAppDataDir() + \"Mod\")</code> 确认插件目录。本机 FreeCAD 1.1.3 实测为 <code>~/Library/Application Support/FreeCAD/v1-1/Mod</code>。",
      manualInstallCopy: "下载并解压 <a href=\"https://github.com/4yi-ai/4yi-cad/archive/refs/heads/main.zip\">4yi CAD main.zip</a>，完全退出 FreeCAD，再将 <code>4yi-cad-main/freecad-addon/fouryi_cad_companion</code> 复制到上述 <code>Mod</code> 目录。",
      configureTitle: "配置连接",
      configureDescription: "安装完成后，在 FreeCAD 中打开“4yi CAD”面板 →“连接设置”，填入以下信息：",
      tokenValueHelp: "上方生成的 Token（仅显示一次，请提前复制保存）",
      configureFinish: "点击“测试连接”确认地址可达，再点击“保存”；保存后完全退出并重新启动 FreeCAD 生效。"
    },
    en: {
      documentTitle: "Connect local FreeCAD - FreeCAD Addon",
      pageTitle: "Connect local FreeCAD",
      pageLede: "Install FreeCAD Addon in your desktop FreeCAD and connect it to this app instance to use the same Prompt, Explain, Patch, and Bundle AI capabilities without the cloud kiosk.",
      tokenTitle: "Manage API Tokens",
      tokenDescription: "This page uses your current sign-in session to call the Token API, so you do not need to provide a Token manually. The generated Token authenticates FreeCAD Addon with this app instance.",
      tokenPlaceholder: "Token label (optional, e.g. My laptop)",
      createToken: "Generate Token",
      tokenWarning: "Shown only once—copy and store this Token now. You cannot view it again after leaving this page.",
      copy: "Copy",
      copied: "Copied",
      copyFailed: "Copy failed—select it manually",
      label: "Label",
      createdAt: "Created",
      lastUsed: "Last used",
      status: "Status",
      loading: "Loading…",
      emptyTokens: "No Tokens have been generated yet.",
      loadFailed: "Failed to load.",
      unnamed: "(Untitled)",
      revoked: "Revoked",
      active: "Active",
      revoke: "Revoke",
      loadTokensError: "Failed to load the Token list",
      createTokenError: "Failed to generate a Token",
      revokeTokenError: "Failed to revoke the Token",
      installTitle: "Installation",
      addonManagerTitle: "Install with Addon Manager (recommended)",
      versionRequirement: "Addon version <code>0.5.2</code> is currently required. The distribution repository is synchronized automatically from the main 4yi CAD repository.",
      installStep1: "Open FreeCAD Preferences → Addon Manager, then select “+” in the Custom repositories section.",
      installStep2: "Enter this Repository URL:",
      installStep2Branch: "Set Branch to <code>main</code>, confirm, and close Preferences.",
      installStep3: "Open Tools → Addon Manager, search for the repository name <code>cad-addon</code>, and install it. FreeCAD 1.1 custom-repository search does not currently match the display name “4yi CAD Companion”.",
      installStep4: "Restart FreeCAD and select “4yi CAD” from the workbench menu. Open “Support Bundle” and confirm that <code>addon_version</code> is <code>0.5.2</code>.",
      manualInstallTitle: "If Addon Manager is unavailable: install manually from the source ZIP",
      manualInstallPath: "In View → Panels → Python console, run <code>print(App.getUserAppDataDir() + \"Mod\")</code> to find your addon directory. With FreeCAD 1.1.3, a verified macOS path is <code>~/Library/Application Support/FreeCAD/v1-1/Mod</code>.",
      manualInstallCopy: "Download and extract <a href=\"https://github.com/4yi-ai/4yi-cad/archive/refs/heads/main.zip\">4yi CAD main.zip</a>. Quit FreeCAD completely, then copy <code>4yi-cad-main/freecad-addon/fouryi_cad_companion</code> into the <code>Mod</code> directory shown above.",
      configureTitle: "Configure the connection",
      configureDescription: "After installation, open the “4yi CAD” panel in FreeCAD → “Connection Settings”, then enter the following values:",
      tokenValueHelp: "The Token generated above (shown only once—copy and save it first)",
      configureFinish: "Select “Test Connection” to verify the address, then select “Save”. Quit FreeCAD completely and restart it to apply the settings."
    }
  };

  var queryLocale = new URLSearchParams(window.location.search).get("lang");
  var savedLocale = null;
  try { savedLocale = window.localStorage.getItem("freecad-addon-locale"); } catch (err) { /* storage may be blocked */ }
  var browserLocale = (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
  var locale = queryLocale === "zh" || queryLocale === "en"
    ? queryLocale
    : (savedLocale === "zh" || savedLocale === "en" ? savedLocale : browserLocale);

  function t(key) {
    return messages[locale][key] || messages.en[key] || key;
  }

  function applyLanguage(nextLocale) {
    locale = nextLocale === "zh" ? "zh" : "en";
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
    document.title = t("documentTitle");
    document.querySelectorAll("[data-i18n]").forEach(function (element) {
      element.textContent = t(element.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-html]").forEach(function (element) {
      element.innerHTML = t(element.getAttribute("data-i18n-html"));
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (element) {
      element.setAttribute("placeholder", t(element.getAttribute("data-i18n-placeholder")));
    });
    document.querySelectorAll("[data-locale]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.getAttribute("data-locale") === locale));
    });
    try { window.localStorage.setItem("freecad-addon-locale", locale); } catch (err) { /* storage may be blocked */ }
  }

  document.querySelectorAll("[data-locale]").forEach(function (button) {
    button.addEventListener("click", function () {
      applyLanguage(button.getAttribute("data-locale"));
      fetchTokens();
    });
  });

  applyLanguage(locale);

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
        throw new Error(t("loadTokensError") + " (HTTP " + resp.status + ")");
      }
      var data = await resp.json();
      var tokens = data.tokens || [];
      if (tokens.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="muted">' + t("emptyTokens") + '</td></tr>';
        return;
      }
      tbody.innerHTML = "";
      tokens.forEach(function (tok) {
        var tr = document.createElement("tr");

        var labelTd = document.createElement("td");
        labelTd.textContent = tok.label || t("unnamed");
        tr.appendChild(labelTd);

        var createdTd = document.createElement("td");
        createdTd.textContent = fmtTime(tok.created_at);
        tr.appendChild(createdTd);

        var lastUsedTd = document.createElement("td");
        lastUsedTd.textContent = fmtTime(tok.last_used_at);
        tr.appendChild(lastUsedTd);

        var statusTd = document.createElement("td");
        var revoked = !!tok.revoked_at;
        statusTd.textContent = revoked ? t("revoked") : t("active");
        statusTd.className = revoked ? "status-revoked" : "status-active";
        tr.appendChild(statusTd);

        var actionTd = document.createElement("td");
        if (!revoked) {
          var revokeBtn = document.createElement("button");
          revokeBtn.type = "button";
          revokeBtn.className = "danger";
          revokeBtn.textContent = t("revoke");
          revokeBtn.addEventListener("click", function () {
            revokeToken(tok.id);
          });
          actionTd.appendChild(revokeBtn);
        }
        tr.appendChild(actionTd);

        tbody.appendChild(tr);
      });
    } catch (err) {
      tbody.innerHTML = '<tr><td colspan="5" class="muted">' + t("loadFailed") + '</td></tr>';
      showError(err.message || t("loadTokensError"));
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
        throw new Error(t("createTokenError") + " (HTTP " + resp.status + ")");
      }
      var created = await resp.json();
      var reveal = document.getElementById("token-reveal");
      var tokenValue = document.getElementById("token-value");
      tokenValue.textContent = created.token;
      reveal.style.display = "block";
      labelInput.value = "";
      await fetchTokens();
    } catch (err) {
      showError(err.message || t("createTokenError"));
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
        throw new Error(t("revokeTokenError") + " (HTTP " + resp.status + ")");
      }
      await fetchTokens();
    } catch (err) {
      showError(err.message || t("revokeTokenError"));
    }
  }

  function copyToClipboard(text, button) {
    function feedback(ok) {
      var original = t("copy");
      button.textContent = ok ? t("copied") : t("copyFailed");
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
