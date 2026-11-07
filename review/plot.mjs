const colors = ["#83f0c9", "#ff9a7a"];
const font = 'font-family="Consolas, monospace"';

export function scopeSvg(reports, selected = "both") {
  const width = 940;
  const height = 348;
  const left = 74;
  const right = 909;
  const top = 43;
  const bottom = 290;
  const x = (time) => left + time / 100 * (right - left);
  const y = (voltage) => (top + bottom) / 2 - voltage / 160 * (bottom - top);
  const vertical = Array.from({ length: 11 }, (_, index) => {
    const time = index * 10;
    return '<path d="M' + x(time) + ' ' + top + 'V' + bottom + '" stroke="#233541"/>' +
      (index % 2 ? "" : '<text x="' + x(time) + '" y="315" text-anchor="middle">' + time + "</text>");
  });
  const horizontal = Array.from({ length: 9 }, (_, index) => {
    const voltage = 80 - index * 20;
    const line = y(voltage);
    return '<path d="M' + left + ' ' + line + 'H' + right + '" stroke="' +
      (voltage ? "#233541" : "#526676") + '"/><text x="58" y="' + (line + 5) +
      '" text-anchor="end">' + voltage + "</text>";
  });
  const traces = reports.map((report, index) => {
    if (selected !== "both" && selected !== String(index)) return "";
    const points = report.points.filter(([time]) => time <= 100)
      .map(([time, voltage]) => x(time).toFixed(2) + "," + y(voltage).toFixed(2)).join(" ");
    return '<polyline points="' + points + '" fill="none" stroke="' + colors[index] +
      '" stroke-width="3.2" stroke-linejoin="round" stroke-linecap="round"/>';
  });
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + width + ' ' + height +
    '" role="img" aria-label="Scope ripple from the recorded samples on a common vertical scale">' +
    '<g ' + font + ' font-size="15" fill="#a6b8c2">' +
    '<rect x="' + left + '" y="' + y(25) + '" width="' + (right - left) +
    '" height="' + (y(-25) - y(25)) + '" fill="#17313c"/>' +
    vertical.join("") + horizontal.join("") + traces.join("") +
    '<text x="74" y="24">RIPPLE (mV)</text><text x="909" y="24" text-anchor="end">' +
    'SHADED BAND: ±25 mV</text><text x="909" y="340" text-anchor="end">TIME (µs)</text></g></svg>';
}

export function overviewSvg(reports) {
  const traces = scopeSvg(reports).replace("<svg ", '<svg x="36" y="144" width="928" height="343" ');
  return [
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 574" role="img" aria-label="Both boards read 4.958 volts. Board A releases at 39.6 millivolts peak-to-peak and Board B holds at 132.">',
    '<rect width="1000" height="574" rx="18" fill="#0b151d"/>',
    '<rect x="1" y="1" width="998" height="572" rx="18" fill="none" stroke="#29424f" stroke-width="2"/>',
    '<g fill="#91a8b6" font-family="Consolas,monospace" font-size="17">',
    '<text x="46" y="40">BLIND BENCH</text><text x="954" y="40" text-anchor="end">SWITCH × MATTERMOST / SIMULATED</text></g>',
    '<text x="46" y="104" fill="#edf6f4" font-family="Segoe UI,system-ui,sans-serif" font-weight="700" font-size="44">Both boards read ' +
      reports[0].dc.toFixed(3) + ' V.</text>',
    '<text x="46" y="134" fill="#a6b8c2" ' + font + ' font-size="17">Same 2 A load. Ripple limit: below 50 mVpp.</text>',
    traces,
    ...reports.map((report, index) => {
      const offset = index ? 514 : 46;
      return '<rect x="' + offset + '" y="502" width="440" height="43" rx="8" fill="#132630"/>' +
        '<text x="' + (offset + 17) + '" y="529" fill="' + colors[index] + '" ' + font +
        ' font-size="19">BOARD ' + ("AB"[index]) + ' · ' + report.ripple.toFixed(1).replace(/\.0$/, "") +
        ' mVpp · ' + report.decision.toUpperCase() + '</text>';
    }),
    "</svg>\n",
  ].join("\n");
}
