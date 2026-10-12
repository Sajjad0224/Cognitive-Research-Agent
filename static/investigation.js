(() => {
  const chatUrl = JSON.parse(document.getElementById("chat-url").textContent);
  const conversation = document.getElementById("conversation");
  const questionForm = document.getElementById("question-form");
  const questionInput = document.getElementById("question");
  const askButton = document.getElementById("ask-button");
  const conclusionForm = document.getElementById("conclusion-form");
  const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]").value;
  const citationList = document.getElementById("citation-list");
  const evidenceList = document.getElementById("evidence-list");

  function addMessage(kind, label, text) {
    const message = document.createElement("div");
    message.className = `message ${kind}-message`;
    const heading = document.createElement("span");
    heading.className = "message-label";
    heading.textContent = label;
    const body = document.createElement("p");
    body.textContent = text;
    message.append(heading, body);
    conversation.append(message);
    conversation.scrollTop = conversation.scrollHeight;
  }

  function addEvidence(item) {
    if (evidenceList.querySelector(`[data-evidence-id="${CSS.escape(item.id)}"]`)) return;
    const record = document.createElement("details");
    record.className = "evidence-record";
    record.dataset.evidenceId = item.id;
    const summary = document.createElement("summary");
    const badge = document.createElement("span");
    badge.className = "evidence-id";
    badge.textContent = item.id;
    const title = document.createElement("span");
    title.textContent = item.title;
    const icon = document.createElement("span");
    icon.className = "evidence-chevron";
    icon.textContent = "+";
    summary.append(badge, title, icon);
    const content = document.createElement("div");
    content.className = "evidence-content text-secondary small";
    content.textContent = "Open record to retrieve its contents.";
    record.append(summary, content);
    evidenceList.append(record);
    const label = document.createElement("label");
    label.className = "form-check citation-item";
    const checkbox = document.createElement("input");
    checkbox.className = "form-check-input citation-checkbox";
    checkbox.type = "checkbox";
    checkbox.value = item.id;
    const caption = document.createElement("span");
    caption.className = "form-check-label";
    caption.textContent = `${item.id} — ${item.title}`;
    label.append(checkbox, caption);
    citationList.append(label);
    document.getElementById("evidence-count").textContent = evidenceList.querySelectorAll(".evidence-record").length;
  }

  questionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = questionInput.value.trim();
    if (!text) return;
    addMessage("user", "YOU", text);
    questionInput.value = "";
    askButton.disabled = true;
    try {
      const response = await fetch(chatUrl, {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken},
        body: JSON.stringify({text}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "The case agent could not process that question.");
      data.new_evidence.forEach(addEvidence);
      data.adaptive_evidence.forEach(addEvidence);
      addMessage("agent", "CASE AGENT", data.response);
    } catch (error) {
      addMessage("agent", "CASE AGENT", error.message);
    } finally {
      askButton.disabled = false;
      questionInput.focus();
    }
  });

  evidenceList.addEventListener("toggle", async (event) => {
    const record = event.target;
    if (!(record instanceof HTMLDetailsElement) || !record.open || record.dataset.loaded) return;
    try {
      const response = await fetch(`${window.location.pathname}evidence/${encodeURIComponent(record.dataset.evidenceId)}/`, {
        method: "POST",
        headers: {"X-CSRFToken": csrfToken},
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not open this evidence record.");
      record.querySelector(".evidence-content").textContent = data.content;
      record.dataset.loaded = "true";
    } catch (error) {
      record.querySelector(".evidence-content").textContent = error.message;
    }
  }, true);

  const confidence = document.getElementById("confidence");
  confidence.addEventListener("input", () => {
    document.getElementById("confidence-value").textContent = `${confidence.value}%`;
  });

  conclusionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const errorBox = document.getElementById("conclusion-error");
    errorBox.classList.add("d-none");
    const button = conclusionForm.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const response = await fetch(conclusionForm.dataset.url, {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken},
        body: JSON.stringify({
          text: document.getElementById("conclusion").value.trim(),
          confidence: Number(confidence.value) / 100,
          cited_evidence: [...document.querySelectorAll(".citation-checkbox:checked")].map((item) => item.value),
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not submit this conclusion.");
      window.location.assign(data.report_url);
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.classList.remove("d-none");
      button.disabled = false;
    }
  });
})();
