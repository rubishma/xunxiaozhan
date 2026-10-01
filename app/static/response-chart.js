/* 仅绘制已保存的历史；本脚本不发起网络请求。 */
(() => {
  const svg = document.querySelector("#response-chart");
  const summary = document.querySelector("#chart-summary");
  const detail = document.querySelector("#chart-detail");
  const ns = "http://www.w3.org/2000/svg";
  let lastInput = [[], "empty", null];
  const shortTime = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  });
  const fullTime = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "medium", hour12: false
  });

  function node(tag, attributes = {}, text) {
    const element = document.createElementNS(ns, tag);
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function render(rows, state, targetId, resizing = false) {
    const previousDetail = resizing ? detail.textContent : "";
    const focusedIndex = resizing ? [...svg.querySelectorAll(".chart-point")].indexOf(document.activeElement) : -1;
    lastInput = [rows, state, targetId];
    svg.replaceChildren();
    svg.setAttribute("hidden", "");
    detail.textContent = "";
    if (!targetId || state === "loading" || state === "error" || !rows.length) {
      summary.textContent = !targetId ? "请选择目标。" : state === "loading" ? "正在读取图表数据…" :
        state === "error" ? "历史读取失败，暂时无法绘图；请刷新历史与事件重试。" :
        "还没有检查记录。完成一次检查后即可看到数据点。";
      return;
    }
    const points = rows.slice(0, 20).map(row => ({ row, time: Date.parse(row.checked_at), value: row.response_ms }));
    if (points.some(p => !Number.isFinite(p.time) || !Number.isFinite(p.value) || p.value < 0)) {
      summary.textContent = "记录中有无效的时间或耗时，暂时无法绘图；请查看下方历史。";
      return;
    }
    points.sort((a, b) => a.time - b.time || a.row.id - b.row.id);
    svg.removeAttribute("hidden");
    const width = Math.max(260, svg.getBoundingClientRect().width);
    const height = 260, left = 52, right = width - 16, top = 28, bottom = 210;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.append(node("title", {}, `目标 #${targetId} 最近 ${points.length} 次检查耗时`));
    const maximum = Math.max(...points.map(p => p.value), 1);
    const unit = 10 ** Math.floor(Math.log10(maximum));
    const upper = Math.ceil(maximum / unit) * unit;
    const first = points[0].time, last = points[points.length - 1].time;
    const x = time => first === last ? (left + right) / 2 : left + (time - first) / (last - first) * (right - left);
    const y = value => bottom - value / upper * (bottom - top);
    for (const value of [0, upper / 2, upper]) {
      svg.append(node("line", { x1: left, y1: y(value), x2: right, y2: y(value), stroke: "#dce4ed" }));
      svg.append(node("text", { x: left - 8, y: y(value) + 4, "text-anchor": "end", class: "chart-axis" },
        new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(value)));
    }
    svg.append(node("text", { x: left, y: 15, class: "chart-axis" }, "耗时 ms"));
    const ticks = first === last ? [first] : [first, (first + last) / 2, last];
    ticks.forEach((time, index) => svg.append(node("text", {
      x: x(time), y: bottom + 24, class: "chart-axis",
      "text-anchor": ticks.length === 1 ? "middle" : index === 0 ? "start" : index === 2 ? "end" : "middle"
    }, shortTime.format(time))));
    svg.append(node("text", { x: right, y: height - 5, "text-anchor": "end", class: "chart-axis" }, "检查时间（北京）"));

    // 只连接相邻、正常且网址相同的采样点；不跨失败或配置变更连线。
    points.forEach((point, index) => {
      const previous = points[index - 1];
      if (previous && previous.row.status === "up" && point.row.status === "up" && previous.row.target_url === point.row.target_url) {
        svg.append(node("line", { class: "chart-segment", x1: x(previous.time), y1: y(previous.value),
          x2: x(point.time), y2: y(point.value), stroke: "#235bc1", "stroke-width": 2 }));
      }
    });
    points.forEach(point => {
      const row = point.row, cx = x(point.time), cy = y(point.value), up = row.status === "up";
      const message = `${fullTime.format(point.time)}（北京时间） · ${up ? "正常" : "异常"} · ` +
        `${point.value} ms · ${row.status_code === null ? "未收到 HTTP 响应" : "HTTP " + row.status_code} · ` +
        `${row.source === "scheduled" ? "定时" : "手动"} · ${row.target_url} · ${row.error || "无错误"}`;
      const group = node("g", { class: "chart-point", tabindex: 0, role: "button", "aria-label": message });
      group.append(node("title", {}, message));
      group.append(node("circle", { cx, cy, r: 12, fill: "transparent" }));
      group.append(up ? node("circle", { class: "point-symbol", cx, cy, r: 4.5, fill: "#235bc1" }) :
        node("path", { class: "point-symbol", d: `M ${cx} ${cy - 6} L ${cx + 6} ${cy} L ${cx} ${cy + 6} L ${cx - 6} ${cy} Z`, fill: "#a22d2d" }));
      const show = () => { detail.textContent = message; };
      group.addEventListener("mouseenter", show);
      group.addEventListener("focus", show);
      group.addEventListener("click", show);
      group.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); show(); }
      });
      svg.append(group);
    });
    const failures = points.filter(p => p.row.status !== "up").length;
    summary.textContent = `目标 #${targetId} · 最近 ${points.length} 次检查，${failures} 次异常；从左到右按检查时间排列。`;
    detail.textContent = previousDetail || "点击、悬停或用 Tab 选择数据点，查看该次检查详情。";
    if (focusedIndex >= 0) svg.querySelectorAll(".chart-point")[focusedIndex]?.focus({ preventScroll: true });
  }

  window.renderResponseChart = render;
  // 重算坐标而非整体缩小文字，窄屏仍保留可读字号。
  if (typeof ResizeObserver !== "undefined") {
    let lastWidth = 0;
    new ResizeObserver(entries => {
      const width = entries[0].contentRect.width;
      if (width > 0 && Math.abs(width - lastWidth) > 1) {
        lastWidth = width;
        render(...lastInput, true);
      }
    }).observe(svg.parentElement);
  }
})();
