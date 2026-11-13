(() => {
  const root = document.documentElement;
  const savedTheme = localStorage.getItem("cf-theme");
  if (savedTheme === "light") root.classList.add("light");
  const toggle = document.querySelector(".theme-toggle");
  if (toggle) toggle.addEventListener("click", () => {
    root.classList.toggle("light");
    localStorage.setItem("cf-theme", root.classList.contains("light") ? "light" : "dark");
  });

  const input = document.getElementById("file-input");
  const zone = document.querySelector(".upload-zone");
  const selected = document.getElementById("selected-files");
  const form = document.getElementById("upload-form");
  const progress = document.getElementById("upload-progress");
  if (input && selected) {
    const showFiles = files => {
      selected.textContent = files.length ? `${files.length} file${files.length === 1 ? "" : "s"} selected` : "";
    };
    input.addEventListener("change", () => showFiles(input.files));
    ["dragenter", "dragover"].forEach(type => zone.addEventListener(type, e => {
      e.preventDefault(); zone.classList.add("dragover");
    }));
    ["dragleave", "drop"].forEach(type => zone.addEventListener(type, e => {
      e.preventDefault(); zone.classList.remove("dragover");
    }));
    zone.addEventListener("drop", e => {
      if (e.dataTransfer.files.length) {
        input.files = e.dataTransfer.files;
        showFiles(input.files);
      }
    });
    form.addEventListener("submit", () => {
      progress.style.display = "block";
      progress.firstElementChild.style.width = "85%";
    });
  }

  document.querySelectorAll(".copy-link").forEach(button => button.addEventListener("click", async () => {
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = "Copied";
      setTimeout(() => button.textContent = original, 1400);
    } catch {
      button.textContent = "Copy unavailable";
      setTimeout(() => button.textContent = original, 1400);
    }
  }));
})();
