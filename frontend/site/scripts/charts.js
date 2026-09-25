/**
 * charts.js
 *
 * A deliberately small SVG line-chart renderer — this isn't a
 * general charting library, just enough to draw a deviation/score
 * series into a container from an array of numbers. Kept dependency
 * -free since the whole site has none.
 */

window.GHAST_CHARTS = {
  /**
   * Renders a line chart of `values` into `container` (a DOM node).
   * `thresholdIndex`, if given, marks the sample where the anomaly
   * crossed the flag threshold (used to call out the spike).
   */
  lineChart: function (container, values, options) {
    options = options || {};
    var width = options.width || 560;
    var height = options.height || 160;
    var pad = 12;
    var max = Math.max.apply(null, values) * 1.08;
    var min = 0;
    var n = values.length;

    function xAt(i) {
      return pad + (i / (n - 1)) * (width - pad * 2);
    }
    function yAt(v) {
      return height - pad - ((v - min) / (max - min)) * (height - pad * 2);
    }

    var points = values.map(function (v, i) {
      return xAt(i) + "," + yAt(v);
    }).join(" ");

    var areaPoints = points + " " + xAt(n - 1) + "," + (height - pad) + " " + xAt(0) + "," + (height - pad);

    var thresholdMarker = "";
    if (typeof options.thresholdIndex === "number") {
      var tx = xAt(options.thresholdIndex);
      var ty = yAt(values[options.thresholdIndex]);
      thresholdMarker =
        '<line x1="' + tx + '" y1="' + pad + '" x2="' + tx + '" y2="' + (height - pad) +
        '" stroke="var(--color-anomaly)" stroke-width="1" stroke-dasharray="3 3" opacity="0.5" />' +
        '<circle cx="' + tx + '" cy="' + ty + '" r="4" fill="var(--color-anomaly)" />';
    }

    container.innerHTML =
      '<svg viewBox="0 0 ' + width + ' ' + height + '" preserveAspectRatio="none" role="img" aria-label="' +
      (options.ariaLabel || "Deviation over time") + '">' +
        '<polygon points="' + areaPoints + '" fill="var(--ocean-100)" opacity="0.6"></polygon>' +
        '<polyline points="' + points + '" fill="none" stroke="var(--ocean-600)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></polyline>' +
        thresholdMarker +
      '</svg>';
  }
};
