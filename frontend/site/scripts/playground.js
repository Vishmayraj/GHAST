/* "Try to fool it": drag the reported position. Inside the dashed circle is plausible, outside is flagged.
   An illustration of the idea, not the real detector. */
(function () {
  var root = document.querySelector('[data-module="playground"]');
  if (!root) return;
  var svg = root.querySelector("svg"), out = root.querySelector(".play__out"), NS = "http://www.w3.org/2000/svg";
  var W = 520, H = 320, R = 52;
  var hist = [[40, 270], [110, 236], [180, 204], [250, 168]], last = hist[hist.length - 1];
  var pred = [last[0] + 70, last[1] - 34], pos = [pred[0] + 30, pred[1] + 22];

  function el(name, attrs, parent) {
    var e = document.createElementNS(NS, name);
    Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    (parent || svg).appendChild(e); return e;
  }
  el("rect", { width: W, height: H, fill: "#eef2f6" });
  for (var x = 0; x <= W; x += 65) el("line", { x1: x, y1: 0, x2: x, y2: H, stroke: "#0a1a2b", "stroke-opacity": 0.07 });
  for (var y = 0; y <= H; y += 64) el("line", { x1: 0, y1: y, x2: W, y2: y, stroke: "#0a1a2b", "stroke-opacity": 0.07 });
  el("polyline", { points: hist.map(function (p) { return p.join(","); }).join(" "), fill: "none", stroke: "#1f4e79", "stroke-width": 3 });
  hist.forEach(function (p) { el("circle", { cx: p[0], cy: p[1], r: 4.5, fill: "#1f4e79" }); });
  el("circle", { cx: pred[0], cy: pred[1], r: R, fill: "#5a6674", "fill-opacity": 0.08, stroke: "#5a6674", "stroke-width": 1.5, "stroke-dasharray": "6 5" });
  el("circle", { cx: pred[0], cy: pred[1], r: 4, fill: "none", stroke: "#5a6674", "stroke-width": 2 });
  var link = el("line", { x1: pred[0], y1: pred[1], x2: pos[0], y2: pos[1], stroke: "#5a6674", "stroke-dasharray": "3 4" });
  var ring = el("circle", { r: 9, fill: "#d8431f", "fill-opacity": 0.5, class: "ping", style: "display:none" });
  var dot = el("circle", { r: 9, fill: "#0a1a2b", tabindex: 0, role: "slider", "aria-label": "Reported position. Use the arrow keys to move it.", style: "cursor:grab;touch-action:none;outline-offset:6px" });

  function draw() {
    var d = Math.hypot(pos[0] - pred[0], pos[1] - pred[1]), flagged = d > R, ratio = d / R;
    dot.setAttribute("cx", pos[0]); dot.setAttribute("cy", pos[1]); ring.setAttribute("cx", pos[0]); ring.setAttribute("cy", pos[1]);
    link.setAttribute("x2", pos[0]); link.setAttribute("y2", pos[1]);
    dot.setAttribute("fill", flagged ? "#d8431f" : "#0a1a2b"); ring.style.display = flagged ? "" : "none";
    dot.setAttribute("aria-valuetext", flagged ? "Flagged" : "Plausible");
    out.textContent = flagged ? "Flagged. That is " + ratio.toFixed(1) + " times further from the prediction than this ship could manage."
      : "Plausible. This ship could really be there. Try somewhere further out.";
  }
  function place(px, py) { pos = [Math.max(10, Math.min(W - 10, px)), Math.max(10, Math.min(H - 10, py))]; draw(); }
  function toSvg(e) { var p = svg.createSVGPoint(); p.x = e.clientX; p.y = e.clientY; var q = p.matrixTransform(svg.getScreenCTM().inverse()); return [q.x, q.y]; }

  dot.addEventListener("pointerdown", function (e) { dot.setPointerCapture(e.pointerId); dot.style.cursor = "grabbing"; });
  dot.addEventListener("pointermove", function (e) { if (dot.hasPointerCapture(e.pointerId)) { var q = toSvg(e); place(q[0], q[1]); } });
  dot.addEventListener("pointerup", function () { dot.style.cursor = "grab"; });
  dot.addEventListener("keydown", function (e) {
    var k = { ArrowLeft: [-10, 0], ArrowRight: [10, 0], ArrowUp: [0, -10], ArrowDown: [0, 10] }[e.key];
    if (k) { e.preventDefault(); place(pos[0] + k[0], pos[1] + k[1]); }
  });
  draw();
})();
