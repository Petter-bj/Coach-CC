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
const todayPage = document.querySelector('[data-page="today"]');
const weekPage = document.querySelector('[data-page="week"]');
const blockPage = document.querySelector('[data-page="block"]');
const weekCalendar = document.querySelector("[data-week-calendar]");
const dayLogCard = document.querySelector("[data-day-log]");
const weekChatForm = document.querySelector("#week-chat-form");
const weekChatMessage = document.querySelector("#week-chat-message");
const weekChatButton = weekChatForm?.querySelector("button");
const weekCoachReply = document.querySelector("[data-week-coach-reply]");
const weekCoachAnswer = document.querySelector("[data-week-coach-answer]");
const weekProposal = document.querySelector("[data-week-proposal]");
const weekProposalOperations = document.querySelector("[data-week-proposal-operations]");
const blockChatForm = document.querySelector("#block-chat-form");
const blockChatMessage = document.querySelector("#block-chat-message");
const blockChatButton = blockChatForm?.querySelector("button");
const blockCoachReply = document.querySelector("[data-block-coach-reply]");
const blockCoachAnswer = document.querySelector("[data-block-coach-answer]");
const blockConversation = document.querySelector("[data-block-conversation]");
const blockProposal = document.querySelector("[data-block-proposal]");
const blockProposalWeeks = document.querySelector("[data-block-proposal-weeks]");
const workoutDetailModal = document.querySelector("[data-workout-detail-modal]");
const workoutDetailStats = document.querySelector("[data-workout-detail-stats]");
const workoutDetailBody = document.querySelector("[data-workout-detail-body]");
let toastTimer;
let currentToday;
let displayedWeekStart;
let currentWeekProposal;
let currentBlockProposal;
const coachHistory = [];
const weekCoachHistory = [];
const blockCoachHistory = [];
let blockConversationExpanded = false;

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

function isoDate(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function dateFromIso(dateString) {
  return new Date(`${dateString}T12:00:00`);
}

function addDays(dateString, amount) {
  const value = dateFromIso(dateString);
  value.setDate(value.getDate() + amount);
  return isoDate(value);
}

function isoWeekNumber(dateString) {
  const value = dateFromIso(dateString);
  const utc = new Date(Date.UTC(value.getFullYear(), value.getMonth(), value.getDate()));
  utc.setUTCDate(utc.getUTCDate() + 4 - (utc.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
  return Math.ceil((((utc - yearStart) / 86400000) + 1) / 7);
}

function formatWeekRange(start, end) {
  const first = dateFromIso(start);
  const last = dateFromIso(end);
  const formatter = new Intl.DateTimeFormat("nb-NO", { day: "numeric", month: "long" });
  return `${formatter.format(first)} – ${formatter.format(last)}`;
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

function workoutTitle(type) {
  const labels = {
    running: "Løp",
    treadmill_running: "Mølleløp",
    cycling: "Sykkel",
    rowing: "Roing",
    strength: "Styrke",
    strength_training: "Styrkeøkt",
    swimming: "Svømming",
  };
  return labels[type] || "Treningsøkt";
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

function formatPace(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")} /km`;
}

function reviewSourceLabel(source) {
  const labels = { garmin: "Garmin", concept2: "Concept2", hevy: "Hevy", strength: "Styrkelogg" };
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

function renderRecentWorkouts(workouts = []) {
  const card = document.querySelector("[data-recent-workouts-card]");
  const list = document.querySelector("[data-recent-workouts-list]");
  if (!card || !list) return;
  if (!workouts.length) {
    card.hidden = true;
    return;
  }

  card.hidden = false;
  list.replaceChildren();
  for (const workout of workouts) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "recent-workout-row";
    row.dataset.workoutId = workout.id;

    const day = document.createElement("time");
    day.textContent = formatDate(workout.local_date);
    const title = document.createElement("strong");
    title.textContent = workoutTitle(workout.type);
    const details = document.createElement("span");
    const detailParts = [formatWorkoutDuration(workout.duration_sec)];
    if (workout.source === "hevy") detailParts.unshift("Hevy");
    const distance = formatDistance(workout.distance_m);
    if (distance !== "—") detailParts.push(distance);
    details.textContent = detailParts.join(" · ");
    const heartRate = document.createElement("small");
    heartRate.textContent = workout.avg_hr == null ? "—" : `${number.format(workout.avg_hr)} bpm`;

    row.append(day, title, details, heartRate);
    list.append(row);
  }
}

function formatHrTarget(value) {
  if (!Array.isArray(value) || value.length !== 2) return null;
  if (!Number.isFinite(value[0]) || !Number.isFinite(value[1])) return null;
  return `${number.format(value[0])}–${number.format(value[1])} bpm`;
}

function detailSection(title, { text, rows = [], tone = "" } = {}) {
  const section = document.createElement("section");
  section.className = `workout-detail-section${tone ? ` ${tone}` : ""}`;
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading);
  if (text) {
    const copy = document.createElement("p");
    copy.textContent = text;
    section.append(copy);
  }
  if (rows.length) {
    const list = document.createElement("div");
    list.className = "workout-detail-list";
    rows.forEach(({ label, value }) => {
      if (!value || value === "—") return;
      const item = document.createElement("div");
      const labelElement = document.createElement("span");
      labelElement.textContent = label;
      const valueElement = document.createElement("strong");
      valueElement.textContent = value;
      item.append(labelElement, valueElement);
      list.append(item);
    });
    if (list.childElementCount) section.append(list);
  }
  return section;
}

function strengthSetValue(strengthSet) {
  const reps = Number.isFinite(strengthSet.reps) ? `${number.format(strengthSet.reps)} reps` : "—";
  if (strengthSet.weight_kg == null) return `${reps} · kroppsvekt`;
  return `${reps} × ${number.format(strengthSet.weight_kg)} kg`;
}

function strengthExerciseSection(exercises = []) {
  const section = document.createElement("section");
  section.className = "workout-detail-section strength-exercise-section";
  const heading = document.createElement("h3");
  heading.textContent = "ØVELSER OG SETT";
  section.append(heading);

  const list = document.createElement("div");
  list.className = "strength-exercise-list";
  exercises.forEach((exercise) => {
    if (!exercise?.exercise || !Array.isArray(exercise.sets) || !exercise.sets.length) return;
    const group = document.createElement("article");
    group.className = "strength-exercise";
    const title = document.createElement("h4");
    title.textContent = exercise.exercise;
    group.append(title);

    const sets = document.createElement("div");
    sets.className = "strength-set-list";
    exercise.sets.forEach((strengthSet) => {
      const row = document.createElement("div");
      const numberLabel = document.createElement("span");
      numberLabel.textContent = `SETT ${strengthSet.set_num}`;
      const value = document.createElement("strong");
      value.textContent = strengthSetValue(strengthSet);
      const details = [
        strengthSet.rpe == null ? null : `RPE ${number.format(strengthSet.rpe)}`,
        strengthSet.notes || null,
      ].filter(Boolean);
      const note = document.createElement("small");
      note.textContent = details.join(" · ");
      row.append(numberLabel, value);
      if (note.textContent) row.append(note);
      sets.append(row);
    });
    group.append(sets);
    list.append(group);
  });
  if (list.childElementCount) section.append(list);
  return section;
}

function hevyDescription(notes) {
  if (!notes?.startsWith("Økt: ")) return notes;
  const [, description] = notes.split(" — ", 2);
  return description || null;
}

function renderWorkoutDetail({ eyebrow, title, intro, stats, sections }) {
  if (!workoutDetailModal || !workoutDetailStats || !workoutDetailBody) return;
  setText("[data-workout-detail-eyebrow]", eyebrow);
  setText("[data-workout-detail-title]", title);
  setText("[data-workout-detail-intro]", intro);
  workoutDetailStats.replaceChildren();
  stats.forEach(({ label, value }) => {
    const card = document.createElement("div");
    const labelElement = document.createElement("span");
    labelElement.textContent = label;
    const valueElement = document.createElement("strong");
    valueElement.textContent = value || "—";
    card.append(labelElement, valueElement);
    workoutDetailStats.append(card);
  });
  workoutDetailBody.replaceChildren(...sections);
  workoutDetailModal.hidden = false;
  document.body.classList.add("workout-detail-open");
}

function closeWorkoutDetail() {
  if (!workoutDetailModal) return;
  workoutDetailModal.hidden = true;
  document.body.classList.remove("workout-detail-open");
}

function plannedWorkoutDetail(session) {
  const target = session?.target_metrics || {};
  const duration = Number.isFinite(target.duration_min) ? `${number.format(target.duration_min)} min` : "Ikke angitt";
  const zone = target.zone || target.intensity_zone || sessionIntensity(session || {});
  const heartRate = formatHrTarget(target.hr_target);
  const rationale = currentToday?.recommendation?.rationale?.join(" ")
    || "Denne økten ligger i planen fordi dagens signaler støtter den. Juster dersom kroppen sier noe annet når du varmer opp.";
  const targetRows = [
    { label: "Planlagt varighet", value: duration },
    { label: "Intensitet", value: zone },
    { label: "Pulsmål", value: heartRate },
  ];
  if (target.distance_km != null) targetRows.push({ label: "Distansemål", value: `${number.format(target.distance_km)} km` });
  if (session?.notes) targetRows.push({ label: "Plan-notat", value: session.notes });
  renderWorkoutDetail({
    eyebrow: `PLANLAGT ØKT · ${formatDate(session?.date || currentToday?.date || "")}`,
    title: sessionTitle(session || {}),
    intro: session?.description || "Ingen øktbeskrivelse er lagret ennå.",
    stats: [
      { label: "VARIGHET", value: duration },
      { label: "INTENSITET", value: zone },
      { label: "STATUS", value: "Sendt til Garmin" },
    ],
    sections: [
      detailSection("MÅL FOR ØKTA", { rows: targetRows }),
      detailSection("COACH-KONTEKST", { text: rationale, tone: "workout-detail-note" }),
    ],
  });
}

function actualWorkoutDetail(detail) {
  const workout = detail.workout || {};
  const source = detail.source_summary || {};
  const samples = detail.sample_summary || {};
  const planned = detail.matched_plan;
  const strength = detail.strength_summary;
  const isStrength = Boolean(strength);
  const performanceRows = [
    { label: "Snittpuls", value: workout.avg_hr == null ? null : `${number.format(workout.avg_hr)} bpm` },
    { label: "Makspuls", value: workout.max_hr == null ? null : `${number.format(workout.max_hr)} bpm` },
    { label: "Snittempo", value: formatPace(samples.avg_pace_sec_per_km) },
    { label: "Høydemeter", value: source.elevation_gain_m == null ? null : `+${number.format(source.elevation_gain_m)} hm` },
    { label: "Aktiv energi", value: workout.calories == null ? null : `${number.format(workout.calories)} kcal` },
    { label: "Kadens", value: samples.avg_cadence == null ? null : `${number.format(samples.avg_cadence)} spm` },
    { label: "Gjennomsnittlig effekt", value: samples.avg_power_w == null ? null : `${number.format(samples.avg_power_w)} W` },
    { label: "Opplevd belastning", value: workout.rpe == null ? null : `${number.format(workout.rpe)} / 10` },
  ];
  const planTarget = planned?.target_metrics || {};
  const planRows = planned ? [
    { label: "Plan", value: planned.description || sessionTitle(planned) },
    { label: "Planlagt varighet", value: Number.isFinite(planTarget.duration_min) ? `${number.format(planTarget.duration_min)} min` : null },
    { label: "Pulsmål", value: formatHrTarget(planTarget.hr_target) },
    { label: "Status", value: planned.status === "completed" ? "Gjennomført" : planned.status },
  ] : [];
  const extraRows = strength ? [
    { label: "Sett", value: String(strength.set_count) },
    { label: "Øvelser", value: String(strength.exercise_count) },
    { label: "Volum", value: strength.volume_kg == null ? null : `${number.format(strength.volume_kg)} kg` },
  ] : [];
  const sections = [
    detailSection("REGISTRERT AV KILDEN", {
      rows: [
        { label: "Kilde", value: reviewSourceLabel(workout.source) },
        { label: "Type", value: strength?.session_name || source.activity_name || workoutTitle(workout.type) },
        ...performanceRows,
      ],
    }),
  ];
  if (planRows.length) sections.push(detailSection("PLAN MOT FAKTISK", { rows: planRows }));
  if (extraRows.length) sections.push(detailSection("STYRKEOVERSIKT", { rows: extraRows }));
  if (strength?.exercises?.length) sections.push(strengthExerciseSection(strength.exercises));
  const ownNote = workout.source === "hevy" ? hevyDescription(workout.notes) : workout.notes;
  if (ownNote) sections.push(detailSection(workout.source === "hevy" ? "NOTAT FRA HEVY" : "EGET NOTAT", { text: ownNote, tone: "workout-detail-note" }));
  renderWorkoutDetail({
    eyebrow: `GJENNOMFØRT ØKT · ${formatDate(workout.local_date || "")}`,
    title: strength?.session_name || source.activity_name || workoutTitle(workout.type),
    intro: isStrength
      ? "Sammendraget er hentet fra den synkroniserte Hevy-økten. Øvelser og sett er registrert automatisk."
      : "Sammendraget er hentet fra den synkroniserte økten. GPS-spor og rå FIT-data vises ikke her.",
    stats: isStrength
      ? [
        { label: "VARIGHET", value: formatWorkoutDuration(workout.duration_sec) },
        { label: "ØVELSER", value: String(strength.exercise_count) },
        { label: "VOLUM", value: strength.volume_kg == null ? "—" : `${number.format(strength.volume_kg)} kg` },
      ]
      : [
        { label: "VARIGHET", value: formatWorkoutDuration(workout.duration_sec) },
        { label: "DISTANSE", value: formatDistance(workout.distance_m) },
        { label: "SNITTPULS", value: workout.avg_hr == null ? "—" : `${number.format(workout.avg_hr)} bpm` },
      ],
    sections,
  });
}

function previewActualWorkoutDetail() {
  actualWorkoutDetail({
    workout: { source: "garmin", local_date: "2026-07-18", type: "running", duration_sec: 3480, distance_m: 10400, avg_hr: 138, max_hr: 153, calories: 716 },
    source_summary: { activity_name: "Rolig langtur", elevation_gain_m: 86 },
    sample_summary: { avg_pace_sec_per_km: 335, avg_cadence: 168 },
    matched_plan: { description: "60 min rolig sone 2", status: "completed", target_metrics: { duration_min: 60, zone: "Z2" } },
  });
}

function previewStrengthWorkoutDetail() {
  actualWorkoutDetail({
    workout: { source: "hevy", local_date: "2026-07-18", type: "strength_training", duration_sec: 3120, notes: "Økt: Overkropp A — Kontrollerte reps og god teknikk." },
    source_summary: {},
    sample_summary: {},
    strength_summary: {
      session_name: "Overkropp A",
      set_count: 8,
      exercise_count: 3,
      volume_kg: 4620,
      exercises: [
        { exercise: "Benkpress", sets: [{ set_num: 1, reps: 8, weight_kg: 70, rpe: 7 }, { set_num: 2, reps: 8, weight_kg: 70, rpe: 8 }, { set_num: 3, reps: 7, weight_kg: 70, rpe: 8 }] },
        { exercise: "Sittende roing", sets: [{ set_num: 1, reps: 10, weight_kg: 55, rpe: 7 }, { set_num: 2, reps: 10, weight_kg: 55, rpe: 8 }, { set_num: 3, reps: 9, weight_kg: 55, rpe: 8 }] },
        { exercise: "Sidehev", sets: [{ set_num: 1, reps: 14, weight_kg: 8, rpe: 8 }, { set_num: 2, reps: 12, weight_kg: 8, rpe: 9 }] },
      ],
    },
  });
}

async function openActualWorkoutDetail(workoutId) {
  if (workoutId === "preview-hevy") {
    previewStrengthWorkoutDetail();
    return;
  }
  if (!/^\d+$/.test(String(workoutId))) {
    previewActualWorkoutDetail();
    return;
  }
  try {
    const response = await fetch(`/api/workouts/${workoutId}`, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("workout detail unavailable");
    actualWorkoutDetail(await response.json());
  } catch {
    previewActualWorkoutDetail();
  }
}

function calendarDayLabel(day) {
  const labels = {
    completed: "fullført",
    planned: "planlagt",
    review: "vurdering klar",
    rest: "hvile",
    empty: "ingen økt",
  };
  const session = day.planned_sessions?.[0];
  const workout = day.workouts?.[0];
  return session?.description || (workout ? workoutTitle(workout.type) : labels[day.status] || "ingen økt");
}

function calendarDayIcon(status) {
  return {
    completed: "✓",
    planned: "↗",
    review: "!",
    rest: "·",
    empty: "·",
  }[status] || "·";
}

function startOfWeek(dateString) {
  const value = dateFromIso(dateString);
  const offset = (value.getDay() + 6) % 7;
  value.setDate(value.getDate() - offset);
  return isoDate(value);
}

function previewWeek(start) {
  const weekStart = startOfWeek(start || "2026-07-13");
  const isReferenceWeek = weekStart === "2026-07-13";
  const referenceDays = [
    { status: "rest", title: "Hvile" },
    { status: "completed", title: "Rolig løp", duration: 2900, distance: 7600 },
    { status: "empty", title: "Ingen økt" },
    { status: "completed", title: "Løp", duration: 2201, distance: 6200 },
    { status: "empty", title: "Ingen økt" },
    { status: "review", title: "Rolig langtur", duration: 3480, distance: 10400 },
    { status: "today", title: "Løp", duration: 3240, distance: 13800 },
  ];
  const futureDays = [
    { status: "rest", title: "Hvile" },
    { status: "planned", title: "Rolig løp" },
    { status: "rest", title: "Hvile" },
    { status: "planned", title: "Kontrollert terskel" },
    { status: "rest", title: "Hvile" },
    { status: "planned", title: "Rolig langtur" },
    { status: "rest", title: "Hvile" },
  ];
  const definitions = isReferenceWeek ? referenceDays : futureDays;
  const days = definitions.map((definition, index) => {
    const localDate = addDays(weekStart, index);
    const workout = definition.duration ? {
      id: `preview-${weekStart}-${index}`,
      local_date: localDate,
      type: "running",
      duration_sec: definition.duration,
      distance_m: definition.distance,
      avg_hr: index === 5 ? 138 : 142,
    } : null;
    const planned = definition.status === "planned" ? {
      id: `preview-plan-${weekStart}-${index}`,
      date: localDate,
      type: index === 3 ? "threshold_run" : index === 5 ? "long_run" : "easy_run",
      description: definition.title,
      status: "planned",
      target_metrics: index === 3 ? { duration_min: 54, zone: "Z3" } : { duration_min: index === 5 ? 70 : 40, zone: "Z2" },
    } : null;
    return {
      date: localDate,
      weekday: index,
      status: definition.status === "today" ? "completed" : definition.status,
      planned_sessions: planned ? [planned] : [],
      workouts: workout ? [workout] : [],
    };
  });
  const workouts = days.flatMap((day) => day.workouts);
  return {
    start: weekStart,
    end: addDays(weekStart, 6),
    days,
    completed_days: workouts.length,
    training_days: workouts.length,
    planned_sessions: days.flatMap((day) => day.planned_sessions).length,
    workout_count: workouts.length,
    total_duration_sec: workouts.reduce((sum, item) => sum + (item.duration_sec || 0), 0),
    total_distance_m: workouts.reduce((sum, item) => sum + (item.distance_m || 0), 0),
    pending_reviews: isReferenceWeek ? 1 : 0,
  };
}

function blockLength(start, end) {
  if (!start || !end) return "—";
  const days = Math.round((dateFromIso(end) - dateFromIso(start)) / 86400000) + 1;
  const weeks = Math.max(1, Math.ceil(days / 7));
  return `${weeks} ${weeks === 1 ? "uke" : "uker"}`;
}

function renderBlock(payload) {
  const block = payload?.block;
  if (!block) return;
  setText("[data-block-title]", block.name);
  setText("[data-block-range]", formatWeekRange(block.start_date, block.end_date));
  setText("[data-block-phase]", block.phase_label || block.phase || "BLOKK");
  setText("[data-block-goal]", block.goal || "Ingen tydelig målsetting er satt for blokken ennå.");
  setText("[data-block-note]", block.notes || "Denne blokken styrer ukene under og kan justeres når virkeligheten endrer seg.");
  setText("[data-block-length]", blockLength(block.start_date, block.end_date));
  setText("[data-block-rhythm]", block.is_example ? "3–4 økter" : "Uke for uke");
  const current = block.weeks?.find((week) => week.status === "current") || block.weeks?.[0];
  setText("[data-block-next]", current ? `Uke ${current.number}` : "Ikke satt");

  const state = document.querySelector("[data-block-state]");
  if (state) {
    state.textContent = block.is_example
      ? "EKSEMPEL · IKKE AKTIV"
      : block.is_active === false ? "PLANLAGT BLOKK" : "AKTIV BLOKK";
    state.classList.toggle("example", Boolean(block.is_example));
    state.classList.toggle("active", !block.is_example);
  }

  const grid = document.querySelector("[data-block-weeks]");
  if (!grid || !Array.isArray(block.weeks)) return;
  grid.replaceChildren();
  block.weeks.forEach((week) => {
    const card = document.createElement("article");
    card.className = `block-week${week.status === "current" ? " current" : ""}${week.is_deload ? " deload" : ""}`;
    const numberLine = document.createElement("div");
    numberLine.className = "block-week-number";
    const numberLabel = document.createElement("span");
    numberLabel.textContent = `UKE ${week.number}`;
    numberLine.append(numberLabel);
    if (week.status === "current" || week.is_deload) {
      const tag = document.createElement("i");
      tag.textContent = week.is_deload ? "DELOAD" : "AKTIV NÅ";
      numberLine.append(tag);
    }
    const title = document.createElement("h3");
    title.textContent = week.focus || "Uke uten fokus";
    const copy = document.createElement("p");
    copy.textContent = week.progression_note || "Denne ukens progresjon er ikke skrevet ennå.";
    const summary = document.createElement("small");
    summary.textContent = week.planned_volume_note || (
      week.planned_session_count ? `${week.planned_session_count} planlagte økter` : "Ingen økter lagt inn ennå"
    );
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.blockOpenWeek = week.week_start;
    button.append("Åpne uke ");
    const arrow = document.createElement("span");
    arrow.textContent = "→";
    button.append(arrow);
    card.append(numberLine, title, copy, summary, button);
    grid.append(card);
  });
}

function blockPhaseLabel(phase) {
  const labels = { base: "Base", build: "Bygg", peak: "Toppform", taper: "Nedtrapping", recovery: "Restitusjon" };
  return labels[phase] || phase || "—";
}

function renderBlockProposal(envelope) {
  const candidate = envelope?.proposal || envelope;
  if (!blockProposal || !blockProposalWeeks || !candidate?.name || !Array.isArray(candidate.weeks)) return;
  currentBlockProposal = envelope;
  setText("[data-block-proposal-name]", candidate.name);
  setText("[data-block-proposal-start]", formatWeekRange(candidate.start_date, candidate.start_date));
  setText("[data-block-proposal-length]", blockLength(candidate.start_date, candidate.end_date));
  setText("[data-block-proposal-phase]", blockPhaseLabel(candidate.phase));
  setText("[data-block-proposal-goal]", candidate.goal || "Målet for blokken er ikke formulert ennå.");
  const applyButton = document.querySelector("[data-block-proposal-apply]");
  if (applyButton) applyButton.textContent = candidate.action === "update" ? "Oppdater blokken" : "Bruk blokkforslaget";

  blockProposalWeeks.replaceChildren();
  candidate.weeks.forEach((week, index) => {
    const card = document.createElement("article");
    card.className = `block-proposal-week${week.is_deload ? " deload" : ""}`;
    const number = document.createElement("span");
    number.textContent = week.is_deload ? `UKE ${index + 1} · DELOAD` : `UKE ${index + 1}`;
    const focus = document.createElement("strong");
    focus.textContent = week.focus;
    const note = document.createElement("small");
    note.textContent = week.planned_volume_note || week.progression_note || "Detaljene fylles ut i ukesplanen.";
    card.append(number, focus, note);
    blockProposalWeeks.append(card);
  });
  blockProposal.hidden = false;
}

function clearBlockProposal() {
  currentBlockProposal = undefined;
  if (blockProposal) blockProposal.hidden = true;
  if (blockProposalWeeks) blockProposalWeeks.replaceChildren();
}

function renderBlockConversation(messages = []) {
  if (!blockConversation) return;
  const visibleMessages = messages.filter((message) => (
    message?.role === "user" || message?.role === "assistant"
  ) && typeof message.content === "string" && message.content.trim());
  blockCoachHistory.splice(0, blockCoachHistory.length, ...visibleMessages.map(({ role, content }) => ({ role, content })));
  blockConversation.replaceChildren();
  if (!visibleMessages.length) {
    blockConversation.hidden = true;
    return;
  }
  const latestCoachMessage = [...visibleMessages].reverse().find((message) => message.role === "assistant")
    || visibleMessages.at(-1);
  const displayedMessages = blockConversationExpanded ? visibleMessages : [latestCoachMessage];
  displayedMessages.forEach((message) => {
    const card = document.createElement("article");
    card.className = `block-conversation-message ${message.role}`;
    const label = document.createElement("p");
    label.className = "eyebrow";
    label.textContent = message.role === "user" ? "DU" : "COACH";
    const copy = document.createElement("p");
    copy.textContent = message.content;
    card.append(label, copy);
    if (message.role === "assistant" && message.model) {
      const model = document.createElement("small");
      model.textContent = message.model;
      card.append(model);
    }
    blockConversation.append(card);
  });
  if (visibleMessages.length > 1) {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "block-conversation-toggle";
    toggle.setAttribute("aria-expanded", String(blockConversationExpanded));
    toggle.textContent = blockConversationExpanded
      ? "Vis bare siste svar"
      : `Vis hele samtalen · ${visibleMessages.length} meldinger`;
    toggle.addEventListener("click", () => {
      blockConversationExpanded = !blockConversationExpanded;
      renderBlockConversation(visibleMessages);
    });
    blockConversation.append(toggle);
  }
  blockConversation.hidden = false;
  if (blockCoachReply) blockCoachReply.hidden = true;
}

function blockPayloadFromProposal(candidate) {
  return {
    block: {
      ...candidate,
      source: "plan",
      is_example: false,
      is_active: true,
      phase_label: blockPhaseLabel(candidate.phase),
      principles: [],
      weeks: candidate.weeks.map((week, index) => {
        const weekStart = addDays(candidate.start_date, index * 7);
        return {
          ...week,
          number: index + 1,
          week_start: weekStart,
          week_end: addDays(weekStart, 6),
          status: index === 0 ? "current" : "upcoming",
          planned_session_count: 0,
        };
      }),
    },
  };
}

function previewBlockCoachReply(question) {
  const wantsProposal = /\b(opprett|opprette|lag|lage|start|bygg|bygge|blokk)\b/i.test(question);
  if (!wantsProposal) {
    return {
      answer: "I previewet kan dere diskutere målet og rammene først. Be coachen lage eller endre en blokk når retningen er klar; da dukker et konkret forslag opp for godkjenning.",
      model: "Preview",
      proposal: null,
    };
  }
  return {
    answer: "Her er et første utkast basert på ønsket om en kontrollert vei tilbake til stabil løping. Se gjennom ukene og juster gjerne retning eller rammer før du bruker forslaget — ingenting er lagret ennå.",
    model: "Preview",
    proposal: {
      action: "create",
      name: "6 uker · Stabil løpsrytme",
      phase: "base",
      start_date: "2026-07-20",
      end_date: "2026-08-30",
      goal: "Bygge stabil løpstoleranse med rom for kroppens respons før mer planlagt kvalitet.",
      notes: "Eksempelutkast fra lokalt preview.",
      weeks: [
        { focus: "Rytme og toleranse", progression_note: "Tre rolige løpsdager uten å jage fart.", planned_volume_note: "3 løpeøkter · 1 valgfri styrke", is_deload: false },
        { focus: "Bygge frekvens", progression_note: "Behold rolig volum og test korte stigninger hvis kroppen er fin.", planned_volume_note: "3–4 rolige økter", is_deload: false },
        { focus: "Kontrollert terskel", progression_note: "Legg inn én kontrollert kvalitetsøkt.", planned_volume_note: "1 kvalitet · 2 rolige", is_deload: false },
        { focus: "Konsolidere", progression_note: "Hold belastningen omtrent lik så kroppen får sette seg.", planned_volume_note: "Samme rytme · ingen volumjakt", is_deload: false },
        { focus: "Robust rolig volum", progression_note: "Utvid én rolig økt forsiktig ved stabil respons.", planned_volume_note: "1 lengre rolig · 2 lette", is_deload: false },
        { focus: "Deload og vurdering", progression_note: "Trekk ned volumet og vurder veien videre.", planned_volume_note: "Redusert volum · behold rytmen", is_deload: true },
      ],
    },
  };
}

function renderWeekPage(week) {
  if (!weekCalendar || !week) return;
  displayedWeekStart = week.start;
  setText("[data-week-page-title]", `Uke ${isoWeekNumber(week.start)}`);
  setText("[data-week-page-range]", formatWeekRange(week.start, week.end));
  setText("[data-week-coach-scope]", `Uke ${isoWeekNumber(week.start)}`);
  setText("[data-week-stat-workouts]", String(week.workout_count ?? 0));
  const trainingDays = week.training_days ?? week.completed_days ?? 0;
  setText("[data-week-stat-workouts-foot]", `${trainingDays} dager med trening`);
  setText("[data-week-stat-duration]", formatDuration(week.total_duration_sec) || "—");
  setText("[data-week-stat-distance]", formatDistance(week.total_distance_m));
  setText("[data-week-stat-reviews]", String(week.pending_reviews ?? 0));
  setText(
    "[data-week-stat-reviews-foot]",
    week.pending_reviews === 1 ? "klar til å se på" : "klare til å se på",
  );

  const summary = document.querySelector("[data-week-calendar-summary]");
  if (summary) {
    summary.replaceChildren();
    const completed = document.createElement("strong");
    completed.textContent = `${trainingDays} ${trainingDays === 1 ? "dag" : "dager"}`;
    if (trainingDays) summary.append(completed, " med registrert trening");
    else if (week.planned_sessions) summary.append(completed, ` registrerte · ${week.planned_sessions} planlagte økter`);
    else summary.append(completed, " med registrert trening");
  }

  weekCalendar.replaceChildren();
  week.days.forEach((day) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `week-calendar-day ${day.status}`;
    item.dataset.day = day.date;
    const date = document.createElement("span");
    const dateValue = dateFromIso(day.date);
    date.textContent = `${weekdays[day.weekday]} ${dateValue.getDate()}.`;
    const title = document.createElement("strong");
    title.textContent = calendarDayLabel(day);
    const state = document.createElement("i");
    state.textContent = calendarDayIcon(day.status);
    item.append(date, title, state);
    weekCalendar.append(item);
  });
}

function setLogSection(selector, label, lines) {
  const section = document.querySelector(selector);
  if (!section) return;
  section.replaceChildren();
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = label;
  section.append(eyebrow);
  lines.forEach(({ title, detail, workoutId }) => {
    const entry = document.createElement(workoutId == null ? "div" : "button");
    entry.className = "day-log-workout";
    if (workoutId != null) {
      entry.type = "button";
      entry.classList.add("day-log-workout-button");
      entry.dataset.workoutId = workoutId;
    }
    const heading = document.createElement("strong");
    heading.textContent = title;
    const copy = document.createElement("span");
    copy.textContent = detail;
    entry.append(heading, copy);
    section.append(entry);
  });
}

function renderDayLog(log) {
  if (!dayLogCard || !log) return;
  dayLogCard.hidden = false;
  setText("[data-day-log-date]", formatDate(log.date));
  setText("[data-day-log-title]", "Dagslogg");

  const workouts = log.workouts || [];
  setLogSection(
    "[data-day-log-workouts]",
    "REGISTRERT TRENING",
    workouts.length
      ? workouts.map((workout) => {
        const details = [formatWorkoutDuration(workout.duration_sec)];
        if (workout.source === "hevy") details.unshift("Hevy");
        const distance = formatDistance(workout.distance_m);
        if (distance !== "—") details.push(distance);
        if (workout.avg_hr != null) details.push(`${number.format(workout.avg_hr)} bpm`);
        if (workout.elevation_gain_m != null) details.push(`+${number.format(workout.elevation_gain_m)} hm`);
        if (workout.calories != null) details.push(`${number.format(workout.calories)} kcal`);
        return {
          title: workoutTitle(workout.type),
          detail: details.join(" · "),
          workoutId: workout.id,
        };
      })
      : [{ title: "Ingen registrert trening", detail: "Ingen aktivitet fra de tilkoblede kildene denne dagen." }],
  );

  const plans = log.planned_sessions || [];
  setLogSection(
    "[data-day-log-plan]",
    "PLAN",
    plans.length
      ? plans.map((plan) => ({
        title: plan.description || sessionTitle(plan),
        detail: plan.status === "completed" ? "Gjennomført" : plan.status === "planned" ? "Planlagt" : "Hvile eller endret",
      }))
      : [{ title: "Ingen plan", detail: "Dagen er ikke lagt inn i en treningsplan." }],
  );

  const coachSection = document.querySelector("[data-day-log-coach]");
  const reviews = log.coach_reviews || [];
  if (coachSection) {
    coachSection.hidden = !reviews.length;
    if (reviews.length) {
      coachSection.replaceChildren();
      const eyebrow = document.createElement("p");
      eyebrow.className = "eyebrow pink";
      eyebrow.textContent = "COACH";
      const copy = document.createElement("p");
      copy.textContent = reviews[0].comment;
      coachSection.append(eyebrow, copy);
    }
  }

  const automatic = log.automatic || {};
  const daily = automatic.garmin_daily || {};
  const sleep = automatic.sleep || {};
  const hrv = automatic.hrv || {};
  const weight = automatic.weight || {};
  const nutrition = automatic.nutrition || {};
  const sleepDuration = formatDuration(sleep.duration_sec);
  setText("[data-day-log-readiness]", daily.training_readiness_score == null ? "—" : number.format(daily.training_readiness_score));
  setText(
    "[data-day-log-readiness-foot]",
    daily.training_readiness_level ? daily.training_readiness_level.toLowerCase() : "Ikke registrert",
  );
  setText("[data-day-log-hrv]", hrv.last_night_avg_ms == null ? "—" : `${number.format(hrv.last_night_avg_ms)} ms`);
  setText("[data-day-log-hrv-foot]", hrv.status ? hrv.status.toLowerCase() : "Ikke registrert");
  setText("[data-day-log-sleep]", sleepDuration || "—");
  setText("[data-day-log-sleep-foot]", sleep.sleep_score == null ? "Ikke registrert" : `${number.format(sleep.sleep_score)} % søvnscore`);
  setText("[data-day-log-resting-hr]", daily.resting_hr == null ? "—" : `${number.format(daily.resting_hr)} bpm`);
  setText("[data-day-log-steps]", daily.steps == null ? "—" : number.format(daily.steps));
  setText("[data-day-log-weight]", weight.weight_kg == null ? "—" : `${number.format(weight.weight_kg)} kg`);
  setText("[data-day-log-weight-foot]", weight.fat_ratio_pct == null ? "Ikke registrert" : `${number.format(weight.fat_ratio_pct)} % fett`);
  setText("[data-day-log-kcal]", nutrition.kcal == null ? "—" : `${number.format(nutrition.kcal)} kcal`);
  setText(
    "[data-day-log-kcal-foot]",
    nutrition.kcal_goal == null ? "Ikke registrert" : `av ${number.format(nutrition.kcal_goal)} kcal`,
  );
  const macros = [
    nutrition.protein_g == null ? null : `${number.format(nutrition.protein_g)} P`,
    nutrition.carbs_g == null ? null : `${number.format(nutrition.carbs_g)} K`,
    nutrition.fat_g == null ? null : `${number.format(nutrition.fat_g)} F`,
  ].filter(Boolean);
  setText("[data-day-log-macros]", macros.length ? macros.join(" · ") : "Ikke registrert");
  setText(
    "[data-day-log-water]",
    nutrition.water_ml == null ? "—" : `${number.format(nutrition.water_ml / 1000)} l`,
  );
}

async function loadDayLog(day) {
  try {
    const response = await fetch(`/api/days/${day}`, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("day log unavailable");
    renderDayLog(await response.json());
  } catch {
    // I lokalt preview beholder vi den representative dagsloggen.
    if (dayLogCard) dayLogCard.hidden = false;
  }
}

async function loadWeek(start) {
  const localPreview = window.location.protocol === "file:" || window.location.search.includes("week-preview");
  const target = start || currentToday?.date || (localPreview ? "2026-07-13" : isoDate(new Date()));
  try {
    const response = await fetch(`/api/week?start=${encodeURIComponent(target)}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("week unavailable");
    renderWeekPage(await response.json());
  } catch {
    // Det lokale previewet må også kunne bla frem og tilbake, selv om det
    // ikke finnes et API under file://.
    renderWeekPage(previewWeek(target));
  }
}

async function loadBlock() {
  void loadBlockConversation();
  try {
    const response = await fetch("/api/blocks", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("block unavailable");
    renderBlock(await response.json());
  } catch {
    // Statisk eksempelblokk i previewet er med hensikt representativ.
  }
}

async function loadBlockConversation() {
  if (window.location.protocol === "file:") {
    if (blockCoachHistory.length) renderBlockConversation(blockCoachHistory);
    return;
  }
  try {
    const response = await fetch("/api/blocks/coach/history", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("block conversation unavailable");
    const payload = await response.json();
    renderBlockConversation(payload.messages || []);
  } catch {
    // Ikke fjern den allerede viste samtalen hvis nettverket er midlertidig nede.
  }
}

function showPage(view, weekStart) {
  const isToday = view === "I dag";
  const isWeek = view === "Uke";
  const isBlock = view === "Blokk";
  todayPage.hidden = !isToday;
  weekPage.hidden = !isWeek;
  blockPage.hidden = !isBlock;
  if (isWeek) loadWeek(weekStart || displayedWeekStart);
  if (isBlock) loadBlock();
  window.scrollTo(0, 0);
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

  renderRecentWorkouts(payload.recent_workouts);
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

function shortDate(dateString) {
  if (!dateString) return "den valgte dagen";
  return new Intl.DateTimeFormat("nb-NO", { weekday: "long", day: "numeric", month: "long" })
    .format(dateFromIso(dateString));
}

function proposalOperationCopy(operation) {
  const before = operation.before || {};
  const after = operation.after || {};
  const label = before.description || after.description || "Økt";
  if (operation.action === "move") {
    return {
      icon: "→",
      title: `Flytt ${label}`,
      detail: `${shortDate(before.date)} → ${shortDate(after.date || operation.to_date)}${operation.reason ? ` · ${operation.reason}` : ""}`,
    };
  }
  if (operation.action === "skip") {
    return {
      icon: "–",
      title: `Hopp over ${label}`,
      detail: operation.reason || "Økten markeres som hoppet over i denne uken.",
    };
  }
  if (operation.action === "replace") {
    return {
      icon: "↺",
      title: `Bytt ${label}`,
      detail: `${before.description || "Planlagt økt"} → ${after.description || operation.description}${operation.reason ? ` · ${operation.reason}` : ""}`,
    };
  }
  return {
    icon: "+",
    title: `Legg til ${after.description || operation.description || "økt"}`,
    detail: `${shortDate(after.date || operation.date)}${operation.reason ? ` · ${operation.reason}` : ""}`,
  };
}

function renderWeekProposal(proposal) {
  if (!weekProposal || !weekProposalOperations || !proposal?.operations?.length) return;
  currentWeekProposal = proposal;
  weekProposalOperations.replaceChildren();
  proposal.operations.forEach((operation) => {
    const copy = proposalOperationCopy(operation);
    const row = document.createElement("article");
    row.className = "week-proposal-operation";
    const icon = document.createElement("i");
    icon.textContent = copy.icon;
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = copy.title;
    const detail = document.createElement("p");
    detail.textContent = copy.detail;
    body.append(title, detail);
    row.append(icon, body);
    weekProposalOperations.append(row);
  });
  weekProposal.hidden = false;
}

function clearWeekProposal() {
  currentWeekProposal = undefined;
  if (weekProposal) weekProposal.hidden = true;
  if (weekProposalOperations) weekProposalOperations.replaceChildren();
}

function previewWeekCoachReply(question) {
  const wantsChange = /\b(flytt|endre|bytt|kutt|legg\s+til|droppe|dropp)\b/i.test(question);
  const weekStart = displayedWeekStart || "2026-07-13";
  if (!wantsChange) {
    return {
      answer: "I previewet kan coachen svare på spørsmål om den valgte uken. Når dashboardet kjører på VPS-en, får svaret den faktiske ukekonteksten og kan ved behov legge ved et bekreftbart endringsforslag.",
      model: "Preview",
      operations: [],
    };
  }
  return {
    answer: "Dette er et mulig oppsett: gi terskeløkta litt mer rom og behold resten av ukens rolige rytme. Se diffen før du tar stilling — ingenting er endret ennå.",
    model: "Preview",
    operations: [{
      action: "move",
      session_id: null,
      to_date: addDays(weekStart, 3),
      reason: "Mer restitusjon rundt kvalitetsøkta.",
      before: { date: addDays(weekStart, 1), description: "Kontrollert terskel" },
      after: { date: addDays(weekStart, 3), description: "Kontrollert terskel", status: "modified" },
    }],
  };
}

navButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const destination = button.dataset.view;
    if (destination === "I dag" || destination === "Uke" || destination === "Blokk") {
      if (button.matches(".nav-item, .mobile-nav-item")) setActiveView(button);
      showPage(destination);
      return;
    }
    if (destination === "Hele planen") {
      const weekNavigation = document.querySelector('[data-view="Uke"]');
      if (weekNavigation) setActiveView(weekNavigation);
      showPage("Uke");
      return;
    }
    const messages = {
      "Vis detaljer": "Øktens detaljer kobles på i neste dashboard-steg.",
      Synkronisering: "Garmin-data oppdateres automatisk på VPS-en.",
      Trender: "Trender kommer når vi bygger historiske grafer oppå dagsloggene.",
    };
    if (messages[destination]) showToast(messages[destination]);
  });
});

weekCalendar?.addEventListener("click", (event) => {
  const day = event.target.closest("[data-day]");
  if (day?.dataset.day) loadDayLog(day.dataset.day);
});

document.querySelectorAll("[data-week-nav]").forEach((button) => {
  button.addEventListener("click", () => {
    const base = displayedWeekStart || currentToday?.date || isoDate(new Date());
    const movement = button.dataset.weekNav;
    clearWeekProposal();
    if (weekCoachReply) weekCoachReply.hidden = true;
    if (movement === "previous") loadWeek(addDays(base, -7));
    else if (movement === "next") loadWeek(addDays(base, 7));
    else loadWeek(currentToday?.date || (window.location.protocol === "file:" ? "2026-07-13" : isoDate(new Date())));
  });
});

document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-block-open-week]");
  if (!opener?.dataset.blockOpenWeek) return;
  const weekNavigation = document.querySelector('[data-view="Uke"]');
  if (weekNavigation) setActiveView(weekNavigation);
  clearWeekProposal();
  if (weekCoachReply) weekCoachReply.hidden = true;
  showPage("Uke", opener.dataset.blockOpenWeek);
});

document.querySelector("[data-day-log-close]")?.addEventListener("click", () => {
  if (dayLogCard) dayLogCard.hidden = true;
});

document.querySelector('[data-workout-detail="planned"]')?.addEventListener("click", () => {
  const session = (currentToday?.planned_sessions || []).find((item) => item.type !== "rest") || {
    date: "2026-07-19",
    type: "threshold_run",
    description: "6 × 3 min @ terskel · 2 min rolig mellom dragene",
    target_metrics: { duration_min: 54, zone: "Z3" },
  };
  plannedWorkoutDetail(session);
});

document.addEventListener("click", (event) => {
  const workout = event.target.closest("[data-workout-id]");
  if (workout?.dataset.workoutId) openActualWorkoutDetail(workout.dataset.workoutId);
});

document.querySelectorAll("[data-workout-detail-close]").forEach((button) => {
  button.addEventListener("click", closeWorkoutDetail);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !workoutDetailModal?.hidden) closeWorkoutDetail();
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
      body: JSON.stringify({ message: question, history: coachHistory.slice(-8) }),
    });
    if (!response.ok) throw new Error("coach chat failed");
    const payload = await response.json();
    if (!payload.answer) throw new Error("coach answer missing");

    coachAnswer.textContent = payload.answer;
    coachReply.hidden = false;
    coachHistory.push(
      { role: "user", content: question },
      { role: "assistant", content: payload.answer },
    );
    if (coachHistory.length > 8) coachHistory.splice(0, coachHistory.length - 8);
    chatMessage.value = "";
  } catch {
    showToast("Coachen svarte ikke. Prøv igjen om litt.");
  } finally {
    chatButton.disabled = false;
    chatButton.textContent = "Send";
  }
});

weekChatForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = weekChatMessage?.value.trim();
  if (!question) {
    weekChatMessage?.focus();
    return;
  }

  if (weekChatButton) {
    weekChatButton.disabled = true;
    weekChatButton.textContent = "Tenker …";
  }
  clearWeekProposal();
  try {
    const weekStart = displayedWeekStart || startOfWeek(currentToday?.date || isoDate(new Date()));
    const response = await fetch(`/api/weeks/${weekStart}/coach`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ message: question, history: weekCoachHistory.slice(-8) }),
    });
    if (!response.ok) throw new Error("week coach unavailable");
    const payload = await response.json();
    if (!payload.answer) throw new Error("week coach answer missing");
    weekCoachAnswer.textContent = payload.answer;
    if (weekCoachReply) weekCoachReply.hidden = false;
    setText("[data-week-coach-model]", `${payload.model || "V4-Pro"} · Ingen endring er gjort`);
    weekCoachHistory.push({ role: "user", content: question }, { role: "assistant", content: payload.answer });
    if (weekCoachHistory.length > 8) weekCoachHistory.splice(0, weekCoachHistory.length - 8);
    if (payload.proposal) renderWeekProposal(payload.proposal);
    if (weekChatMessage) weekChatMessage.value = "";
  } catch {
    const preview = previewWeekCoachReply(question);
    weekCoachAnswer.textContent = preview.answer;
    if (weekCoachReply) weekCoachReply.hidden = false;
    setText("[data-week-coach-model]", `${preview.model} · Ingen endring er gjort`);
    if (preview.operations.length) renderWeekProposal({ id: null, operations: preview.operations, status: "pending" });
    if (weekChatMessage) weekChatMessage.value = "";
  } finally {
    if (weekChatButton) {
      weekChatButton.disabled = false;
      weekChatButton.textContent = "Send";
    }
  }
});

document.querySelector("[data-week-proposal-apply]")?.addEventListener("click", async () => {
  if (!currentWeekProposal) return;
  const button = document.querySelector("[data-week-proposal-apply]");
  if (button) {
    button.disabled = true;
    button.textContent = "Lagrer …";
  }
  try {
    if (!Number.isInteger(currentWeekProposal.id)) {
      clearWeekProposal();
      showToast("Forslaget er brukt i previewet. På VPS-en blir samme handling lagret i planen.");
      return;
    }
    const response = await fetch(`/api/week-proposals/${currentWeekProposal.id}/apply`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("proposal apply failed");
    clearWeekProposal();
    await loadWeek(displayedWeekStart);
    showToast("Ukeplanen er oppdatert. Ingen økt er sendt til Garmin herfra.");
  } catch {
    showToast("Kunne ikke bruke forslaget. Planen er ikke endret.");
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Bruk forslaget";
    }
  }
});

document.querySelector("[data-week-proposal-discard]")?.addEventListener("click", async () => {
  const proposal = currentWeekProposal;
  clearWeekProposal();
  if (!proposal || !Number.isInteger(proposal.id)) {
    showToast("Forslaget er forkastet.");
    return;
  }
  try {
    const response = await fetch(`/api/week-proposals/${proposal.id}/discard`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("proposal discard failed");
    showToast("Forslaget er forkastet.");
  } catch {
    showToast("Forslaget kunne ikke forkastes på serveren. Planen er uansett ikke endret.");
  }
});

blockChatForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = blockChatMessage?.value.trim();
  if (!question) {
    blockChatMessage?.focus();
    return;
  }

  if (blockChatButton) {
    blockChatButton.disabled = true;
    blockChatButton.textContent = "Tenker …";
  }
  clearBlockProposal();
  try {
    const response = await fetch("/api/blocks/coach", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ message: question, history: blockCoachHistory.slice(-8) }),
    });
    if (!response.ok) throw new Error("block coach unavailable");
    const payload = await response.json();
    if (!payload.answer) throw new Error("block coach answer missing");
    blockCoachAnswer.textContent = payload.answer;
    setText("[data-block-coach-model]", `${payload.model || "V4-Pro"} · Ingen endring er gjort`);
    blockConversationExpanded = false;
    if (payload.messages) {
      renderBlockConversation(payload.messages);
    } else {
      blockCoachHistory.push({ role: "user", content: question }, { role: "assistant", content: payload.answer });
      if (blockCoachHistory.length > 8) blockCoachHistory.splice(0, blockCoachHistory.length - 8);
      renderBlockConversation(blockCoachHistory);
    }
    if (payload.proposal) renderBlockProposal(payload.proposal);
    if (blockChatMessage) blockChatMessage.value = "";
  } catch {
    if (window.location.protocol !== "file:") {
      showToast("Coachen svarte ikke. Blokken er ikke endret.");
      return;
    }
    const preview = previewBlockCoachReply(question);
    blockCoachAnswer.textContent = preview.answer;
    setText("[data-block-coach-model]", `${preview.model} · Ingen endring er gjort`);
    blockCoachHistory.push({ role: "user", content: question }, { role: "assistant", content: preview.answer });
    if (blockCoachHistory.length > 8) blockCoachHistory.splice(0, blockCoachHistory.length - 8);
    blockConversationExpanded = false;
    renderBlockConversation(blockCoachHistory);
    if (preview.proposal) renderBlockProposal({ id: null, proposal: preview.proposal, status: "pending" });
    if (blockChatMessage) blockChatMessage.value = "";
  } finally {
    if (blockChatButton) {
      blockChatButton.disabled = false;
      blockChatButton.textContent = "Send";
    }
  }
});

document.querySelector("[data-block-proposal-apply]")?.addEventListener("click", async () => {
  if (!currentBlockProposal) return;
  const envelope = currentBlockProposal;
  const candidate = envelope.proposal || envelope;
  const button = document.querySelector("[data-block-proposal-apply]");
  if (button) {
    button.disabled = true;
    button.textContent = "Lagrer …";
  }
  try {
    if (!Number.isInteger(envelope.id)) {
      renderBlock(blockPayloadFromProposal(candidate));
      clearBlockProposal();
      showToast("Blokken er opprettet i previewet. På VPS-en lagres samme valg først etter bekreftelsen.");
      return;
    }
    const response = await fetch(`/api/block-proposals/${envelope.id}/apply`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("block proposal apply failed");
    clearBlockProposal();
    await loadBlock();
    showToast("Blokken er lagret. Konkrete økter planlegges videre i hver uke.");
  } catch {
    showToast("Kunne ikke bruke blokkforslaget. Blokken er ikke endret.");
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = candidate.action === "update" ? "Oppdater blokken" : "Bruk blokkforslaget";
    }
  }
});

document.querySelector("[data-block-proposal-discard]")?.addEventListener("click", async () => {
  const envelope = currentBlockProposal;
  clearBlockProposal();
  if (!envelope || !Number.isInteger(envelope.id)) {
    showToast("Blokkforslaget er forkastet.");
    return;
  }
  try {
    const response = await fetch(`/api/block-proposals/${envelope.id}/discard`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("block proposal discard failed");
    showToast("Blokkforslaget er forkastet.");
  } catch {
    showToast("Forslaget kunne ikke forkastes på serveren. Blokken er uansett ikke endret.");
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
