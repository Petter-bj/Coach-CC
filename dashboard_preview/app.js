const toast = document.querySelector(".toast");
const navButtons = document.querySelectorAll("[data-view]");
const chatForm = document.querySelector("#chat-form");
const chatMessage = document.querySelector("#chat-message");
const chatButton = chatForm.querySelector("button");
const coachReply = document.querySelector("#coach-reply");
const coachAnswer = document.querySelector("[data-coach-answer]");
const reviewForm = document.querySelector("#review-form");
const reviewNote = document.querySelector("#review-note");
const reviewButton = reviewForm.querySelector("button");
const reviewCard = document.querySelector("#review-card");
const saturday = document.querySelector('[data-day="lørdag"]');
let toastTimer;
let currentToday;

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

function formatWorkoutDuration(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  return `${number.format(Math.round(seconds / 60))} min`;
}

function formatDistance(meters) {
  if (!Number.isFinite(meters)) return "—";
  return `${number.format(meters / 1000)} km`;
}

function reviewSourceLabel(source) {
  const labels = { garmin: "Garmin", concept2: "Concept2", strength: "Styrkelogg" };
  return labels[source] || "enheten din";
}

function renderReview(review) {
  if (!reviewCard || !review) return;
  const planned = review.planned_session || {};
  const actual = review.actual || {};
  const target = planned.target_metrics || {};
  const reviewDate = formatDate(planned.date || currentToday?.date || "").toLowerCase();

  reviewCard.hidden = false;
  reviewCard.classList.remove("reviewed");
  reviewForm.dataset.reviewId = String(review.id);
  setText("[data-review-title]", planned.description || sessionTitle(planned));
  setText("[data-review-source]", `${reviewDate} · registrert av ${reviewSourceLabel(actual.source)}`);
  setText("[data-review-duration]", formatWorkoutDuration(actual.duration_sec));
  setText(
    "[data-review-duration-plan]",
    Number.isFinite(target.duration_min) ? `plan: ${number.format(target.duration_min)} min` : "plan: —",
  );
  setText("[data-review-hr]", actual.avg_hr == null ? "—" : `${number.format(actual.avg_hr)} bpm`);
  setText("[data-review-hr-plan]", target.zone ? `plan: ${target.zone}` : "plan: —");
  setText("[data-review-distance]", formatDistance(actual.distance_m));
  setText("[data-review-distance-plan]", "plan: —");
  setText("[data-review-comment]", review.coach?.comment || "Økten er registrert og klar for vurdering.");
  reviewNote.value = "";
  updateReviewAction();
}

function updateReviewAction() {
  if (!reviewButton || reviewButton.disabled) return;
  reviewButton.textContent = reviewNote.value.trim()
    ? "Oppdater vurdering"
    : "Marker som vurdert";
}

function markReviewConfirmed() {
  // Når det store gule kortet kollapser, kan nettleserens scroll-anchoring
  // ellers flytte brukeren mange hundre piksler. Behold stedet de var på og
  // la bare selve innholdet i kortet endre seg.
  const scrollY = window.scrollY;
  const restoreScroll = () => window.scrollTo(0, scrollY);
  reviewCard.classList.add("reviewed");
  // Dobbelt animation frame + en kort timeout dekker både layout-skiftet og
  // nettleserens etterfølgende scroll-anchoring (særlig på mobil-Safari).
  window.requestAnimationFrame(() => window.requestAnimationFrame(restoreScroll));
  window.setTimeout(restoreScroll, 80);
}

function renderWeek(week, targetDate, reviews = []) {
  const summary = document.querySelector("[data-week-summary]");
  const daysElement = document.querySelector("[data-week-days]");
  if (!week || !summary || !daysElement) return;

  summary.replaceChildren();
  const completed = document.createElement("strong");
  completed.textContent = `${week.completed_sessions} av ${week.planned_sessions}`;
  summary.append(completed, " økter gjennomført");

  const pendingReviewDates = new Set(
    reviews.filter((review) => review.status === "pending").map((review) => review.planned_session?.date),
  );

  daysElement.replaceChildren();
  week.days.forEach((day) => {
    const sessions = day.sessions || [];
    const isToday = day.date === targetDate;
    const isDone = day.status === "completed";
    const isRest = day.status === "rest" || day.status === "skipped" || sessions.length === 0;
    const needsReview = pendingReviewDates.has(day.date);
    const item = document.createElement("div");
    item.className = `day${isToday ? " today" : needsReview ? " review" : isDone ? " done" : isRest ? " rest" : ""}`;
    const label = document.createElement("span");
    label.textContent = weekdays[day.weekday] || "";
    const state = document.createElement("i");
    state.textContent = isToday && sessions.length ? "↗" : needsReview ? "!" : isDone ? "✓" : "·";
    item.append(label, state);
    daysElement.append(item);
  });
}

function hydrateDashboard(payload) {
  currentToday = payload;
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

  renderWeek(payload.week, payload.date, payload.reviews);
  if (payload.reviews?.length) renderReview(payload.reviews[0]);
  else if (reviewCard) reviewCard.hidden = true;
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

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = chatMessage.value.trim();
  if (!question) {
    chatMessage.focus();
    return;
  }

  chatButton.disabled = true;
  chatButton.textContent = "Tenker …";
  try {
    const response = await fetch("/api/coach/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ message: question }),
    });
    if (!response.ok) throw new Error("coach chat failed");
    const payload = await response.json();
    if (!payload.answer) throw new Error("coach answer missing");

    coachAnswer.textContent = payload.answer;
    coachReply.hidden = false;
    chatMessage.value = "";
  } catch {
    showToast("Coachen svarte ikke. Prøv igjen om litt.");
  } finally {
    chatButton.disabled = false;
    chatButton.textContent = "Send";
  }
});

reviewForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const note = reviewNote.value.trim();
  const reviewId = reviewForm.dataset.reviewId;

  if (!reviewId) {
    // Det statiske previewet har ingen database. Behold interaksjonen der som
    // en ren visuell demonstrasjon av begge review-stegene.
    if (note) {
      setText(
        "[data-review-comment]",
        "Notatet ditt er tatt med i en oppdatert vurdering. Se gjennom vurderingen før du markerer økten som vurdert.",
      );
      reviewNote.value = "";
      updateReviewAction();
      showToast("Coachen har oppdatert vurderingen. Kortet venter fortsatt på bekreftelse.");
      return;
    }
    markReviewConfirmed();
    saturday?.classList.remove("review");
    saturday?.classList.add("done");
    saturday?.querySelector("i")?.replaceChildren("✓");
    showToast("Økten er markert som vurdert.");
    return;
  }

  const isReconsideration = Boolean(note);
  reviewButton.disabled = true;
  reviewButton.textContent = isReconsideration ? "Vurderer …" : "Lagrer …";
  try {
    const endpoint = isReconsideration ? "reconsider" : "confirm";
    const response = await fetch(`/api/reviews/${reviewId}/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ note: isReconsideration ? note : null }),
    });
    if (!response.ok) throw new Error("review request failed");
    const payload = await response.json();

    if (isReconsideration) {
      const updated = payload.review;
      setText("[data-review-comment]", updated?.coach_comment || "Vurderingen ble oppdatert.");
      reviewNote.value = "";
      if (currentToday?.reviews) {
        const review = currentToday.reviews.find((item) => item.id === Number(reviewId));
        if (review) {
          review.coach = { source: updated?.coach_source, comment: updated?.coach_comment };
          review.user_note = updated?.user_note;
        }
      }
      showToast("Coachen har oppdatert vurderingen. Se gjennom den før du bekrefter.");
      return;
    }

    markReviewConfirmed();
    currentToday.reviews = currentToday.reviews.filter((review) => review.id !== Number(reviewId));
    renderWeek(currentToday.week, currentToday.date, currentToday.reviews);
    showToast(note ? "Vurderingen ble lagret med notatet ditt." : "Økten er markert som vurdert.");
  } catch {
    showToast(isReconsideration ? "Coachen kunne ikke oppdatere vurderingen. Prøv igjen." : "Kunne ikke lagre vurderingen. Prøv igjen.");
  } finally {
    reviewButton.disabled = false;
    updateReviewAction();
  }
});

reviewNote.addEventListener("input", updateReviewAction);

loadToday();
