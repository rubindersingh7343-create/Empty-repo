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
});
