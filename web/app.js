(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const GROUPS = {
    spacing: "Khoảng cách / padding",
    font: "Font chữ",
    color: "Màu sắc",
    size: "Kích thước",
  };
  const CATEGORY = {
    spacing: "Khoảng cách",
    padding: "Padding",
    margin: "Lề màn hình",
    alignment: "Căn chỉnh",
    "font-size": "Cỡ chữ",
    "font-weight": "Độ đậm",
    "font-family": "Font family",
    "text-color": "Màu chữ",
    background: "Màu nền",
    border: "Viền",
    size: "Kích thước",
  };

  const form = $("#form");
  const files = { design: null, dev: null };
  let designTab = "figma";
  let lastDrop = null;
  let result = null;
  let activeId = null;
  let filter = "all";

  // ---------------------------------------------------------------- inputs
  $$(".tab").forEach((tab) =>
    tab.addEventListener("click", () => {
      designTab = tab.dataset.tab;
      $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      $$(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== designTab));
    })
  );

  const tokenInput = form.elements.figma_token;
  const remember = $("#remember-token");
  try {
    const saved = localStorage.getItem("uicheck.figmaToken");
    if (saved) {
      tokenInput.value = saved;
      remember.checked = true;
    }
    const url = localStorage.getItem("uicheck.figmaUrl");
    if (url) form.elements.figma_url.value = url;
  } catch (_) { /* storage unavailable */ }

  function setFile(kind, file) {
    if (!file || !file.type.startsWith("image/")) return;
    files[kind] = file;
    const drop = $(`.drop[data-input="${kind}"]`);
    const img = $(".drop-preview", drop);
    img.src = URL.createObjectURL(file);
    img.classList.remove("hidden");
    $(".drop-empty", drop).classList.add("hidden");
  }

  $$(".drop").forEach((drop) => {
    const kind = drop.dataset.input;
    const input = $("input[type=file]", drop);
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
    });
    drop.addEventListener("focus", () => (lastDrop = kind));
    drop.addEventListener("mouseenter", () => (lastDrop = kind));
    input.addEventListener("change", () => setFile(kind, input.files[0]));
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      setFile(kind, e.dataTransfer.files[0]);
    });
  });

  document.addEventListener("paste", (e) => {
    const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
    if (!item) return;
    let kind = lastDrop;
    if (!kind) kind = !files.dev ? "dev" : "design";
    if (kind === "design" && designTab !== "upload") $('.tab[data-tab="upload"]').click();
    setFile(kind, item.getAsFile());
  });

  // ---------------------------------------------------------------- submit
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = $("#status");
    status.classList.remove("error");
    if (!files.dev) return fail("Hãy chọn screenshot bản dev.");
    const fd = new FormData();
    fd.append("dev", files.dev);
    if (designTab === "upload") {
      if (!files.design) return fail("Hãy chọn ảnh design.");
      fd.append("design", files.design);
    } else {
      const url = form.elements.figma_url.value.trim();
      if (!url) return fail("Hãy nhập link Figma.");
      fd.append("figma_url", url);
      fd.append("figma_token", tokenInput.value.trim());
      try {
        localStorage.setItem("uicheck.figmaUrl", url);
        if (remember.checked) localStorage.setItem("uicheck.figmaToken", tokenInput.value.trim());
        else localStorage.removeItem("uicheck.figmaToken");
      } catch (_) { /* ignore */ }
    }
    for (const name of ["design_scale", "dev_scale", "tol_spacing", "tol_font_size", "tol_color", "lang", "ignore_top", "ignore_bottom"]) {
      fd.append(name, form.elements[name].value);
    }
    fd.append("checks", $$("input[name=checks]:checked").map((c) => c.value).join(","));

    const btn = $("#run");
    btn.disabled = true;
    status.textContent = "Đang phân tích… (OCR + đo đạc, thường mất 3–10 giây)";
    try {
      const res = await fetch("/api/compare", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Lỗi server (${res.status})`);
      result = data;
      status.textContent = "";
      render();
      $("#results").scrollIntoView({ behavior: "smooth" });
    } catch (err) {
      fail(err.message);
    } finally {
      btn.disabled = false;
    }

    function fail(msg) {
      status.textContent = msg;
      status.classList.add("error");
    }
  });

  // ---------------------------------------------------------------- results
  function render() {
    activeId = null;
    filter = "all";
    $("#results").classList.remove("hidden");
    renderSummary();
    $("#warnings").innerHTML = (result.warnings || []).map((w) => `<div class="warning">${esc(w)}</div>`).join("");
    $("#img-design").src = result.design.image;
    $("#img-dev").src = result.dev.image;
    $("#img-ov-design").src = result.design.image;
    $("#img-ov-dev").src = result.dev.image;
    $("#design-meta").textContent = `${result.design.width}px · @${result.design.scale}x${result.figma ? " · Figma: " + (result.figma.name || "") : ""}`;
    $("#dev-meta").textContent = `${result.dev.width}px · @${result.dev.scale}x`;
    setupOverlay();
    renderFilters();
    renderIssues();
    renderMarks();
  }

  function renderSummary() {
    const s = result.summary;
    const ok = s.total === 0;
    $("#summary").innerHTML =
      `<div class="stat total ${ok ? "ok" : ""}"><b>${s.total}</b><span>${ok ? "Không phát hiện khác biệt" : `vấn đề · ${s.major} nghiêm trọng`}</span></div>` +
      Object.entries(GROUPS)
        .map(([g, name]) => `<div class="stat" data-group="${g}"><b>${s.by_group[g] || 0}</b><span>${name}</span></div>`)
        .join("");
  }

  function renderFilters() {
    const counts = result.summary.by_group;
    const chips = [["all", `Tất cả (${result.summary.total})`], ["major", `Nghiêm trọng (${result.summary.major})`]]
      .concat(Object.entries(GROUPS).filter(([g]) => counts[g]).map(([g, n]) => [g, `${n} (${counts[g]})`]));
    $("#filters").innerHTML = chips
      .map(([k, label]) => `<button type="button" class="chip ${k === filter ? "active" : ""}" data-filter="${k}">${esc(label)}</button>`)
      .join("");
    $$("#filters .chip").forEach((c) =>
      c.addEventListener("click", () => {
        filter = c.dataset.filter;
        renderFilters();
        renderIssues();
        renderMarks();
      })
    );
  }

  function visibleIssues() {
    return result.issues.filter((i) => filter === "all" || (filter === "major" ? i.severity === "major" : i.group === filter));
  }

  function swatch(v) {
    const m = /#[0-9A-Fa-f]{6}/.exec(v || "");
    return m ? `<span class="swatch" style="background:${m[0]}"></span>` : "";
  }

  function renderIssues() {
    const list = $("#issue-list");
    const items = visibleIssues();
    if (!result.issues.length) {
      list.innerHTML = `<li class="empty">🎉 Bản code khớp với design trong ngưỡng sai số đã chọn.<br><small>${result.summary.matched} phần tử đã được đối chiếu.</small></li>`;
      return;
    }
    list.innerHTML = items
      .map(
        (i) => `<li class="issue ${i.id === activeId ? "active" : ""}" data-group="${i.group}" data-id="${i.id}">
          <div class="issue-head">
            <span class="issue-num">${i.id}</span>
            <span class="issue-label">${esc(i.label)}</span>
            <span class="sev ${i.severity}">${i.severity === "major" ? "Nghiêm trọng" : "Nhẹ"}</span>
          </div>
          <dl class="issue-vals">
            <dt>Loại</dt><dd>${esc(CATEGORY[i.category] || i.category)}</dd>
            <dt>Design</dt><dd>${swatch(i.expected)}${esc(i.expected)}</dd>
            <dt>Bản code</dt><dd>${swatch(i.actual)}${esc(i.actual)}</dd>
            <dt>Chênh lệch</dt><dd>${esc(i.delta)}</dd>
          </dl>
          <div class="issue-msg">${esc(i.message)}</div>
        </li>`
      )
      .join("");
    $$(".issue", list).forEach((li) => li.addEventListener("click", () => select(Number(li.dataset.id), true)));
  }

  function select(id, scroll) {
    activeId = activeId === id ? null : id;
    $$(".issue").forEach((li) => li.classList.toggle("active", Number(li.dataset.id) === activeId));
    renderMarks();
    if (activeId == null) return;
    const issue = result.issues.find((i) => i.id === activeId);
    if (scroll) {
      scrollToMark("design", issue.design_marks[0]);
    } else {
      $(`.issue[data-id="${id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function scrollToMark(side, mark) {
    if (!mark) return;
    const viewer = $("#viewer");
    const img = $(side === "design" ? "#img-design" : "#img-dev");
    const [, y] = mark.rect;
    const size = result[side].size;
    const top = img.offsetTop + (y / size[1]) * img.clientHeight;
    viewer.scrollTo({ top: Math.max(0, top - viewer.clientHeight / 3), behavior: "smooth" });
  }

  function renderMarks() {
    for (const side of ["design", "dev"]) {
      const stage = $(`#pane-${side} .stage`);
      const svg = $("svg", stage);
      const badges = $(".badges", stage);
      const [W, H] = result[side].size;
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      svg.setAttribute("preserveAspectRatio", "none");
      const key = side === "design" ? "design_marks" : "dev_marks";
      const issues = visibleIssues();
      let out = "";
      if ($("#show-elements").checked) {
        for (const el of result.elements[side]) {
          const [x, y, w, h] = el.rect;
          out += `<rect class="det" x="${x}" y="${y}" width="${w}" height="${h}"><title>${esc(el.kind + " " + el.id)}</title></rect>`;
        }
      }
      let badgeHtml = "";
      for (const i of issues) {
        const marks = i[key];
        if (!marks.length) continue;
        const cls = activeId == null ? "" : i.id === activeId ? "active" : "dim";
        const color = `var(--${i.group})`;
        out += `<g class="${cls}">` +
          marks.map((m) => {
            const [x, y, w, h] = m.rect;
            return `<rect class="${m.type}" x="${x}" y="${y}" width="${Math.max(w, 1)}" height="${Math.max(h, 1)}" style="${m.type === "gap" ? "fill" : "stroke"}:${color}"></rect>`;
          }).join("") + "</g>";
        // Badge in the middle of the measured gap, or on the top-right corner of the element.
        const gap = marks.find((m) => m.type === "gap");
        const [x, y, w, h] = (gap || marks[0]).rect;
        const left = ((gap ? x + w / 2 : x + w) / W) * 100;
        const top = ((gap ? y + h / 2 : y) / H) * 100;
        badgeHtml += `<span class="badge ${cls}" data-group="${i.group}" data-id="${i.id}" style="left:${left}%;top:${top}%" title="${esc(i.label)}">${i.id}</span>`;
      }
      svg.innerHTML = out;
      badges.innerHTML = badgeHtml;
      $$(".badge", badges).forEach((b) => b.addEventListener("click", () => select(Number(b.dataset.id), false)));
    }
  }

  $("#show-elements").addEventListener("change", () => result && renderMarks());

  // ---------------------------------------------------------------- overlay
  function setupOverlay() {
    const ov = $("#img-ov-dev");
    const [W] = result.design.size;
    const [w] = result.dev.size;
    // Dev image drawn in design pixel space, shifted so matched elements line up.
    ov.style.width = `${(w / W) * 100}%`;
    ov.style.transform = `translate(${(-result.alignment.dx / w) * 100}%, ${(-result.alignment.dy / result.dev.size[1]) * 100}%)`;
    ov.style.opacity = $("#opacity").value / 100;
  }

  $("#opacity").addEventListener("input", (e) => ($("#img-ov-dev").style.opacity = e.target.value / 100));
  $("#difference").addEventListener("change", (e) => $(".overlay-stage").classList.toggle("difference", e.target.checked));

  $$("#view-mode button").forEach((b) =>
    b.addEventListener("click", () => {
      const overlay = b.dataset.mode === "overlay";
      $$("#view-mode button").forEach((x) => x.classList.toggle("active", x === b));
      $("#viewer").classList.toggle("overlay", overlay);
      $("#pane-design").classList.toggle("hidden", overlay);
      $("#pane-dev").classList.toggle("hidden", overlay);
      $("#pane-overlay").classList.toggle("hidden", !overlay);
      $$(".overlay-ctl").forEach((c) => c.classList.toggle("hidden", !overlay));
    })
  );

  $("#export").addEventListener("click", () => {
    if (!result) return;
    const copy = { ...result, design: { ...result.design, image: undefined }, dev: { ...result.dev, image: undefined } };
    const blob = new Blob([JSON.stringify(copy, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "ui-check-report.json";
    a.click();
  });

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }
})();
