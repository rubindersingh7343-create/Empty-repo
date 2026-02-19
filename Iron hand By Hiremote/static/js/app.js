document.addEventListener("DOMContentLoaded", () => {
  const fileInputs = document.querySelectorAll('input[type="file"]');
  fileInputs.forEach((input) => {
    input.addEventListener("change", () => {
      const label = input.closest("label");
      if (!label) {
        return;
      }
      const helper = label.querySelector(".file-helper") || document.createElement("small");
      helper.classList.add("file-helper");
      helper.textContent = input.files.length
        ? `${input.files.length} file${input.files.length > 1 ? "s" : ""} selected`
        : "No file selected";
      if (!label.contains(helper)) {
        label.appendChild(helper);
      }
    });
  });

  const assistantToggle = document.getElementById("assistant-toggle");
  if (!assistantToggle) {
    return;
  }

  const assistantModal = document.getElementById("assistant-modal");
  const assistantClose = document.getElementById("assistant-close");
  const assistantMessages = document.getElementById("assistant-messages");
  const assistantForm = document.getElementById("assistant-form");
  const assistantInput = document.getElementById("assistant-input");
  const voiceButton = document.getElementById("assistant-voice");

  const history = [];
  let voiceMode = false;
  let listening = false;
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  const recognition = SpeechRecognition ? new SpeechRecognition() : null;

  const updateVoiceButton = () => {
    if (!voiceButton) {
      return;
    }
    voiceButton.classList.toggle("active", voiceMode);
    voiceButton.textContent = listening
      ? "Listening..."
      : voiceMode
      ? "Voice On"
      : "Voice Off";
  };

  const speakText = (text) => {
    if (!voiceMode || !window.speechSynthesis) {
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1;
    utterance.pitch = 1;
    window.speechSynthesis.speak(utterance);
  };

  const appendMessage = (role, text) => {
    const bubble = document.createElement("div");
    bubble.classList.add("assistant-message", role);
    bubble.textContent = text;
    assistantMessages.appendChild(bubble);
    assistantMessages.scrollTop = assistantMessages.scrollHeight;
    return bubble;
  };

  const setModalOpen = (open) => {
    assistantModal.classList.toggle("active", open);
    assistantModal.setAttribute("aria-hidden", open ? "false" : "true");
    if (open) {
      assistantInput.focus();
    }
  };

  assistantToggle.addEventListener("click", () => setModalOpen(true));
  assistantClose.addEventListener("click", () => setModalOpen(false));
  assistantModal.addEventListener("click", (event) => {
    if (event.target === assistantModal) {
      setModalOpen(false);
    }
  });

  const sendMessage = async (text) => {
    if (!text) {
      return;
    }
    appendMessage("user", text);
    history.push({ role: "user", content: text });
    assistantInput.value = "";

    const loadingBubble = appendMessage("assistant", "Thinking...");

    try {
      const response = await fetch("/api/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          history: history.slice(-6),
        }),
      });
      const data = await response.json();
      const reply = data.reply || "I couldn't find that information yet.";
      loadingBubble.textContent = reply;
      history.push({ role: "assistant", content: reply });
      speakText(reply);
    } catch (error) {
      loadingBubble.textContent =
        "Sorry, I ran into an error while reaching the assistant.";
    }
  };

  assistantForm.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage(assistantInput.value.trim());
  });

  if (voiceButton) {
    if (!recognition) {
      voiceButton.textContent = "Voice Unavailable";
      voiceButton.disabled = true;
    } else {
      recognition.lang = "en-US";
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;

      recognition.addEventListener("result", (event) => {
        const transcript = event.results?.[0]?.[0]?.transcript || "";
        listening = false;
        updateVoiceButton();
        if (transcript) {
          sendMessage(transcript);
        }
      });

      recognition.addEventListener("end", () => {
        listening = false;
        updateVoiceButton();
      });

      recognition.addEventListener("error", () => {
        listening = false;
        updateVoiceButton();
      });

      voiceButton.addEventListener("click", () => {
        if (!voiceMode) {
          voiceMode = true;
        } else if (!listening) {
          voiceMode = false;
          updateVoiceButton();
          return;
        }

        if (listening) {
          recognition.stop();
          listening = false;
          updateVoiceButton();
          return;
        }

        listening = true;
        updateVoiceButton();
        recognition.start();
      });
    }
  }
});
