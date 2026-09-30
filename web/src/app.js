/* Beamline exposure scheduler — frontend (no build step). */
"use strict";

const state = {
  horizon: 1000,
  exposures: [
    { id: "A", duration: 4, earliest_start: 0, latest_start: 50, equipment: "X", cooling: 2, startup_demand: 6 },
    { id: "B", duration: 3, earliest_start: 0, latest_start: 50, equipment: "X", cooling: 1, startup_demand: 5 },
    { id: "C", duration: 5, earliest_start: 0, latest_start: 50, equipment: "Y", cooling: 0, startup_demand: 1 },
    { id: "D", duration: 2, earliest_start: 0, latest_start: 50, equipment: "Y", cooling: 3, startup_demand: 1 },
    { id: "E", duration: 6, earliest_start: 2, latest_start: 40, equipment: "Z", cooling: 0, startup_demand: 1 },
  ],
  links: [{ from_id: "A", to_id: "B", min_gap: 0, max_gap: "" }],
  cooling: { enabled: false, capacity: 10, initial: 6, recovery: 2 },
  result: null,
  stale: false,
};

const $ = (sel) => document.querySelector(sel);

/* ---------------- table rendering ---------------- */

function renderExpRows() {
  const tbody = $("#exp-table tbody");
  tbody.innerHTML = "";
  state.exposures.forEach((row, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="muted">${i + 1}</td>
      <td class="id-cell"><input data-i="${i}" data-k="id" value="${row.id}"></td>
      <td><input type="number" min="1" step="1" data-i="${i}" data-k="duration" value="${row.duration}"></td>
      <td><input type="number" min="0" step="1" data-i="${i}" data-k="earliest_start" value="${row.earliest_start}"></td>
      <td><input type="number" min="0" step="1" data-i="${i}" data-k="latest_start" value="${row.latest_start}"></td>
      <td class="cell-eq"><input data-i="${i}" data-k="equipment" value="${row.equipment}"></td>
      <td><input type="number" min="0" step="1" data-i="${i}" data-k="cooling" value="${row.cooling}"></td>
      <td class="cool-col"><input type="number" min="0" step="1" data-i="${i}" data-k="startup_demand" value="${row.startup_demand ?? 0}"${state.cooling.enabled ? "" : " disabled"}></td>
      <td><button type="button" class="btn secondary" data-del-i="${i}">删除</button></td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("input").forEach((inp) => {
    inp.addEventListener("input", onExpInput);
  });
  tbody.querySelectorAll("button[data-del-i]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (state.exposures.length <= 5) {
        flashForm("至少需要 5 项曝光", true);
        return;
      }
      state.exposures.splice(Number(btn.dataset.delI), 1);
      reconcileLinks();
      markDirty();
      renderAll();
    });
  });
  syncLinkIdOptions();
}

function onExpInput(ev) {
  const i = Number(ev.target.dataset.i);
  const k = ev.target.dataset.k;
  let v = ev.target.value;
  if (k !== "id" && k !== "equipment") v = v === "" ? "" : Number(v);
  state.exposures[i][k] = v;
  if (k === "id") syncLinkIdOptions();
  markDirty();
}

function renderLinkRows() {
  const tbody = $("#link-table tbody");
  tbody.innerHTML = "";
  state.links.forEach((ln, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><select data-i="${i}" data-k="from_id"></select></td>
      <td><select data-i="${i}" data-k="to_id"></select></td>
      <td><input type="number" min="0" step="1" data-i="${i}" data-k="min_gap" value="${ln.min_gap}"></td>
      <td><input type="number" min="0" step="1" data-max-i="${i}" placeholder="不限" value="${ln.max_gap}"></td>
      <td><button type="button" class="btn secondary" data-del-link="${i}">删除</button></td>`;
    tbody.appendChild(tr);
  });
  syncLinkIdOptions();
  tbody.querySelectorAll("select, input").forEach((el) => {
    el.addEventListener("input", onLinkInput);
    el.addEventListener("change", onLinkInput);
  });
  tbody.querySelectorAll("button[data-del-link]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.links.splice(Number(btn.dataset.delLink), 1);
      markDirty();
      renderAll();
    });
  });
}

function syncLinkIdOptions() {
  const ids = state.exposures.map((e) => e.id);
  document.querySelectorAll("#link-table select").forEach((sel) => {
    const i = Number(sel.dataset.i);
    const k = sel.dataset.k;
    const current = state.links[i] ? state.links[i][k] : "";
    sel.innerHTML = ids
      .map((id) => `<option value="${id}"${id === current ? " selected" : ""}>${id}</option>`)
      .join("");
  });
}

function onLinkInput(ev) {
  const el = ev.target;
  if (el.dataset.maxI !== undefined) {
    const i = Number(el.dataset.maxI);
    state.links[i].max_gap = el.value === "" ? "" : Number(el.value);
  } else {
    const i = Number(el.dataset.i);
    const k = el.dataset.k;
    let v = el.value;
    if (k === "min_gap") v = v === "" ? "" : Number(v);
    state.links[i][k] = v;
  }
  markDirty();
}

function reconcileLinks() {
  const ids = new Set(state.exposures.map((e) => e.id));
  state.links = state.links.filter((l) => ids.has(l.from_id) && ids.has(l.to_id));
}

function renderAll() {
  renderExpRows();
  renderLinkRows();
}

/* ---------------- draft invalidation ---------------- */

function markDirty() {
  state.stale = true;
  state.result = null;
  $("#stale-banner").classList.remove("hidden");
  $("#result-panel").classList.add("hidden");
  $("#input-error-panel").classList.add("hidden");
  $("#infeasible-panel").classList.add("hidden");
  flashForm("", false);
}

/* ---------------- client-side validation ---------------- */

function validateDraft() {
  const errors = [];
  const ids = [];
  state.exposures.forEach((e, idx) => {
    const tag = `第 ${idx + 1} 项（${e.id || "未命名"}）`;
    if (!String(e.id).trim()) errors.push(`${tag}：编号不能为空`);
    ids.push(e.id);
    if (!String(e.equipment).trim()) errors.push(`${tag}：设备不能为空`);
    for (const [k, label] of [
      ["duration", "持续时间"],
      ["earliest_start", "最早开始"],
      ["latest_start", "最晚开始"],
      ["cooling", "冷却时间"],
    ]) {
      const v = e[k];
      if (!Number.isInteger(v)) errors.push(`${tag}：${label}必须是整数`);
    }
    if (Number.isInteger(e.duration) && e.duration < 1)
      errors.push(`${tag}：持续时间必须 ≥ 1`);
    for (const k of ["earliest_start", "latest_start", "cooling"]) {
      if (Number.isInteger(e[k]) && e[k] < 0)
        errors.push(`${tag}：${k} 不能为负`);
    }
    if (Number.isInteger(e.earliest_start) && Number.isInteger(e.latest_start)
        && e.earliest_start > e.latest_start)
      errors.push(`${tag}：最早开始晚于最晚开始`);
    if (Number.isInteger(e.latest_start) && Number.isInteger(e.duration)
        && e.latest_start + e.duration > state.horizon)
      errors.push(`${tag}：最晚结束超过时间范围 H=${state.horizon}`);
    if (state.cooling.enabled) {
      if (!Number.isInteger(e.startup_demand))
        errors.push(`${tag}：启动耗量必须是整数`);
      else if (e.startup_demand < 0)
        errors.push(`${tag}：启动耗量不能为负`);
    }
  });
  const dupes = ids.filter((id, i) => id && ids.indexOf(id) !== i);
  [...new Set(dupes)].forEach((d) => errors.push(`曝光编号重复：${d}`));

  state.links.forEach((ln, i) => {
    const tag = `约束 ${i + 1}（${ln.from_id} → ${ln.to_id}）`;
    if (ln.from_id === ln.to_id) errors.push(`${tag}：不能衔接自身`);
    if (!Number.isInteger(ln.min_gap) || ln.min_gap < 0)
      errors.push(`${tag}：最小间隔必须为非负整数`);
    if (ln.max_gap !== "" && ln.max_gap !== null) {
      if (!Number.isInteger(ln.max_gap) || ln.max_gap < 0)
        errors.push(`${tag}：最大间隔必须为非负整数或留空`);
      else if (Number.isInteger(ln.min_gap) && ln.max_gap < ln.min_gap)
        errors.push(`${tag}：最大间隔小于最小间隔`);
    }
  });

  if (state.cooling.enabled) {
    const c = state.cooling;
    if (!Number.isInteger(c.capacity) || c.capacity < 1)
      errors.push("共享冷量：容量必须是 ≥ 1 的整数");
    if (!Number.isInteger(c.initial) || c.initial < 0)
      errors.push("共享冷量：时刻零初始量必须是非负整数");
    if (!Number.isInteger(c.recovery) || c.recovery < 0)
      errors.push("共享冷量：每整数时刻恢复量必须是非负整数");
    if (Number.isInteger(c.capacity) && Number.isInteger(c.initial)
        && c.initial > c.capacity)
      errors.push(`共享冷量：初始量（${c.initial}）超过容量（${c.capacity}）`);
  }
  return errors;
}

/* ---------------- solve ---------------- */

async function solve() {
  if (state.stale === false && state.result) return;
  $("#input-error-panel").classList.add("hidden");
  $("#infeasible-panel").classList.add("hidden");
  $("#result-panel").classList.add("hidden");

  const localErrors = validateDraft();
  if (localErrors.length) {
    showInputErrors(localErrors);
    return;
  }

  const payload = {
    horizon: state.horizon,
    exposures: state.exposures.map((e) => ({
      id: String(e.id).trim(),
      duration: e.duration,
      earliest_start: e.earliest_start,
      latest_start: e.latest_start,
      equipment: String(e.equipment).trim(),
      cooling: e.cooling,
      startup_demand: state.cooling.enabled ? e.startup_demand : null,
    })),
    links: state.links.map((l) => ({
      from_id: l.from_id,
      to_id: l.to_id,
      min_gap: l.min_gap,
      max_gap: l.max_gap === "" ? null : l.max_gap,
    })),
    shared_cooling: state.cooling.enabled
      ? {
          capacity: state.cooling.capacity,
          initial_amount: state.cooling.initial,
          recovery_per_time: state.cooling.recovery,
        }
      : null,
  };

  const btn = $("#solve-btn");
  btn.disabled = true;
  btn.textContent = "求解中…";
  try {
    const resp = await fetch("/api/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    let data = null;
    try { data = await resp.json(); } catch (_) { /* non-JSON */ }

    if (resp.status === 400 || resp.status === 422) {
      const msgs = data && data.field_errors && data.field_errors.length
        ? data.field_errors
        : ["服务端拒绝了请求（字段校验未通过）。"];
      showInputErrors(msgs);
      return;
    }
    if (resp.status === 503) {
      flashForm("求解器在时限内未能判定，请增大时间范围或放宽约束后重试。", true);
      return;
    }
    if (!resp.ok || !data) {
      flashForm(`服务异常（HTTP ${resp.status}），请检查 API 健康状态。`, true);
      return;
    }
    if (!data.feasible) {
      // Valid input, but no executable timing exists.
      $("#infeasible-panel").classList.remove("hidden");
      $("#infeasible-hint").textContent = state.cooling.enabled
        ? "建议：放宽最晚开始时刻、错开启动时刻以等待冷量恢复、调大容量/初始量/恢复量或减小启动耗量，也可放宽设备与衔接约束。冷量不足不会返回任何部分排程，也不会沿用旧方案。"
        : "建议：放宽最晚开始时刻、缩短冷却、错开设备或放松最大衔接间隔。不会返回任何部分曝光方案，也不会沿用旧方案。";
      $("#stale-banner").classList.add("hidden");
      state.stale = false;
      state.result = null;
      return;
    }
    state.result = data;
    state.stale = false;
    $("#stale-banner").classList.add("hidden");
    renderResult(data);
  } catch (err) {
    flashForm(`无法连接 API：${err.message}`, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "求解排程";
  }
}

function showInputErrors(messages) {
  $("#field-error-list").innerHTML = messages
    .map((m) => `<li>${escapeHtml(m)}</li>`)
    .join("");
  $("#input-error-panel").classList.remove("hidden");
}

/* ---------------- result rendering ---------------- */

function renderResult(r) {
  $("#m-makespan").textContent = r.makespan;
  $("#m-sum").textContent = r.sum_starts;
  $("#m-vector").textContent = `[${r.starts.join(", ")}]`;
  $("#m-time").textContent = `${r.solver_time_ms} ms`;

  drawGantt(r);
  drawOrders(r.equipment_orders);
  drawSlacks(r.slacks);
  drawCooling(r);

  $("#result-panel").classList.remove("hidden");
}

function drawOrders(orders) {
  const box = $("#equipment-orders");
  box.innerHTML = "";
  orders.forEach((o) => {
    const row = document.createElement("div");
    row.className = "order-row";
    row.innerHTML =
      `<span class="eq-name">${escapeHtml(o.equipment)}</span>` +
      o.sequence
        .map((id, i) =>
          (i ? '<span class="arrow">→</span>' : "") +
          `<span class="chip">${escapeHtml(id)}</span>`)
        .join("");
    box.appendChild(row);
  });
}

function drawSlacks(slacks) {
  const tbody = $("#slack-table tbody");
  if (!slacks.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="muted">未设置衔接约束。</td></tr>`;
    return;
  }
  tbody.innerHTML = slacks
    .map((s) => {
      const req = `[${s.min_gap}, ${s.max_gap === null ? "∞" : s.max_gap}]`;
      const slack = s.slack === null ? "—" : s.slack;
      return `<tr>
        <td>${escapeHtml(s.from_id)} → ${escapeHtml(s.to_id)}</td>
        <td>${req}</td>
        <td>${s.actual_gap}</td>
        <td>${slack}</td>
      </tr>`;
    })
    .join("");
}

/* ---------------- shared coolant account ---------------- */

function drawCooling(r) {
  const panel = $("#cooling-result");
  if (!state.cooling.enabled || !r.cooling_events) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  const cfg = state.cooling;
  const events = r.cooling_events;
  $("#cool-summary").textContent =
    `容量 ${cfg.capacity}，时刻零初始量 ${cfg.initial}，每整数时刻恢复 ${cfg.recovery}；` +
    `共 ${events.length} 次启动扣减，最终余量 ${events[events.length - 1].level_after}。`;

  drawCoolingTimeline(events, cfg);
  drawCoolingTable(events);
}

function drawCoolingTable(events) {
  const tbody = $("#cool-table tbody");
  tbody.innerHTML = events.map((ev, k) => {
    let why;
    if (k === 0) {
      why = `自时刻零起经过 ${ev.start} 个整数时刻，恢复 ${ev.recovered}` +
        `（不超过容量 ${state.cooling.capacity}）`;
    } else {
      const prev = events[k - 1];
      const dt = ev.start - prev.start;
      why = dt === 0
        ? `与第 ${prev.order + 1} 项（${escapeHtml(prev.exposure_id)}）同一时刻启动，按录入顺序连续扣减、不恢复`
        : `距上次启动 ${dt} 个时刻，恢复 min(${state.cooling.recovery}×${dt}, 容量−余量) = ${ev.recovered}`;
    }
    why += `；扣减 ${ev.demand} 后余量 ${ev.level_after}` +
      (ev.level_after === 0 ? "（恰好耗尽，仍未透支）" : "");
    return `<tr>
      <td>${ev.order + 1}</td>
      <td>${escapeHtml(ev.exposure_id)}</td>
      <td>${ev.start}</td>
      <td>${ev.recovered}</td>
      <td>${ev.level_before}</td>
      <td>${ev.demand}</td>
      <td><strong>${ev.level_after}</strong></td>
      <td class="muted">${why}</td>
    </tr>`;
  }).join("");
}

function drawCoolingTimeline(events, cfg) {
  const host = $("#cool-timeline");
  host.innerHTML = "";
  const svgNS = "http://www.w3.org/2000/svg";
  const cap = cfg.capacity;
  const maxT = Math.max(...events.map((e) => e.start), 1);
  const mL = 46, mR = 20, mT = 14, mB = 34;
  const W = Math.max(host.clientWidth - 8, 680);
  const rowH = 150;
  const plotW = W - mL - mR;
  const plotH = rowH - mT - mB;
  const x = (t) => mL + (t / maxT) * plotW;
  const y = (lvl) => mT + plotH - (lvl / cap) * plotH;

  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", W);
  svg.setAttribute("height", rowH);
  svg.setAttribute("viewBox", `0 0 ${W} ${rowH}`);

  // capacity / zero grid lines
  for (const [lvl, label, color] of [[cap, `容量 ${cap}`, "#7ee0c8"], [0, "0", "#93a4c0"]]) {
    const ln = document.createElementNS(svgNS, "line");
    ln.setAttribute("x1", mL); ln.setAttribute("x2", W - mR);
    ln.setAttribute("y1", y(lvl)); ln.setAttribute("y2", y(lvl));
    ln.setAttribute("stroke", color); ln.setAttribute("stroke-dasharray", "4 3");
    ln.setAttribute("stroke-width", "1");
    svg.appendChild(ln);
    const t = document.createElementNS(svgNS, "text");
    t.setAttribute("x", 6); t.setAttribute("y", y(lvl) + 3);
    t.setAttribute("fill", color); t.setAttribute("font-size", "9");
    t.textContent = label;
    svg.appendChild(t);
  }

  const addText = (cx, cy, str, color, size = 9, anchor = "middle") => {
    const t = document.createElementNS(svgNS, "text");
    t.setAttribute("x", cx); t.setAttribute("y", cy);
    t.setAttribute("fill", color); t.setAttribute("font-size", size);
    t.setAttribute("text-anchor", anchor);
    t.textContent = str;
    svg.appendChild(t);
    return t;
  };

  // Initial charge marker at t=0.
  addText(x(0) - 4, y(cfg.initial) - 5, `初始 ${cfg.initial}`, "#93a4c0", 9, "end");

  let cursorLvl = cfg.initial;
  let cursorT = 0;
  events.forEach((ev) => {
    // recovery arc (level rises between distinct start times)
    if (ev.start > cursorT) {
      const ln = document.createElementNS(svgNS, "line");
      ln.setAttribute("x1", x(cursorT)); ln.setAttribute("y1", y(cursorLvl));
      ln.setAttribute("x2", x(ev.start)); ln.setAttribute("y2", y(ev.level_before));
      ln.setAttribute("stroke", "#4ade80"); ln.setAttribute("stroke-width", "2");
      svg.appendChild(ln);
      if (ev.recovered > 0)
        addText((x(cursorT) + x(ev.start)) / 2, y(ev.level_before) - 5,
                `+${ev.recovered}`, "#4ade80");
    }
    // deduction arrow (vertical at the start instant)
    const ax = x(ev.start);
    const arrow = document.createElementNS(svgNS, "line");
    arrow.setAttribute("x1", ax); arrow.setAttribute("x2", ax);
    arrow.setAttribute("y1", y(ev.level_before) - 2);
    arrow.setAttribute("y2", y(ev.level_after) + 2);
    arrow.setAttribute("stroke", "#4da3ff"); arrow.setAttribute("stroke-width", "2.5");
    arrow.setAttribute("marker-end", "url(#arrowBlue)");
    svg.appendChild(arrow);

    const dot = document.createElementNS(svgNS, "circle");
    dot.setAttribute("cx", ax); dot.setAttribute("cy", y(ev.level_after));
    dot.setAttribute("r", 3); dot.setAttribute("fill", "#4da3ff");
    const tip = document.createElementNS(svgNS, "title");
    tip.textContent =
      `${ev.exposure_id} @t=${ev.start}：恢复 ${ev.recovered}，` +
      `扣减前 ${ev.level_before}，耗量 ${ev.demand}，扣减后 ${ev.level_after}`;
    dot.appendChild(tip);
    svg.appendChild(dot);

    addText(ax, y(ev.level_after) + (ev.level_after === 0 ? 14 : -7),
            `${ev.exposure_id} −${ev.demand}`, "#e8eef8");
    addText(ax, rowH - 18, `t=${ev.start}`, "#93a4c0");
    cursorLvl = ev.level_after;
    cursorT = ev.start;
  });

  // arrowhead marker
  const defs = document.createElementNS(svgNS, "defs");
  const marker = document.createElementNS(svgNS, "marker");
  marker.setAttribute("id", "arrowBlue");
  marker.setAttribute("markerWidth", "8");
  marker.setAttribute("markerHeight", "8");
  marker.setAttribute("refX", "6");
  marker.setAttribute("refY", "3");
  marker.setAttribute("orient", "auto");
  marker.setAttribute("markerUnits", "strokeWidth");
  const path = document.createElementNS(svgNS, "path");
  path.setAttribute("d", "M0,0 L6,3 L0,6 z");
  path.setAttribute("fill", "#4da3ff");
  marker.appendChild(path);
  defs.appendChild(marker);
  svg.insertBefore(defs, svg.firstChild);

  host.appendChild(svg);
}

/* ---------------- SVG gantt ---------------- */

function drawGantt(r) {
  const host = $("#gantt");
  host.innerHTML = "";
  const exps = state.exposures;
  const eqNames = [...new Set(exps.map((e) => e.equipment))].sort();
  const byEq = new Map(eqNames.map((q) => [q, []]));
  exps.forEach((e, i) => {
    byEq.get(e.equipment).push({ e, i, start: r.starts[i], finish: r.finishes[i] });
  });

  const maxT = Math.max(
    ...exps.map((e, i) => r.finishes[i] + e.cooling),
    ...exps.map((e) => e.latest_start),
    1
  );

  const labelW = 80;
  const padR = 30;
  const axisH = 26;
  const rowH = 38;
  const availW = Math.max(host.clientWidth - 24, 640);
  const unit = Math.min(40, Math.max(3, (availW - labelW - padR) / (maxT + 1)));
  const chartW = Math.ceil(maxT * unit) + padR;
  const W = labelW + chartW;
  const Hpx = axisH + eqNames.length * rowH + 10;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", W);
  svg.setAttribute("height", Hpx);
  svg.setAttribute("viewBox", `0 0 ${W} ${Hpx}`);

  const x = (t) => labelW + t * unit;

  // axis + ticks
  const step = niceStep(maxT);
  for (let t = 0; t <= maxT; t += step) {
    const g = document.createElementNS(svgNS, "g");
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", x(t)); line.setAttribute("x2", x(t));
    line.setAttribute("y1", axisH - 8); line.setAttribute("y2", Hpx - 4);
    line.setAttribute("stroke", "#2b3a55"); line.setAttribute("stroke-width", "1");
    const txt = document.createElementNS(svgNS, "text");
    txt.setAttribute("x", x(t)); txt.setAttribute("y", 14);
    txt.setAttribute("fill", "#93a4c0"); txt.setAttribute("font-size", "10");
    txt.setAttribute("text-anchor", "middle");
    txt.textContent = String(t);
    g.appendChild(line); g.appendChild(txt);
    svg.appendChild(g);
  }

  eqNames.forEach((eq, ri) => {
    const top = axisH + ri * rowH;
    const lbl = document.createElementNS(svgNS, "text");
    lbl.setAttribute("x", 8); lbl.setAttribute("y", top + rowH / 2 + 4);
    lbl.setAttribute("fill", "#7ee0c8"); lbl.setAttribute("font-size", "12");
    lbl.setAttribute("font-weight", "600");
    lbl.textContent = eq;
    svg.appendChild(lbl);

    byEq.get(eq).forEach(({ e, i, start, finish }) => {
      // start-window band
      const win = document.createElementNS(svgNS, "rect");
      win.setAttribute("x", x(e.earliest_start));
      win.setAttribute("y", top + 14);
      win.setAttribute("width", Math.max((e.latest_start - e.earliest_start) * unit, 1));
      win.setAttribute("height", 12);
      win.setAttribute("fill", "#2b3a55");
      win.setAttribute("opacity", "0.45");
      win.setAttribute("rx", "2");
      svg.appendChild(win);

      // cooling block
      if (e.cooling > 0) {
        const c = document.createElementNS(svgNS, "rect");
        c.setAttribute("x", x(finish));
        c.setAttribute("y", top + 6);
        c.setAttribute("width", Math.max(e.cooling * unit - 1, 2));
        c.setAttribute("height", 26);
        c.setAttribute("fill", "#8a7dff");
        c.setAttribute("opacity", "0.85");
        c.setAttribute("rx", "3");
        const title = document.createElementNS(svgNS, "title");
        title.textContent = `${e.id} 冷却 ${e.cooling}（${finish} → ${finish + e.cooling}）`;
        c.appendChild(title);
        svg.appendChild(c);
      }

      // exposure block
      const b = document.createElementNS(svgNS, "rect");
      b.setAttribute("x", x(start));
      b.setAttribute("y", top + 6);
      b.setAttribute("width", Math.max(e.duration * unit - 1, 2));
      b.setAttribute("height", 26);
      b.setAttribute("fill", "#4da3ff");
      b.setAttribute("rx", "3");
      const t = document.createElementNS(svgNS, "title");
      t.textContent = `${e.id}：开始 ${start}，结束 ${finish}，设备 ${e.equipment}`;
      b.appendChild(t);
      svg.appendChild(b);

      const lab = document.createElementNS(svgNS, "text");
      lab.setAttribute("x", x(start) + 4);
      lab.setAttribute("y", top + 23);
      lab.setAttribute("fill", "#0b1626");
      lab.setAttribute("font-size", "11");
      lab.setAttribute("font-weight", "700");
      lab.textContent = e.id;
      svg.appendChild(lab);

      if (state.cooling.enabled) {
        const d = Number.isInteger(e.startup_demand) ? e.startup_demand : 0;
        const flake = document.createElementNS(svgNS, "text");
        flake.setAttribute("x", x(start));
        flake.setAttribute("y", top + 2);
        flake.setAttribute("fill", "#7ee0c8");
        flake.setAttribute("font-size", "10");
        flake.setAttribute("text-anchor", "middle");
        const ft = document.createElementNS(svgNS, "title");
        ft.textContent = `${e.id} 启动耗冷量 ${d}（见下方共享冷量账户）`;
        flake.appendChild(ft);
        flake.textContent = `❄${d}`;
        svg.appendChild(flake);
      }
    });
  });

  host.appendChild(svg);
}

function niceStep(maxT) {
  const target = Math.max(1, Math.round(maxT / 8));
  const pow = Math.pow(10, Math.floor(Math.log10(target)));
  for (const m of [1, 2, 5, 10]) {
    if (m * pow >= target) return m * pow;
  }
  return 10 * pow;
}

/* ---------------- misc ---------------- */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

let flashTimer = null;
function flashForm(msg, isError) {
  const el = $("#form-msg");
  el.textContent = msg;
  el.style.color = isError ? "var(--danger)" : "var(--ok)";
  clearTimeout(flashTimer);
  if (msg) flashTimer = setTimeout(() => { el.textContent = ""; }, 4000);
}

async function checkHealth() {
  const pill = $("#api-health");
  try {
    const resp = await fetch("/health", { cache: "no-store" });
    if (resp.ok) {
      pill.textContent = "● API 正常";
      pill.className = "health-pill ok";
    } else {
      throw new Error("bad status");
    }
  } catch (_) {
    pill.textContent = "● API 不可达";
    pill.className = "health-pill bad";
  }
}

/* ---------------- wiring ---------------- */

$("#add-exp").addEventListener("click", () => {
  if (state.exposures.length >= 10) {
    flashForm("最多 10 项曝光", true);
    return;
  }
  const n = state.exposures.length;
  state.exposures.push({
    id: String.fromCharCode(65 + n),
    duration: 1, earliest_start: 0, latest_start: 100,
    equipment: "X", cooling: 0, startup_demand: 0,
  });
  markDirty();
  renderAll();
});
$("#del-exp").addEventListener("click", () => {
  if (state.exposures.length <= 5) {
    flashForm("至少需要 5 项曝光", true);
    return;
  }
  state.exposures.pop();
  reconcileLinks();
  markDirty();
  renderAll();
});
$("#add-link").addEventListener("click", () => {
  const ids = state.exposures.map((e) => e.id);
  state.links.push({
    from_id: ids[0], to_id: ids[Math.min(1, ids.length - 1)],
    min_gap: 0, max_gap: "",
  });
  markDirty();
  renderAll();
});
$("#horizon").addEventListener("input", (ev) => {
  state.horizon = ev.target.value === "" ? 0 : Number(ev.target.value);
  markDirty();
});

/* ---------------- shared cooling wiring ---------------- */

function syncCoolingUi() {
  const on = state.cooling.enabled;
  $("#cool-enabled").checked = on;
  $("#cool-fields").classList.toggle("disabled-area", !on);
  $("#cool-capacity").value = state.cooling.capacity;
  $("#cool-initial").value = state.cooling.initial;
  $("#cool-recovery").value = state.cooling.recovery;
  $("#cool-capacity").disabled = !on;
  $("#cool-initial").disabled = !on;
  $("#cool-recovery").disabled = !on;
  renderExpRows();
}

$("#cool-enabled").addEventListener("change", (ev) => {
  state.cooling.enabled = ev.target.checked;
  syncCoolingUi();
  markDirty();
});
for (const [id, key] of [
  ["cool-capacity", "capacity"],
  ["cool-initial", "initial"],
  ["cool-recovery", "recovery"],
]) {
  $(`#${id}`).addEventListener("input", (ev) => {
    state.cooling[key] = ev.target.value === "" ? "" : Number(ev.target.value);
    markDirty();
  });
}

$("#solve-btn").addEventListener("click", solve);

renderAll();
syncCoolingUi();
checkHealth();
setInterval(checkHealth, 10000);
