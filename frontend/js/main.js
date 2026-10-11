/**
 * main.js - wires the dashboard together.
 *
 * Flow: read filters from the form -> ask the API -> draw every section.
 * Any filter change re-runs refresh(); each section loads independently so a
 * slow query never blocks the others.
 */

import { ApiError, esc, fmt, get } from "./api.js";
import { BLUE_RAMP, ORANGE_RAMP, barChart, heatmap, hbars, lineChart, rampLegend } from "./charts.js";
import { createZoneMap } from "./map.js";
import { initThemeToggle } from "./theme.js";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOLIDAYS = { "2019-01-01": "New Year", "2019-01-21": "MLK Day" };

const $ = (sel) => document.querySelector(sel);
const form = $("#filters");

const state = {
  meta: null,
  map: null,
  mapMetric: "trips",
  heatMetric: "trips_per_day",
  selectedZone: null,
  trips: { sort: "pickup_datetime", order: "asc", page: 1, perPage: 25 },
  cache: {},           // last data per section, for redraw on resize
};

/* ------------------------------------------------------------------------ */
/* Filters                                                                   */
/* ------------------------------------------------------------------------ */
function readFilters() {
  const data = new FormData(form);
  const f = {};
  for (const [k, v] of data.entries()) f[k] = typeof v === "string" ? v.trim() : v;
  f.airport_only = data.has("airport_only");
  // Friendly fix-ups instead of errors
  if (f.hour_min && f.hour_max && Number(f.hour_min) > Number(f.hour_max)) {
    [f.hour_min, f.hour_max] = [f.hour_max, f.hour_min];
  }
  if (f.start_date && f.end_date && f.start_date > f.end_date) {
    [f.start_date, f.end_date] = [f.end_date, f.start_date];
  }
  return f;
}

function populateFilters(meta) {
  const borough = $("#f-borough");
  for (const b of meta.boroughs) borough.add(new Option(b.name, b.borough_id));

  const pay = $("#f-payment");
  for (const p of meta.payment_types) pay.add(new Option(p.description, p.payment_type_id));

  const hmin = $("#f-hmin");
  const hmax = $("#f-hmax");
  hmin.add(new Option("from", ""));
  hmax.add(new Option("to", ""));
  for (let h = 0; h < 24; h++) {
    hmin.add(new Option(fmt.hour(h), h));
    hmax.add(new Option(fmt.hour(h), h));
  }

  for (const id of ["#f-start", "#f-end"]) {
    $(id).min = meta.date_range.first;
    $(id).max = meta.date_range.last;
  }
  fillZoneSelect("");
}

/** The zone list only shows zones in the chosen borough. */
function fillZoneSelect(boroughId) {
  const zone = $("#f-zone");
  const current = zone.value;
  zone.length = 1;
  for (const z of state.meta.zones) {
    if (boroughId && String(z.borough_id) !== String(boroughId)) continue;
    zone.add(new Option(boroughId ? z.zone_name : `${z.zone_name} (${z.borough})`, z.zone_id));
  }
  zone.value = [...zone.options].some((o) => o.value === current) ? current : "";
}

let debounce;
function scheduleRefresh() {
  clearTimeout(debounce);
  debounce = setTimeout(refresh, 250);
}

form.addEventListener("change", (e) => {
  if (e.target.id === "f-borough") fillZoneSelect(e.target.value);
  if (e.target.id === "f-zone") state.selectedZone = e.target.value ? Number(e.target.value) : null;
  state.trips.page = 1;
  scheduleRefresh();
});
form.addEventListener("submit", (e) => e.preventDefault());
form.addEventListener("reset", () => {
  setTimeout(() => {          // let the browser clear the inputs first
    fillZoneSelect("");
    state.selectedZone = null;
    state.trips.page = 1;
    refresh();
  });
});

$("#more-toggle").addEventListener("click", (e) => {
  const open = e.currentTarget.getAttribute("aria-expanded") !== "true";
  e.currentTarget.setAttribute("aria-expanded", String(open));
  $("#more-filters").hidden = !open;
});

/* ------------------------------------------------------------------------ */
/* Loading helper: busy state + friendly errors per section                  */
/* ------------------------------------------------------------------------ */
async function section(panelId, work) {
  const panel = document.getElementById(panelId);
  panel?.setAttribute("aria-busy", "true");
  try {
    await work();
  } catch (err) {
    if (err.name === "AbortError") return;           // superseded by a newer request
    console.error(err);
    const target = panel?.querySelector("[data-error-slot]") ?? panel;
    const note = document.createElement("p");
    note.className = "error-note";
    note.textContent = err instanceof ApiError ? err.message : "Something went wrong loading this section.";
    target?.querySelector(".error-note")?.remove();
    target?.appendChild(note);
  } finally {
    panel?.setAttribute("aria-busy", "false");
  }
}

/* ------------------------------------------------------------------------ */
/* Meter (headline numbers)                                                  */
/* ------------------------------------------------------------------------ */
async function loadMeter(f) {
  const meter = $(".meter");
  meter.classList.add("is-loading");
  try {
    const { data, ms } = await get("summary", f);
    $("#m-trips").textContent = fmt.int(data.trips);
    $("#m-fare").textContent = fmt.money(data.avg_fare);
    $("#m-speed").textContent = fmt.mph(data.avg_speed_mph);
    $("#m-fpm").textContent = fmt.money(data.fare_per_mile);
    $("#m-tip").textContent = fmt.pct(data.avg_tip_pct);
    const from = data.source === "trips" ? "the full 7.5M-row trip table" : "the pre-aggregated summary table";
    $("#m-source").textContent = `Answered in ${ms} ms from ${from}.`;
  } catch (err) {
    if (err.name !== "AbortError") $("#m-source").textContent = err.message;
  } finally {
    meter.classList.remove("is-loading");
  }
}

/* ------------------------------------------------------------------------ */
/* Map + zone detail                                                         */
/* ------------------------------------------------------------------------ */
function drawMap() {
  if (state.map && state.cache.zones) state.map.update(state.cache.zones, state.mapMetric);
}

async function loadMap(f) {
  await section("map-section", async () => {
    const { data } = await get("zone-stats", f);
    state.cache.zones = data;
    drawMap();
    state.map?.setSelected(state.selectedZone);
  });
}

$("#map-metric").addEventListener("change", (e) => {
  state.mapMetric = e.target.value;
  drawMap();
});
$("#zoom-in").addEventListener("click", () => state.map.zoomIn());
$("#zoom-out").addEventListener("click", () => state.map.zoomOut());
$("#zoom-reset").addEventListener("click", () => state.map.reset());

function selectZone(zoneId) {
  state.selectedZone = zoneId;
  state.map.setSelected(zoneId);
  loadZoneDetail(readFilters());
}

async function loadZoneDetail(f) {
  const panel = $("#zone-panel");
  const id = state.selectedZone;
  if (!id) {
    panel.innerHTML = `<p class="zone-empty">Pick a zone on the map, or choose one in the “Pickup zone” filter,
      to see its riders’ top destinations and typical fare.</p>`;
    return;
  }
  panel.innerHTML = `<p class="zone-empty">Loading ${state.map.escName(id)}…</p>`;
  try {
    const { data } = await get(`zones/${id}`, { ...f, zone_id: "", borough_id: "" }, {}, "zone-detail");
    const z = data.zone;
    const filtered = String(f.zone_id) === String(id);
    if (!data.trips) {
      panel.innerHTML = `<h3>${esc(z.zone_name)}</h3><p class="zone-meta">${esc(z.borough)}</p>
        <p class="zone-empty">No pickups here match the current filters.</p>`;
      return;
    }
    panel.innerHTML = `
      <h3>${esc(z.zone_name)}</h3>
      <p class="zone-meta">${esc(z.borough)}${z.service_zone ? ` · ${esc(z.service_zone)}` : ""}</p>
      <dl class="stat-list">
        <div><dt>Pickups</dt><dd>${fmt.int(data.trips)}</dd></div>
        <div><dt>Median fare</dt><dd>${fmt.money(data.median_fare)}</dd></div>
        <div><dt>Average fare</dt><dd>${fmt.money(data.avg_fare)}</dd></div>
        <div><dt>Average speed</dt><dd>${fmt.mph(data.avg_speed_mph)}</dd></div>
        <div><dt>Average distance</dt><dd>${fmt.miles(data.avg_distance_mi)}</dd></div>
        <div><dt>Card tip</dt><dd>${fmt.pct(data.avg_tip_pct)}</dd></div>
      </dl>
      <p class="sub-title" style="margin-top:0">Where riders go next</p>
      <div class="hbars" id="zone-dest"></div>
      <div class="zone-actions">
        ${filtered
          ? `<button type="button" class="btn-ghost" id="zone-clear">Show all zones again</button>`
          : `<button type="button" class="btn-solid" id="zone-filter">Show only rides from here</button>`}
      </div>`;
    hbars($("#zone-dest"), data.top_destinations.map((d) => ({
      label: d.zone, value: d.trips, display: `${d.share_pct}%`,
    })));
    $("#zone-filter")?.addEventListener("click", () => {
      $("#f-borough").value = "";
      fillZoneSelect("");
      $("#f-zone").value = String(id);
      state.trips.page = 1;
      refresh();
    });
    $("#zone-clear")?.addEventListener("click", () => {
      $("#f-zone").value = "";
      state.trips.page = 1;
      refresh();
    });
  } catch (err) {
    if (err.name !== "AbortError") panel.innerHTML = `<p class="error-note">${esc(err.message)}</p>`;
  }
}

/* ------------------------------------------------------------------------ */
/* Hourly speed & fare-per-mile (two charts, one shared crosshair)          */
/* ------------------------------------------------------------------------ */
function drawHourly() {
  const data = state.cache.hourly;
  if (!data) return;
  const tooltip = (d) => [fmt.hour(d.hour), [
    ["Speed", fmt.mph(d.avg_speed_mph)],
    ["Fare per mile", fmt.money(d.fare_per_mile)],
    ["Rides", fmt.int(d.trips)],
  ]];
  const common = { data, x: (d) => d.hour, xTickFormat: (h) => fmt.hourShort(h), xTickEvery: 3, height: 210, tooltip };
  let a, b;
  a = lineChart($("#chart-speed"), {
    ...common, y: (d) => d.avg_speed_mph, yFormat: (v) => v, zeroBased: true, color: "var(--series-1)",
    onHover: (i) => (i == null ? b.clear() : b.highlight(i)),
  });
  b = lineChart($("#chart-fpm"), {
    ...common, y: (d) => d.fare_per_mile, yFormat: (v) => `$${v}`, zeroBased: true, color: "var(--series-2)",
    onHover: (i) => (i == null ? a.clear() : a.highlight(i)),
  });
}

async function loadHourly(f) {
  await section("speed-section", async () => {
    const { data } = await get("by-hour", f);
    state.cache.hourly = data;
    drawHourly();
  });
}

/* ------------------------------------------------------------------------ */
/* Heatmap + daily bars                                                      */
/* ------------------------------------------------------------------------ */
function drawHeatmap() {
  const rows = state.cache.heat;
  if (!rows) return;
  const speed = state.heatMetric === "avg_speed_mph";
  const cells = rows.map((r) => ({ ...r, value: r[state.heatMetric] }));
  const ramp = speed ? ORANGE_RAMP : BLUE_RAMP;
  const { min, max } = heatmap($("#heatmap"), cells, {
    ramp,
    invert: speed,                     // darker = slower, so congestion stands out
    dayNames: DAY_NAMES,
    hourLabel: fmt.hourShort,
    tooltip: (c) => [`${c.day} ${fmt.hour(c.hour)}`, [
      ["Rides per day", fmt.int(c.trips_per_day)],
      ["Average speed", fmt.mph(c.avg_speed_mph)],
      ["Average fare", fmt.money(c.avg_fare)],
    ]],
  });
  rampLegend($("#heatmap-legend"), speed
    ? { title: "Average speed (darker = slower)", ramp, minLabel: `${max.toFixed(0)} mph`, maxLabel: `${min.toFixed(0)} mph` }
    : { title: "Rides on an average day", ramp, minLabel: fmt.int(min), maxLabel: fmt.int(max) });
}

function drawDaily() {
  const data = state.cache.daily;
  if (!data) return;
  const special = (d) => d.dow >= 5 || HOLIDAYS[d.date];
  barChart($("#chart-daily"), {
    data,
    x: (d) => d.date,
    y: (d) => d.trips,
    color: (d) => (special(d) ? "var(--series-2)" : "var(--series-1)"),
    height: 230,
    yFormat: fmt.compact,
    xTickFormat: (iso) => String(Number(iso.slice(8))),
    xTickEvery: data.length > 16 ? 2 : 1,
    annotate: (d) => HOLIDAYS[d.date] ?? null,
    tooltip: (d) => [fmt.date(d.date) + (HOLIDAYS[d.date] ? ` (${HOLIDAYS[d.date]})` : ""), [
      ["Rides", fmt.int(d.trips)],
      ["Average speed", fmt.mph(d.avg_speed_mph)],
      ["Average fare", fmt.money(d.avg_fare)],
    ]],
  });
  const legend = document.createElement("div");
  legend.className = "chart-legend";
  legend.innerHTML = `<span><i style="background:var(--series-1)"></i>Weekday</span>
                      <span><i style="background:var(--series-2)"></i>Weekend or public holiday</span>`;
  $("#chart-daily").appendChild(legend);
}

async function loadRhythm(f) {
  await section("rhythm-section", async () => {
    const [heat, daily] = await Promise.all([get("heatmap", f), get("by-day", f)]);
    state.cache.heat = heat.data;
    state.cache.daily = daily.data;
    drawHeatmap();
    drawDaily();
  });
}

document.querySelectorAll(".segmented button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".segmented button").forEach((b) => b.setAttribute("aria-checked", String(b === btn)));
    state.heatMetric = btn.dataset.metric;
    drawHeatmap();
  });
});

/* ------------------------------------------------------------------------ */
/* Top routes                                                                */
/* ------------------------------------------------------------------------ */
async function loadRoutes(f) {
  await section("routes-section", async () => {
    const { data } = await get("top-routes", f, { k: 10 });
    const tbody = $("#routes-table tbody");
    if (!data.routes.length) {
      tbody.innerHTML = `<tr><td colspan="8">No routes match these filters.</td></tr>`;
      return;
    }
    const max = data.routes[0].trips;
    tbody.innerHTML = data.routes.map((r) => `
      <tr>
        <td class="num">${r.rank}</td>
        <td class="trunc" title="${esc(r.pickup_zone)}">${esc(r.pickup_zone)}</td>
        <td class="trunc" title="${esc(r.dropoff_zone)}">${esc(r.dropoff_zone)}</td>
        <td class="num"><span class="rank-bar"><i style="width:${Math.round((r.trips / max) * 80)}px"></i>${fmt.int(r.trips)}</span></td>
        <td class="num">${fmt.miles(r.avg_distance_mi)}</td>
        <td class="num">${fmt.minutes(r.avg_duration_min)}</td>
        <td class="num">${fmt.mph(r.avg_speed_mph)}</td>
        <td class="num">${fmt.money(r.avg_fare)}</td>
      </tr>`).join("");
    $("#routes-sub").textContent =
      `The most travelled pickup → drop-off pairs, ranked from ${fmt.int(data.distinct_routes_ranked)} routes ` +
      `with a hand-written min-heap (top-K). Rides with an unknown zone are left out.`;
  });
}

/* ------------------------------------------------------------------------ */
/* Trip explorer                                                             */
/* ------------------------------------------------------------------------ */
async function loadTrips(f) {
  await section("trips-section", async () => {
    const t = state.trips;
    const { data } = await get("trips", f, { sort: t.sort, order: t.order, page: t.page, per_page: t.perPage });
    const tbody = $("#trips-table tbody");
    tbody.innerHTML = data.trips.length ? data.trips.map((r) => `
      <tr class="trip-row" tabindex="0" aria-expanded="false" data-id="${r.trip_id}">
        <td>${esc(fmt.datetime(r.pickup_datetime))}</td>
        <td class="trunc" title="${esc(r.pickup_zone)}">${esc(r.pickup_zone)}</td>
        <td class="trunc" title="${esc(r.dropoff_zone)}">${esc(r.dropoff_zone)}</td>
        <td class="num">${fmt.miles(r.trip_distance)}</td>
        <td class="num">${fmt.minutes(r.trip_duration_min)}</td>
        <td class="num">${fmt.mph(r.avg_speed_mph)}</td>
        <td class="num">${fmt.money(r.fare_amount)}</td>
        <td class="num">${r.tip_pct == null ? "–" : fmt.pct(r.tip_pct)}</td>
      </tr>
      <tr class="trip-detail" hidden><td colspan="8"><dl>
        <div><dt>Trip ID</dt><dd>${r.trip_id}</dd></div>
        <div><dt>Pickup</dt><dd>${esc(r.pickup_datetime)}</dd></div>
        <div><dt>Drop-off</dt><dd>${esc(r.dropoff_datetime)}</dd></div>
        <div><dt>Passengers</dt><dd>${r.passenger_count ?? "unknown"}</dd></div>
        <div><dt>Payment</dt><dd>${esc(r.payment_type)}</dd></div>
        <div><dt>Tip</dt><dd>${fmt.money(r.tip_amount)}</dd></div>
        <div><dt>Total charged</dt><dd>${fmt.money(r.total_amount)}</dd></div>
      </dl></td></tr>`).join("")
      : `<tr><td colspan="8">No rides match these filters.</td></tr>`;

    $("#page-label").textContent = `Page ${t.page}`;
    $("#prev-page").disabled = t.page === 1;
    $("#next-page").disabled = data.trips.length < t.perPage;
    document.querySelectorAll("#trips-table .sort").forEach((b) => {
      b.setAttribute("aria-sort", b.dataset.sort === t.sort ? (t.order === "asc" ? "ascending" : "descending") : "none");
    });
  });
}

$("#trips-table tbody").addEventListener("click", (e) => toggleTrip(e.target.closest(".trip-row")));
$("#trips-table tbody").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    toggleTrip(e.target.closest(".trip-row"));
  }
});
function toggleTrip(row) {
  if (!row) return;
  const open = row.getAttribute("aria-expanded") !== "true";
  row.setAttribute("aria-expanded", String(open));
  row.nextElementSibling.hidden = !open;
}

document.querySelectorAll("#trips-table .sort").forEach((btn) => {
  btn.addEventListener("click", () => {
    const t = state.trips;
    if (t.sort === btn.dataset.sort) t.order = t.order === "asc" ? "desc" : "asc";
    else { t.sort = btn.dataset.sort; t.order = "desc"; }
    t.page = 1;
    loadTrips(readFilters());
  });
});
$("#prev-page").addEventListener("click", () => { state.trips.page--; loadTrips(readFilters()); });
$("#next-page").addEventListener("click", () => { state.trips.page++; loadTrips(readFilters()); });

/* ------------------------------------------------------------------------ */
/* Data quality (loaded once; it describes the cleaning, not the filters)    */
/* ------------------------------------------------------------------------ */
const FLAG_LABELS = {
  dropoff_zone_unknown: "Drop-off zone unknown",
  pickup_zone_unknown: "Pickup zone unknown",
  passenger_count_0_set_unknown: "“0 passengers” set to unknown",
  undocumented_vendor_id: "Vendor ID not in the 2019 dictionary",
  total_not_equal_sum_of_parts: "Total ≠ sum of fare items",
  invalid_ratecode_set_unknown: "Invalid rate code set to unknown",
};

async function loadQuality() {
  await section("quality-section", async () => {
    const { data } = await get("data-quality");
    $("#quality-sub").textContent =
      `Of ${fmt.int(data.raw_rows)} raw records, ${fmt.int(data.excluded_rows)} (${data.excluded_pct}%) were physically ` +
      `or logically impossible and were removed. Every one is logged with its reason. Others were kept and repaired.`;
    hbars($("#quality-excluded"), data.exclusion_reasons.map((r) => ({
      label: r.description, value: r.records, display: fmt.int(r.records),
    })));
    hbars($("#quality-flagged"), Object.entries(data.repaired_or_flagged)
      .sort((a, b) => b[1] - a[1]).map(([k, v]) => ({
      label: FLAG_LABELS[k] ?? k, value: v, display: fmt.int(v),
    })));
  });
}

/* ------------------------------------------------------------------------ */
/* Refresh everything                                                        */
/* ------------------------------------------------------------------------ */
function refresh() {
  const f = readFilters();
  if (f.zone_id) state.selectedZone = Number(f.zone_id);
  state.map?.setSelected(state.selectedZone);
  loadMeter(f);
  loadMap(f);
  loadZoneDetail(f);
  loadHourly(f);
  loadRhythm(f);
  loadRoutes(f);
  loadTrips(f);
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { drawHourly(); drawDaily(); }, 150);
});

/* A hosted copy may run on a random sample; say so, since counts shrink with it. */
function showSampleNote(fraction) {
  if (!(fraction < 1)) return;
  const pct = `${+(fraction * 100).toFixed(2)}%`;
  $(".masthead-text").insertAdjacentHTML("beforeend",
    `<p class="sample-note">Showing a random ${esc(pct)} sample of the rides. Ride counts are
      about ${esc(pct)} of the real totals; averages, speeds and patterns are unaffected.</p>`);
}

async function init() {
  initThemeToggle($("#theme-toggle"));
  try {
    const [{ data: meta }, { data: geo }] = await Promise.all([get("meta"), get("zones/geojson")]);
    state.meta = meta;
    populateFilters(meta);
    showSampleNote(meta.sample_fraction);
    state.map = createZoneMap($("#map"), $("#map-legend"), geo, { onSelect: selectZone });
  } catch (err) {
    $("#main").insertAdjacentHTML("afterbegin",
      `<p class="error-note">${esc(err.message || "The dashboard could not start.")}</p>`);
    return;
  }
  refresh();
  loadQuality();
}

init();