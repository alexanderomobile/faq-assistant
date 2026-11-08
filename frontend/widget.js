// Чат-виджет FAQ-ассистента: кнопка в правом нижнем углу и окно диалога.
// Без встроенных скриптов и innerHTML: подходит для сайта со строгой политикой CSP (script-src 'self').
// Настройки — data-атрибуты контейнера #faq-chat:
//   data-endpoint  адрес POST-запроса с вопросом (на сайте /api/chat, на демо-странице http://127.0.0.1:8301/chat)
//   data-reset     адрес сброса диалога
//   data-greeting  первое сообщение помощника
// Если на странице есть <meta name="csrf-token">, токен уходит в заголовке X-CSRFToken.
(function () {
  "use strict";

  var root = document.getElementById("faq-chat");
  if (!root) return;

  var STORE = "faq-chat-v1";
  var MAX_STORED = 30;
  var endpoint = root.getAttribute("data-endpoint") || "/api/chat";
  var resetUrl = root.getAttribute("data-reset") || "";
  var greeting = root.getAttribute("data-greeting") || "Здравствуйте. Чем помочь?";
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');

  var launcher = root.querySelector("[data-chat-open]");
  var panel = root.querySelector("[data-chat-panel]");
  var closeBtn = root.querySelector("[data-chat-close]");
  var newBtn = root.querySelector("[data-chat-new]");
  var log = root.querySelector("[data-chat-log]");
  var form = root.querySelector("[data-chat-form]");
  var input = root.querySelector("[data-chat-input]");
  var sendBtn = root.querySelector("[data-chat-send]");
  var sending = false;
  var rendered = false;

  function newSessionId() {
    var bytes = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }

  // Диалог живёт во вкладке: при переходе между страницами сайта переписка и память помощника сохраняются.
  function load() {
    try {
      var data = JSON.parse(sessionStorage.getItem(STORE) || "null");
      if (data && typeof data.sid === "string" && Array.isArray(data.messages)) return data;
    } catch (e) { /* хранилище недоступно — начнём заново */ }
    return { sid: newSessionId(), messages: [] };
  }

  var state = load();

  function save() {
    try {
      state.messages = state.messages.slice(-MAX_STORED);
      sessionStorage.setItem(STORE, JSON.stringify(state));
    } catch (e) { /* без хранилища виджет всё равно работает */ }
  }

  // Ссылки https://, адреса почты и @ники Telegram становятся кликабельными; остальной текст — только textContent.
  // Без lookbehind в регулярном выражении: Safari до 16.4 его не понимает, и скрипт не запустился бы вовсе.
  var LINK = /(https:\/\/[^\s<>"«»()]+[^\s<>"«»().,;:!?])|([\w.+-]+@[\w-]+(?:\.[\w-]+)+)|(@[A-Za-z][A-Za-z0-9_]{3,})/g;

  function fillText(el, text) {
    var last = 0;
    var match;
    LINK.lastIndex = 0;
    while ((match = LINK.exec(text)) !== null) {
      // «@ник» сразу после буквы, цифры или точки — часть чего-то другого, а не ник.
      if (match[3] && match.index > 0 && /[A-Za-z0-9_.]/.test(text.charAt(match.index - 1))) continue;
      if (match.index > last) el.appendChild(document.createTextNode(text.slice(last, match.index)));
      var a = document.createElement("a");
      a.href = match[1] ? match[1] : match[2] ? "mailto:" + match[2] : "https://t.me/" + match[3].slice(1);
      a.textContent = match[0];
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      el.appendChild(a);
      last = match.index + match[0].length;
    }
    if (last < text.length) el.appendChild(document.createTextNode(text.slice(last)));
  }

  function render(message) {
    var div = document.createElement("div");
    div.className = "faq-chat__msg faq-chat__msg--" + message.role;
    fillText(div, message.text);
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function add(role, text, persist) {
    var message = { role: role, text: text };
    render(message);
    if (persist !== false) {
      state.messages.push(message);
      save();
    }
  }

  // Переписка рисуется один раз: при повторном открытии окна экранный чтец не перечитывает её заново.
  function renderAll() {
    while (log.firstChild) log.removeChild(log.firstChild);
    render({ role: "bot", text: greeting });
    state.messages.forEach(render);
    rendered = true;
  }

  function typing(on) {
    var el = log.querySelector(".faq-chat__typing");
    if (on && !el) {
      el = document.createElement("div");
      el.className = "faq-chat__msg faq-chat__msg--bot faq-chat__typing";
      el.setAttribute("role", "status");
      var note = document.createElement("span");
      note.className = "faq-chat__sr";
      note.textContent = "Помощник печатает";
      el.appendChild(note);
      for (var i = 0; i < 3; i++) el.appendChild(document.createElement("span"));
      log.appendChild(el);
      log.scrollTop = log.scrollHeight;
    } else if (!on && el) {
      el.remove();
    }
  }

  function headers() {
    var h = { "Content-Type": "application/json" };
    if (csrfMeta) h["X-CSRFToken"] = csrfMeta.getAttribute("content");
    return h;
  }

  function send(text) {
    if (sending) return;
    sending = true;
    sendBtn.disabled = true;
    var sid = state.sid;
    add("user", text);
    typing(true);
    fetch(endpoint, {
      method: "POST",
      headers: headers(),
      credentials: "same-origin",
      body: JSON.stringify({ message: text, session_id: sid })
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (res.ok && data.answer) return data.answer;
          return (data && typeof data.detail === "string") ? data.detail
            : "Не получилось получить ответ. Попробуйте ещё раз чуть позже.";
        });
      })
      .catch(function () { return "Нет связи с помощником. Проверьте интернет и попробуйте ещё раз."; })
      .then(function (answer) {
        typing(false);
        sending = false;
        sendBtn.disabled = false;
        // Пока шёл ответ, посетитель начал новый диалог: ответ на старый вопрос в него не попадает.
        if (state.sid !== sid) return;
        add("bot", answer);
        input.focus();
      });
  }

  function open() {
    panel.hidden = false;
    launcher.setAttribute("aria-expanded", "true");
    root.classList.add("is-open");
    if (!rendered) renderAll();
    setTimeout(function () { input.focus(); }, 30);
  }

  function close() {
    panel.hidden = true;
    launcher.setAttribute("aria-expanded", "false");
    root.classList.remove("is-open");
    launcher.focus();
  }

  launcher.addEventListener("click", function () { panel.hidden ? open() : close(); });
  closeBtn.addEventListener("click", close);
  root.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) close(); });

  if (newBtn) {
    newBtn.addEventListener("click", function () {
      var old = state.sid;
      state = { sid: newSessionId(), messages: [] };
      save();
      renderAll();
      if (resetUrl) {
        var warn = function () {
          add("bot", "Не получилось очистить память помощника на сервере. Она забудется сама через 30 минут без сообщений.", false);
        };
        fetch(resetUrl, { method: "POST", headers: headers(), credentials: "same-origin",
          body: JSON.stringify({ session_id: old }) })
          .then(function (res) { if (!res.ok) warn(); })
          .catch(warn);
      }
      input.focus();
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (sending) return;  // ответ ещё идёт: текст остаётся в поле, а не пропадает
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    send(text);
  });

  // Кнопка в разметке скрыта и появляется, только когда скрипт запустился: без него она ничего бы не делала.
  launcher.hidden = false;

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });
})();
