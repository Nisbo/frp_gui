(function () {
  const key = "frp-gui-theme";
  const root = document.documentElement;
  const saved = localStorage.getItem(key);
  if (saved === "dark") {
    root.dataset.theme = "dark";
  }

  const updateThemeButtons = () => {
    const active = root.dataset.theme === "dark" ? "dark" : "light";
    document.querySelectorAll(".theme-option").forEach((button) => {
      button.classList.toggle("active", button.dataset.themeChoice === active);
    });
  };

  document.querySelectorAll(".theme-option").forEach((button) => {
    button.addEventListener("click", () => {
      const next = button.dataset.themeChoice;
      if (next === "dark") {
        root.dataset.theme = "dark";
        localStorage.setItem(key, "dark");
      } else {
        delete root.dataset.theme;
        localStorage.setItem(key, "light");
      }
      updateThemeButtons();
    });
  });
  updateThemeButtons();

  const confirmModal = document.querySelector("[data-confirm-modal]");
  const confirmMessage = document.querySelector("[data-confirm-message]");
  const confirmCancel = document.querySelector("[data-confirm-cancel]");
  const confirmSubmit = document.querySelector("[data-confirm-submit]");
  let pendingForm = null;

  const closeConfirm = () => {
    if (!confirmModal) return;
    confirmModal.hidden = true;
    pendingForm = null;
  };

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.confirmed === "true") {
        delete form.dataset.confirmed;
        return;
      }
      event.preventDefault();
      pendingForm = form;
      if (confirmMessage) {
        confirmMessage.textContent = form.dataset.confirm || "Continue?";
      }
      if (confirmModal) {
        confirmModal.hidden = false;
      }
    });
  });

  confirmCancel?.addEventListener("click", closeConfirm);
  confirmModal?.addEventListener("click", (event) => {
    if (event.target === confirmModal) {
      closeConfirm();
    }
  });
  confirmSubmit?.addEventListener("click", () => {
    if (!pendingForm) return;
    pendingForm.dataset.confirmed = "true";
    pendingForm.requestSubmit();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && confirmModal && !confirmModal.hidden) {
      closeConfirm();
    }
  });

  const networkForm = document.querySelector(".network-form");
  const nginxPreview = document.querySelector("#nginx-preview");

  if (networkForm && nginxPreview) {
    const read = (name, fallback) => {
      const value = networkForm.elements[name]?.value?.trim();
      return value || fallback;
    };

    const renderPreview = () => {
      const publicPort = read("public_port", "8844");
      const serverName = read("server_name", "_");
      const internalHost = read("internal_host", "127.0.0.1");
      const internalPort = read("internal_port", "8845");

      nginxPreview.textContent = `server {
    listen ${publicPort};
    server_name ${serverName};

    location / {
        proxy_pass http://${internalHost}:${internalPort};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
`;
    };

    networkForm.addEventListener("input", renderPreview);
    renderPreview();
  }

  const wizardForm = document.querySelector(".wizard-form");
  if (wizardForm) {
    const portInput = wizardForm.elements.local_port;
    const updateWizardDefaults = () => {
      const selectedType = wizardForm.elements.type?.value || "https";
      if (!portInput || portInput.dataset.userEdited === "true") return;
      portInput.value = selectedType === "http" ? "80" : "443";
    };

    portInput?.addEventListener("input", () => {
      portInput.dataset.userEdited = "true";
    });
    wizardForm.querySelectorAll("input[name='type']").forEach((input) => {
      input.addEventListener("change", updateWizardDefaults);
    });
    updateWizardDefaults();
  }
})();
