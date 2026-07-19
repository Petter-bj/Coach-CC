const toast = document.querySelector(".toast");
const navButtons = document.querySelectorAll("[data-view]");
const promptButtons = document.querySelectorAll("[data-prompt]");
const chatForm = document.querySelector("#chat-form");
const chatMessage = document.querySelector("#chat-message");
const reviewForm = document.querySelector("#review-form");
const reviewCard = document.querySelector("#review-card");
const saturday = document.querySelector('[data-day="lørdag"]');
let toastTimer;

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

navButtons.forEach((button) => {
  button.addEventListener("click", () => {
    if (button.matches(".nav-item, .mobile-nav-item")) setActiveView(button);
    const destination = button.dataset.view;
    const messages = {
      "Start økt": "Øktstart kommer når Garmin-integrasjonen kobles på.",
      "Detaljert plan": "Her åpnes øktens detaljer i den ferdige appen.",
      "Synkronisering": "Previewet bruker eksempeldata – ekte sync vises her senere.",
      Varsler: "Ingen nye avvik i previewet.",
      "Øktvalg": "Her kan du flytte, erstatte eller forkorte økten.",
      "Ukens plan": "Ukens plan blir neste skjerm i dashboardet.",
    };
    if (messages[destination]) showToast(messages[destination]);
    else if (destination && !button.matches(".nav-item, .mobile-nav-item")) showToast(`${destination} åpnes i neste dashboard-steg.`);
  });
});

promptButtons.forEach((button) => {
  button.addEventListener("click", () => {
    chatMessage.value = button.dataset.prompt;
    chatMessage.focus();
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
