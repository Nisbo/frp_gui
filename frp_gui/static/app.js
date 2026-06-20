(function () {
  const key = "frp-gui-theme";
  const root = document.documentElement;
  const saved = localStorage.getItem(key);
  if (saved === "dark") {
    root.dataset.theme = "dark";
  }

  document.querySelector(".theme-toggle")?.addEventListener("click", () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    if (next === "dark") {
      root.dataset.theme = "dark";
      localStorage.setItem(key, "dark");
    } else {
      delete root.dataset.theme;
      localStorage.setItem(key, "light");
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
      const internalPort = read("internal_port", "8844");

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
})();
