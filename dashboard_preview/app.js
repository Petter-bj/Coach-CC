const toast = document.querySelector(".toast");
const navButtons = document.querySelectorAll("[data-view]");
const chatForm = document.querySelector("#chat-form");
const chatMessage = document.querySelector("#chat-message");
const reviewForm = document.querySelector("#review-form");
const reviewCard = document.querySelector("#review-card");
const saturday = document.querySelector('[data-day="lørdag"]');
let toastTimer;

const number = new Intl.NumberFormat("nb-NO", { maximumFractionDigits: 1 });
const weekdays = ["MAN", "TIR", "ONS", "TOR", "FRE", "LØR", "SØN"];

function showToast(message) {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add("show");
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 3200);
}

function setActiveView(button) {
  const view = button.dataset.view;
  document.querySelectorAll(".nav-item, .mobile-nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view);
  });
}

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element && value !== null && value !== undefined) element.textContent = value;
}

function setMetricValue(selector, value, unit = "") {
  const element = document.querySelector(selector);
  if (!element) return;
  element.replaceChildren();
  if (value === null || value === undefined) {
    element.textContent = "—";
    return;
  }
  element.append(document.createTextNode(String(value)));
  if (unit) {
    const unitElement = document.createElement("span");
    unitElement.textContent = unit;
    element.append(unitElement);
  }
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return null;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return `${hours}:${String(minutes).padStart(2, "0")}`;
}

function formatSigned(value, unit, { lowIsGood = false } = {}) {
  if (value === null || value === undefined) return { text: "—", good: false };
  const direction = value > 0 ? "↑" : value < 0 ? "↓" : "→";
  const good = value === 0 || (lowIsGood ? value < 0 : value > 0);
  return {
    text: `${direction} ${number.format(Math.abs(value))}${unit ? ` ${unit}` : ""}`,
    good,
  };
}

function setDelta(selector, value, unit, options) {
  const element = document.querySelector(selector);
  if (!element) return;
  const delta = formatSigned(value, unit, options);
  element.textContent = delta.text;
  element.classList.toggle("positive", delta.good);
  element.classList.toggle("caution", !delta.good);
}

function formatDate(dateString) {
  const date = new Date(`${dateString}T12:00:00`);
  return new Intl.DateTimeFormat("nb-NO", {
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(date).toUpperCase();
}

function formatSync(timestamp) {
  if (!timestamp) return "Garmin er ikke synkronisert ennå";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "Oppdatert fra Garmin";
  const time = new Intl.DateTimeFormat("nb-NO", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Europe/Oslo",
  }).format(date);
  return `Oppdatert fra Garmin ${time}`;
}

function sessionTitle(session) {
  const labels = {
    threshold_run: "Terskeløkt",
    easy_run: "Rolig løpetur",
    long_run: "Langtur",
    intervals: "Intervalløkt",
    strength: "Styrkeøkt",
    rest: "Hvile",
  };
  return labels[session.type] || "Dagens økt";
}

function sessionIntensity(session) {
  const labels = {
    threshold_run: "Moderat+",
    easy_run: "Rolig",
    long_run: "Rolig",
    intervals: "Hard",
    strength: "Moderat",
    rest: "Hvile",
  };
  return labels[session.type] || "Planlagt";
}

function recommendationTitle(kind, session) {
  const plan = session ? "Følg planen som planlagt." : "Hold deg til en rolig, normal dag.";
  const titles = {
    normal: `Kroppen din er klar — ${plan}`,
    light: "Hold dagens økt lettere enn planlagt.",
    easy: "La dagens økt være rolig og enkel.",
    rest: "Kroppen trenger hvile i dag.",
  };
  return titles[kind] || "Se dagens signaler før du trener.";
}

function renderWeek(week, targetDate) {
  const summary = document.querySelector("[data-week-summary]");
  const daysElement = document.querySelector("[data-week-days]");
  if (!week || !summary || !daysElement) return;

  summary.replaceChildren();
  const completed = document.createElement("strong");
  completed.textContent = `${week.completed_sessions} av ${week.planned_sessions}`;
  summary.append(completed, " økter gjennomført");

  daysElement.replaceChildren();
  week.days.forEach((day) => {
    const sessions = day.sessions || [];
    const isToday = day.date === targetDate;
    const isDone = day.status === "completed";
    const isRest = day.status === "rest" || day.status === "skipped" || sessions.length === 0;
    const item = document.createElement("div");
    item.className = `day${isToday ? " today" : isDone ? " done" : isRest ? " rest" : ""}`;
    const label = document.createElement("span");
    label.textContent = weekdays[day.weekday] || "";
    const state = document.createElement("i");
    state.textContent = isToday && sessions.length ? "↗" : isDone ? "✓" : "·";
    item.append(label, state);
    daysElement.append(item);
  });
}

function hydrateDashboard(payload) {
  const metrics = payload.metrics || {};
  const session = (payload.planned_sessions || []).find((item) => item.type !== "rest");
  const readiness = metrics.readiness || {};
  const sleep = metrics.sleep || {};
  const hrv = metrics.hrv || {};
  const restingHr = metrics.resting_hr || {};

  setText("[data-recommendation-title]", recommendationTitle(payload.recommendation?.kind, session));
  setText(
    "[data-recommendation-rationale]",
    payload.recommendation?.rationale?.join(" ") || "Ingen detaljert anbefaling er tilgjengelig ennå.",
  );
  setText(
    '[data-signal="readiness"]',
    readiness.value === null || readiness.value === undefined
      ? "—"
      : readiness.delta === null || readiness.delta === undefined
        ? number.format(readiness.value)
        : `${number.format(readiness.value)} (${readiness.delta >= 0 ? "+" : ""}${number.format(readiness.delta)})`,
  );
  setText(
    '[data-signal="hrv"]',
    hrv.value === null || hrv.value === undefined
      ? "—"
      : hrv.delta === null || hrv.delta === undefined
        ? `${number.format(hrv.value)} ms`
        : `${number.format(hrv.value)} ms (${hrv.delta >= 0 ? "+" : ""}${number.format(hrv.delta)})`,
  );
  const sleepDuration = formatDuration(sleep.duration_sec);
  setText(
    '[data-signal="sleep"]',
    sleepDuration && sleep.value !== null && sleep.value !== undefined
      ? `${sleepDuration.replace(":", " t ")} m · ${number.format(sleep.value)} %`
      : "—",
  );

  if (session) {
    setText("[data-workout-date]", `DAGENS ØKT · ${formatDate(payload.date)}`);
    setText("[data-workout-title]", sessionTitle(session));
    setText("[data-workout-description]", session.description || "Ingen detaljer registrert.");
    const duration = session.target_metrics?.duration_min;
    setText("[data-workout-duration]", duration ? `${number.format(duration)} min` : "—");
    setText("[data-workout-intensity]", sessionIntensity(session));
  } else {
    document.querySelector("[data-workout-card]")?.setAttribute("hidden", "");
  }

  setText("[data-sync-status]", formatSync(payload.sources?.garmin?.last_synced_at));
  setMetricValue("[data-metric-readiness]", readiness.value == null ? null : number.format(readiness.value), "/100");
  setText(
    "[data-metric-readiness-foot]",
    readiness.delta === null || readiness.delta === undefined
      ? "Ingen baseline ennå"
      : `${readiness.delta >= 0 ? "+" : ""}${number.format(readiness.delta)} over din baseline`,
  );
  const readinessTrack = document.querySelector("[data-readiness-track]");
  if (readinessTrack && Number.isFinite(readiness.value)) {
    readinessTrack.style.width = `${Math.min(100, Math.max(0, readiness.value))}%`;
  }

  setMetricValue("[data-metric-sleep]", sleepDuration, "t");
  setDelta("[data-metric-sleep-delta]", sleep.delta, "poeng");
  setText(
    "[data-metric-sleep-foot]",
    sleep.value === null || sleep.value === undefined ? "Ingen søvnscore ennå" : `${number.format(sleep.value)} % søvnscore`,
  );
  setMetricValue("[data-metric-hrv]", hrv.value == null ? null : number.format(hrv.value), "ms");
  setDelta("[data-metric-hrv-delta]", hrv.delta, "ms");
  setText("[data-metric-hrv-foot]", hrv.baseline === null || hrv.baseline === undefined ? "Ingen baseline ennå" : `Baseline ${number.format(hrv.baseline)} ms`);
  setMetricValue("[data-metric-rhr]", restingHr.value == null ? null : number.format(restingHr.value), "bpm");
  setDelta("[data-metric-rhr-delta]", restingHr.delta, "bpm", { lowIsGood: true });
  setText("[data-metric-rhr-foot]", restingHr.baseline === null || restingHr.baseline === undefined ? "Ingen baseline ennå" : `Baseline ${number.format(restingHr.baseline)} bpm`);

  renderWeek(payload.week, payload.date);
  if (!payload.reviews?.length && reviewCard) reviewCard.hidden = true;
}

async function loadToday() {
  try {
    const response = await fetch("/api/today", { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    hydrateDashboard(await response.json());
  } catch {
    // Local preview has no API. It deliberately continues with representative data.
  }
}

navButtons.forEach((button) => {
  button.addEventListener("click", () => {
    if (button.matches(".nav-item, .mobile-nav-item")) setActiveView(button);
    const destination = button.dataset.view;
    const messages = {
      "Vis detaljer": "Øktens detaljer kobles på i neste dashboard-steg.",
      Synkronisering: "Garmin-data oppdateres automatisk på VPS-en.",
      "Hele planen": "Uke-siden bygges oppå den samme datakontrakten.",
    };
    if (messages[destination]) showToast(messages[destination]);
  });
});

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = chatMessage.value.trim();
  if (!question) {
    chatMessage.focus();
    return;
  }
  showToast("Chat kobles på når Kimi-harnesset bygges. Spørsmålet ditt ble ikke sendt.");
});

reviewForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const note = document.querySelector("#review-note").value.trim();
  reviewCard.classList.add("reviewed");
  saturday.classList.remove("review");
  saturday.classList.add("done");
  saturday.querySelector("i").textContent = "✓";
  showToast(note ? "Vurderingen ble lagret med notatet ditt." : "Økten er markert som vurdert.");
});

loadToday();
