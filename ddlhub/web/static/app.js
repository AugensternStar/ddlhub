/* 日历初始化（数据由服务端注入 #events-data，FullCalendar v6 全局构建） */
(function () {
  function initCalendar() {
    var el = document.getElementById("calendar");
    var dataEl = document.getElementById("events-data");
    if (!el || !dataEl || typeof FullCalendar === "undefined") return;
    var events;
    try {
      events = JSON.parse(dataEl.textContent);
    } catch (e) {
      return;
    }
    var cal = new FullCalendar.Calendar(el, {
      initialView: window.innerWidth < 700 ? "listWeek" : "dayGridMonth",
      height: "auto",
      firstDay: 1,
      locale: "zh-cn",
      events: events,
      eventClick: function (info) {
        if (info.event.url) {
          info.jsEvent.preventDefault();
          window.open(info.event.url, "_blank", "noopener");
        }
      }
    });
    cal.render();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCalendar);
  } else {
    initCalendar();
  }
})();

/* 有头人工登录状态轮询：成功后刷新页面（管理器线程已完成同步） */
window.pollLoginStatus = function (accountId) {
  var started = Date.now();
  var timer = setInterval(function () {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", "/api/accounts/" + accountId + "/login-status");
    xhr.onload = function () {
      if (xhr.status !== 200) return;
      var s = JSON.parse(xhr.responseText);
      var el = document.getElementById("login-status");
      if (el) el.textContent = s.message || "";
      document.title = s.state === "waiting" ? "⏳ 等待登录…" : "DDLHub · 作业时间线";
      if (s.state === "success") {
        clearInterval(timer);
        window.location.href = "/?view=timeline";
      } else if (s.state === "failed" || s.state === "timeout") {
        clearInterval(timer);
        alert("登录未完成：" + (s.message || "请重试"));
      } else if (Date.now() - started > 200000) {
        clearInterval(timer);
      }
    };
    xhr.send();
  }, 2000);
};

