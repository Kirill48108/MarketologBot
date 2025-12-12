from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.control_center.config import BOTS, BotConfig

app = FastAPI(title="Bots Control Center")


def _get_bot(bot_name: str) -> BotConfig:
    bot = BOTS.get(bot_name)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Unknown bot '{bot_name}'")
    return bot


def _forward_request(
    method: str,
    bot: BotConfig,
    path: str,
    body: dict[str, Any] | None,
    x_admin_token: str | None,
) -> JSONResponse:
    """
    Простой HTTP-прокси к admin-API конкретного бота.
    - method: "GET" / "POST" / "DELETE"
    - bot.base_url: например, http://localhost:8000
    - path: например, "/admin/stats/overview" или "/status"
    """
    url = bot.base_url.rstrip("/") + path
    # S310: разрешаем только http/https, чтобы случайно не открыть file:/… и т.п.
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Unsupported URL scheme")

    headers = {
        "Content-Type": "application/json",
    }
    if x_admin_token:
        headers["X-Admin-Token"] = x_admin_token

    data_bytes: bytes | None = None
    if body is not None:
        data_bytes = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url=url, data=data_bytes, headers=headers, method=method) # noqa: S310
    try:
        with urllib.request.urlopen(req) as resp: # noqa: S310
            resp_body = resp.read()
            status = resp.getcode()
            if resp_body:
                try:
                    payload = json.loads(resp_body.decode("utf-8"))
                except Exception:
                    payload = {"raw": resp_body.decode("utf-8", errors="replace")}
            else:
                payload = None
            return JSONResponse(content=payload, status_code=status)
    except urllib.error.HTTPError as e:
        try:
            detail_body = e.read()
            if detail_body:
                detail_json = json.loads(detail_body.decode("utf-8"))
                return JSONResponse(content=detail_json, status_code=e.code)
        except Exception:
            pass
        raise HTTPException(status_code=e.code, detail=str(e)) from e
    except urllib.error.URLError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to contact bot '{bot.name}' at {bot.base_url}: {e}",
        ) from e


# ---------------------------------------------------------------------------
# Публичные эндпоинты центра
# ---------------------------------------------------------------------------


@app.get("/admin/bots")
def list_bots() -> list[dict[str, str]]:
    """
    Список известных ботов для центра управления.
    Пока берём из статического конфига BOTS.
    """
    return [
        {
            "name": cfg.name,
            "base_url": cfg.base_url,
        }
        for cfg in BOTS.values()
    ]


# ---------- ПРОКСИ СТАТИСТИКИ ----------


@app.get("/admin/{bot_name}/stats/overview")
async def proxy_stats_overview(
    bot_name: str,
    x_admin_token: str | None = Header(default=None, alias="x-admin-token"),
) -> JSONResponse:
    bot = _get_bot(bot_name)
    return _forward_request(
        method="GET",
        bot=bot,
        path="/admin/stats/overview",
        body=None,
        x_admin_token=x_admin_token,
    )


@app.get("/admin/{bot_name}/stats/channels")
async def proxy_stats_channels(
    bot_name: str,
    x_admin_token: str | None = Header(default=None, alias="x-admin-token"),
) -> JSONResponse:
    bot = _get_bot(bot_name)
    return _forward_request(
        method="GET",
        bot=bot,
        path="/admin/stats/channels",
        body=None,
        x_admin_token=x_admin_token,
    )


@app.get("/admin/{bot_name}/stats/links")
async def proxy_stats_links(
    bot_name: str,
    x_admin_token: str | None = Header(default=None, alias="x-admin-token"),
) -> JSONResponse:
    bot = _get_bot(bot_name)
    return _forward_request(
        method="GET",
        bot=bot,
        path="/admin/stats/links",
        body=None,
        x_admin_token=x_admin_token,
    )


# ---------- ПРОКСИ СТАТУСА БОТА И УПРАВЛЕНИЯ ----------


@app.get("/admin/{bot_name}/status")
async def proxy_status(
    bot_name: str,
) -> JSONResponse:
    """
    Проксирование /status (без админ-токена).
    """
    bot = _get_bot(bot_name)
    return _forward_request(
        method="GET",
        bot=bot,
        path="/status",
        body=None,
        x_admin_token=None,
    )


@app.post("/admin/{bot_name}/control/{action}")
async def proxy_control_action(
    bot_name: str,
    action: str,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="x-admin-token"),
) -> JSONResponse:
    """
    Проксирование управляющих действий:
    - /admin/{bot_name}/control/enable        -> /admin/control/enable
    - /admin/{bot_name}/control/disable       -> /admin/control/disable
    - /admin/{bot_name}/control/restart_bot   -> /admin/control/restart_bot
    """
    if action not in ("enable", "disable", "restart_bot"):
        raise HTTPException(status_code=404, detail="Unknown control action")

    bot = _get_bot(bot_name)
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else None
    )

    return _forward_request(
        method="POST",
        bot=bot,
        path=f"/admin/control/{action}",
        body=body,
        x_admin_token=x_admin_token,
    )


# ---------- ПРОКСИ КАНАЛОВ / ALLOWLIST ----------


@app.get("/admin/{bot_name}/chats/overview")
async def proxy_chats_overview(
    bot_name: str,
    x_admin_token: str | None = Header(default=None, alias="x-admin-token"),
) -> JSONResponse:
    bot = _get_bot(bot_name)
    return _forward_request(
        method="GET",
        bot=bot,
        path="/admin/chats/overview",
        body=None,
        x_admin_token=x_admin_token,
    )


@app.post("/admin/{bot_name}/allowlist/add")
async def proxy_allowlist_add(
    bot_name: str,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="x-admin-token"),
) -> JSONResponse:
    bot = _get_bot(bot_name)
    body = await request.json()
    return _forward_request(
        method="POST",
        bot=bot,
        path="/admin/allowlist/add",
        body=body,
        x_admin_token=x_admin_token,
    )


@app.delete("/admin/{bot_name}/allowlist/{chat_id}")
async def proxy_allowlist_delete(
    bot_name: str,
    chat_id: int,
    x_admin_token: str | None = Header(default=None, alias="x-admin-token"),
) -> JSONResponse:
    bot = _get_bot(bot_name)
    return _forward_request(
        method="DELETE",
        bot=bot,
        path=f"/admin/allowlist/{chat_id}",
        body=None,
        x_admin_token=x_admin_token,
    )


# ---------- ПРОКСИ СМЕНЫ СЕССИИ ----------


@app.post("/admin/{bot_name}/session/update")
async def proxy_session_update(
    bot_name: str,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="x-admin-token"),
) -> JSONResponse:
    bot = _get_bot(bot_name)
    body = await request.json()
    return _forward_request(
        method="POST",
        bot=bot,
        path="/admin/session/update",
        body=body,
        x_admin_token=x_admin_token,
    )


# ---------- ЕДИНЫЙ ДАШБОРД ЦЕНТРА ----------


@app.get("/admin/center/dashboard", response_class=HTMLResponse)
def center_dashboard() -> HTMLResponse:
    """
    Единый дашборд:
    - выбор бота из /admin/bots;
    - все запросы идут через /admin/{bot_name}/... (центр проксирует к конкретному боту).
    """
    html = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
      <meta charset="UTF-8">
      <title>Bots Control Center</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">

      <link
        href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
        rel="stylesheet"
      >
      <style>
        body { padding-top: 1rem; padding-bottom: 2rem; }
        .token-input { max-width: 420px; }
        .status-badge { font-size: 0.85rem; }
        .table-fixed { table-layout: fixed; word-wrap: break-word; }
        .small-text { font-size: 0.85rem; }
        .clickable { cursor: pointer; }
      </style>
    </head>
    <body class="bg-light">
    <div class="container">
      <h1 class="mb-4">Центр управления ботами</h1>

      <!-- Блок 0: Админ-токен -->
      <div class="card mb-4">
        <div class="card-header">Шаг 0. Доступ в панель</div>
        <div class="card-body">
          <p class="small-text text-muted">
            Введите админ-токен (тот же, что у ботов). Он хранится только в вашем браузере (localStorage).
          </p>
          <div class="row g-2 align-items-center mb-2">
            <div class="col-md-6">
              <input id="adminToken" type="password" class="form-control token-input" placeholder="Введите X-Admin-Token">
            </div>
            <div class="col-auto">
              <button class="btn btn-primary" onclick="saveTokenAndInit()">Сохранить токен и обновить данные</button>
            </div>
            <div class="col-auto">
              <button class="btn btn-outline-secondary btn-sm" onclick="toggleTokenVisible()">Показать/скрыть</button>
            </div>
          </div>
          <div id="tokenStatus" class="mt-2 small-text text-muted"></div>
        </div>
      </div>

      <!-- Блок 0.1: Выбор бота -->
      <div class="card mb-4">
        <div class="card-header">Шаг 0.1. Выбор бота</div>
        <div class="card-body">
          <p class="small-text text-muted">
            Выберите бота, с которым хотите работать. Все блоки ниже относятся к выбранному боту.
          </p>
          <div class="row g-2 align-items-center mb-2">
            <div class="col-md-6">
              <select id="botSelector" class="form-select" onchange="onBotChange()">
              </select>
            </div>
            <div class="col-auto">
              <button class="btn btn-outline-primary" onclick="initDashboard()">Обновить данные по боту</button>
            </div>
          </div>
          <div id="botSelectorStatus" class="small-text text-muted"></div>
        </div>
      </div>

      <!-- Блок 1: Статус бота и управление -->
      <div class="card mb-4">
        <div class="card-header">Шаг 1. Статус бота и управление</div>
        <div class="card-body">
          <div class="row mb-2">
            <div class="col-md-4">
              <div>Текущее состояние:</div>
              <div id="botStatusText" class="fw-bold">—</div>
              <div id="botStatusDetails" class="small-text text-muted"></div>
            </div>
            <div class="col-md-8 text-md-end mt-2 mt-md-0">
              <button class="btn btn-success me-1" onclick="controlBot('enable')">Включить</button>
              <button class="btn btn-warning me-1" onclick="controlBot('disable')">Пауза</button>
              <button class="btn btn-outline-primary" onclick="controlBot('restart_bot')">Перезапустить</button>
            </div>
          </div>
          <div id="botStatusError" class="text-danger small-text"></div>
        </div>
      </div>

      <!-- Блок 1.1: Смена сессии Telegram -->
      <div class="card mb-4">
        <div class="card-header">Шаг 1.1. Смена сессии Telegram (для выбранного бота)</div>
        <div class="card-body">
          <p class="small-text text-muted">
            Здесь можно обновить TELEGRAM_SESSION_STRING для текущего бота.
            После сохранения сессии нажмите «Перезапустить» в блоке выше, чтобы бот перезапустился с новой сессией.
          </p>
          <div class="row g-2 align-items-center mb-2">
            <div class="col-md-8">
              <input id="sessionInput" type="text" class="form-control"
                     placeholder="Вставьте TELEGRAM_SESSION_STRING">
            </div>
            <div class="col-md-4">
              <button class="btn btn-outline-primary w-100" onclick="updateSession()">
                Сохранить сессию
              </button>
            </div>
          </div>
          <div id="sessionStatus" class="small-text text-muted"></div>
          <div id="sessionError" class="small-text text-danger"></div>
        </div>
      </div>

      <!-- Блок 2: Каналы (allowlist) для выбранного бота -->
      <div class="card mb-4">
        <div class="card-header">Шаг 2. Каналы, в которых бот работает</div>
        <div class="card-body">
          <p class="small-text text-muted">
            Здесь список каналов/чатов для выбранного бота. Можно добавить новый по @username или ссылке,
            либо удалить проблемный канал.
          </p>

          <!-- Добавление канала -->
          <div class="row g-2 align-items-center mb-3">
            <div class="col-md-5">
              <input id="peerInput" type="text" class="form-control" placeholder="@channel или https://t.me/...">
            </div>
            <div class="col-md-3">
              <button class="btn btn-primary w-100" onclick="addChannel()">Добавить канал</button>
            </div>
            <div class="col-md-4 small-text text-muted">
              Можно добавить и по ID (например, -1001234567890). Для этого впишите ID вместо @.
            </div>
          </div>
          <div id="peerResolveInfo" class="small-text mb-2"></div>
          <div id="peerError" class="text-danger small-text mb-2"></div>

          <!-- Таблица каналов -->
          <div class="table-responsive">
            <table class="table table-sm table-hover align-middle table-fixed">
              <thead class="table-light">
                <tr>
                  <th style="width: 25%;">Chat ID</th>
                  <th style="width: 25%;">В allowlist</th>
                  <th style="width: 25%;">Локальный статус</th>
                  <th style="width: 25%;">Действия</th>
                </tr>
              </thead>
              <tbody id="chatsTableBody">
              </tbody>
            </table>
          </div>
          <div id="chatsError" class="text-danger small-text mt-2"></div>
        </div>
      </div>

      <!-- Блок 3: Статистика для выбранного бота -->
      <div class="card mb-4">
        <div class="card-header">Шаг 3. Статистика</div>
        <div class="card-body">
          <div class="row">
            <!-- Overview -->
            <div class="col-md-4 mb-3">
              <h6>Общее</h6>
              <ul class="list-unstyled small-text" id="statsOverviewList">
                <li>Загружается...</li>
              </ul>
            </div>

            <!-- Статусы каналов -->
            <div class="col-md-4 mb-3">
              <h6>Статусы каналов</h6>
              <div id="statsChannels" class="small-text"></div>
            </div>

            <!-- Ссылки -->
            <div class="col-md-4 mb-3">
              <h6>Переходы по ссылкам</h6>
              <div id="statsLinks" class="small-text"></div>
            </div>
          </div>
          <div id="statsError" class="text-danger small-text mt-2"></div>
        </div>
      </div>
    </div>

    <script>
    // -----------------------------
    // Токен и выбор бота
    // -----------------------------

    function getToken() {
      return window.localStorage.getItem("adminToken") || "";
    }

    function setToken(token) {
      window.localStorage.setItem("adminToken", token || "");
    }

    function getCurrentBot() {
      return window.localStorage.getItem("currentBot") || "";
    }

    function setCurrentBot(name) {
      window.localStorage.setItem("currentBot", name || "");
    }

    function saveTokenAndInit() {
      const tokenInput = document.getElementById("adminToken");
      const token = tokenInput.value.trim();
      setToken(token);

      const statusEl = document.getElementById("tokenStatus");
      if (!token) {
        statusEl.textContent = "Токен очищен. Введите действительный токен, чтобы получить данные.";
      } else {
        statusEl.textContent = "Токен сохранён. Обновляю данные для выбранного бота...";
        initDashboard();
      }
    }

    function toggleTokenVisible() {
      const input = document.getElementById("adminToken");
      input.type = (input.type === "password") ? "text" : "password";
    }

    async function loadBotsList() {
      const sel = document.getElementById("botSelector");
      const st = document.getElementById("botSelectorStatus");
      sel.innerHTML = "";
      st.textContent = "";

      try {
        const bots = await apiGet("/admin/bots");
        if (!bots || !bots.length) {
          st.textContent = "Ботов в конфиге нет.";
          return;
        }
        const current = getCurrentBot() || bots[0].name;

        bots.forEach(b => {
          const opt = document.createElement("option");
          opt.value = b.name;
          opt.textContent = b.name + " (" + b.base_url + ")";
          if (b.name === current) {
            opt.selected = true;
          }
          sel.appendChild(opt);
        });

        setCurrentBot(current);
        st.textContent = "Выбран бот: " + current + ".";
      } catch (e) {
        st.textContent = "Не удалось загрузить список ботов: " + e.message;
      }
    }

    function onBotChange() {
      const sel = document.getElementById("botSelector");
      const val = sel.value || "";
      setCurrentBot(val);
      const st = document.getElementById("botSelectorStatus");
      if (val) {
        st.textContent = "Выбран бот: " + val + ". Нажмите «Обновить данные по боту».";
      } else {
        st.textContent = "Бот не выбран.";
      }
    }

    function currentBotPath(path) {
      const bot = getCurrentBot();
      if (!bot) {
        throw new Error("Бот не выбран");
      }
      return "/admin/" + encodeURIComponent(bot) + path;
    }

    // -----------------------------
    // HTTP-утилиты
    // -----------------------------

    async function apiRequest(method, url, body) {
      const token = getToken();
      const headers = {
        "Content-Type": "application/json"
      };
      if (token) {
        headers["x-admin-token"] = token;
      }

      const opts = { method, headers };
      if (body !== undefined && body !== null) {
        opts.body = JSON.stringify(body);
      }

      const resp = await fetch(url, opts);
      if (!resp.ok) {
        let msg = resp.status + " " + resp.statusText;
        try {
          const data = await resp.json();
          if (data.detail) {
            msg += " — " + data.detail;
          }
        } catch (e) {
          // ignore
        }
        throw new Error(msg);
      }
      try {
        return await resp.json();
      } catch {
        return null;
      }
    }

    async function apiGet(url) {
      return apiRequest("GET", url);
    }
    async function apiPost(url, body) {
      return apiRequest("POST", url, body);
    }
    async function apiDelete(url) {
      return apiRequest("DELETE", url);
    }

    // -----------------------------
    // Инициализация
    // -----------------------------

    async function initDashboard() {
      document.getElementById("botStatusError").textContent = "";
      document.getElementById("chatsError").textContent = "";
      document.getElementById("statsError").textContent = "";
      document.getElementById("peerError").textContent = "";
      const sessionStatusEl = document.getElementById("sessionStatus");
      const sessionErrorEl = document.getElementById("sessionError");
      if (sessionStatusEl) sessionStatusEl.textContent = "";
      if (sessionErrorEl) sessionErrorEl.textContent = "";

      try {
        await Promise.all([
          loadBotStatus(),
          loadChats(),
          loadStats()
        ]);
        document.getElementById("tokenStatus").textContent = "Данные по выбранному боту загружены.";
      } catch (e) {
        document.getElementById("tokenStatus").textContent =
          "Ошибка при загрузке данных: " + e.message;
      }
    }

    // -----------------------------
    // Блок 1: статус бота и управление
    // -----------------------------

    async function loadBotStatus() {
      try {
        const url = currentBotPath("/status");
        const data = await apiGet(url);
        const statusEl = document.getElementById("botStatusText");
        const detailsEl = document.getElementById("botStatusDetails");
        if (!data) {
          statusEl.textContent = "Неизвестно";
          detailsEl.textContent = "";
          return;
        }
        const running = !!data.bot_running;
        const enabled = !!data.enabled;

        let text = "";
        if (running && enabled) text = "Бот запущен и активен";
        else if (running && !enabled) text = "Бот запущен, но выключен (пауза)";
        else if (!running && enabled) text = "Бот включён в настройках, но не запущен";
        else text = "Бот остановлен";

        statusEl.textContent = text;
        detailsEl.textContent = "bot_running=" + running + ", enabled=" + enabled;
      } catch (e) {
        document.getElementById("botStatusError").textContent =
          "Не удалось получить статус бота: " + e.message;
      }
    }

    async function controlBot(action) {
      const errorEl = document.getElementById("botStatusError");
      errorEl.textContent = "";
      let path = "";
      if (action === "enable") {
        path = "/control/enable";
      } else if (action === "disable") {
        path = "/control/disable";
      } else if (action === "restart_bot") {
        path = "/control/restart_bot";
      } else {
        return;
      }
      try {
        const url = currentBotPath(path);
        await apiPost(url, {});
        await loadBotStatus();
      } catch (e) {
        errorEl.textContent = "Ошибка при управлении ботом: " + e.message;
      }
    }

    // -----------------------------
    // Блок 1.1: смена сессии
    // -----------------------------

    async function updateSession() {
      const input = document.getElementById("sessionInput");
      const statusEl = document.getElementById("sessionStatus");
      const errorEl = document.getElementById("sessionError");
      statusEl.textContent = "";
      errorEl.textContent = "";

      const val = (input.value || "").trim();
      if (!val) {
        errorEl.textContent = "Вставьте TELEGRAM_SESSION_STRING.";
        return;
      }

      try {
        const url = currentBotPath("/session/update");
        await apiPost(url, { telegram_session_string: val });
        statusEl.textContent =
          "Сессия сохранена в .env нужного бота. Нажмите «Перезапустить», чтобы бот перезапустился с новой сессией.";
      } catch (e) {
        errorEl.textContent = "Не удалось сохранить сессию: " + e.message;
      }
    }

    // -----------------------------
    // Блок 2: Каналы (allowlist)
    // -----------------------------

    async function loadChats() {
      const tbody = document.getElementById("chatsTableBody");
      tbody.innerHTML = "";
      try {
        const urlChats = currentBotPath("/chats/overview");
        const data = await apiGet(urlChats);
        const chats = data && data.chats ? data.chats : [];

        let byStatus = {};
        try {
          const urlStatus = currentBotPath("/stats/channels");
          const st = await apiGet(urlStatus);
          if (st && Array.isArray(st.items)) {
            st.items.forEach(c => {
              byStatus[String(c.chat_id)] = c;
            });
          }
        } catch (e) {
          // статы могут быть не настроены — не критично
        }

        if (!chats.length) {
          const tr = document.createElement("tr");
          const td = document.createElement("td");
          td.colSpan = 4;
          td.textContent = "Список каналов пуст.";
          tr.appendChild(td);
          tbody.appendChild(tr);
          return;
        }

        chats.forEach(ch => {
          const tr = document.createElement("tr");

          const tdId = document.createElement("td");
          tdId.textContent = ch.chat_id;
          tr.appendChild(tdId);

          const tdAllow = document.createElement("td");
          tdAllow.textContent = ch.in_allowlist ? "Да" : "Нет";
          tr.appendChild(tdAllow);

          const tdStatus = document.createElement("td");
          const statusInfo = byStatus[String(ch.chat_id)];
          if (statusInfo) {
            const st = statusInfo.status || "ok";
            if (st === "banned_local") {
              tdStatus.innerHTML = '<span class="badge bg-danger status-бadge">Проблемный / бан</span>';
            } else if (st === "flood_limited") {
              tdStatus.innerHTML = '<span class="badge bg-warning text-dark status-бadge">Лимит / Flood</span>';
            } else {
              tdStatus.innerHTML = '<span class="badge bg-success status-бadge">OK</span>';
            }
          } else {
            tdStatus.innerHTML = '<span class="badge bg-secondary status-бadge">нет данных</span>';
          }
          tr.appendChild(tdStatus);

          const tdAct = document.createElement("td");
          const btnDel = document.createElement("button");
          btnDel.className = "btn btn-sm btn-outline-danger";
          btnDel.textContent = "Удалить из списка";
          btnDel.onclick = () => removeChannel(ch.chat_id);
          tdAct.appendChild(btnDel);
          tr.appendChild(tdAct);

          tbody.appendChild(tr);
        });
      } catch (e) {
        document.getElementById("chatsError").textContent =
          "Не удалось загрузить список каналов: " + e.message;
      }
    }

    async function addChannel() {
      const input = document.getElementById("peerInput");
      const peerError = document.getElementById("peerError");
      const infoEl = document.getElementById("peerResolveInfo");
      peerError.textContent = "";
      infoEl.textContent = "";

      const raw = (input.value || "").trim();
      if (!raw) {
        peerError.textContent = "Введите @username, ссылку или ID канала.";
        return;
      }

      const parts = raw.split(/\\s+/).filter(Boolean);
      if (!parts.length) {
        peerError.textContent = "Не получилось выделить каналы из строки.";
        return;
      }

      let added = [];
      let failed = [];

      for (const part of parts) {
        try {
          let body = {};
          if (/^-?\\d+$/.test(part)) {
            body = { chat_id: parseInt(part, 10) };
          } else {
            body = { peer: part };
          }

          const url = currentBotPath("/allowlist/add");
          const data = await apiPost(url, body);
          added.push(data.added_chat_id);
        } catch (e) {
          failed.push(part);
        }
      }

      if (added.length) {
        infoEl.textContent = "Добавлены каналы с ID: " + added.join(", ");
        await loadChats();
      }

      if (failed.length) {
        peerError.textContent =
          "Не удалось добавить: " + failed.join(", ") + ". Проверь ссылки/юзернеймы.";
      }
    }

    async function removeChannel(chatId) {
      const chatsError = document.getElementById("chatsError");
      chatsError.textContent = "";
      if (!confirm("Точно удалить канал " + chatId + " из списка?")) {
        return;
      }
      try {
        const url = currentBotPath("/allowlist/" + encodeURIComponent(chatId));
        await apiDelete(url);
        await loadChats();
      } catch (e) {
        chatsError.textContent = "Не удалось удалить канал: " + e.message;
      }
    }

    // -----------------------------
    // Блок 3: Статистика
    // -----------------------------

    async function loadStats() {
      await Promise.all([
        loadStatsOverview(),
        loadStatsChannels(),
        loadStatsLinks()
      ]);
    }

    async function loadStatsOverview() {
      const listEl = document.getElementById("statsOverviewList");
      listEl.innerHTML = "";
      try {
        const url = currentBotPath("/stats/overview");
        const data = await apiGet(url);
        if (!data) {
          listEl.innerHTML = "<li>Нет данных.</li>";
          return;
        }
        const items = [
          "Бот: " + (data.bot_name || "—"),
          "Каналов всего: " + data.channels_total,
          "Проблемных (бан): " + data.channels_banned,
          "На лимите (flood): " + data.channels_flood_limited,
          "Сообщений за 24 часа: " + data.messages_last_24h,
          "Сообщений за 7 дней: " + data.messages_last_7d,
          "Всего кликов по ссылкам: " + data.links_total_clicks
        ];
        items.forEach(t => {
          const li = document.createElement("li");
          li.textContent = t;
          listEl.appendChild(li);
        });
      } catch (e) {
        listEl.innerHTML = "<li class='text-danger'>Ошибка: " + e.message + "</li>";
      }
    }

    async function loadStatsChannels() {
      const el = document.getElementById("statsChannels");
      el.innerHTML = "";
      try {
        const url = currentBotPath("/stats/channels");
        const data = await apiGet(url);
        const items = data && Array.isArray(data.items) ? data.items : [];
        if (!items.length) {
          el.textContent = "Нет данных по статусам каналов.";
          return;
        }
        const ok = items.filter(i => i.status === "ok").length;
        const banned = items.filter(i => i.status === "banned_local").length;
        const flood = items.filter(i => i.status === "flood_limited").length;

        const lines = [
          "Всего каналов в статусах: " + items.length,
          "OK: " + ok,
          "Проблемных (ban): " + banned,
          "На лимите (flood): " + flood
        ];

        lines.forEach(t => {
          const div = document.createElement("div");
          div.textContent = t;
          el.appendChild(div);
        });
      } catch (e) {
        el.innerHTML = "<span class='text-danger'>Ошибка: " + e.message + "</span>";
      }
    }

    async function loadStatsLinks() {
      const el = document.getElementById("statsLinks");
      el.innerHTML = "";
      try {
        const url = currentBotPath("/stats/links");
        const data = await apiGet(url);
        const items = data && Array.isArray(data.items) ? data.items : [];
        if (!items.length) {
          el.textContent = "Пока нет ни одной трекаемой ссылки.";
          return;
        }

        items.forEach(row => {
          const div = document.createElement("div");
          div.textContent = row.slug + " → " + row.target_url + " (кликов: " + row.clicks + ")";
          el.appendChild(div);
        });
      } catch (e) {
        el.innerHTML = "<span class='text-danger'>Ошибка: " + e.message + "</span>";
      }
    }

    // -----------------------------
    // Автоинициализация
    // -----------------------------

    window.addEventListener("DOMContentLoaded", async () => {
      const stored = getToken();
      const input = document.getElementById("adminToken");
      if (stored) {
        input.value = stored;
        document.getElementById("tokenStatus").textContent = "Токен загружен из браузера. Обновляю список ботов...";
      } else {
        document.getElementById("tokenStatus").textContent =
          "Введите админ-токен и нажмите «Сохранить токен и обновить данные».";
      }

      await loadBotsList();
    });
    </script>

    </body>
    </html>
    """
    return HTMLResponse(html)
